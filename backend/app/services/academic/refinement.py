"""Academic-side refinement glue.

The verify→triage→url_fallback orchestrator itself lives in
``services.grounded_extraction.refinement.refine_jsonl`` (Tier 2
refactor, 2026-05-02) and is shared with portfolio entity news. This
module just supplies the academic-specific ``LedgerStorage`` (scholar
dossier paths, scholar write lock, scholar tombstones, scholar
destinations) and keeps the dispatcher for the items.json categories
(patents, startups) which haven't been generalised yet.

Public API unchanged: ``refine_pending_items(scholar_id, category,
*, context)`` keeps working for academic source modules.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from app.services.grounded_extraction import (
    LedgerStorage,
    apply_url_fallback,
    refine_jsonl,
    triage,
    verify_item,
)
from .destinations import accept_into
from .file_utils import dossier_path, read_json, write_json
from .locks import scholar_write_lock
from .tombstones import write_tombstone

logger = logging.getLogger(__name__)

_JSONL_CATEGORIES = {"news", "red_flags"}
_ITEMS_JSON_CATEGORIES = {"patents", "startups"}
_ALL_CATEGORIES = _JSONL_CATEGORIES | _ITEMS_JSON_CATEGORIES

# Verification concurrency — Gemini flash-lite + google_search. Keep
# modest to avoid rate-limiting the model provider.
_VERIFY_CONCURRENCY = 5
# HTTP fallback concurrency — pure HTTP, can be higher.
_HTTP_CONCURRENCY = 10


# ── LedgerStorage adapter for the scholar side ────────────────────────


def _scholar_jsonl_path(scholar_id: str, category: str) -> Path:
    return dossier_path(scholar_id) / f"{category}.jsonl"


@asynccontextmanager
async def _scholar_lock_ctx(scholar_id: str):
    """Adapter from scholar_write_lock to the LedgerStorage write_lock
    contract (a single-arg async context manager)."""
    async with scholar_write_lock(scholar_id):
        yield


def _scholar_tombstone(
    scholar_id: str, *, category: str, title: str, reason: str,
) -> None:
    write_tombstone(
        scholar_id, category=category, title=title, reason=reason,
    )


SCHOLAR_STORAGE = LedgerStorage(
    jsonl_path=_scholar_jsonl_path,
    write_lock=_scholar_lock_ctx,
    write_tombstone=_scholar_tombstone,
    accept_into=accept_into,
)


# ── Public API ────────────────────────────────────────────────────────


async def refine_pending_items(
    scholar_id: str,
    category: str,
    *,
    context: str,
) -> dict[str, Any]:
    """Refine every ``_refinement_status == "pending"`` record in the
    given ledger. Idempotent; safe to call repeatedly.

    JSONL categories (news, red_flags) route through the shared
    ``grounded_extraction.refine_jsonl`` orchestrator. ``items.json``
    categories (patents, startups) still use the local handler below
    until they're generalised to JSONL too.
    """
    if category not in _ALL_CATEGORIES:
        logger.warning("refine: unknown category %r — skipping", category)
        return {"refined": 0, "kept": 0, "dropped": 0, "skipped": True}

    if category in _JSONL_CATEGORIES:
        return await refine_jsonl(
            scholar_id, category,
            context=context,
            storage=SCHOLAR_STORAGE,
        )
    return await _refine_items_json(scholar_id, category, context)


# ── JSON-items ledgers (patents, startups) ────────────────────────────
#
# These haven't been generalised into the shared module yet because
# they use a different on-disk shape (``{count, items: [...]}`` wrapper)
# and the destinations matter (route patent → patent ledger). Lift to
# grounded_extraction in a follow-up if portfolio ever needs the same
# ledger shape.


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


# ── Per-record refinement (scholar items.json categories) ─────────────


async def _refine_record(
    record: dict[str, Any],
    scholar_id: str,
    category: str,
    *,
    context: str,
    http: httpx.AsyncClient,
    sem_http: asyncio.Semaphore,
) -> None:
    """Verify → triage → URL fallback. Mutates the record in place.
    Used for items.json categories only — the shared orchestrator in
    grounded_extraction handles the JSONL path."""
    vr = await verify_item(record, context=context, source_category=category)
    record["_verification"] = {
        "verdict": vr.verdict,
        "subject_match": vr.subject_match,
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
        record["_rejected"] = True
        record["_rejection_reason"] = decision.reason
        record["_refinement_status"] = "rejected"

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

    if vr.authoritative_url:
        existing = record.get("_grounding_chunk_urls") or []
        if not isinstance(existing, list):
            existing = []
        if vr.authoritative_url not in existing:
            record["_grounding_chunk_urls"] = [vr.authoritative_url, *existing]
            record["_all_grounding_urls"] = record["_grounding_chunk_urls"]

    async with sem_http:
        try:
            await apply_url_fallback(record, client=http)
        except Exception as exc:  # noqa: BLE001
            logger.debug("refine: url_fallback failed: %s", exc)

    record["_refinement_status"] = "finalized"


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


__all__ = ["refine_pending_items", "build_scholar_context", "SCHOLAR_STORAGE"]
