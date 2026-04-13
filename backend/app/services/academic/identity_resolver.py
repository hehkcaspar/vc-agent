"""One-shot identity resolution for new scholars.

This is the foundation of downstream data collection — a wrong or
missing GS/SS id poisons every Layer 2 source and every Layer 3
dim eval. First-principles design, informed by the legacy
scholar_agent Phase A pattern:

1. **Homepage crawl first.** If the scholar provided a homepage /
   lab page, fetch it and parse outbound links. This is the
   cheapest, most deterministic, and most authoritative signal — the
   scholar published these links themselves.

2. **Grounded LLM discovery** fills gaps. One grounded-search call
   to locate any credible source URLs the homepage crawl didn't
   find, plus affiliation / department / role / research areas.

3. **Deterministic URL parsing** (`classify_urls`). The union of
   URLs from (1) and (2) goes through a rule-based parser that
   extracts ids for every known source shape. Never trust an LLM
   to return an id directly — always parse it out of a URL.

4. **GS verification** (authoritative anchor). If we found a GS
   id, fetch the profile (SerpAPI first, direct-scrape fallback)
   to verify it's real and capture h-index + total citations. The
   h-index becomes the anchor for SS disambiguation.

5. **SS resolution with legacy cross-check.** EVEN IF the grounded
   search returned a parseable SS URL, we verify it by running the
   author's papers through SS API and checking the h-index matches
   the GS anchor (`verify_ss_metrics`). If it doesn't, we fall
   back to SS `search_author` (Tier 1) with h-index disambiguation,
   then SS paper search (Tier 2) for common names.

6. **Write profile.json** with per-source confidence levels.

The resolver is idempotent: if GS + SS are already resolved, it
returns `{status: skipped}` without any LLM/API calls.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from ...config import settings
from .file_utils import dossier_path, read_json, write_json
from .identity_verifier import (
    IdentityVerdict,
    IdentityVerifier,
    ScholarContext,
    append_rejection,
    build_rejection_entry,
    commit_label,
    is_rejected,
)
from .llm_client import generate_structured, grounded_generate_text
from .orcid_client import fetch_orcid_fingerprint
from .semantic_scholar import SemanticScholarService
from .sources.google_scholar_stats import fetch_gs_profile, search_gs_by_papers
from .tool_utils import classify_urls, names_match, verify_ss_metrics

logger = logging.getLogger(__name__)


_HTTP_TIMEOUT = 20.0
# Browser-ish UA. Some .edu sites (Cloudflare/Akamai-fronted) block
# bot-ish UAs outright, but sending a real browser string gets us
# past the simpler WAF rules. Cloudflare-hard-blocked sites (e.g.
# UMich LSA) still 403 — the resolver degrades to grounded search
# + SerpAPI paper-search fallback.
_CRAWL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
_CRAWL_MAX_BYTES = 2_000_000  # cap to avoid huge pages
_SS_SEARCH_LIMIT = 20


class ResolvedIdentity(BaseModel):
    """Structured output of the grounded-search synthesis pass.

    Every field is optional — the resolver merges this with
    homepage crawl results and lets `classify_urls` do the id
    extraction.
    """

    google_scholar_url: Optional[str] = None
    semantic_scholar_url: Optional[str] = None
    orcid_url: Optional[str] = None
    dblp_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    twitter_url: Optional[str] = None
    openreview_url: Optional[str] = None
    arxiv_author_url: Optional[str] = None
    homepage_url: Optional[str] = None
    lab_page_url: Optional[str] = None
    current_affiliation: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    research_areas: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


_DISCOVERY_PROMPT = """\
You are locating every credible public profile / identifier for an
academic researcher. Use Google Search thoroughly. Return a
structured evidence summary in prose — do NOT output JSON.

For each of the following, give the FULL URL you find and a one-line
source/justification. Do not guess. Say "not found" if you are not
confident.

Primary identity sources:
1. **Google Scholar profile** — URL of the form
   `scholar.google.com/citations?user=XXXX...`
2. **Semantic Scholar author page** — URL of the form
   `semanticscholar.org/author/Name/NUMERIC_ID`
3. **ORCID** — URL of the form `orcid.org/XXXX-XXXX-XXXX-XXXX`
4. **DBLP** (for CS) — URL of the form `dblp.org/pid/NN/NNNN`
5. **arXiv author page** — URL of the form `arxiv.org/a/surname_i_X`
6. **OpenReview profile** — URL of the form
   `openreview.net/profile?id=~Full_Name1`

