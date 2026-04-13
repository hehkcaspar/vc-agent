"""Layer 2 source — Google Scholar stats.

Primary path: SerpAPI `google_scholar_author` engine.
Fallback: direct HTML scrape of `scholar.google.com/citations?user=...`
(SerpAPI returns `search_metadata.status=Success` with empty author
data for some ids — a known upstream bug. Direct scrape is the
legacy-tested workaround.)

Exposes `fetch_gs_profile(gs_id)` as a module-level helper so
`identity_resolver` can use the same code path during bootstrap
identity verification.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ....config import settings
from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json

logger = logging.getLogger(__name__)

SOURCE_ID = "google_scholar_stats"
_SERPAPI_BASE = "https://serpapi.com/search.json"
_HTTP_TIMEOUT = 30.0
_DIRECT_USER_AGENT = "Mozilla/5.0 (VC-Academic-Tracker/1.0)"


# ── Public helper used by identity_resolver ───────────────────────────


async def fetch_gs_profile(gs_id: str) -> dict[str, Any] | None:
    """Return a normalised GS profile dict, or None if the id is dead.

    Shape:
        {
            "source": "serpapi" | "direct_scrape",
            "name": str | None,
            "affiliations": list[str],
            "h_index": int | None,
            "i10_index": int | None,
            "total_citations": int | None,
            "website": str | None,
        }
    """
    # Pass 1 — SerpAPI.
    if settings.SERPAPI_KEY:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    _SERPAPI_BASE,
                    params={
                        "engine": "google_scholar_author",
                        "author_id": gs_id,
                        "hl": "en",
                        "api_key": settings.SERPAPI_KEY,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    parsed = _parse_serpapi_response(data)
                    if parsed:
                        return parsed
                    logger.info(
                        "fetch_gs_profile: SerpAPI returned empty data for %s, "
                        "falling back to direct scrape",
                        gs_id,
                    )
        except Exception as e:
            logger.warning(
                "fetch_gs_profile: SerpAPI fetch failed for %s: %s", gs_id, e
            )

    # Pass 2 — direct scrape.
    return await _direct_scrape_gs(gs_id)


def _parse_serpapi_response(data: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the SerpAPI google_scholar_author response.

    Returns None if the response is empty (the known upstream bug) so
    callers can fall back to direct scraping.
    """
    author = data.get("author") or {}
    cited_by = data.get("cited_by") or {}
    table = cited_by.get("table") or []
    if not author.get("name") and not table:
        return None

    h = i10 = total = None
    for row in table:
        if "citations" in row:
            total = row["citations"].get("all")
        if "h_index" in row:
            h = row["h_index"].get("all")
        if "i10_index" in row:
            i10 = row["i10_index"].get("all")

    affs_raw = author.get("affiliations")
    if isinstance(affs_raw, str):
        affiliations = [affs_raw]
    elif isinstance(affs_raw, list):
        affiliations = affs_raw
    else:
        affiliations = []

    return {
        "source": "serpapi",
        "name": author.get("name"),
        "affiliations": affiliations,
        "h_index": h,
        "i10_index": i10,
        "total_citations": total,
        "website": author.get("website"),
    }


