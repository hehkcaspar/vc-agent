"""Entity-agnostic refinement orchestrator.

Runs as a fire-and-forget task after each source's synchronous write:

    SOURCE.run() → persist items with `_refinement_status: "pending"`
                 → asyncio.create_task(refine_jsonl(
                       subject_id, category, context=..., storage=...))

Per record, in parallel:

    (B) verify_item         — flash-lite grounded search (existence +
                              subject identity)
    (C) triage              — pure keep/drop/route decision
    (D) apply_url_fallback  — multi-candidate URL content match (only
                              on KEEP)

Writes back to the ledger in place:

    KEEP  → url updated, `_refinement_status: "finalized"`
    DROP  → `_rejected: True`, `_refinement_status: "rejected"`,
            `_rejection_reason: <note>`, plus a tombstone (when the
            storage backend supports it) so the next run's prompt
            doesn't re-emit the same fictional item.

Crash-safe: if the task dies, records stay `"pending"` and the next
sweep picks them up.

This module previously lived under ``services/academic/refinement.py``
and was scholar-specific. Lifted out 2026-05-02 so portfolio entity
news can use the same pipeline (Tier 2 of the news-quality fix).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .item_triage import triage
from .item_verification import verify_item
from .storage import LedgerStorage
from .url_fallback import apply_url_fallback

logger = logging.getLogger(__name__)

# Verification concurrency — Gemini flash-lite + google_search. Keep
# modest to avoid rate-limiting the model provider.
_VERIFY_CONCURRENCY = 5
# HTTP fallback concurrency — pure HTTP, can be higher.
_HTTP_CONCURRENCY = 10


# ── Public API ────────────────────────────────────────────────────────


async def refine_jsonl(
    subject_id: str,
    category: str,
    *,
    context: str,
    storage: LedgerStorage,
) -> dict[str, Any]:
    """Refine every ``_refinement_status == "pending"`` record in the
    given subject's ``{category}.jsonl`` ledger. Idempotent; safe to
    call repeatedly.

    Mutation strategy: we read the ledger, run verify+triage+url_fallback
    on each pending record concurrently, and atomically write the
    updated ledger back inside the storage's per-subject write lock.

    Returns ``{"refined": int, "kept": int, "dropped": int}``.
    """
    path = storage.jsonl_path(subject_id, category)
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
        "refine_jsonl/%s: %d pending records for subject %s",
        category, len(pending_idx), subject_id,
    )

    sem_verify = asyncio.Semaphore(_VERIFY_CONCURRENCY)
    sem_http = asyncio.Semaphore(_HTTP_CONCURRENCY)

    async with _http_client() as http:
        async def _one(i: int) -> None:
            async with sem_verify:
                await _refine_record(
                    records[i], subject_id, category,
                    context=context, http=http, sem_http=sem_http,
                    storage=storage,
                )

        await asyncio.gather(*(_one(i) for i in pending_idx))

    async with storage.write_lock(subject_id):
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


# ── Per-record refinement ─────────────────────────────────────────────


async def _refine_record(
    record: dict[str, Any],
    subject_id: str,
    category: str,
    *,
    context: str,
    http: httpx.AsyncClient,
    sem_http: asyncio.Semaphore,
    storage: LedgerStorage,
) -> None:
    """Verify → triage → URL fallback. Mutates the record in place."""
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
        # In both cases the source ledger no longer wants this record.
        # Soft-delete here; differ only in what happens downstream.
        record["_rejected"] = True
        record["_rejection_reason"] = decision.reason
        record["_refinement_status"] = "rejected"

        # Tombstone only in the SOURCE category so the next source run
        # doesn't re-emit. Optional — domains without a tombstone ledger
        # pass a no-op writer.
        try:
            storage.write_tombstone(
                subject_id,
                category=category,
                title=str(title),
                reason=decision.reason,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("refine: tombstone write failed: %s", exc)

        if decision.action == "route" and decision.destination:
            if storage.accept_into is None:
                # Domain doesn't support routing — drop fully.
                logger.info(
                    "refine: route %r → %s requested but storage has no "
                    "accept_into; treating as drop",
                    title[:80], decision.destination,
                )
                return
            try:
                result = await storage.accept_into(
                    decision.destination,
                    subject_id,
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

    # KEEP → register the authoritative URL as a candidate and let
    # url_fallback decide whether it (or the LLM URL, or another chunk)
    # actually content-matches.
    if vr.authoritative_url:
        existing = record.get("_grounding_chunk_urls") or []
        if not isinstance(existing, list):
            existing = []
        if vr.authoritative_url not in existing:
            # Push to front so url_fallback prefers verify's pick over
            # older chunk URLs from the original grounded search.
            record["_grounding_chunk_urls"] = [vr.authoritative_url, *existing]
            record["_all_grounding_urls"] = record["_grounding_chunk_urls"]

    async with sem_http:
        try:
            await apply_url_fallback(record, client=http)
        except Exception as exc:  # noqa: BLE001
            logger.debug("refine: url_fallback failed: %s", exc)

    record["_refinement_status"] = "finalized"


# ── helpers ────────────────────────────────────────────────────────────


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


__all__ = ["refine_jsonl"]
