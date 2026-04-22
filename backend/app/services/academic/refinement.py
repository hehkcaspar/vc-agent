"""Background URL/category refinement for grounded-search items.

Runs as a fire-and-forget task after each source's synchronous write:

    SOURCE.run() → persist items with `_refinement_status: "pending"`
                 → asyncio.create_task(refine_pending_items(scholar_id,
                                                             category))

Per record, in parallel:

    (B) verify_item         — flash-lite grounded search
    (C) triage              — pure keep/drop decision
    (D) apply_url_fallback  — 3-tier URL quality check (only on KEEP)

Writes back to the ledger in place:

    KEEP  → url updated, `_refinement_status: "finalized"`
    DROP  → `_rejected: True`, `_refinement_status: "rejected"`,
            `_rejection_reason: <note>`, and a row appended to
            `_tombstones.jsonl` for the next run's prompt guard.

Crash-safe: if the task dies, records stay `"pending"` and the next
sweep picks them up.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .destinations import accept_into
from .file_utils import dossier_path, read_json, write_json
from .item_triage import triage
from .item_verification import verify_item
from .locks import scholar_write_lock
from .tombstones import write_tombstone
from .url_fallback import apply_url_fallback

logger = logging.getLogger(__name__)

_JSONL_CATEGORIES = {"news", "red_flags"}
_ITEMS_JSON_CATEGORIES = {"patents", "startups"}
_ALL_CATEGORIES = _JSONL_CATEGORIES | _ITEMS_JSON_CATEGORIES

# Verification concurrency — Gemini flash-lite + google_search. Keep
# modest to avoid rate-limiting the model provider.
_VERIFY_CONCURRENCY = 5
# HTTP fallback concurrency — pure HTTP, can be higher.
_HTTP_CONCURRENCY = 10


# ── Public API ────────────────────────────────────────────────────────


async def refine_pending_items(
    scholar_id: str,
    category: str,
    *,
    context: str,
) -> dict[str, Any]:
    """Refine every ``_refinement_status == "pending"`` record in the
    given ledger. Idempotent; safe to call repeatedly.
    """
    if category not in _ALL_CATEGORIES:
        logger.warning("refine: unknown category %r — skipping", category)
        return {"refined": 0, "kept": 0, "dropped": 0, "skipped": True}

    if category in _JSONL_CATEGORIES:
        return await _refine_jsonl(scholar_id, category, context)
    return await _refine_items_json(scholar_id, category, context)


# ── JSONL ledgers (news, red_flags) ───────────────────────────────────


async def _refine_jsonl(
    scholar_id: str, category: str, context: str,
) -> dict[str, Any]:
    path = dossier_path(scholar_id) / f"{category}.jsonl"
    if not path.exists():
        return {"refined": 0, "kept": 0, "dropped": 0}

    records = _read_jsonl(path)
    pending_idx = [
        i for i, r in enumerate(records)
        if isinstance(r, dict) and r.get("_refinement_status") == "pending"
    ]
    if not pending_idx:
        return {"refined": 0, "kept": 0, "dropped": 0}

    logger.info(
        "refine/%s: %d pending records for scholar %s",
        category, len(pending_idx), scholar_id,
    )

    sem_verify = asyncio.Semaphore(_VERIFY_CONCURRENCY)
    sem_http = asyncio.Semaphore(_HTTP_CONCURRENCY)

    async with _http_client() as http:
        async def _one(i: int) -> None:
            async with sem_verify:
                await _refine_record(
                    records[i], scholar_id, category,
                    context=context, http=http, sem_http=sem_http,
                )

        await asyncio.gather(*(_one(i) for i in pending_idx))

    async with scholar_write_lock(scholar_id):
        _write_jsonl(path, records)

    kept = sum(
        1 for i in pending_idx
        if records[i].get("_refinement_status") == "finalized"
    )
    dropped = sum(
        1 for i in pending_idx
        if records[i].get("_refinement_status") == "rejected"
    )
    return {"refined": len(pending_idx), "kept": kept, "dropped": dropped}


# ── JSON-items ledgers (patents, startups) ────────────────────────────


async def _refine_items_json(
    scholar_id: str, category: str, context: str,
) -> dict[str, Any]:
    path = dossier_path(scholar_id) / f"{category}.json"
    data = read_json(path)
    items: list[dict[str, Any]] = data.get("items") or []
    if not items:
        return {"refined": 0, "kept": 0, "dropped": 0}

    pending_idx = [
        i for i, it in enumerate(items)
        if isinstance(it, dict) and it.get("_refinement_status") == "pending"
    ]
    if not pending_idx:
        return {"refined": 0, "kept": 0, "dropped": 0}

    logger.info(
        "refine/%s: %d pending items for scholar %s",
        category, len(pending_idx), scholar_id,
    )

    sem_verify = asyncio.Semaphore(_VERIFY_CONCURRENCY)
    sem_http = asyncio.Semaphore(_HTTP_CONCURRENCY)

    async with _http_client() as http:
        async def _one(i: int) -> None:
            async with sem_verify:
                await _refine_record(
                    items[i], scholar_id, category,
                    context=context, http=http, sem_http=sem_http,
                )

        await asyncio.gather(*(_one(i) for i in pending_idx))

    async with scholar_write_lock(scholar_id):
        data["items"] = items
        data["count"] = len(items)
        write_json(path, data)

    kept = sum(
        1 for i in pending_idx
        if items[i].get("_refinement_status") == "finalized"
    )
    dropped = sum(
        1 for i in pending_idx
        if items[i].get("_refinement_status") == "rejected"
    )
    return {"refined": len(pending_idx), "kept": kept, "dropped": dropped}


# ── Per-record refinement ─────────────────────────────────────────────


async def _refine_record(
    record: dict[str, Any],
    scholar_id: str,
    category: str,
    *,
    context: str,
    http: httpx.AsyncClient,
    sem_http: asyncio.Semaphore,
) -> None:
    """Verify → triage → URL fallback. Mutates the record in place."""
    vr = await verify_item(record, context=context, source_category=category)
    record["_verification"] = {
        "verdict": vr.verdict,
        "category_correct": vr.category_correct,
        "evidence": vr.evidence,
        "correction_note": vr.correction_note,
        "error": vr.error,
    }

    decision = triage(record, vr, source_category=category)

    title = (
        record.get("title")
        or record.get("name")
        or record.get("claim")
        or ""
    )

    if decision.action in ("drop", "route"):
        # In both cases the source ledger no longer wants this record.
        # Soft-delete here; differ only in what happens downstream.
        record["_rejected"] = True
        record["_rejection_reason"] = decision.reason
        record["_refinement_status"] = "rejected"

        # Tombstone only in the SOURCE category so the next source run
        # doesn't re-emit. The destination (if any) runs its own
        # acceptance policy; we never tombstone its ledger.
        try:
            write_tombstone(
                scholar_id,
                category=category,
                title=str(title),
                reason=decision.reason,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("refine: tombstone write failed: %s", exc)

        if decision.action == "route" and decision.destination:
            try:
                result = await accept_into(
                    decision.destination,
                    scholar_id,
                    record,
                    source_category=category,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "refine: accept_into(%s) failed: %s",
                    decision.destination, exc,
                )
                result = {
                    "accepted": False,
                    "action": "exception",
                    "reason": str(exc),
                    "stored_id": "",
                }
            record["_routed_to"] = decision.destination
            record["_routing_result"] = result
            logger.info(
                "refine: routed %r from %s → %s: accepted=%s action=%s",
                title[:80], category, decision.destination,
                result.get("accepted"), result.get("action"),
            )
        return

    # KEEP → adopt authoritative URL if verification found one, then
    # run the 3-tier HTTP fallback to finalize.
    if vr.authoritative_url:
        field = _active_url_field(record)
        record[field] = vr.authoritative_url
        record["_url_source"] = "grounding"  # from verify's chunks

    async with sem_http:
        try:
            await apply_url_fallback(record, client=http)
        except Exception as exc:  # noqa: BLE001
            logger.debug("refine: url_fallback failed: %s", exc)

    record["_refinement_status"] = "finalized"


# ── helpers ────────────────────────────────────────────────────────────


def _active_url_field(item: dict[str, Any]) -> str:
    for f in ("url", "source_url"):
        if f in item:
            return f
    return "url"


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=8.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        },
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    tmp.replace(path)


async def build_scholar_context(scholar_id: str) -> str:
    """Pull a short one-liner from profile.json so verify_item can
    resolve ambiguous names.
    """
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    if not profile:
        return ""
    name = profile.get("name") or scholar_id
    aff = ((profile.get("affiliation") or {}).get("current") or "").strip()
    areas = profile.get("research_areas") or []
    if isinstance(areas, list) and areas:
        areas_str = ", ".join(str(a) for a in areas[:3])
    else:
        areas_str = ""
    parts = [name]
    if aff:
        parts.append(f"at {aff}")
    if areas_str:
        parts.append(f"(research: {areas_str})")
    return " ".join(parts)


__all__ = ["refine_pending_items", "build_scholar_context"]
