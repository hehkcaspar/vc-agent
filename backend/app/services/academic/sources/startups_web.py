"""Layer 2 source — scholar-founded startups via Gemini grounded search.

Uses Path 2 (single-shot with google_search tool) to discover ventures,
Path 1 (generate_structured) to filter for commercial relevance, and
Path 1 again to canonicalize candidates against the existing ledger
(fuzzy match beyond rule-based name keys). Writes wholesale to
`startups.json`; emits `log_event` rows for newly-discovered ventures
and status transitions (acquired / ipo / closed).

Two modes:

- ``bootstrap`` — full-sweep "find every venture this scholar founded"
  query. Used for first evaluation or user-forced refresh.
- ``incremental`` — status-check on known ventures since last tick +
  scan for new ventures since last tick. Saves tokens, actively
  surfaces status transitions (operating → acquired) that a plain
  full-sweep query might miss if grounded search doesn't resurface
  the known venture.

Upstream of `news_web._collect_known_ventures`, which reads
`startups.json` to enrich the news prompt with known venture names.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..events_sync import log_event
from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json
from ..refinement import build_scholar_context, refine_pending_items
from ..tombstones import (
    format_for_prompt as format_tombstones_for_prompt,
    load_tombstones,
    matches_tombstone,
)
from ._canonicalize import canonicalize_candidates
from ._incremental import (
    format_cutoff,
    format_known_ventures,
    incremental_cutoff,
    should_use_bootstrap,
)

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
    """Tiebreaker key for a venture — lowercase alphanumerics of name.

    Used only as a fallback when the LLM canonicalizer returns no
    match for a candidate — catches obvious same-string cases if the
    canon pass errored or skipped.
    """
    name = (item.get("name") or "").strip().lower()
    return "".join(c for c in name if c.isalnum())


_RETURN_SHAPE = (
    "Return a JSON array where each item has: "
    "`name`, `url`, `founded_year`, `one_liner` (≤ 2 sentences), "
    "`current_status` (one of: operating, acquired, closed, ipo), "
    "`funding_total_usd` (number or null), "
    "`last_funding_type` (seed | series_a | series_b | series_c | ipo | "
    "acquired | null), `acquirer` (if acquired, else null), "
    "`acquisition_date` (ISO 8601 if known, else null), `notes`."
)

_COMMERCIAL_GUARD = (
    "Only include **commercial** entities — for-profit companies or "
    "commercialisation spin-outs. Do NOT include research consortia, "
    "academic networks, non-profit foundations, grant-funded "
    "collaborations, or incubators. "
)

_TOMBSTONES_SECTION = (
    "\nThe following items were previously surfaced and REJECTED by "
    "verification — do NOT re-emit them:\n{tombstones_block}\n"
)

_BOOTSTRAP_PROMPT = (
    "Find startups or companies founded or co-founded by academic "
    "researcher **{name}**{affiliation_clause}. " + _COMMERCIAL_GUARD +
    "Cover ventures that are active, acquired, closed, or went public — "
    "entire career, no time limit. "
    + _TOMBSTONES_SECTION + _RETURN_SHAPE
)


_INCREMENTAL_PROMPT = (
    "For researcher **{name}**{affiliation_clause}, return a JSON array "
    "covering two concerns together: "
    "(a) any known venture from the list below whose RECORDED state is "
    "inaccurate — e.g. our ledger says 'operating' but the venture is in "
    "fact acquired, closed, or public; or our recorded acquirer / "
    "funding_total is materially out of date. Include such ventures "
    "with the corrected values. If all recorded fields accurately match "
    "current reality, SKIP the venture (do not include it in output); "
    "(b) any new venture {name} has founded or co-founded since {cutoff} "
    "that is NOT in the list below. " + _COMMERCIAL_GUARD +
    "Known ventures (verify each; include only if recorded state is "
    "stale or wrong):\n{known_block}\n"
    + _TOMBSTONES_SECTION +
    "Return ONLY a JSON array — no prose, no markdown, no section "
    "headers. " + _RETURN_SHAPE
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
    """Filter candidates for commercial-venture relevance + within-batch dedupe."""
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

    # Load existing ledger first — needed by the incremental prompt
    # AND by the merge step later.
    path = dossier_path(scholar_id) / "startups.json"
    existing = read_json(path) or {}
    existing_items = [
        it for it in (existing.get("items") or []) if isinstance(it, dict)
    ]

    # Decide mode: fall back to bootstrap if no prior snapshot.
    cutoff = incremental_cutoff(scholar_id, SOURCE_ID)
    use_bootstrap = should_use_bootstrap(mode, cutoff) or not existing_items

    tombstones = load_tombstones(scholar_id, category="startups")
    tombstones_block = format_tombstones_for_prompt(tombstones)

    if use_bootstrap:
        prompt = _BOOTSTRAP_PROMPT.format(
            name=name, affiliation_clause=affiliation_clause,
            tombstones_block=tombstones_block,
        )
        mode_used = "bootstrap"
    else:
        prompt = _INCREMENTAL_PROMPT.format(
            name=name,
            affiliation_clause=affiliation_clause,
            cutoff=format_cutoff(cutoff),
            known_block=format_known_ventures(existing_items),
            tombstones_block=tombstones_block,
        )
        mode_used = "incremental"

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

    # Early-exit: grounded search returned nothing usable.
    if not valid:
        snapshot_id = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={
                "mode": mode, "mode_used": mode_used, "reason": reason,
                "new_events": 0, "total": len(existing_items),
            },
        )
        return {
            "changed": False, "snapshot_id": snapshot_id,
            "new_events": 0, "total": len(existing_items),
        }

    try:
        filtered = await _filter_ventures(valid, name, affiliation)
    except Exception:
        logger.warning(
            "startups_web: relevance filter failed; using unfiltered",
            exc_info=True,
        )
        filtered = valid

    if not filtered:
        snapshot_id = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={
                "mode": mode, "mode_used": mode_used, "reason": reason,
                "new_events": 0, "total": len(existing_items),
            },
        )
        return {
            "changed": False, "snapshot_id": snapshot_id,
            "new_events": 0, "total": len(existing_items),
        }

    # Tombstone guard — drop anything previously rejected.
    filtered = [
        it for it in filtered
        if not matches_tombstone(it.get("name") or "", tombstones, category="startups")
    ]

    # ── Canonicalize survivors against existing ledger ───────────
    scholar_context = {
        "name": name,
        "affiliation": profile.get("affiliation"),
        "research_areas": profile.get("research_areas"),
    }
    canon_map = await canonicalize_candidates(
        filtered, existing_items, scholar_context, "venture",
    )

    # ── Merge loop: update matched existing, append new ──────────
    transitions: list[tuple[str, dict[str, Any]]] = []
    modified_existing: dict[int, dict[str, Any]] = {}
    new_items: list[dict[str, Any]] = []

    for cand_idx, cand in enumerate(filtered):
        existing_idx = canon_map.get(cand_idx)
        # Fallback tiebreaker for when canon couldn't decide or skipped.
        if existing_idx is None:
            k = _venture_key(cand)
            if k:
                for ei, eit in enumerate(existing_items):
                    if (
                        _venture_key(eit) == k
                        and ei not in modified_existing
                    ):
                        existing_idx = ei
                        break

        if existing_idx is not None:
            prev = existing_items[existing_idx]
            merged = {**prev, **cand}
            # Updated rows need re-verification too (URL may have changed).
            merged["_refinement_status"] = "pending"
            prev_status = prev.get("current_status")
            new_status = cand.get("current_status")
            if (
                prev_status != new_status
                and new_status in _TRANSITION_STATUSES
            ):
                transitions.append((f"startup_{new_status}", merged))
            modified_existing[existing_idx] = merged
        else:
            merged = dict(cand)
            merged["_refinement_status"] = "pending"
            new_status = cand.get("current_status")
            transitions.append(("startup_founded", merged))
            if new_status in _TRANSITION_STATUSES:
                transitions.append((f"startup_{new_status}", merged))
            new_items.append(merged)

    # Rebuild items list preserving original order for untouched entries.
    final_items: list[dict[str, Any]] = []
    for i, it in enumerate(existing_items):
        final_items.append(modified_existing.get(i, it))
    final_items.extend(new_items)

    final_items.sort(
        key=lambda r: int(r.get("founded_year") or 0),
        reverse=True,
    )
    write_json(path, {"items": final_items, "count": len(final_items)})

    if transitions:
        ctx = await build_scholar_context(scholar_id)
        asyncio.create_task(
            refine_pending_items(scholar_id, "startups", context=ctx)
        )

    for event_type, item in transitions:
        try:
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
            "mode_used": mode_used,
            "reason": reason,
            "new_events": len(transitions),
            "total": len(final_items),
        },
    )
    return {
        "changed": len(transitions) > 0,
        "snapshot_id": snapshot_id,
        "new_events": len(transitions),
        "total": len(final_items),
    }
