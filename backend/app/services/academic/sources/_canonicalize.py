"""Cross-batch LLM canonicalization for grounded-search sources.

Rule-based string keys miss fuzzy matches (``TVision`` vs ``TVision
Medical``, rewritten headlines, branded variants). This pass reads
existing ledger + scholar keywords and answers "does candidate Ci
refer to the same entity/story as existing Ej?" for each candidate.

Called from ``startups_web`` and ``news_web``. ``patents_web`` keeps
rule-based ``_patent_key`` dedup — patent numbers are strong
identifiers and the marginal value of an LLM call here is low.

The pass is SKIPPED when either side is empty (nothing to match
against). Uses Flash (``settings.ACADEMIC_GEMINI_MODEL``) for cost /
latency.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ....config import settings
from ..llm_client import generate_structured
from ..schemas import CanonResult

logger = logging.getLogger(__name__)


_ITEM_TYPE_HINTS: dict[str, str] = {
    "venture": (
        "commercial ventures (startups, companies, spin-outs). Two items "
        "refer to the same venture if they describe the same legal entity "
        "or its direct rebrand — e.g. 'Rivet AI' and 'Rivet' are the same; "
        "'TVision' (attention-tracking AI lens) and 'TVision Medical' "
        "(sleep diagnostic) are different."
    ),
    "news": (
        "news stories. Two items refer to the same story if they report "
        "the same underlying event on the same date — even if the "
        "headlines are reworded, aggregated, or republished on a "
        "different outlet. A follow-up analysis piece a week later is a "
        "different story."
    ),
}


_PROMPT_TEMPLATE = """\
You are a canonical-entity matcher for a {system_label} system.
You are matching {item_type_plural}.

{subject_label}: **{name}**{affiliation_clause}
{domain_label}: {research_areas}

Context: {item_type_hint}

Known {item_type_plural} already in the ledger:
{known_block}

New candidates to classify:
{candidates_block}

For each candidate, decide whether it refers to the same real-world
{item_type} as one of the known entries above. Use domain-area
alignment, URL overlap, team/founder continuity, product or event
description — NOT just name-prefix similarity.

Return for each candidate (by index): `matches_existing_index`
(the Ei number if it matches, else null) + a 1-sentence `reason`.
"""


def _format_ventures_candidates(cands: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(cands):
        name = (it.get("name") or "").strip()
        one_liner = (it.get("one_liner") or "").strip()
        url = (it.get("url") or "").strip()
        if len(one_liner) > 140:
            one_liner = one_liner[:137] + "…"
        bits = [b for b in (url, one_liner) if b]
        tail = f" — {' · '.join(bits)}" if bits else ""
        lines.append(f"[C{i}] {name}{tail}")
    return "\n".join(lines) if lines else "(empty)"


def _format_ventures_existing(items: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(items):
        name = (it.get("name") or "").strip()
        one_liner = (it.get("one_liner") or "").strip()
        status = (it.get("current_status") or "").strip()
        url = (it.get("url") or "").strip()
        if len(one_liner) > 140:
            one_liner = one_liner[:137] + "…"
        head = f"[E{i}] {name}"
        if status:
            head += f" ({status})"
        bits = [b for b in (url, one_liner) if b]
        if bits:
            head += f" — {' · '.join(bits)}"
        lines.append(head)
    return "\n".join(lines) if lines else "(empty)"


def _format_news_candidates(cands: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(cands):
        title = (it.get("title") or "").strip()
        date = (it.get("published_date") or "").strip()
        src = (it.get("source") or "").strip()
        summary = (it.get("summary") or "").strip()
        if len(summary) > 140:
            summary = summary[:137] + "…"
        bits = [b for b in (src, date, summary) if b]
        tail = f" — {' · '.join(bits)}" if bits else ""
        lines.append(f"[C{i}] {title}{tail}")
    return "\n".join(lines) if lines else "(empty)"


def _format_news_existing(items: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(items):
        title = (it.get("title") or "").strip()
        date = (it.get("published_date") or "").strip()
        src = (it.get("source") or "").strip()
        bits = [b for b in (src, date) if b]
        tail = f" — {' · '.join(bits)}" if bits else ""
        lines.append(f"[E{i}] {title}{tail}")
    return "\n".join(lines) if lines else "(empty)"


_FORMATTERS: dict[str, tuple[Callable, Callable, str, str]] = {
    "venture": (
        _format_ventures_candidates,
        _format_ventures_existing,
        "venture",
        "ventures",
    ),
    "news": (
        _format_news_candidates,
        _format_news_existing,
        "news story",
        "news stories",
    ),
}


async def canonicalize_candidates(
    candidates: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    scholar_context: dict[str, Any],
    item_type: str,
    *,
    model: str | None = None,
    subject_label: str = "Scholar",
    system_label: str = "scholar-tracking",
    domain_label: str = "Research areas",
) -> dict[int, int | None]:
    """Match each candidate to an existing entry, or mark as new.

    Returns ``{candidate_index: matching_existing_index | None}`` for
    EVERY input candidate. Missing indices mean "match was not
    decidable" → caller should treat as None (new entry).

    ``subject_label`` / ``system_label`` / ``domain_label`` customise
    the prompt so portfolio callers can pass "Company" /
    "portfolio-tracking" / "Industry" without duplicating the module.

    Skips the LLM call and returns empty dict when:
    - `candidates` is empty (nothing to classify)
    - `existing` is empty (nothing to match against — everything is new)
    """
    if not candidates or not existing:
        return {}

    if item_type not in _FORMATTERS:
        raise ValueError(f"Unknown item_type '{item_type}'")
    fmt_cands, fmt_existing, singular, plural = _FORMATTERS[item_type]

    affiliation = (
        scholar_context.get("affiliation") if scholar_context else None
    ) or ""
    if isinstance(affiliation, dict):
        affiliation = affiliation.get("current") or ""
    affiliation_clause = f" at {affiliation}" if affiliation else ""

    research_areas = scholar_context.get("research_areas") or []
    if isinstance(research_areas, list):
        research_areas = ", ".join(str(x) for x in research_areas) or "(none listed)"
    else:
        research_areas = str(research_areas) or "(none listed)"

    prompt = _PROMPT_TEMPLATE.format(
        item_type=singular,
        item_type_plural=plural,
        item_type_hint=_ITEM_TYPE_HINTS.get(item_type, ""),
        name=scholar_context.get("name") or "",
        affiliation_clause=affiliation_clause,
        research_areas=research_areas,
        known_block=fmt_existing(existing),
        candidates_block=fmt_cands(candidates),
        subject_label=subject_label,
        system_label=system_label,
        domain_label=domain_label,
    )

    try:
        result = await generate_structured(
            model=model or settings.ACADEMIC_GEMINI_MODEL,
            prompt_parts=[prompt],
            response_schema=CanonResult,
        )
    except Exception:
        logger.warning(
            "canonicalize: LLM call failed for item_type=%s; treating all as new",
            item_type,
            exc_info=True,
        )
        return {}

    out: dict[int, int | None] = {}
    for m in result.items:
        if m.candidate_index < 0 or m.candidate_index >= len(candidates):
            continue
        mi = m.matches_existing_index
        if mi is not None and (mi < 0 or mi >= len(existing)):
            mi = None  # out-of-range match → treat as new
        out[m.candidate_index] = mi
    return out