Secondary sources:
7. **LinkedIn profile** (if public)
8. **GitHub user page** (the scholar's personal account, not a lab)
9. **Twitter / X handle** (if public)
10. **Personal homepage**
11. **Lab / group page**

Affiliation + role:
12. **Current affiliation** (university / institution / company)
13. **Department** and **role** (Associate Professor, Research
    Scientist, etc.)
14. **Research areas** — 3-5 short phrases describing what the
    scholar is best known for (e.g. "efficient deep learning",
    "hardware-aware neural architecture search").

If the scholar has a common name, use the affiliation hint and
input URLs below to disambiguate. Prefer the scholar's most recent
or most cited ML/CS work to discriminate.

Scholar info:
"""


_SYNTHESIS_PROMPT = """\
Extract the URLs from the evidence dossier into a ResolvedIdentity
JSON object. Rules:
- Only populate a URL field if the dossier contains a full URL
  matching the source's canonical shape. Null otherwise.
- For Google Scholar, the URL must contain `user=` with an id.
- For Semantic Scholar, the URL must be `semanticscholar.org/author/...`
- For ORCID, the URL must be `orcid.org/XXXX-XXXX-XXXX-XXXX`.
- For DBLP, the URL must be `dblp.org/pid/...` or `dblp.org/pers/...`.
- `research_areas`: 3-5 short phrases, not sentences.
- Never fabricate. Missing is better than wrong.

=== EVIDENCE DOSSIER ===
"""


# ── Homepage crawl ────────────────────────────────────────────────────


async def _crawl_homepage(url: str) -> list[str]:
    """Fetch a homepage and return every absolute http(s) href found.

    Best-effort — any error returns an empty list. Capped at
    `_CRAWL_MAX_BYTES`. Relative URLs are resolved against the
    fetched page's base.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _CRAWL_USER_AGENT},
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.info(
                    "identity_resolver: homepage crawl %s → %d", url, r.status_code
                )
                return []
            html = r.text[:_CRAWL_MAX_BYTES]
    except Exception as e:
        logger.info("identity_resolver: homepage crawl failed %s: %s", url, e)
        return []

    raw = re.findall(r'href=["\']([^"\']+)["\']', html)
    base = str(r.url)

    out: list[str] = []
    for href in raw:
        # Resolve relative URLs against the base.
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(base, href)
        elif not href.startswith(("http://", "https://")):
            continue
        # Strip HTML-entity escapes commonly seen in href strings.
        href = href.replace("&amp;", "&")
        out.append(href)
    return out


async def _homepage_text(url: str) -> str:
    """Fetch a homepage and return ~4 KB of plain text for LLM verification.

    Strips tags, scripts, and styles; collapses whitespace. Best-effort
    — errors return an empty string and the verifier decides based on
    whatever other context it has.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _CRAWL_USER_AGENT},
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return ""
            html = r.text[:_CRAWL_MAX_BYTES]
    except Exception as e:  # noqa: BLE001
        logger.info("identity_resolver: homepage text fetch failed %s: %s", url, e)
        return ""

    # Strip script / style blocks, then all tags, then normalise whitespace.
    stripped = re.sub(r"(?is)<script.*?</script>", " ", html)
    stripped = re.sub(r"(?is)<style.*?</style>", " ", stripped)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped[:4000]


# ── SS resolution (Tier 1 name search + Tier 2 paper search) ─────────
#
# Both tiers now yield an ordered sequence of candidates instead of
# picking one. The main resolve loop walks the sequence, runs the
# cheap pre-filter + LLM verifier, and commits the first accept.
# Rejections are persisted so subsequent tiers and future runs skip
# known-bad ids. A tier returning an empty list means "nothing left
# to try" — the resolver moves on.

_SS_TIER1_TOP_K = 3
_SS_TIER2_MAX_CANDIDATES = 5


async def _candidates_via_name(
    ss: SemanticScholarService,
    name: str,
    expected_h_index: int | None,
    expected_citations: int | None,
    affiliation_hint: str | None,
) -> list[dict[str, Any]]:
    """Tier 1: SS `search_author`, scored and filtered, top-K returned.

    When a strong anchor exists (`expected_h_index > 10`), zero-h
    candidates are dropped from the pool entirely — there is no
    scenario where a real high-impact scholar is represented by a
    zero-h author profile. Without a strong anchor we fall back to
    ranking by paper_count + citation_count and let the LLM verifier
    decide.
    """
    candidates = await ss.search_author(name, limit=_SS_SEARCH_LIMIT)
    if not candidates:
        return []

    strong_anchor = bool(expected_h_index and expected_h_index > 10)

    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        c_name, c_id = c.get("name", ""), c.get("id")
        if not c_id or not c_name or not names_match(name, c_name):
            continue
        c_h = int(c.get("h_index") or 0)
        if strong_anchor and c_h <= 0:
            # Zero-h against a strong anchor is a mismatch, not a
            # "score low". Drop from the pool so we don't waste an
            # LLM call on it.
            continue
        score = 0.0
        if strong_anchor and c_h > 0:
            ratio = min(c_h, expected_h_index) / max(c_h, expected_h_index)
            score += ratio * 100
        else:
            score += int(c.get("citation_count") or 0) * 0.001
            score += int(c.get("paper_count") or 0) * 0.01
        if affiliation_hint:
            for aff in c.get("affiliations") or []:
                if _affiliation_substr_match(affiliation_hint, aff):
                    score += 20
                    break
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for _, c in scored[:_SS_TIER1_TOP_K]:
        out.append(
            {
                "id": str(c["id"]),
                "url": f"https://www.semanticscholar.org/author/{c['id']}",
                "name": c.get("name"),
                "affiliations": c.get("affiliations") or [],
                "paper_count": int(c.get("paper_count") or 0),
                "citation_count": int(c.get("citation_count") or 0),
                "h_index": int(c.get("h_index") or 0),
                "tier": "name_search",
            }
        )
    return out


async def _candidates_via_papers(
    ss: SemanticScholarService,
    name: str,
    research_areas: list[str],
) -> list[dict[str, Any]]:
    """Tier 2: SS paper search, for common names where Tier 1 fails.

    Queries by `name` alone and `name + research_area`, extracts
    author ids matching the scholar name, and returns up to
    `_SS_TIER2_MAX_CANDIDATES` candidates enriched with details.
    """
    queries: list[str] = [name]
    for area in research_areas[:2]:
        queries.append(f"{name} {area}")

    seen_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    for query in queries:
        if len(out) >= _SS_TIER2_MAX_CANDIDATES:
            break
        papers = await ss.search_papers(query, limit=10)
        if not papers:
            continue
        papers.sort(key=lambda p: int(p.get("citations") or 0), reverse=True)
        for paper in papers:
            if len(out) >= _SS_TIER2_MAX_CANDIDATES:
                break
            for author in paper.get("authors", []):
                a_id = author.get("authorId")
                a_name = author.get("name") or ""
                if not a_id or a_id in seen_ids or not names_match(name, a_name):
                    continue
                seen_ids.add(a_id)
                details = await ss.get_author_details(a_id)
                if not details:
                    continue
                if not names_match(name, details.get("name", "")):
                    continue
                out.append(
                    {
                        "id": str(a_id),
                        "url": f"https://www.semanticscholar.org/author/{a_id}",
                        "name": details.get("name"),
                        "affiliations": details.get("affiliations") or [],
                        "paper_count": int(details.get("paper_count") or 0),
                        "citation_count": int(details.get("citation_count") or 0),
                        "h_index": int(details.get("h_index") or 0),
                        "tier": "paper_search",
                    }
                )
                break  # one match per paper is enough
    return out


async def _resolve_ss_candidate(
    ss: SemanticScholarService,
    verifier: IdentityVerifier,
    candidate: dict[str, Any],
    expected_h: int | None,
    expected_cites: int | None,
    rejected_identity: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, IdentityVerdict | None]:
    """Apply cheap pre-filter + LLM verifier to one SS candidate.

    Returns `(committed_source, verdict)` on a match, or `(None, None)`
    if the candidate is rejected. Rejections are appended to
    `rejected_identity` in place so the caller can write them back
    to disk at the end of the resolve pass.
    """
    c_id = candidate["id"]
    if is_rejected(rejected_identity, "semantic_scholar", c_id):
        logger.info(
            "identity_resolver: skipping SS candidate %s — already rejected", c_id
        )
        return None, None

    # Cheap pre-filter. A zero-metric candidate against a strong
    # anchor fails here without ever hitting the LLM.
    if not verify_ss_metrics(
        candidate.get("h_index") or 0,
        candidate.get("citation_count") or 0,
        expected_h,
        expected_cites,
    ):
        append_rejection(
            rejected_identity,
            "semantic_scholar",
            {
                "id": c_id,
                "url": candidate.get("url"),
                "rejected_at": _now_iso(),
                "reason": "cheap pre-filter: zero or divergent metrics vs anchor",
                "rejected_by": "verify_ss_metrics",
            },
        )
        return None, None

    # LLM verification.
    enrichment = await _ss_enrichment(ss, c_id)
    verdict = await verifier.verify("semantic_scholar", candidate, enrichment)
    if not verdict.match:
        append_rejection(
            rejected_identity,
            "semantic_scholar",
            build_rejection_entry(candidate, verdict),
        )
        return None, None

    confidence_label, verified_by = commit_label(verdict, "semantic_scholar")
    committed = {
        "id": c_id,
        "url": candidate["url"],
        "confidence": confidence_label,
        "verified_by": verified_by,
        "llm_confidence": round(verdict.confidence, 2),
        "llm_reason": verdict.reason,
        "h_index": candidate.get("h_index"),
        "citation_count": candidate.get("citation_count"),
        "tier": candidate.get("tier"),
    }
    return committed, verdict


async def _ss_enrichment(
    ss: SemanticScholarService, ss_id: str
) -> dict[str, Any]:
    """Fetch the evidence we hand to the LLM verifier for an SS candidate.

    SS returns papers in upstream order (roughly paperId), not sorted
    by citations. We fetch 30 and pick the top 5 by citation count so
    the LLM sees the scholar's signature papers rather than random
    ones — a much stronger discriminator.
    """
    details = await ss.get_author_details(ss_id) or {}
    try:
        papers = await ss.get_author_papers(ss_id, limit=30)
    except Exception as e:  # noqa: BLE001
        logger.warning("identity_resolver: get_author_papers(%s) failed: %s", ss_id, e)
        papers = []
    ranked = sorted(
        (p for p in papers if p.get("title")),
        key=lambda p: int(p.get("citations") or 0),
        reverse=True,
    )[:5]
    return {
        "name": details.get("name"),
        "affiliations": details.get("affiliations") or [],
        "paper_count": details.get("paper_count"),
        "citation_count": details.get("citation_count"),
        "h_index": details.get("h_index"),
        "top_papers": [
            {
                "title": p.get("title"),
                "year": p.get("year"),
                "venue": p.get("venue") or p.get("journal"),
                "citations": p.get("citations"),
            }
            for p in ranked
        ],
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _affiliation_substr_match(needle: str, haystack: str) -> bool:
    """Loose case-insensitive affiliation substring match."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    n = norm(needle)
    h = norm(haystack)
    if not n or not h:
        return False
    return n in h or h in n


# ── Main entry ───────────────────────────────────────────────────────


async def resolve_identity(scholar_id: str) -> dict[str, Any]:
    """Populate profile.json identity fields. Idempotent.

    Returns a dict with status + which sources were resolved.
    """
    profile_path = dossier_path(scholar_id) / "profile.json"
    profile = read_json(profile_path) or {}
    identity: dict[str, Any] = dict(profile.get("identity") or {})
    rejected_identity: dict[str, list[dict[str, Any]]] = {
        k: list(v)
        for k, v in (profile.get("rejected_identity") or {}).items()
        if isinstance(v, list)
    }

    already_gs = (identity.get("google_scholar") or {}).get("id")
    already_ss = (identity.get("semantic_scholar") or {}).get("id")
    if already_gs and already_ss:
        return {"status": "skipped", "reason": "already_resolved"}

    name = profile.get("name") or ""
    if not name:
        return {"status": "skipped", "reason": "no_scholar_name"}
    affiliation_current = (profile.get("affiliation") or {}).get("current") or ""
    input_urls: list[str] = profile.get("input_urls") or []

    # Scholar context + verifier. Built initially from whatever
    # profile fields exist, then enriched in-place with grounded-LLM
    # discovery results (affiliation / research areas) before the
    # first source verification runs. Keeping it as a mutable object
    # lets every later pass add newly-verified sources to
    # `already_verified` as cross-reference context.
    scholar_ctx = ScholarContext.from_profile(profile)
    verifier = IdentityVerifier(scholar_ctx)

    log: dict[str, Any] = {
        "homepage_urls_found": 0,
        "grounded_search_ran": False,
        "gs_verified": False,
        "ss_verified_via": None,
        "verifications": [],
    }

    # ── Pass 1 — homepage crawl ─────────────────────────────────
    crawled_urls: list[str] = []
    for u in input_urls:
        crawled = await _crawl_homepage(u)
        crawled_urls.extend(crawled)
        # Keep the input URL itself so classify_urls can pick it up
        # as homepage_url if it matches nothing stronger.
        crawled_urls.append(u)
    log["homepage_urls_found"] = len(crawled_urls)

    deterministic = classify_urls(crawled_urls)

    # ── Pass 2 — grounded LLM discovery (only if gaps remain) ───
    needs_grounded = not (
        deterministic.get("google_scholar_id")
        and deterministic.get("semantic_scholar_id")
    )

    resolved_llm: ResolvedIdentity | None = None
    if needs_grounded:
        log["grounded_search_ran"] = True
        scholar_info_lines = [f"Name: {name}"]
        if affiliation_current:
            scholar_info_lines.append(f"Known affiliation: {affiliation_current}")
        if input_urls:
            scholar_info_lines.append("Input URLs (homepage / lab page):")
            scholar_info_lines.extend(f"  - {u}" for u in input_urls)
        if deterministic:
            found_lines = [f"  - {k}: {v}" for k, v in deterministic.items()]
            scholar_info_lines.append(
                "Already discovered from homepage crawl:\n" + "\n".join(found_lines)
            )
        scholar_info = "\n".join(scholar_info_lines)

        try:
            dossier_text = await grounded_generate_text(
                [_DISCOVERY_PROMPT, scholar_info],
                model=settings.ACADEMIC_GEMINI_MODEL,
            )
        except Exception as e:
            logger.exception(
                "identity_resolver: discovery failed for %s", scholar_id
            )
            return {
                "status": "error",
                "phase": "discovery",
                "error": str(e),
                "log": log,
            }

        if dossier_text.strip():
            try:
                resolved_llm = await generate_structured(
                    model=settings.ACADEMIC_GEMINI_MODEL,
                    prompt_parts=[_SYNTHESIS_PROMPT, dossier_text],
                    response_schema=ResolvedIdentity,
                )
            except Exception as e:
                logger.warning(
                    "identity_resolver: synthesis failed for %s: %s",
                    scholar_id,
                    e,
                )

        if resolved_llm is not None:
            llm_urls = [
                resolved_llm.google_scholar_url,
                resolved_llm.semantic_scholar_url,
                resolved_llm.orcid_url,
                resolved_llm.dblp_url,
                resolved_llm.linkedin_url,
                resolved_llm.github_url,
                resolved_llm.twitter_url,
                resolved_llm.openreview_url,
                resolved_llm.arxiv_author_url,
                resolved_llm.homepage_url,
                resolved_llm.lab_page_url,
            ]
            llm_parsed = classify_urls([u for u in llm_urls if u])
            # Homepage crawl results take precedence over LLM — fill gaps only.
            for k, v in llm_parsed.items():
                deterministic.setdefault(k, v)

    # Enrich scholar_ctx with grounded-discovery results before any
    # LLM verification runs. A bare "Terence Tao" context (just the
    # name) is too thin for the verifier to decide — we want it to
    # see the affiliation + research areas we just discovered so it
    # can compare candidates against real signals.
    if resolved_llm is not None:
        if not scholar_ctx.affiliation_current and resolved_llm.current_affiliation:
            scholar_ctx.affiliation_current = resolved_llm.current_affiliation
        if not scholar_ctx.affiliation_department and resolved_llm.department:
            scholar_ctx.affiliation_department = resolved_llm.department
        if not scholar_ctx.research_areas and resolved_llm.research_areas:
            scholar_ctx.research_areas = list(resolved_llm.research_areas)

    # ── Pass 3 — GS verification + LLM gate ─────────────────────
    gs_metrics: dict[str, Any] | None = None
    gs_id: str | None = None
    gs_committed: dict[str, Any] | None = None

    async def _try_gs_candidate(
        candidate_gs_id: str,
        source_tag: str,
    ) -> dict[str, Any] | None:
        """Fetch + LLM-verify one GS candidate, commit if accepted.

        Rejections append to `rejected_identity.google_scholar` so we
        don't revisit the same id in this run or subsequent runs.
        """
        nonlocal gs_metrics, gs_id, gs_committed
        if is_rejected(rejected_identity, "google_scholar", candidate_gs_id):
            logger.info(
                "identity_resolver: skipping GS %s — already rejected", candidate_gs_id
            )
            return None
        prof = await fetch_gs_profile(candidate_gs_id)
        if not prof:
            return None
        if not names_match(name, prof.get("name") or ""):
            return None
        candidate_obj = {
            "id": candidate_gs_id,
            "url": f"https://scholar.google.com/citations?user={candidate_gs_id}",
            "name": prof.get("name"),
            "affiliations": prof.get("affiliations") or [],
            "h_index": prof.get("h_index"),
            "total_citations": prof.get("total_citations"),
        }
        enrichment = {
            "name": prof.get("name"),
            "affiliations": prof.get("affiliations") or [],
            "h_index": prof.get("h_index"),
            "i10_index": prof.get("i10_index"),
            "total_citations": prof.get("total_citations"),
            "website": prof.get("website"),
            "fetch_source": prof.get("source"),
        }
        verdict = await verifier.verify("google_scholar", candidate_obj, enrichment)
        log["verifications"].append(
            {"source": "google_scholar", "id": candidate_gs_id, "match": verdict.match,
             "confidence": verdict.confidence}
        )
        if not verdict.match:
            append_rejection(
                rejected_identity,
                "google_scholar",
                build_rejection_entry(candidate_obj, verdict),
            )
            return None
        confidence_label, verified_by = commit_label(verdict, "google_scholar")
        gs_id = candidate_gs_id
        gs_metrics = prof
        gs_committed = {
            "id": candidate_gs_id,
            "url": candidate_obj["url"],
            "confidence": confidence_label,
            "verified_by": f"{verified_by}|gs_fetch:{prof.get('source')}",
            "llm_confidence": round(verdict.confidence, 2),
            "llm_reason": verdict.reason,
        }
        identity["google_scholar"] = gs_committed
        scholar_ctx.already_verified["google_scholar"] = gs_committed
        log["gs_verified"] = True
        return gs_committed

    # Step 3a: deterministic-parsed GS id (homepage or grounded search).
    parsed_gs_id = deterministic.get("google_scholar_id")
    if parsed_gs_id:
        await _try_gs_candidate(parsed_gs_id, source_tag="deterministic")

    # Step 3b: SerpAPI paper-search fallback. Triggers when (a) no
    # parsed GS id, (b) parsed id was rejected (verifier or name
    # mismatch), or (c) profile fetch returned None.
    if gs_committed is None:
        hint_source = (resolved_llm.research_areas if resolved_llm else []) or []
        hint = " ".join(hint_source[:2])[:80] if hint_source else ""
        candidates = await search_gs_by_papers(name, hint_keywords=hint, limit=5)
        for c in candidates:
            committed = await _try_gs_candidate(
                c["gs_id"], source_tag="serpapi_paper_search"
            )
            if committed:
                log["gs_fallback"] = "serpapi_paper_search"
                break

    expected_h = (gs_metrics or {}).get("h_index")
    expected_cites = (gs_metrics or {}).get("total_citations")

    # ── Pass 4 — SS resolution (cheap filter → LLM verifier) ────
    #
    # No longer treats GS as a hard anchor: the LLM verifier uses
    # name + affiliation + research areas (plus any already-verified
    # sources) as context, and works whether or not GS resolved.
    # `expected_h` / `expected_cites` feed only the cheap pre-filter.

    ss = SemanticScholarService(api_key=settings.SEMANTIC_SCHOLAR_API_KEY)
    ss_committed: dict[str, Any] | None = None

    async def _try_ss_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal ss_committed
        committed, _verdict = await _resolve_ss_candidate(
            ss,
            verifier,
            candidate,
            expected_h,
            expected_cites,
            rejected_identity,
        )
        if committed:
            ss_committed = committed
            identity["semantic_scholar"] = committed
            scholar_ctx.already_verified["semantic_scholar"] = committed
            log["ss_verified_via"] = committed.get("tier")
        return committed

    # Step 4a: parsed SS id from deterministic URL extraction.
    parsed_ss_id = deterministic.get("semantic_scholar_id")
    if parsed_ss_id and not is_rejected(
        rejected_identity, "semantic_scholar", parsed_ss_id
    ):
        parsed_details = await ss.get_author_details(parsed_ss_id) or {}
        if parsed_details and names_match(name, parsed_details.get("name", "")):
            parsed_candidate = {
                "id": parsed_ss_id,
                "url": f"https://www.semanticscholar.org/author/{parsed_ss_id}",
                "name": parsed_details.get("name"),
                "affiliations": parsed_details.get("affiliations") or [],
                "paper_count": int(parsed_details.get("paper_count") or 0),
                "citation_count": int(parsed_details.get("citation_count") or 0),
                "h_index": int(parsed_details.get("h_index") or 0),
                "tier": "parsed_verified",
            }
            await _try_ss_candidate(parsed_candidate)

    # Step 4b: Tier 1 — SS `search_author`, iterate top-K.
    if ss_committed is None:
        for candidate in await _candidates_via_name(
            ss, name, expected_h, expected_cites, affiliation_current
        ):
            if await _try_ss_candidate(candidate):
                break

    # Step 4c: Tier 2 — SS paper search, iterate candidates.
    if ss_committed is None:
        areas = (
            (resolved_llm.research_areas if resolved_llm else [])
            or profile.get("research_areas")
            or []
        )
        for candidate in await _candidates_via_papers(ss, name, areas):
            if await _try_ss_candidate(candidate):
                break

    ss_resolved = ss_committed

    # ── Pass 5 — ORCID verification (LLM gate) ──────────────────
    orcid_parsed = deterministic.get("orcid_id")
    if orcid_parsed and not is_rejected(
        rejected_identity, "orcid", orcid_parsed
    ):
        orcid_candidate = {
            "id": orcid_parsed,
            "url": deterministic.get("orcid_url")
            or f"https://orcid.org/{orcid_parsed}",
        }
        orcid_enrichment = await fetch_orcid_fingerprint(orcid_parsed)
        verdict = await verifier.verify("orcid", orcid_candidate, orcid_enrichment)
        log["verifications"].append(
            {"source": "orcid", "id": orcid_parsed, "match": verdict.match,
             "confidence": verdict.confidence}
        )
        if verdict.match:
            confidence_label, verified_by = commit_label(verdict, "orcid")
            identity["orcid"] = {
                "id": orcid_parsed,
                "url": orcid_candidate["url"],
                "confidence": confidence_label,
                "verified_by": verified_by,
                "llm_confidence": round(verdict.confidence, 2),
                "llm_reason": verdict.reason,
            }
            scholar_ctx.already_verified["orcid"] = identity["orcid"]
        else:
            append_rejection(
                rejected_identity,
                "orcid",
                build_rejection_entry(orcid_candidate, verdict),
            )

    # ── Pass 6 — low-signal sources (heuristic commit) ─────────
    if deterministic.get("dblp_id") or deterministic.get("dblp_url"):
        identity["dblp"] = {
            "id": deterministic.get("dblp_id"),
            "url": deterministic.get("dblp_url"),
            "confidence": "verified",
            "verified_by": "deterministic_parse",
        }
    if deterministic.get("linkedin_url"):
        identity["linkedin"] = {
            "handle": deterministic.get("linkedin_handle"),
            "url": deterministic["linkedin_url"],
            "confidence": "high",
        }
    if deterministic.get("github_user"):
        identity["github"] = {
            "user": deterministic["github_user"],
            "url": deterministic["github_url"],
            "confidence": "high",
        }
    if deterministic.get("twitter_handle"):
        identity["twitter"] = {
            "handle": deterministic["twitter_handle"],
            "url": deterministic["twitter_url"],
            "confidence": "medium",
        }
    if deterministic.get("openreview_id"):
        identity["openreview"] = {
            "id": deterministic["openreview_id"],
            "url": deterministic["openreview_url"],
            "confidence": "high",
        }
    if deterministic.get("arxiv_author"):
        identity["arxiv"] = {
            "author": deterministic["arxiv_author"],
            "url": deterministic["arxiv_url"],
            "confidence": "high",
        }

    # ── Pass 7 — Homepage verification (LLM gate) ──────────────
    # Prefer the user-supplied input URLs (authoritative) over the
    # crawl fallback, then run the LLM verifier on the extracted
    # plain text.
    if "homepage" not in identity:
        homepage_url: str | None = None
        for u in input_urls:
            if u.startswith(("http://", "https://")):
                homepage_url = u
                break
        homepage_url = homepage_url or deterministic.get("homepage_url")
        if homepage_url and not is_rejected(
            rejected_identity, "homepage", homepage_url
        ):
            homepage_candidate = {"id": homepage_url, "url": homepage_url}
            homepage_text = await _homepage_text(homepage_url)
            verdict = await verifier.verify(
                "homepage",
                homepage_candidate,
                {"url": homepage_url, "text": homepage_text},
            )
            log["verifications"].append(
                {"source": "homepage", "id": homepage_url, "match": verdict.match,
                 "confidence": verdict.confidence}
            )
            if verdict.match:
                confidence_label, verified_by = commit_label(verdict, "homepage")
                identity["homepage"] = {
                    "url": homepage_url,
                    "confidence": confidence_label,
                    "verified_by": verified_by,
                    "llm_confidence": round(verdict.confidence, 2),
                    "llm_reason": verdict.reason,
                }
                scholar_ctx.already_verified["homepage"] = identity["homepage"]
            else:
                append_rejection(
                    rejected_identity,
                    "homepage",
                    build_rejection_entry(homepage_candidate, verdict),
                )

    # ── Pass 8 — write profile ──────────────────────────────────
    profile["identity"] = identity
    profile["rejected_identity"] = rejected_identity

    existing_aff = dict(profile.get("affiliation") or {})
    llm_aff = resolved_llm.current_affiliation if resolved_llm else None
    gs_aff = (gs_metrics or {}).get("affiliations") or []
    if not existing_aff.get("current"):
        existing_aff["current"] = llm_aff or (gs_aff[0] if gs_aff else None)
    if resolved_llm and resolved_llm.department and not existing_aff.get("department"):
        existing_aff["department"] = resolved_llm.department
    if resolved_llm and resolved_llm.role and not existing_aff.get("role"):
        existing_aff["role"] = resolved_llm.role
    profile["affiliation"] = existing_aff

    if not profile.get("research_areas"):
        if resolved_llm and resolved_llm.research_areas:
            profile["research_areas"] = resolved_llm.research_areas[:5]

    # Seed metrics. Prefer Google Scholar when verified (most
    # comprehensive h-index / citation counts). Fall back to
    # Semantic Scholar author metrics when the scholar has no GS
    # profile — not everyone curates one, and SS coverage is still
    # useful for downstream dim scoring and display.
    metrics = dict(profile.get("metrics") or {})
    if gs_metrics:
        if gs_metrics.get("h_index") is not None:
            metrics["h_index"] = gs_metrics["h_index"]
        if gs_metrics.get("i10_index") is not None:
            metrics["i10_index"] = gs_metrics["i10_index"]
        if gs_metrics.get("total_citations") is not None:
            metrics["total_citations"] = gs_metrics["total_citations"]
        metrics["source"] = f"google_scholar:{gs_metrics.get('source')}"
    elif ss_resolved:
        # SS fallback — note the lower-fidelity source explicitly
        # so the UI can render a caveat badge if desired.
        if ss_resolved.get("h_index") is not None:
            metrics["h_index"] = ss_resolved["h_index"]
        if ss_resolved.get("citation_count") is not None:
            metrics["total_citations"] = ss_resolved["citation_count"]
        metrics["source"] = "semantic_scholar_fallback"
    if metrics:
        profile["metrics"] = metrics

    write_json(profile_path, profile)

    final_gs = (identity.get("google_scholar") or {}).get("id")
    final_ss = (identity.get("semantic_scholar") or {}).get("id")
    source_count = sum(
        1
        for k in (
            "google_scholar",
            "semantic_scholar",
            "orcid",
            "dblp",
            "linkedin",
            "github",
            "twitter",
            "openreview",
            "arxiv",
            "homepage",
        )
        if identity.get(k)
    )

    return {
        "status": "resolved" if (final_gs and final_ss) else "partial",
        "google_scholar_id": final_gs,
        "semantic_scholar_id": final_ss,
        "affiliation": existing_aff.get("current"),
        "department": existing_aff.get("department"),
        "role": existing_aff.get("role"),
        "research_areas": profile.get("research_areas"),
        "gs_metrics": gs_metrics,
        "ss_metrics": {
            "h_index": (identity.get("semantic_scholar") or {}).get("h_index"),
            "citation_count": (identity.get("semantic_scholar") or {}).get(
                "citation_count"
            ),
        } if final_ss else None,
        "sources_resolved": source_count,
        "log": log,
    }
