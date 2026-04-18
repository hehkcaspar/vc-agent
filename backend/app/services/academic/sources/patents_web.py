"""Layer 2 source — scholar patents via Gemini grounded search.

Uses Path 2 (single-shot with google_search tool) to discover patents
where the scholar is a named inventor, then Path 1 (generate_structured)
to reject false positives (non-patents, cited-but-not-inventor) and
dedupe patent families. Writes wholesale to `patents.json`; emits
`log_event` rows for newly-discovered filings/grants and
filed→granted transitions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..events_sync import log_event
from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json

logger = logging.getLogger(__name__)

SOURCE_ID = "patents_web"


def _parse_date(raw: str | None) -> datetime | None:
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


def _patent_key(item: dict[str, Any]) -> str:
    """Dedup key — normalised patent_number (strip spaces, commas, slashes)."""
    num = (item.get("patent_number") or "").strip().upper()
    return "".join(c for c in num if c.isalnum())


_PROMPT_TEMPLATE = (
    "Find patents granted to or filed by inventor **{name}**"
    "{affiliation_clause}. Only include patents where {name} is a "
    "**named inventor**, not merely cited or referenced. Dedupe patent "
    "families — return only one entry per invention, preferring the "
    "granted version over the application. "
    "Return a JSON array where each item has: "
    "`title`, `url` (link to the patent record — patents.google.com, "
    "lens.org, or the relevant national patent office), "
    "`patent_number`, `inventors` (list of full names), "
    "`assignee` (institution or company that owns the patent), "
    "`filing_date` (ISO 8601 if known, else null), "
    "`grant_date` (ISO 8601 if known, else null), "
    "`abstract` (1–2 sentences), "
    "`jurisdiction` (US | EP | WO | JP | CN | etc.)."
)


_FILTER_PROMPT = (
    "You are a relevance filter for a patent-inventorship tracker.\n"
    "Scholar: **{name}**{affiliation_clause}\n\n"
    "For each candidate patent below, decide:\n"
    "1. `relevant` — Is {name} a **named inventor** on this patent? "
    "Merely being cited, referenced, or mentioned in the abstract "
    "does NOT count — the scholar must appear in the inventor list. "
    "The `url` must point to an actual patent record (e.g. "
    "patents.google.com, lens.org, uspto.gov, epo.org, wipo.int). "
    "URLs pointing to journal articles (nature.com, sciencedirect, "
    "doi.org) are NOT patents and should be rejected.\n"
    "2. `duplicate_of` — If this item covers the same invention as "
    "an earlier item (same patent family, different jurisdiction or "
    "application vs. grant), set this to the index of the earlier "
    "item. Otherwise null.\n\n"
    "Candidates:\n{candidates}\n"
)


async def _filter_patents(
    candidates: list[dict[str, Any]],
    name: str,
    affiliation: str,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    from ....config import settings
    from ..llm_client import generate_structured
    from ..schemas import NewsFilterResult

    affiliation_clause = f" at {affiliation}" if affiliation else ""
    lines: list[str] = []
    for i, it in enumerate(candidates):
        title = (it.get("title") or "").strip()
        num = (it.get("patent_number") or "").strip()
        inv = ", ".join(it.get("inventors") or [])
        url = (it.get("url") or "").strip()
        lines.append(f"[{i}] {num} {title} — inventors: {inv} — {url}")

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
        logger.info("patents_web: llm_client not yet available; skipping")
        sid = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={"mode": mode, "skipped": "no_llm_client"},
        )
        return {"changed": False, "snapshot_id": sid, "skipped": True}

    try:
        candidates = await grounded_search_json(prompt)
    except Exception as e:
        logger.exception("patents_web: grounded search failed for %s", scholar_id)
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "error": str(e)},
        )
        return {"changed": False, "snapshot_id": sid, "error": str(e)}

    if not isinstance(candidates, list):
        candidates = []

    valid: list[dict[str, Any]] = []
    for it in candidates:
        if not isinstance(it, dict):
            continue
        if not (it.get("patent_number") or "").strip():
            continue
        if not (it.get("title") or "").strip():
            continue
        valid.append(it)

    try:
        filtered = await _filter_patents(valid, name, affiliation)
    except Exception:
        logger.warning(
            "patents_web: relevance filter failed; using unfiltered",
            exc_info=True,
        )
        filtered = valid

    path = dossier_path(scholar_id) / "patents.json"
    existing = read_json(path) or {}
    existing_items = [
        it for it in (existing.get("items") or []) if isinstance(it, dict)
    ]
    by_key: dict[str, dict[str, Any]] = {}
    for it in existing_items:
        k = _patent_key(it)
        if k:
            by_key[k] = it

    transitions: list[tuple[str, dict[str, Any]]] = []
    for it in filtered:
        k = _patent_key(it)
        if not k:
            continue
        prev = by_key.get(k)
        merged = {**(prev or {}), **it}
        prev_grant = (prev or {}).get("grant_date")
        new_grant = it.get("grant_date")
        if prev is None:
            event_type = "patent_granted" if new_grant else "patent_filed"
            transitions.append((event_type, merged))
        elif not prev_grant and new_grant:
            transitions.append(("patent_granted", merged))
        by_key[k] = merged

    # Sort by grant_date then filing_date, newest first.
    def _sort_key(r: dict[str, Any]) -> str:
        return (r.get("grant_date") or r.get("filing_date") or "")

    items = sorted(by_key.values(), key=_sort_key, reverse=True)
    write_json(path, {"items": items, "count": len(items)})

    for event_type, item in transitions:
        try:
            date_raw = (
                item.get("grant_date")
                if event_type == "patent_granted"
                else item.get("filing_date")
            )
            parsed_date = _parse_date(date_raw)
            await log_event(
                scholar_id,
                event_type=event_type,
                title=(item.get("title") or "")[:120],
                significance="medium",
                event_date=parsed_date,
                payload={
                    "url": item.get("url"),
                    "patent_number": item.get("patent_number"),
                    "assignee": item.get("assignee"),
                    "inventors": item.get("inventors"),
                    "jurisdiction": item.get("jurisdiction"),
                    "abstract": (item.get("abstract") or "")[:300],
                },
            )
        except Exception:
            logger.warning("patents_web: log_event failed", exc_info=True)

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
