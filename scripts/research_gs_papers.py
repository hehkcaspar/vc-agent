"""Side research: fetch Song Han's recent papers from Google Scholar
via SerpAPI's ``google_scholar_author`` engine, compare against the
current ``papers.json`` (Semantic Scholar authoritative).

We want to know:

1. Does SerpAPI surface recent papers SS is missing? (The SS lag
   hypothesis behind the BACKLOG entry.)
2. What shape does SerpAPI's ``articles[]`` have vs the SS shape?
3. Pagination & quota implications.
4. Fallback path: does the direct-scrape route also work?

No production changes; outputs land under ``data/audits/``.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "audits"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SCHOLAR_ID = "1e563bf0-1892-4690-8222-164a3c230d08"  # Song Han
PAPERS_PATH = REPO_ROOT / "data" / "scholars" / SCHOLAR_ID / "papers.json"
PROFILE_PATH = REPO_ROOT / "data" / "scholars" / SCHOLAR_ID / "profile.json"

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.config import settings  # noqa: E402

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
SERPAPI = "https://serpapi.com/search.json"


# ─────────────────────────────────────────────────────────────────
# SerpAPI fetcher — paginate articles via start=0,20,40,...
# ─────────────────────────────────────────────────────────────────


async def serpapi_articles(gs_id: str, max_pages: int = 5) -> list[dict[str, Any]]:
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY not configured")

    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30) as c:
        for page in range(max_pages):
            start = page * 20
            r = await c.get(SERPAPI, params={
                "engine": "google_scholar_author",
                "author_id": gs_id,
                "hl": "en",
                "sort": "pubdate",   # most-recent first — surfaces SS-lag papers
                "start": start,
                "num": 20,
                "api_key": settings.SERPAPI_KEY,
            })
            if r.status_code != 200:
                print(f"  serpapi page {page}: HTTP {r.status_code}",
                      file=sys.stderr)
                break
            data = r.json()
            arts = data.get("articles") or []
            if not arts:
                break
            out.extend(arts)
            # Respect pagination.next — stop when absent.
            if not data.get("serpapi_pagination", {}).get("next"):
                break
    return out


# ─────────────────────────────────────────────────────────────────
# Direct scrape fallback — raw HTML parse of citations page
# ─────────────────────────────────────────────────────────────────


_TR_RE = re.compile(
    r'<tr class="gsc_a_tr">([\s\S]*?)</tr>',
)
_TITLE_RE = re.compile(
    r'<a[^>]*class="gsc_a_at"[^>]*>([\s\S]*?)</a>',
)
_GRAY_RE = re.compile(r'<div class="gs_gray">([\s\S]*?)</div>')
_CITES_RE = re.compile(
    r'<a[^>]*class="gsc_a_ac[^"]*"[^>]*>([0-9]*)</a>',
)
_YEAR_RE = re.compile(
    r'<span[^>]*class="gsc_a_h[^"]*"[^>]*>([0-9]{4})?</span>',
)


def _detag(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


async def direct_scrape_articles(
    gs_id: str, max_rows: int = 100,
) -> list[dict[str, Any]]:
    """Fetch `scholar.google.com/citations?user=ID&cstart=0&pagesize=100&sortby=pubdate`
    and parse the visible rows.
    """
    url = (
        f"https://scholar.google.com/citations?user={gs_id}"
        f"&hl=en&cstart=0&pagesize={max_rows}&sortby=pubdate"
    )
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        r = await c.get(url)
        if r.status_code != 200:
            print(f"  direct scrape: HTTP {r.status_code}", file=sys.stderr)
            return []
        body = r.text
    rows = _TR_RE.findall(body)
    out: list[dict[str, Any]] = []
    for row in rows:
        title_m = _TITLE_RE.search(row)
        title = _detag(title_m.group(1)) if title_m else ""
        grays = [_detag(g) for g in _GRAY_RE.findall(row)]
        authors = grays[0] if len(grays) >= 1 else ""
        venue = grays[1] if len(grays) >= 2 else ""
        cites_m = _CITES_RE.search(row)
        citations = int(cites_m.group(1)) if (cites_m and cites_m.group(1)) else 0
        year_m = _YEAR_RE.search(row)
        year = int(year_m.group(1)) if (year_m and year_m.group(1)) else None
        if title:
            out.append({
                "title": title,
                "authors": authors,
                "venue": venue,
                "citations": citations,
                "year": year,
            })
    return out


# ─────────────────────────────────────────────────────────────────
# Coverage comparison vs papers.json (SS truth)
# ─────────────────────────────────────────────────────────────────


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    if not s:
        return ""
    return _WS.sub(" ", s.strip().lower()).strip('.,;:!?"\'()[]{}- ')


def _index_ss(papers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_norm(p.get("title") or ""): p for p in papers}


async def main() -> int:
    profile = json.loads(PROFILE_PATH.read_text())
    gs_id = profile["identity"]["google_scholar"]["id"]
    print(f"Scholar: Song Han (GS id: {gs_id})", file=sys.stderr)

    ss_items = json.loads(PAPERS_PATH.read_text()).get("items", [])
    ss_index = _index_ss(ss_items)
    print(f"SS papers.json has {len(ss_items)} papers", file=sys.stderr)

    # Pass 1 — SerpAPI
    print("\nStage 1: SerpAPI google_scholar_author (sort=pubdate)",
          file=sys.stderr)
    try:
        serp_articles = await serpapi_articles(gs_id, max_pages=3)
        print(f"  fetched {len(serp_articles)} SerpAPI articles",
              file=sys.stderr)
    except Exception as exc:
        print(f"  SerpAPI failed: {exc}", file=sys.stderr)
        serp_articles = []

    # Pass 2 — direct scrape (fallback + coverage cross-check)
    print("\nStage 2: direct scrape /citations?user=... (pubdate sort)",
          file=sys.stderr)
    try:
        scrape_articles = await direct_scrape_articles(gs_id, max_rows=100)
        print(f"  parsed {len(scrape_articles)} rows from HTML",
              file=sys.stderr)
    except Exception as exc:
        print(f"  direct scrape failed: {exc}", file=sys.stderr)
        scrape_articles = []

    # ── Coverage comparisons ──
    def _coverage(label: str, candidates: list[dict[str, Any]]):
        matched = 0
        missing_from_ss: list[dict[str, Any]] = []
        for a in candidates:
            t = _norm(a.get("title") or "")
            if not t:
                continue
            if t in ss_index:
                matched += 1
            else:
                missing_from_ss.append(a)
        print(f"\n{label}:")
        print(f"  {len(candidates)} papers total")
        print(f"  {matched} match papers.json (SS)")
        print(f"  {len(missing_from_ss)} NOT in papers.json — "
              "potential SS-lag gap")
        # Show 10 most recent gaps by reported year/citations
        def _year_of(a):
            y = a.get("year")
            if isinstance(y, int):
                return y
            pub = a.get("publication") or ""
            m = re.search(r"(19\d{2}|20\d{2})", pub)
            return int(m.group(1)) if m else 0
        missing_from_ss.sort(key=_year_of, reverse=True)
        for a in missing_from_ss[:10]:
            y = _year_of(a)
            cits = a.get("cited_by", {}).get("value") or a.get("citations", 0)
            print(f"    [{y}] cits={cits}  {str(a.get('title'))[:80]}")
            if a.get("publication"):
                print(f"          {a['publication'][:110]}")
            if a.get("link"):
                print(f"          → {a['link'][:110]}")
            elif a.get("venue"):
                print(f"          venue: {a['venue'][:110]}")

    _coverage("SerpAPI articles", serp_articles)
    _coverage("Direct-scrape rows", scrape_articles)

    # Inspect one raw SerpAPI record for shape reference.
    if serp_articles:
        print("\nRaw SerpAPI article shape (first record):")
        print(json.dumps(serp_articles[0], indent=2)[:1200])

    # Persist everything for the audit.
    out_payload = {
        "gs_id": gs_id,
        "ss_paper_count": len(ss_items),
        "serpapi_count": len(serp_articles),
        "serpapi_sample": serp_articles[:10],
        "direct_scrape_count": len(scrape_articles),
        "direct_scrape_sample": scrape_articles[:10],
    }
    (OUT_DIR / "gs_papers_research.json").write_text(
        json.dumps(out_payload, indent=2), encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
