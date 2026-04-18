"""Layer 2 source — scholar-founded startups via Gemini grounded search.

Uses Path 2 (single-shot with google_search tool) to discover ventures
founded or co-founded by the scholar, then Path 1 (generate_structured)
to reject non-commercial entities (research consortia, academic
networks, non-profits) and dedupe within-batch. Writes wholesale to
`startups.json`; emits `log_event` rows for newly-discovered ventures
and status transitions (acquired / ipo / closed).

Upstream of `news_web._collect_known_ventures`, which reads
`startups.json` to enrich the news prompt with known venture names —
so populating this file closes Gap 3 in the first-eval pipeline for
free.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..events_sync import log_event
from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json

logger = logging.getLogger(__name__)

SOURCE_ID = "startups_web"


def _parse_date(raw: str | None) -> datetime | None:
    """Best-effort parse of a date string. Accepts bare year like '2016'."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z", "%B %d, %Y", "%d %B %Y", "%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _venture_key(item: dict[str, Any]) -> str:
    """Canonical dedup key for a venture — lowercase alphanumerics of name."""
    name = (item.get("name") or "").strip().lower()
    return "".join(c for c in name if c.isalnum())


_PROMPT_TEMPLATE = (
    "Find startups or companies founded or co-founded by academic "
    "researcher **{name}**{affiliation_clause}. Only include "
    "**commercial** entities — for-profit companies or commercialisation "
    "spin-outs. Do NOT include research consortia, academic networks, "
    "non-profit foundations, grant-funded collaborations, or incubators. "
    "Cover ventures that are active, acquired, closed, or went public. "
    "Return a JSON array where each item has: "
    "`name`, `url`, `founded_year`, `one_liner` (≤ 2 sentences), "
    "`current_status` (one of: operating, acquired, closed, ipo), "
    "`funding_total_usd` (number or null), "
    "`last_funding_type` (seed | series_a | series_b | series_c | ipo | "
    "acquired | null), `acquirer` (if acquired, else null), "
    "`acquisition_date` (ISO 8601 if known, else null), `notes`."
)


_FILTER_PROMPT = (
    "You are a relevance filter for a venture-commercial-history tracker.\n"
    "Scholar: **{name}**{affiliation_clause}\n\n"
    "For each candidate venture below, decide:\n"
    "1. `relevant` — Is this a **commercial** venture that {name} "
    "founded or co-founded? It must be a for-profit company or "
    "commercialisation spin-out where {name} is a founder or "
    "co-founder. Research consortia, academic networks, non-profit "
    "foundations, grant-funded collaborations, and incubators do NOT "
    "count. Ventures where {name} is only an advisor, board member, "
    "or investor do NOT count.\n"
    "2. `duplicate_of` — If this item covers the same venture as an "
    "earlier item (same company, different URL / spelling / "
    "subsidiary name), set this to the index of the earlier item. "
    "Otherwise null.\n\n"
    "Candidates:\n{candidates}\n"
)


async def _filter_ventures(
    candidates: list[dict[str, Any]],
    name: str,
    affiliation: str,
) -> list[dict[str, Any]]:
    """Filter candidates for commercial-venture relevance + dedupe."""
    if not candidates:
        return []

    from ....config import settings
    from ..llm_client import generate_structured
    from ..schemas import NewsFilterResult

    affiliation_clause = f" at {affiliation}" if affiliation else ""
    lines: list[str] = []
    for i, it in enumerate(candidates):
        nm = (it.get("name") or "").strip()
        summary = (it.get("one_liner") or "").strip()
        url = (it.get("url") or "").strip()
        lines.append(f"[{i}] {nm} — {url}: {summary}")

    prompt = _FILTER_PROMPT.format(
        name=name,
        affiliation_clause=affiliation_clause,
        candidates="\n".join(lines),
    )

    result = await generate_structured(
        model=settings.ACADEMIC_GEMINI_MODEL,
        prompt_parts=[prompt],
        response_schema=NewsFilterResult,
    )
    dominated = {
        it.duplicate_of for it in result.items if it.duplicate_of is not None
    }
    accepted = {
        it.index for it in result.items
        if it.relevant and it.index not in dominated
    }
    return [candidates[i] for i in sorted(accepted) if i < len(candidates)]


