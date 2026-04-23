"""Destination autonomy layer.

When triage emits a ``ROUTE`` decision, the source tombstones the item
(so it's not re-emitted from that search prompt) and hands the item
off to ``accept_into(destination, scholar_id, item, source_category)``.
Each destination decides, on its own terms, whether to accept.

Accept results are uniform:

    {
        "accepted":   bool,   # did the destination store the record?
        "action":     str,    # what it did: added_stub / already_tracked / ...
        "reason":     str,    # one-sentence explanation
        "stored_id":  str,    # record id if stored, else empty
    }

The source refinement logs the result on the original (now-rejected)
record so there's a clear audit trail: source dropped + destination's
decision.

v1 implements ``papers`` only — that's the observed need (patent
sources keep mislabelling research papers as patents). Other
destinations declare ``not_implemented`` so routes surface loudly in
the logs until we wire them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_utils import dossier_path, read_json, write_json
from .locks import scholar_write_lock
from .papers_merge import _normalize_title

logger = logging.getLogger(__name__)


def _paper_stub_id(title: str) -> str:
    """Deterministic id for a stub paper so repeated accepts dedup."""
    h = hashlib.sha1(_normalize_title(title).encode("utf-8")).hexdigest()[:12]
    return f"stub-{h}"


async def accept_into(
    destination: str,
    scholar_id: str,
    item: dict[str, Any],
    *,
    source_category: str,
) -> dict[str, Any]:
    """Dispatch. Each destination owns its acceptance policy."""
    if destination == "papers":
        return await _accept_into_papers(
            scholar_id, item, source_category=source_category,
        )
    # Other destinations not yet implemented — triage-surfaced routes
    # to these will tombstone the source and end up as unaccepted.
    # That's a loud "we need to wire X" signal.
    return {
        "accepted": False,
        "action": "not_implemented",
        "reason": (
            f"accept_into('{destination}') is not implemented yet; "
            f"item was tombstoned in source ('{source_category}') but "
            f"no destination picked it up."
        ),
        "stored_id": "",
    }


# ── papers: minimal stub acceptance ────────────────────────────────────


async def _accept_into_papers(
    scholar_id: str,
    item: dict[str, Any],
    *,
    source_category: str,
) -> dict[str, Any]:
    """papers.json is Semantic-Scholar-authoritative. If SS already has
    the paper, decline (no-op) — SS will enrich on its own cadence.
    Otherwise append a minimal stub so the paper is not lost.
    """
    title = (
        item.get("title")
        or item.get("name")
        or item.get("claim")
        or ""
    ).strip()
    if not title:
        return {
            "accepted": False,
            "action": "shape_invalid",
            "reason": "no title on incoming item",
            "stored_id": "",
        }

    path = dossier_path(scholar_id) / "papers.json"
    data = read_json(path) or {}
    items: list[dict[str, Any]] = data.get("items") or []
    norm = _normalize_title(title)

    # SS-authoritative duplicate check: normalized title match.
    for existing in items:
        if _normalize_title(existing.get("title") or "") == norm:
            return {
                "accepted": False,
                "action": "already_tracked",
                "reason": (
                    "paper already in papers.json (Semantic-Scholar "
                    "authoritative); no stub added"
                ),
                "stored_id": existing.get("id") or "",
            }

    # Build a minimal stub. Enrichment (citations, venue, etc.) is SS's
    # job; we flag _stub + _needs_ss_enrichment so UI can render a
    # muted row and SS's ingestion can upgrade it in-place by id.
    inventors = item.get("inventors")
    if not isinstance(inventors, list):
        inventors = []
    authors = [
        {"name": str(n)} for n in inventors if isinstance(n, (str, bytes))
    ]
    year = _infer_year(item)

    stub = {
        "id": _paper_stub_id(title),
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": (
            item.get("abstract")
            or item.get("summary")
            or item.get("source_summary")
            or ""
        )[:1200],
        "_stub": True,
        "_origin": f"routed_from:{source_category}",
        "_original_url": item.get("url") or item.get("source_url") or "",
        "_routed_at": datetime.now(timezone.utc).isoformat(),
        "_needs_ss_enrichment": True,
    }

    async with scholar_write_lock(scholar_id):
        # Re-read under lock to avoid losing concurrent writes.
        data = read_json(path) or {}
        items = data.get("items") or []
        for existing in items:
            if existing.get("id") == stub["id"]:
                # Raced — someone else wrote the same stub; no-op.
                return {
                    "accepted": False,
                    "action": "already_tracked",
                    "reason": "concurrent stub write; no-op",
                    "stored_id": stub["id"],
                }
        items.append(stub)
        data["items"] = items
        data["count"] = len(items)
        write_json(path, data)

    return {
        "accepted": True,
        "action": "added_stub",
        "reason": f"added papers.json stub from {source_category}",
        "stored_id": stub["id"],
    }


def _infer_year(item: dict[str, Any]) -> int | None:
    for key in ("grant_date", "filing_date", "publication_date",
                "published_date", "founded_year"):
        v = item.get(key)
        if v is None:
            continue
        if isinstance(v, int):
            return v if 1900 <= v <= 2100 else None
        s = str(v)
        m = re.search(r"(19\d{2}|20\d{2})", s)
        if m:
            return int(m.group(1))
    return None


__all__ = ["accept_into"]
