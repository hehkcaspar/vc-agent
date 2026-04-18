"""Layer 2 source — targeted news search via Gemini grounded search.

Uses Path 2 (single-shot with google_search tool). Appends each
discovered news item as a record to `news.jsonl` and marks a snapshot.

Post-search relevance filtering via Path 1 (generate_structured)
removes tangential results (institutional news, colleague mentions,
field trends) and deduplicates stories across different source URLs.

The Gemini client is imported lazily to avoid a hard dependency while
Phase 2 ships without Phase 3's llm_client.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..events_sync import log_event, news_significance
from ..fact_store import record_snapshot
from ..file_utils import append_record, dossier_path, read_json, read_records

logger = logging.getLogger(__name__)

SOURCE_ID = "news_web"


def _parse_date(raw: str | None) -> datetime | None:
    """Best-effort parse of an ISO-ish date string from LLM output."""
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


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip tracking params, www., trailing slash."""
    if not url:
        return ""
    parsed = urlparse(url.strip().lower())
    params = parse_qs(parsed.query)
    for k in list(params):
        if k.startswith("utm_") or k in ("ref", "source", "campaign", "fbclid", "gclid"):
            del params[k]
    cleaned = parsed._replace(
        netloc=parsed.netloc.removeprefix("www."),
        path=parsed.path.rstrip("/"),
        query=urlencode(params, doseq=True),
        fragment="",
    )
    return urlunparse(cleaned)


_PROMPT_TEMPLATE = (
    "Find recent (last 30 days) news, press releases, or blog posts related "
    "to academic researcher **{name}**{affiliation_clause} — either they "
    "are a named protagonist (quoted, awarded, appointed, or their "
    "specific work is the subject), OR the story is primarily about a "
    "commercial venture (startup, company, product) they have founded or "
    "co-founded. "
    "{ventures_clause}"
    "Do NOT include: news about their university, institute, or lab that "
    "doesn't name {name} or one of their ventures; news primarily about "
    "colleagues, students, or co-workers; general field trends or blog "
    "posts that don't feature {name} or a venture directly. "
    "Return a JSON array where each item has: "
    "`title`, `url`, `published_date` (ISO 8601 if known), `source`, "
    "`summary` (1-3 sentences), `category` (one of: funding, launch, "
    "partnership, award, appointment, acquisition, talk, other)."
)


_FILTER_PROMPT = (
    "You are a relevance filter for a scholar-tracking system.\n"
    "Scholar: **{name}**{affiliation_clause}\n"
    "{ventures_line}"
    "\nFor each candidate news item below, decide:\n"
    "1. `relevant` — Is this item (a) directly about {name} as a named "
    "protagonist (quoted, awarded, appointed, or their specific work is "
    "the subject), OR (b) primarily about a commercial venture {name} has "
    "founded or co-founded (acquisition, funding, launch, etc.)? "
    "Institutional news about their university or lab, news about "
    "colleagues or students, and field-level trends do NOT count even if "
    "they mention {name} in passing.\n"
    "2. `duplicate_of` — If this item covers the same underlying story as "
    "an earlier item in the list (same event, different source URL), set "
    "this to the index of the earlier item. Otherwise null.\n\n"
    "Candidates:\n{candidates}\n\n"
    "Already-stored titles (for cross-batch dedup awareness):\n{existing}\n"
)


def _collect_known_ventures(scholar_id: str) -> list[str]:
    """Best-effort list of venture names from prior source data.

    Reads `startups.json` (written by ``startups_web``). Returns an empty list
    on a fresh bootstrap where nothing has been discovered yet — the
    search prompt then asks Gemini to discover ventures as part of its
    grounded search. Venture enrichment closes a known blind spot:
    headlines that mention only the company (e.g. "Viant Acquires
    TVision") are otherwise filtered out because the scholar isn't
    named as a protagonist.
    """
    startups = read_json(dossier_path(scholar_id) / "startups.json") or {}
    names: set[str] = set()
    for item in startups.get("items") or []:
        name = (item.get("name") or item.get("company") or "").strip()
        if name:
            names.add(name)
    return sorted(names)


def _build_ventures_clauses(
    name: str, ventures: list[str]
) -> tuple[str, str]:
    """Return (search_clause, filter_line) pair based on known ventures.

    Search clause goes into `_PROMPT_TEMPLATE.ventures_clause`; filter
    line goes into `_FILTER_PROMPT.ventures_line`. Keeping both pieces
    here so the branching logic lives in one place.
    """
    if ventures:
        venture_list = ", ".join(ventures)
        search = (
            f"Ventures {name} has founded or co-founded include: "
            f"{venture_list}. Actively search for recent news mentioning "
            f"any of these by name, even if {name} isn't in the headline. "
        )
        filter_line = f"Known ventures founded/co-founded: {venture_list}.\n"
    else:
        search = (
            f"If {name} has founded or co-founded any startups, companies, "
            f"or products, briefly identify them (consult their homepage, "
            f"LinkedIn, or biographical sources) and include recent news "
            f"about any such venture even if {name} isn't named in the "
            f"headline. "
        )
        filter_line = ""
    return search, filter_line