_TRANSITION_STATUSES = {"acquired", "ipo", "closed"}


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    profile = read_json(dossier_path(scholar_id) / "profile.json") or {}
    name = profile.get("name")
    if not name:
        sid = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={"mode": mode, "skipped": "no_scholar_name"},
        )
        return {"changed": False, "snapshot_id": sid, "error": "no_scholar_name"}

    affiliation = ((profile.get("affiliation") or {}).get("current")) or ""
    affiliation_clause = f" at {affiliation}" if affiliation else ""
    prompt = _PROMPT_TEMPLATE.format(
        name=name, affiliation_clause=affiliation_clause
    )

    try:
        from ..llm_client import grounded_search_json  # type: ignore
    except ImportError:
        logger.info("startups_web: llm_client not yet available; skipping")
        sid = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={"mode": mode, "skipped": "no_llm_client"},
        )
        return {"changed": False, "snapshot_id": sid, "skipped": True}

    try:
        candidates = await grounded_search_json(prompt)
    except Exception as e:
        logger.exception("startups_web: grounded search failed for %s", scholar_id)
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "error": str(e)},
        )
        return {"changed": False, "snapshot_id": sid, "error": str(e)}

    if not isinstance(candidates, list):
        candidates = []

    # Minimum-shape validation
    valid: list[dict[str, Any]] = []
    for it in candidates:
        if not isinstance(it, dict):
            continue
        if not (it.get("name") or "").strip():
            continue
        valid.append(it)

    try:
        filtered = await _filter_ventures(valid, name, affiliation)
    except Exception:
        logger.warning(
            "startups_web: relevance filter failed; using unfiltered",
            exc_info=True,
        )
        filtered = valid

    # Merge with existing startups.json — keyed by normalised name.
    path = dossier_path(scholar_id) / "startups.json"
    existing = read_json(path) or {}
    # Drop scaffold sentinel on first real run.
    existing_items = [
        it for it in (existing.get("items") or []) if isinstance(it, dict)
    ]
    by_key: dict[str, dict[str, Any]] = {}
    for it in existing_items:
        k = _venture_key(it)
        if k:
            by_key[k] = it

    # Emit one "startup_founded" event per newly-discovered venture, plus
    # a terminal-transition event if its current status is non-operating.
    # First discovery of an already-acquired venture therefore logs BOTH
    # founding (at founded_year) and acquisition (at acquisition_date) —
    # both are real timeline events we just didn't know about before.
    transitions: list[tuple[str, dict[str, Any]]] = []
    for it in filtered:
        k = _venture_key(it)
        if not k:
            continue
        prev = by_key.get(k)
        merged = {**(prev or {}), **it}
        prev_status = (prev or {}).get("current_status")
        new_status = it.get("current_status")
        if prev is None:
            transitions.append(("startup_founded", merged))
            if new_status in _TRANSITION_STATUSES:
                transitions.append((f"startup_{new_status}", merged))
        elif (
            prev_status != new_status
            and new_status in _TRANSITION_STATUSES
        ):
            transitions.append((f"startup_{new_status}", merged))
        by_key[k] = merged

    items = sorted(
        by_key.values(),
        key=lambda r: int(r.get("founded_year") or 0),
        reverse=True,
    )
    write_json(path, {"items": items, "count": len(items)})

    for event_type, item in transitions:
        try:
            # Founding uses founded_year; transitions use acquisition_date
            # when present (falls back to None = unknown).
            if event_type == "startup_founded":
                parsed_date = _parse_date(str(item.get("founded_year") or ""))
            else:
                parsed_date = _parse_date(item.get("acquisition_date"))
            await log_event(
                scholar_id,
                event_type=event_type,
                title=(item.get("name") or "")[:120],
                significance="high",
                event_date=parsed_date,
                payload={
                    "url": item.get("url"),
                    "one_liner": (item.get("one_liner") or "")[:300],
                    "current_status": item.get("current_status"),
                    "funding_total_usd": item.get("funding_total_usd"),
                    "acquirer": item.get("acquirer"),
                },
            )
        except Exception:
            logger.warning("startups_web: log_event failed", exc_info=True)

    snapshot_id = await record_snapshot(
        scholar_id, SOURCE_ID,
        detail={
            "mode": mode,
            "reason": reason,
            "new_events": len(transitions),
            "total": len(items),
        },
    )
    return {
        "changed": len(transitions) > 0,
        "snapshot_id": snapshot_id,
        "new_events": len(transitions),
        "total": len(items),
    }