async def search_gs_by_papers(
    name: str,
    hint_keywords: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find a scholar's GS author id by searching their papers.

    SerpAPI's `google_scholar_profiles` engine was discontinued, so we
    use the regular `google_scholar` engine to search for papers by
    author and extract GS author ids from the `author_id` link on
    each result's authors list. This is the same Tier-2 pattern the
    legacy `search_ss_by_papers` used for Semantic Scholar.

    `hint_keywords` should be a signature-work-style query — for
    example "growing string method" for Paul Zimmerman or "deep
    compression" for Song Han. Grounded search or the SS paper list
    can provide these.

    Returns a deduped list of candidates:
        {
            "gs_id": "DByD9dgAAAAJ",
            "name": "Paul Zimmerman",
            "paper_title": "Growing string method...",
        }
    Caller verifies each candidate via `fetch_gs_profile(gs_id)`
    and picks the best name+h-index match.
    """
    if not settings.SERPAPI_KEY:
        return []

    query_parts = [f'author:"{name}"']
    if hint_keywords:
        query_parts.append(hint_keywords)
    query = " ".join(query_parts)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            r = await c.get(
                _SERPAPI_BASE,
                params={
                    "engine": "google_scholar",
                    "q": query,
                    "api_key": settings.SERPAPI_KEY,
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception as e:
        logger.warning("search_gs_by_papers: SerpAPI failed: %s", e)
        return []

    # Tokenize the queried name for a simple match check.
    name_tokens = {t.lower() for t in name.split() if len(t) > 1}

    seen: dict[str, dict[str, Any]] = {}
    for res in (data.get("organic_results") or [])[:limit * 2]:
        pub = res.get("publication_info") or {}
        for author in pub.get("authors") or []:
            gs_id = author.get("author_id")
            if not gs_id or gs_id in seen:
                continue
            a_name = author.get("name") or ""
            a_tokens = {t.lower().rstrip(".") for t in a_name.split() if t}
            # Require at least one name token overlap (surname match).
            if not (name_tokens & a_tokens):
                continue
            seen[gs_id] = {
                "gs_id": gs_id,
                "name": a_name,
                "paper_title": res.get("title"),
            }
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break

    return list(seen.values())


async def _direct_scrape_gs(gs_id: str) -> dict[str, Any] | None:
    """Fetch scholar.google.com profile page and regex-scrape stats.

    Google Scholar is unauthenticated for profile pages but has no
    official API. This is a best-effort fallback — do not call this
    at high frequency or we will get rate-limited / blocked.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _DIRECT_USER_AGENT},
        ) as c:
            r = await c.get(
                f"https://scholar.google.com/citations?user={gs_id}&hl=en"
            )
            if r.status_code != 200:
                return None
            html = r.text
    except Exception as e:
        logger.warning("fetch_gs_profile: direct scrape failed for %s: %s", gs_id, e)
        return None

    name_m = re.search(r'id="gsc_prf_in">([^<]+)<', html)
    if not name_m:
        return None

    # Stat table layout: [citations_all, citations_recent, h_all,
    # h_recent, i10_all, i10_recent]
    stat_values = re.findall(r'gsc_rsb_std[^>]*>(\d+)<', html)
    total = int(stat_values[0]) if len(stat_values) > 0 else None
    h = int(stat_values[2]) if len(stat_values) > 2 else None
    i10 = int(stat_values[4]) if len(stat_values) > 4 else None

    # gsc_prf_il div layout (in order):
    #   0: affiliation line ("Associate Professor, MIT" or similar)
    #   1: verified email / homepage line
    #   2: interests (space-separated)
    blocks = re.findall(r'<div class="gsc_prf_il"[^>]*>(.*?)</div>', html)
    affiliation_line = None
    interests: list[str] = []
    homepage = None
    if blocks:
        # First block = affiliation. Strip any nested tags.
        affiliation_line = re.sub(r"<[^>]+>", " ", blocks[0]).strip() or None
    if len(blocks) >= 2:
        m = re.search(r'href="([^"]+)"', blocks[1])
        if m:
            homepage = m.group(1)
    if len(blocks) >= 3:
        interests = [
            re.sub(r"<[^>]+>", "", part).strip()
            for part in re.findall(
                r"<a[^>]*class=\"gsc_prf_ila\"[^>]*>(.*?)</a>", blocks[2]
            )
        ]
        interests = [i for i in interests if i]

    return {
        "source": "direct_scrape",
        "name": name_m.group(1),
        "affiliations": [affiliation_line] if affiliation_line else [],
        "h_index": h,
        "i10_index": i10,
        "total_citations": total,
        "website": homepage,
        "interests": interests,
    }


# ── Layer 2 source entry point ───────────────────────────────────────


def _scholar_gs_id(scholar_id: str) -> str | None:
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    ident = (profile or {}).get("identity") or {}
    gs = ident.get("google_scholar") or {}
    gs_id = gs.get("id")
    if gs_id:
        return gs_id
    url = gs.get("url")
    if not url:
        return None
    qs = parse_qs(urlparse(url).query)
    return (qs.get("user") or [None])[0]


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    gs_id = _scholar_gs_id(scholar_id)
    if not gs_id:
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_gs_author_id"},
        )
        return {"changed": False, "snapshot_id": sid, "error": "no_gs_author_id"}

    profile_data = await fetch_gs_profile(gs_id)
    if not profile_data:
        logger.warning(
            "google_scholar_stats: no profile data for %s / %s", scholar_id, gs_id
        )
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "error": "gs_fetch_empty"},
        )
        return {"changed": False, "snapshot_id": sid, "error": "gs_fetch_empty"}

    h_index = profile_data.get("h_index")
    i10 = profile_data.get("i10_index")
    total = profile_data.get("total_citations")

    profile_path = dossier_path(scholar_id) / "profile.json"
    profile = read_json(profile_path) or {}
    prev_metrics = dict(profile.get("metrics") or {})
    new_metrics = {
        **prev_metrics,
        "h_index": h_index,
        "i10_index": i10,
        "total_citations": total,
        "source": f"google_scholar:{profile_data['source']}",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    changed = (
        prev_metrics.get("h_index") != h_index
        or prev_metrics.get("total_citations") != total
    )
    profile["metrics"] = new_metrics
    write_json(profile_path, profile)

    snapshot_id = await record_snapshot(
        scholar_id,
        SOURCE_ID,
        detail={
            "mode": mode,
            "reason": reason,
            "h_index": h_index,
            "total_citations": total,
            "changed": changed,
            "fetch_source": profile_data["source"],
        },
    )

    return {"changed": changed, "snapshot_id": snapshot_id, "metrics": new_metrics}
