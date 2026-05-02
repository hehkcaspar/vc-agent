"""Layer 2 source — targeted news search for a portfolio entity.

Mirrors ``services/academic/sources/news_web.py`` but tracks a company
and its key persons (founders + top team, up to 5) as parallel search
targets. Every run re-reads entity metadata so team evolution or
industry re-tagging naturally reshapes the search scope.

Three-layer dedup (shared shape with scholar news_web):

1. Pre-search scope: incremental mode passes cutoff + recent headlines.
2. Rule-based: normalised URL + (title, date) tuple.
3. LLM canonicalization via academic ``canonicalize_candidates``
   (with ``subject_label="Company"``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import select

from ...academic.sources._canonicalize import canonicalize_candidates
from ...academic.sources._incremental import (
    format_cutoff,
    format_known_titles,
    sort_items_recent_first,
)
from ...grounded_extraction import apply_url_fallback, refine_jsonl
from ..events_sync import log_event, news_significance
from ..file_utils import (
    append_record,
    last_snapshot_for_source,
    read_records,
    record_snapshot,
    rewrite_records,
)
from ..refinement_storage import PORTFOLIO_STORAGE

logger = logging.getLogger(__name__)

SOURCE_ID = "news_web"


# ── Utilities ─────────────────────────────────────────────────────────


def _parse_date(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%B %d, %Y",
        "%d %B %Y",
        "%Y",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _normalize_url(url: str) -> str:
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


# ── Entity context loader ─────────────────────────────────────────────


async def _load_entity_context(entity_id: str) -> dict[str, Any] | None:
    """Read entity row + parse metadata_json; return structured context.

    Returns None when the entity doesn't exist.
    """
    from ....database import AsyncSessionLocal
    from ....models import Entity

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if entity is None:
            return None

        try:
            metadata = json.loads(entity.metadata_json) if entity.metadata_json else {}
        except (TypeError, ValueError):
            metadata = {}

        return {
            "id": entity.id,
            "name": entity.name,
            "website": entity.website,
            "status": entity.status,
            "deal_stage": entity.deal_stage,
            "metadata": metadata,
        }


def _active_founders(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Return founders whose `status` is not 'departed'."""
    out: list[dict[str, Any]] = []
    for f in metadata.get("founders") or []:
        if not isinstance(f, dict):
            continue
        if (f.get("status") or "").strip().lower() == "departed":
            continue
        if (f.get("name") or "").strip():
            out.append(f)
    return out


