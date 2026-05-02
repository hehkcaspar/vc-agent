"""Per-item grounded-search verification.

Given one item emitted by a source's grounded search, run an
independent grounded-search call (``gemini-3.1-flash-lite-preview``)
that focuses on THAT item only. Returns:

    - verdict:              confirmed | partial | unconfirmed
    - subject_match:        whether the search result is about the SAME
                            entity as in SUBJECT CONTEXT (vs. a name-
                            sharing different entity)
    - authoritative_url:    best URL from grounding chunks (vertex redirect)
    - category_correct:     whether the item really belongs to its source
                            category (e.g. a "patent" that's actually a paper)
    - correction_note:      one-sentence explanation when category is wrong
    - evidence:             one-sentence summary of what search turned up

Pure: one grounded search call, no side effects. Callers compose this
with the triage function to decide keep/drop, and with the url_fallback
module to finalize the URL.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from google.genai import types
from pydantic import BaseModel

from app.services.academic.llm_client import genai_client

logger = logging.getLogger(__name__)

# Light, fast, and explicitly grounded — we never want it answering
# from memory alone.
DEFAULT_VERIFY_MODEL = "gemini-3.1-flash-lite-preview"


class VerifyResult(BaseModel):
    verdict: str = "unconfirmed"          # confirmed | partial | unconfirmed
    authoritative_url: str = ""           # from grounding chunks, vertex redirect
    category_correct: bool = True
    # Did the search result describe the SAME ENTITY as in SUBJECT
    # CONTEXT (vs. a different entity sharing the name)? Defaults True
    # so legacy parsers that don't supply this field don't accidentally
    # block confirmed results — but we OVERRIDE verdict to "unconfirmed"
    # downstream when subject_match is False, regardless of what the
    # model wrote in `verdict` (defence in depth).
    subject_match: bool = True
    # When category_correct is False, what category does it actually
    # belong to? One of: news | patents | startups | red_flags | papers
    # (or empty if the verifier couldn't tell). Triage uses this to
    # decide whether to DROP or ROUTE.
    suggested_category: str = ""
    correction_note: str = ""
    evidence: str = ""
    # Grounding chunk URLs (vertex redirects); first is the authoritative_url.
    grounding_urls: list[str] = []
    # True when the call itself failed (network, parse, etc.). Callers
    # should treat this as "unconfirmed" but not tombstone — it's flaky,
    # not a hallucination.
    error: str = ""


_PROMPT = (
    "You MUST use Google Search to verify ONE specific item. Do NOT rely "
    "on training-data memory alone — ground every claim in the search "
    "results you retrieve.\n\n"
    "SUBJECT CONTEXT: {context}\n\n"
    "ITEM UNDER REVIEW\n"
    "  source category: {category}\n"
    "  title / name: {title}\n"
    "  summary / claim: {summary}\n\n"
    "Your job, in this exact order:\n\n"
    "1. **EXISTENCE**: Search for this item. Does a story / paper / "
    "patent / company-event matching the title and summary actually "
    "exist on the open web? If your search surfaces nothing matching, "
    "the verdict is `unconfirmed` — STOP here and explain in `evidence`.\n\n"
    "2. **SUBJECT IDENTITY** (MOST IMPORTANT — read carefully): "
    "Many companies, products, and people share names. The item must "
    "be about THE SAME ENTITY described in SUBJECT CONTEXT, not a "
    "different entity that happens to share part of the name. "
    "Cross-check at least one corroborating signal from SUBJECT "
    "CONTEXT against the search result:\n"
    "   - For companies: founders' names, sector, HQ, recent funding, "
    "founding year, distinctive product line.\n"
    "   - For people: institution / employer, research areas, "
    "co-authors, degrees, location.\n"
    "If the search result is about a DIFFERENT entity sharing the "
    "name (e.g. SUBJECT CONTEXT names a fintech founder 'John Smith' "
    "but the search hits an actor 'John Smith'; or SUBJECT CONTEXT "
    "describes a chronic-pain startup 'Override Health' but the "
    "ITEM is about a code-management product also called 'Override') "
    "→ verdict is `unconfirmed` with evidence "
    "'subject mismatch: <what entity it's actually about>'. Do NOT "
    "return `confirmed` for stories about a same-named DIFFERENT "
    "entity, even when those stories are real.\n\n"
    "3. **CATEGORY**: Is the source category correct? Classify the "
    "ITEM itself by its MEDIUM, not by what the item is about. Use "
    "the 'click test': if the authoritative URL opens a(n) ...\n"
    "   - news article, press release, blog post, podcast page, "
    "university announcement → news\n"
    "   - research-paper record (arXiv abstract, journal article page, "
    "NeurIPS/ICML/ICLR/CVPR proceedings, PDF of the paper itself) → "
    "papers\n"
    "   - patent-office record (patents.google.com, uspto.gov, "
    "lens.org, epo.org, wipo.int) with a real patent number → patents\n"
    "   - commercial company homepage or company press release → "
    "startups\n"
    "   - documented reputational concern (retraction notice, ORI "
    "finding, lawsuit filing, etc.) → red_flags\n\n"
    "Critical category distinctions (do NOT confuse these):\n"
    "   - A news article ABOUT a paper is **news**, not papers. The "
    "medium is the article page, not the paper. Only classify as "
    "papers when the URL would land on the paper's own publication "
    "record / abstract / PDF.\n"
    "   - Writeups of research on a university or lab site (MIT News, "
    "research.umich.edu, mit-ibm.watson.ai, etc.) are **news**.\n"
    "   - A research paper that also inspired a patent filing stays "
    "**papers** unless the URL you surface is an actual patent-office "
    "record with a patent number.\n\n"
    "4. If the source category is WRONG, pick the category the item "
    "actually belongs to from the list above (news | patents | startups "
    "| red_flags | papers). Leave empty only if nothing in that list "
    "fits.\n\n"
    "5. **AUTHORITATIVE URL**: Identify the single authoritative URL "
    "(the primary / official source). Prefer the actual event page, "
    "patent-office record, company homepage, or news article — NOT "
    "aggregator pages or generic hubs.\n\n"
    "Verdict logic:\n"
    "  `confirmed`    — both EXISTENCE and SUBJECT IDENTITY pass.\n"
    "  `unconfirmed`  — EXISTENCE failed OR SUBJECT IDENTITY failed "
    "(use evidence to say which).\n"
    "  `partial`      — story exists about our subject but key facts "
    "in title/summary are off (e.g. wrong amount, wrong year).\n\n"
    "Return a JSON object only:\n"
    '{{"verdict": "confirmed" | "partial" | "unconfirmed",\n'
    '  "subject_match": true | false,\n'
    '  "category_correct": true | false,\n'
    '  "suggested_category": "<news|patents|startups|red_flags|papers>" '
    '(only when category_correct is false; else empty),\n'
    '  "correction_note": "<if category wrong or facts off, explain; '
    'else empty>",\n'
    '  "authoritative_url": "<best URL from your search, or empty>",\n'
    '  "evidence": "<one sentence: what you found AND whether it was '
    'about our SUBJECT or a different entity sharing the name>"}}\n'
)


async def verify_item(
    item: dict[str, Any],
    *,
    context: str,
    source_category: str,
    model: str = DEFAULT_VERIFY_MODEL,
) -> VerifyResult:
    """Run one grounded-search verification for one item."""
    title = item.get("title") or item.get("name") or item.get("claim") or ""
    summary = (
        item.get("summary")
        or item.get("one_liner")
        or item.get("source_summary")
        or item.get("abstract")
        or ""
    )
    if not title:
        return VerifyResult(verdict="unconfirmed",
                            error="no title/name/claim on item")

    prompt = _PROMPT.format(
        context=context or "n/a",
        category=source_category,
        title=str(title)[:300],
        summary=str(summary)[:400],
    )

    try:
        client = genai_client()
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        resp = await client.aio.models.generate_content(
            model=model,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("verify_item: API call failed (%s)", exc)
        return VerifyResult(verdict="unconfirmed", error=str(exc))

    text = getattr(resp, "text", None) or ""
    parsed: dict[str, Any] = {}
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            logger.debug("verify_item: JSON parse failed: %s", text[:200])

    grounding_urls = _extract_grounding_urls(resp)

    # Defence-in-depth: if the model marked subject_match=False but
    # forgot to flip the verdict to unconfirmed (sometimes the LLM
    # says "confirmed" because the story exists, even though it
    # acknowledged the subject doesn't match), force unconfirmed here.
    raw_verdict = (parsed.get("verdict") or "").strip().lower() or "unconfirmed"
    raw_subject_match = bool(parsed.get("subject_match", True))
    final_verdict = raw_verdict
    if not raw_subject_match and raw_verdict == "confirmed":
        final_verdict = "unconfirmed"
        logger.info(
            "verify_item: forced verdict→unconfirmed (subject_match=False) "
            "for title=%r",
            (item.get("title") or item.get("name") or item.get("claim") or "")[:80],
        )

    return VerifyResult(
        verdict=final_verdict,
        authoritative_url=(
            grounding_urls[0]
            if grounding_urls
            else (parsed.get("authoritative_url") or "").strip()
        ),
        subject_match=raw_subject_match,
        category_correct=bool(parsed.get("category_correct", True)),
        suggested_category=(
            (parsed.get("suggested_category") or "").strip().lower()
        ),
        correction_note=(parsed.get("correction_note") or "").strip(),
        evidence=(parsed.get("evidence") or "").strip(),
        grounding_urls=grounding_urls,
    )


def _extract_grounding_urls(response: Any) -> list[str]:
    out: list[str] = []
    cands = getattr(response, "candidates", None) or []
    if not cands:
        return out
    gm = getattr(cands[0], "grounding_metadata", None)
    if gm is None:
        return out
    for ch in getattr(gm, "grounding_chunks", None) or []:
        w = getattr(ch, "web", None)
        if w and getattr(w, "uri", ""):
            out.append(w.uri)
    return out


__all__ = ["verify_item", "VerifyResult", "DEFAULT_VERIFY_MODEL"]
