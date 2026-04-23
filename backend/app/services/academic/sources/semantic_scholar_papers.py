"""Layer 2 source — Semantic Scholar papers.

Fetches the scholar's full paper list via the existing
`SemanticScholarService`, writes `papers.json`, recomputes
`attributed_metrics.json`, and appends a snapshot marker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ....config import settings
from ..attributed_metrics import compute_attributed_metrics
from ..events_sync import log_event, paper_significance
from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json
from ..papers_merge import _merge_papers_by_priority, normalize_ledger_row
from ..semantic_scholar import SemanticScholarService

logger = logging.getLogger(__name__)

SOURCE_ID = "semantic_scholar_papers"
_DEFAULT_LIMIT = 500


def _scholar_ss_id(scholar_id: str) -> str | None:
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    ident = (profile or {}).get("identity") or {}
    ss = ident.get("semantic_scholar") or {}
    return ss.get("id") or None


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    ss_id = _scholar_ss_id(scholar_id)
    if not ss_id:
        logger.warning(
            "semantic_scholar_papers: scholar %s has no SS author id; skipping",
            scholar_id,
        )
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_ss_author_id"},
        )
        return {"changed": False, "snapshot_id": sid, "error": "no_ss_author_id"}

    ss = SemanticScholarService(api_key=settings.SEMANTIC_SCHOLAR_API_KEY)
    try:
        papers = await ss.get_author_papers(ss_id, limit=_DEFAULT_LIMIT)
    except Exception as e:
        logger.exception(
            "semantic_scholar_papers: fetch failed for %s", scholar_id
        )
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "error": str(e)},
        )
        return {"changed": False, "snapshot_id": sid, "error": str(e)}

    papers_path = dossier_path(scholar_id) / "papers.json"
    prev = read_json(papers_path)
    prev_items = [
        normalize_ledger_row(p)
        for p in (prev.get("items") or []) if isinstance(p, dict)
    ]
    prev_ids = {p.get("id") for p in prev_items if p.get("id")}
    new_ids = {p.get("id") for p in papers if p.get("id")}

    # Enrichment pass — SS runs alongside google_scholar_papers as a
    # co-primary. GS owns recency; SS enriches matched titles with
    # authorId / DOI / s2_fields / influential_citations. Merge logic
    # (papers_merge.py) encodes the priority matrix.
    merged_items = _merge_papers_by_priority(
        papers, prev_items, incoming_source="semantic_scholar",
    )
    changed = (
        prev_ids != new_ids
        or len(merged_items) != len(prev_items)
    )

    write_json(papers_path, {"items": merged_items, "count": len(merged_items)})

    # Attributed metrics draw on the MERGED ledger so GS-only papers
    # participate via their _author_position heuristic; SS-enriched
    # papers use their authorId.
    metrics = compute_attributed_metrics(merged_items, ss_id)
    write_json(dossier_path(scholar_id) / "attributed_metrics.json", metrics)

    # Mirror newly-discovered papers to the timeline + signal feed.
    # Skip on the bootstrap cold-start: every paper looks "new" on
    # the first run which would dump hundreds of events into the
    # feed. We only log events on incremental refreshes.
    newly_added_events = 0
    if mode == "incremental" and prev_ids:
        ss_id_str = str(ss_id)
        truly_new = [p for p in papers if p.get("id") and p["id"] not in prev_ids]
        for p in truly_new[:20]:  # cap per-run to avoid spam
            try:
                position = _scholar_position(p, ss_id_str)
                paper_date = None
                if p.get("year"):
                    try:
                        paper_date = datetime(int(p["year"]), 1, 1, tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass
                await log_event(
                    scholar_id,
                    event_type="new_paper",
                    title=f"New paper: {(p.get('title') or '?')[:100]}",
                    significance=paper_significance(
                        int(p.get("citations") or 0), position
                    ),
                    event_date=paper_date,
                    payload={
                        "ss_paper_id": p.get("id"),
                        "title": p.get("title"),
                        "year": p.get("year"),
                        "venue": p.get("venue") or p.get("journal"),
                        "citations": p.get("citations"),
                        "position": position,
                    },
                )
                newly_added_events += 1
            except Exception:
                logger.warning(
                    "semantic_scholar_papers: log_event failed", exc_info=True
                )

    snapshot_id = await record_snapshot(
        scholar_id,
        SOURCE_ID,
        detail={
            "mode": mode,
            "reason": reason,
            "paper_count": len(papers),          # what SS returned
            "merged_count": len(merged_items),   # size of the ledger after merge
            "changed": changed,
            "events_logged": newly_added_events,
        },
    )

    return {
        "changed": changed,
        "snapshot_id": snapshot_id,
        "paper_count": len(papers),
        "merged_count": len(merged_items),
        "events_logged": newly_added_events,
    }


def _scholar_position(paper: dict[str, Any], ss_id: str) -> str | None:
    """Return 'first' | 'last' | 'middle' | None for the scholar on a paper."""
    authors = paper.get("authors") or []
    if not authors:
        return None
    n = len(authors)
    for i, a in enumerate(authors):
        if str(a.get("authorId") or "") == ss_id:
            if i == 0:
                return "first"
            if i == n - 1 and n >= 3:
                return "last"
            return "middle"
    return None