def _key_team(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in metadata.get("key_team") or []:
        if not isinstance(m, dict):
            continue
        if (m.get("name") or "").strip():
            out.append(m)
    return out


# ── Key-person ranker ─────────────────────────────────────────────────


_RANKER_PROMPT = (
    "You are selecting which people from a company to include as news-"
    "search targets. Up to 5 picks — return FEWER if the remaining "
    "candidates don't add signal (e.g. a small-share co-founder with "
    "marketing background at a deep-tech company usually isn't worth a "
    "search slot). Active founders usually outrank key_team hires. "
    "Weigh role relevance to the company's core discipline, domain fit "
    "(PhD/engineering depth for a technical startup; operator chops for "
    "a go-to-market play), and equity/decision-making stake when visible.\n\n"
    "Company: **{company}**{tag_clause}\n"
    "One-liner: {one_liner}\n"
    "Business model: {business_model}\n\n"
    "Candidates (founders first, then key_team):\n{candidates_block}\n\n"
    "Return a JSON object matching the schema — `picks` is an array of "
    "at most 5 items, ordered most-important first."
)


def _format_candidates_for_ranker(
    founders: list[dict[str, Any]], key_team: list[dict[str, Any]]
) -> str:
    lines: list[str] = []
    idx = 0
    for grp_label, group in (("founder", founders), ("key_team", key_team)):
        for m in group:
            name = (m.get("name") or "").strip()
            if not name:
                continue
            role = (m.get("role") or m.get("title") or "").strip()
            bg = (m.get("background") or m.get("bio") or "").strip()
            equity = m.get("equity_pct") or m.get("equity") or ""
            status = (m.get("status") or "").strip()
            if len(bg) > 180:
                bg = bg[:177] + "…"
            bits = [grp_label]
            if role:
                bits.append(role)
            if status:
                bits.append(f"status={status}")
            if equity:
                bits.append(f"equity={equity}")
            tail = f" ({', '.join(bits)})"
            desc = f" — {bg}" if bg else ""
            lines.append(f"[{idx}] {name}{tail}{desc}")
            idx += 1
    return "\n".join(lines) if lines else "(none)"


async def _rank_key_persons(
    company_name: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return up to 5 person dicts (name + role) most worth tracking.

    Uses Flash (``settings.ACADEMIC_GEMINI_MODEL``). Returns fewer than
    5 when the ranker decides the tail is low-signal. Falls back to
    "all active founders" on LLM failure.
    """
    founders = _active_founders(metadata)
    key_team = _key_team(metadata)
    if not founders and not key_team:
        return []

    # Cheap fallback when only 1-2 candidates exist — no ranker needed.
    if len(founders) + len(key_team) <= 2:
        picks: list[dict[str, Any]] = []
        for m in founders + key_team:
            name = (m.get("name") or "").strip()
            if not name:
                continue
            picks.append({
                "name": name,
                "role": (m.get("role") or m.get("title") or "").strip(),
            })
        return picks

    one_liner = (metadata.get("one_liner") or metadata.get("description") or "").strip()
    if len(one_liner) > 300:
        one_liner = one_liner[:297] + "…"
    business_model = (metadata.get("business_model") or "").strip()
    tags = metadata.get("industry_tags") or []
    if isinstance(tags, list) and tags:
        tag_clause = f" ({', '.join(str(t) for t in tags[:6])})"
    else:
        tag_clause = ""

    prompt = _RANKER_PROMPT.format(
        company=company_name,
        tag_clause=tag_clause,
        one_liner=one_liner or "(not provided)",
        business_model=business_model or "(not provided)",
        candidates_block=_format_candidates_for_ranker(founders, key_team),
    )

    from ....config import settings
    from ...academic.llm_client import generate_structured  # lazy import
    from ..schemas import KeyPersonRankerResult

    try:
        result = await generate_structured(
            model=settings.ACADEMIC_GEMINI_MODEL,
            prompt_parts=[prompt],
            response_schema=KeyPersonRankerResult,
        )
    except Exception:
        logger.warning(
            "news_web: key-person ranker failed; falling back to active founders",
            exc_info=True,
        )
        fallback: list[dict[str, Any]] = []
        for f in founders:
            name = (f.get("name") or "").strip()
            if not name:
                continue
            fallback.append({
                "name": name,
                "role": (f.get("role") or f.get("title") or "").strip(),
            })
        return fallback

    out: list[dict[str, Any]] = []
    for p in result.picks[:5]:
        name = (p.name or "").strip()
        if name:
            out.append({"name": name, "role": (p.role or "").strip(), "reason": (p.reason or "").strip()})
    return out


# ── Prompts ───────────────────────────────────────────────────────────


_COMMON_GROUNDING_RULE = (
    "You MUST use Google Search to find each item. Do NOT rely on "
    "training-data memory. Every item you emit must be backed by a "
    "specific real article that you actually retrieved. If Google "
    "Search returns no relevant results for this company, return an "
    "empty array `[]` — do NOT fabricate plausible-sounding stories. "
    "Only emit items where the URL points to a SPECIFIC article page "
    "(not a homepage, listing index, or profile page). If you cannot "
    "find a specific article URL for a claim, omit that item. "
)

_COMMON_NEWS_SHAPE = (
    "Return a JSON array where each item has: "
    "`title`, `url`, `published_date` (ISO 8601 if known), `source`, "
    "`summary` (1-3 sentences), `category` (one of: funding, launch, "
    "partnership, award, acquisition, appointment, product, "
    "talk, other). "
    "Return ONLY the JSON array — no prose, no markdown, no headers."
)

_COMMON_EXCLUSIONS = (
    "Do NOT include: generic industry trend pieces that don't name "
    "{company} or one of the listed people; stories primarily about "
    "competitor companies; press releases that mention {company} only "
    "as a passing reference. "
)

_BOOTSTRAP_PROMPT = (
    _COMMON_GROUNDING_RULE +
    "Find news, press releases, product launches, funding "
    "announcements, or blog posts about portfolio company "
    "**{company}**{tag_clause} — full history, no time limit. "
    "Include stories where {company} is a primary subject "
    "(funded, launched, acquired, partnered, appointed leadership, "
    "etc.) OR stories about any of the following people when they "
    "are acting in their capacity tied to {company}: {person_list}. "
    + _COMMON_EXCLUSIONS
    + _COMMON_NEWS_SHAPE
)

_INCREMENTAL_PROMPT = (
    _COMMON_GROUNDING_RULE +
    "Find news, press releases, product launches, funding "
    "announcements, or blog posts about portfolio company "
    "**{company}**{tag_clause} published since {cutoff}. "
    "Include stories where {company} is a primary subject OR stories "
    "about any of the following people when they are acting in their "
    "capacity tied to {company}: {person_list}. "
    + _COMMON_EXCLUSIONS +
    "The following headlines are already in our ledger — skip reposts "
    "and reworded versions, only include fresh developments:\n"
    "{known_titles_block}\n"
    + _COMMON_NEWS_SHAPE
)


_FILTER_PROMPT = (
    "You are a relevance filter for a portfolio-tracking system.\n"
    "Company: **{company}**{tag_clause}\n"
    "Tracked people (each acting in capacity tied to {company}):\n{people_block}\n\n"
    "For each candidate below, decide:\n"
    "1. `relevant` — Is this item (a) primarily about {company} as a "
    "company (funded, launched, acquired, partnered, leadership "
    "change, regulatory), OR (b) about one of the tracked people "
    "acting in their role at {company}? Generic industry trends, "
    "competitor-focused pieces, and passing mentions do NOT count.\n"
    "2. `duplicate_of` — If this item covers the same underlying story "
    "as an earlier item in the list (same event, different source URL), "
    "set this to the index of the earlier item. Otherwise null.\n\n"
    "Candidates:\n{candidates}\n\n"
    "Already-stored titles (for cross-batch dedup awareness):\n"
    "{existing}\n"
)


def _format_people_block(people: list[dict[str, Any]]) -> str:
    if not people:
        return "(no individuals tracked — match only on company mentions)"
    lines: list[str] = []
    for p in people:
        name = (p.get("name") or "").strip()
        role = (p.get("role") or "").strip()
        if not name:
            continue
        if role:
            lines.append(f"- {name} ({role})")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines) if lines else "(no individuals tracked)"


def _format_person_list(people: list[dict[str, Any]]) -> str:
    """Inline list for the grounded-search prompt."""
    if not people:
        return "(no individuals listed; match only on company-level stories)"
    bits: list[str] = []
    for p in people:
        name = (p.get("name") or "").strip()
        role = (p.get("role") or "").strip()
        if not name:
            continue
        if role:
            bits.append(f"{name} ({role})")
        else:
            bits.append(name)
    return ", ".join(bits) if bits else "(no individuals listed)"


# ── Filter pass ───────────────────────────────────────────────────────


async def _filter_news(
    candidates: list[dict[str, Any]],
    company: str,
    tag_clause: str,
    people: list[dict[str, Any]],
    existing_titles: list[str],
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    from ....config import settings
    from ...academic.llm_client import generate_structured
    from ..schemas import CompanyNewsFilterResult

    candidate_lines = []
    for i, it in enumerate(candidates):
        title = (it.get("title") or "").strip()
        summary = (it.get("summary") or "").strip()
        source = (it.get("source") or "").strip()
        candidate_lines.append(f"[{i}] {title} — {source}: {summary}")

    existing_block = "\n".join(
        f"- {t}" for t in existing_titles[-15:]
    ) if existing_titles else "(none)"

    prompt = _FILTER_PROMPT.format(
        company=company,
        tag_clause=tag_clause,
        people_block=_format_people_block(people),
        candidates="\n".join(candidate_lines),
        existing=existing_block,
    )

    try:
        result = await generate_structured(
            model=settings.ACADEMIC_GEMINI_MODEL,
            prompt_parts=[prompt],
            response_schema=CompanyNewsFilterResult,
        )
    except Exception:
        logger.warning("news_web: relevance filter failed; passing all candidates", exc_info=True)
        return candidates

    dominated = {it.duplicate_of for it in result.items if it.duplicate_of is not None}
    accepted = set()
    for it in result.items:
        if it.relevant and it.index not in dominated:
            accepted.add(it.index)

    return [candidates[i] for i in sorted(accepted) if i < len(candidates)]


# ── Verify-item context ───────────────────────────────────────────────


def _build_verify_context(
    company: str,
    metadata: dict[str, Any],
    tags: list[Any],
) -> str:
    """Compose the ``context`` string fed to ``verify_item`` so the
    flash-lite grounded search has enough disambiguation signal to tell
    'Glacian Technologies (Penn State data-center cooling spin-out)'
    from any other company sharing a similar name.

    Mirrors academic ``build_scholar_context`` but for portfolio
    entities — name + sector tags + HQ + founder names + one-liner.
    """
    parts: list[str] = [f"Company: {company}"]
    one_liner = (metadata.get("one_liner") or "").strip()
    if one_liner:
        parts.append(f"One-liner: {one_liner[:200]}")
    if isinstance(tags, list) and tags:
        parts.append(
            "Sectors: " + ", ".join(str(t) for t in tags[:6] if str(t).strip())
        )
    hq = (metadata.get("hq_location") or "").strip()
    if hq:
        parts.append(f"HQ: {hq}")
    founders = _active_founders(metadata)
    if founders:
        names = [
            (f.get("name") or "").strip()
            for f in founders[:3] if (f.get("name") or "").strip()
        ]
        if names:
            parts.append("Founders: " + ", ".join(names))
    return ". ".join(parts)


# ── URL-status backfill ───────────────────────────────────────────────


async def _backfill_url_status(
    entity_id: str, records: list[dict[str, Any]],
) -> None:
    """One-shot validate any existing records lacking ``_url_status``.

    Portfolio news_web pre-2026-05-01 wrote records without going through
    ``apply_url_fallback`` (the validator wasn't wired). After the wiring
    fix, those rows still claim every URL is fine (no `_url_status` ⇒
    frontend treats it as `verified` and shows no badge). This pass
    re-validates them in place — vertex redirects get resolved, dead
    URLs get tagged, and the records are rewritten atomically.

    Mutates ``records`` in place AND rewrites the underlying .jsonl.
    Caller's ``existing`` reference reflects the new state, so the
    incremental prompt + dedup downstream pick up resolved URLs.
    """
    pending = [r for r in records if r.get("_url_status") in (None, "")]
    if not pending:
        return
    import httpx

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        },
    ) as client:
        for r in pending:
            try:
                await apply_url_fallback(r, client=client)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "news_web backfill: apply_url_fallback failed (non-fatal)",
                    exc_info=True,
                )
                r.setdefault("_url_status", "timeout")

    try:
        await rewrite_records(entity_id, "news", records)
        logger.info(
            "news_web: backfilled _url_status on %d existing record(s) for %s",
            len(pending), entity_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "news_web: backfill rewrite failed for %s — records mutated in memory only",
            entity_id, exc_info=True,
        )


# ── Main run ──────────────────────────────────────────────────────────


_CANON_POOL_SIZE = 30


async def run(
    entity_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    """Run news_web against the given entity.

    Snapshot contract: every exit path (error, skipped, success, zero-hit)
    calls ``record_snapshot`` so heartbeat cadence is honored.
    """
    ctx = await _load_entity_context(entity_id)
    if ctx is None:
        sid = await record_snapshot(
            entity_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_entity"},
        )
        return {
            "changed": False,
            "snapshot_id": sid,
            "error": "no_entity",
            "mode_used": mode,
        }

    company = (ctx.get("name") or "").strip()
    if not company:
        sid = await record_snapshot(
            entity_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_company_name"},
        )
        return {
            "changed": False,
            "snapshot_id": sid,
            "error": "no_company_name",
            "mode_used": mode,
        }

    metadata = ctx.get("metadata") or {}
    tags = metadata.get("industry_tags") or []
    if isinstance(tags, list) and tags:
        tag_clause = f" ({', '.join(str(t) for t in tags[:6])})"
    else:
        tag_clause = ""

    # Key-person selection — up to 5, may return fewer.
    try:
        people = await _rank_key_persons(company, metadata)
    except Exception:
        logger.warning("news_web: person ranker errored; no people this run", exc_info=True)
        people = []

    person_list = _format_person_list(people)

    # Existing ledger — used for incremental prompt context + post-search dedup.
    existing = read_records(entity_id, "news")

    # Backfill: any record predating url-validation lacks `_url_status`.
    # Validate them in-place once so the user's already-displayed broken
    # URLs get an unverified badge (or fall back to a Google search).
    # First ever run will see existing == [] and skip this whole block.
    await _backfill_url_status(entity_id, existing)

    # Crash-recovery: kick off refinement on any leftover `_refinement_status: pending`
    # records from a prior run that didn't finish. fire-and-forget; this
    # task and the post-fetch refinement task may run concurrently —
    # refine_jsonl reads + atomically rewrites under the per-entity write
    # lock so they serialize safely. If no pending records exist, the
    # background task short-circuits to a no-op.
    company_context_for_recovery = _build_verify_context(company, metadata, tags)
    if any(r.get("_refinement_status") == "pending" for r in existing):
        asyncio.create_task(
            refine_jsonl(
                entity_id, "news",
                context=company_context_for_recovery,
                storage=PORTFOLIO_STORAGE,
            )
        )

    # Cutoff / bootstrap decision. We inline a thin version of the
    # scholar ``incremental_cutoff`` helper because the scholar version
    # reads scholar-side ``snapshot_log.jsonl`` via the academic
    # fact_store; we need the portfolio-side dossier.
    cutoff: datetime | None = None
    snap = last_snapshot_for_source(entity_id, SOURCE_ID)
    if snap:
        created = snap.get("created_at")
        if isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                cutoff = dt - timedelta(hours=24)
            except ValueError:
                cutoff = None

    use_bootstrap = mode == "bootstrap" or cutoff is None or not existing

    if use_bootstrap:
        prompt = _BOOTSTRAP_PROMPT.format(
            company=company,
            tag_clause=tag_clause,
            person_list=person_list,
        )
        mode_used = "bootstrap"
    else:
        recent = sort_items_recent_first(existing, date_key="published_date")
        prompt = _INCREMENTAL_PROMPT.format(
            company=company,
            tag_clause=tag_clause,
            person_list=person_list,
            cutoff=format_cutoff(cutoff),
            known_titles_block=format_known_titles(recent, max_items=_CANON_POOL_SIZE),
        )
        mode_used = "incremental"

    # Grounded search.
    try:
        from ...academic.llm_client import grounded_search_json
    except ImportError:
        sid = await record_snapshot(
            entity_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_llm_client"}
        )
        return {
            "changed": False,
            "snapshot_id": sid,
            "skipped": True,
            "mode_used": mode_used,
        }

    try:
        items = await grounded_search_json(prompt)
    except Exception as e:
        logger.exception("news_web: grounded search failed for %s", entity_id)
        sid = await record_snapshot(
            entity_id, SOURCE_ID, detail={"mode": mode, "error": str(e)},
        )
        return {
            "changed": False,
            "snapshot_id": sid,
            "error": str(e),
            "mode_used": mode_used,
        }

    if not isinstance(items, list):
        items = []

    # Minimum-shape validation.
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

    if not valid_items:
        sid = await record_snapshot(
            entity_id,
            SOURCE_ID,
            detail={
                "mode": mode,
                "mode_used": mode_used,
                "reason": reason,
                "new_items": 0,
                "people_count": len(people),
            },
        )
        return {
            "changed": False,
            "snapshot_id": sid,
            "new_items": 0,
            "mode_used": mode_used,
        }

    existing_titles = [
        (r.get("title") or "").strip() for r in existing if r.get("title")
    ]

    # Relevance filter.
    filtered = await _filter_news(
        valid_items, company, tag_clause, people, existing_titles
    )

    # Rule-based dedup.
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

    after_rule: list[dict[str, Any]] = []
    for it in filtered:
        title = (it.get("title") or "").strip()
        url_key = _normalize_url(it.get("url") or "")
        if url_key and url_key in existing_urls:
            continue
        tk_key = (title.lower(), (it.get("published_date") or "").strip())
        if tk_key in existing_keys:
            continue
        after_rule.append(it)

    if not after_rule:
        sid = await record_snapshot(
            entity_id,
            SOURCE_ID,
            detail={
                "mode": mode,
                "mode_used": mode_used,
                "reason": reason,
                "new_items": 0,
                "people_count": len(people),
            },
        )
        return {
            "changed": False,
            "snapshot_id": sid,
            "new_items": 0,
            "mode_used": mode_used,
        }

    # LLM canonicalization — reuses academic _canonicalize with Company labels.
    canon_pool = sort_items_recent_first(existing, date_key="published_date")[
        :_CANON_POOL_SIZE
    ]
    subject_context = {
        "name": company,
        "affiliation": metadata.get("hq_location"),
        "research_areas": tags,
    }
    canon_map = await canonicalize_candidates(
        after_rule,
        canon_pool,
        subject_context,
        "news",
        subject_label="Company",
        system_label="portfolio-tracking",
        domain_label="Industry",
    )

    # Persist all surviving items as `_refinement_status: "pending"`
    # then fire-and-forget the verify→triage→url_fallback orchestrator
    # in the background. Matches academic news_web's UX pattern:
    # REFRESH NOW returns fast (~10s for the grounded search), and
    # the background task marks records as finalized / rejected over
    # the next ~30-60s. The frontend filters records flagged
    # `_rejected: True` (set by refinement) so the user only sees
    # surviving items once refinement completes.
    company_context = _build_verify_context(company, metadata, tags)

    # Append unmatched items; log events.
    count = 0
    for cand_idx, it in enumerate(after_rule):
        if canon_map.get(cand_idx) is not None:
            continue
        it["_refinement_status"] = "pending"
        title = (it.get("title") or "").strip()
        url_key = _normalize_url(it.get("url") or "")
        await append_record(entity_id, "news", it)
        if url_key:
            existing_urls.add(url_key)
        existing_keys.add(
            (title.lower(), (it.get("published_date") or "").strip())
        )
        try:
            parsed_date = _parse_date(it.get("published_date"))
            await log_event(
                entity_id,
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

    # Fire-and-forget: verify → triage → URL fallback in the background.
    # Each newly-persisted record gets re-marked `_refinement_status:
    # finalized` (or `rejected` with `_rejected: True`) over the next
    # ~30-60s. Crash-recovery for prior pending items is handled by the
    # earlier task at the top of run() — both serialize on the per-entity
    # write lock inside refine_jsonl.
    if count > 0:
        asyncio.create_task(
            refine_jsonl(
                entity_id, "news",
                context=company_context,
                storage=PORTFOLIO_STORAGE,
            )
        )

    sid = await record_snapshot(
        entity_id,
        SOURCE_ID,
        detail={
            "mode": mode,
            "mode_used": mode_used,
            "reason": reason,
            "new_items": count,
            "people_count": len(people),
            "people": [p.get("name") for p in people if p.get("name")],
        },
    )
    return {
        "changed": count > 0,
        "snapshot_id": sid,
        "new_items": count,
        "mode_used": mode_used,
    }