async def _filter_news(
    candidates: list[dict],
    name: str,
    affiliation: str,
    ventures: list[str],
    existing_titles: list[str],
) -> list[dict]:
    """Filter candidates for relevance and semantic dedup via structured LLM."""
    if not candidates:
        return []

    from ..llm_client import generate_structured
    from ..schemas import NewsFilterResult
    from ....config import settings

    affiliation_clause = f" at {affiliation}" if affiliation else ""
    _, ventures_line = _build_ventures_clauses(name, ventures)

    # Build numbered candidate list
    candidate_lines = []
    for i, it in enumerate(candidates):
        title = (it.get("title") or "").strip()
        summary = (it.get("summary") or "").strip()
        source = (it.get("source") or "").strip()
        candidate_lines.append(f"[{i}] {title} — {source}: {summary}")

    # Include last 15 existing titles for cross-batch dedup
    existing_block = "\n".join(
        f"- {t}" for t in existing_titles[-15:]
    ) if existing_titles else "(none)"

    prompt = _FILTER_PROMPT.format(
        name=name,
        affiliation_clause=affiliation_clause,
        ventures_line=ventures_line,
        candidates="\n".join(candidate_lines),
        existing=existing_block,
    )

    result = await generate_structured(
        model=settings.ACADEMIC_GEMINI_MODEL,
        prompt_parts=[prompt],
        response_schema=NewsFilterResult,
    )

    # Build set of accepted indices
    dominated = {it.duplicate_of for it in result.items if it.duplicate_of is not None}
    accepted = set()
    for it in result.items:
        if it.relevant and it.index not in dominated:
            accepted.add(it.index)

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
            scholar_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_scholar_name"},
        )
        return {"changed": False, "snapshot_id": sid, "error": "no_scholar_name"}

    affiliation = ((profile.get("affiliation") or {}).get("current")) or ""
    affiliation_clause = f" at {affiliation}" if affiliation else ""
    ventures = _collect_known_ventures(scholar_id)
    ventures_clause, _ = _build_ventures_clauses(name, ventures)
    prompt = _PROMPT_TEMPLATE.format(
        name=name,
        affiliation_clause=affiliation_clause,
        ventures_clause=ventures_clause,
    )

    # Lazy import — llm_client is built in Phase 3. Soft-fail if absent.
    try:
        from ..llm_client import grounded_search_json  # type: ignore
    except ImportError:
        logger.info("news_web: llm_client not yet available; skipping")
        snapshot_id = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_llm_client"}
        )
        return {"changed": False, "snapshot_id": snapshot_id, "skipped": True}

    try:
        items = await grounded_search_json(prompt)
    except Exception as e:
        logger.exception("news_web: grounded search failed for %s", scholar_id)
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "error": str(e)},
        )
        return {"changed": False, "snapshot_id": sid, "error": str(e)}

    if not isinstance(items, list):
        items = []

    # ── Minimum-shape validation before filtering ────────────────────
    valid_items = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        if not (it.get("url") or it.get("summary")):
            continue
        valid_items.append(it)

    # ── Relevance + semantic dedup filter ────────────────────────────
    existing = read_records(scholar_id, "news")
    existing_titles = [
        (r.get("title") or "").strip() for r in existing if r.get("title")
    ]

    try:
        filtered = await _filter_news(
            valid_items, name, affiliation, ventures, existing_titles
        )
    except Exception:
        logger.warning("news_web: relevance filter failed; using unfiltered items", exc_info=True)
        filtered = valid_items

    # ── URL dedup against existing records ───────────────────────────
    existing_urls: set[str] = set()
    existing_keys: set[tuple[str, str]] = set()
    for rec in existing:
        u = _normalize_url(rec.get("url") or "")
        if u:
            existing_urls.add(u)
        t = (rec.get("title") or "").strip().lower()
        d = (rec.get("published_date") or "").strip()
        if t:
            existing_keys.add((t, d))

    count = 0
    for it in filtered:
        title = (it.get("title") or "").strip()
        # Dedupe against existing records.
        url_key = _normalize_url(it.get("url") or "")
        if url_key and url_key in existing_urls:
            continue
        tk_key = (title.lower(), (it.get("published_date") or "").strip())
        if tk_key in existing_keys:
            continue
        await append_record(scholar_id, "news", it)
        if url_key:
            existing_urls.add(url_key)
        existing_keys.add(tk_key)
        # Mirror to timeline + signal feed.
        try:
            parsed_date = _parse_date(it.get("published_date"))
            await log_event(
                scholar_id,
                event_type="news_mention",
                title=title[:120],
                significance=news_significance(title, it.get("category") or ""),
                event_date=parsed_date,
                payload={
                    "url": it.get("url"),
                    "source": it.get("source"),
                    "published_date": it.get("published_date"),
                    "category": it.get("category"),
                    "summary": (it.get("summary") or "")[:300],
                },
            )
        except Exception:
            logger.warning("news_web: log_event failed", exc_info=True)
        count += 1

    snapshot_id = await record_snapshot(
        scholar_id,
        SOURCE_ID,
        detail={"mode": mode, "reason": reason, "new_items": count},
    )
    return {"changed": count > 0, "snapshot_id": snapshot_id, "new_items": count}
