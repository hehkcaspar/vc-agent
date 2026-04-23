"""Layer 2 source — Google Scholar papers (PRIMARY).

Fetches the scholar's paper list from Google Scholar and writes to
``papers.json`` as the primary source. Semantic Scholar
(``semantic_scholar_papers.py``) runs alongside as an enrichment pass
that adds authorId / DOI / s2_fields / influential_citations to
matched titles.

Why primary?
------------

Empirical finding (2026-04-21 audit): for active researchers, SS lags
real-world publication by months. Song Han had 97 SS papers and zero
overlap with his 60 most-recent GS papers. GS via SerpAPI's
``google_scholar_author`` engine surfaces fresh arXiv preprints
within ~24h — SS sees them only eventually.

Pipeline:
    1. SerpAPI ``google_scholar_author`` with ``sort=pubdate`` —
       paginated (start=0,100,200,...) until articles empty or
       pagination.next absent.
    2. Empty / errored → direct HTML scrape of the citations page
       as fallback (same page URL, sortby=pubdate, pagesize=100,
       cstart=0,100,...).
    3. Shape-adapt each GS article to papers.json schema, including
       a name-match heuristic for the scholar's first/last/middle
       position on the paper (GS doesn't expose SS-style authorIds).
    4. Merge into papers.json via the source-aware priority rules
       in ``papers_merge.py`` — preserves any SS enrichment on
       overlapping titles and any routed stubs.
    5. Recompute ``attributed_metrics.json`` against the merged
       ledger so GS-only papers participate via ``_author_position``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ....config import settings
from ..attributed_metrics import compute_attributed_metrics
from ..channel_pollers import _serpapi_request
from ..events_sync import log_event, paper_significance
from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json
from ..papers_merge import _merge_papers_by_priority, normalize_ledger_row

logger = logging.getLogger(__name__)

SOURCE_ID = "google_scholar_papers"

# SerpAPI ceiling: iterate pages of 100 up to this many papers.
# Matches SS's _DEFAULT_LIMIT so both sources have the same reach.
_MAX_PAPERS = 500
_PAGE_SIZE = 100

# Direct-scrape fallback UA (matches google_scholar_stats convention).
_DIRECT_UA = "Mozilla/5.0 (VC-Academic-Tracker/1.0)"
_HTTP_TIMEOUT = 30.0

_WS_RE = re.compile(r"\s+")


# ══════════════════════════════════════════════════════════════════════
# Scholar identity helpers
# ══════════════════════════════════════════════════════════════════════


def _scholar_gs_id(scholar_id: str) -> str | None:
    """Read the verified GS author id from profile.json."""
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    ident = (profile or {}).get("identity") or {}
    gs = ident.get("google_scholar") or {}
    gs_id = gs.get("id")
    if gs_id:
        return gs_id
    # Last-ditch fallback: parse from url query string.
    url = gs.get("url") or ""
    if not url:
        return None
    qs = parse_qs(urlparse(url).query)
    return (qs.get("user") or [None])[0]


def _scholar_name(scholar_id: str) -> str:
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    return (profile or {}).get("name") or ""


# ══════════════════════════════════════════════════════════════════════
# SerpAPI fetch (primary)
# ══════════════════════════════════════════════════════════════════════


async def _fetch_serpapi(
    gs_id: str,
    *,
    known_gs_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Paginated fetch of the author's papers via SerpAPI
    ``google_scholar_author``. Sorts by ``pubdate`` so recent preprints
    come first.

    Returns ``(articles, early_break)``. ``early_break`` is True when
    the loop stopped because a page was fully in ``known_gs_ids`` —
    useful for snapshot observability ("we saved N API calls").

    If ``known_gs_ids`` is non-empty (incremental runs), the loop
    breaks early as soon as a page's GS ids are fully contained in the
    set — everything on subsequent pages is older still and already in
    the ledger, so fetching them wastes API credits. Bootstrap runs
    should pass ``known_gs_ids=None`` to fetch the full ceiling.
    """
    if not settings.SERPAPI_KEY:
        return [], False
    collected: list[dict[str, Any]] = []
    early_break = False
    for start in range(0, _MAX_PAPERS, _PAGE_SIZE):
        try:
            data = await _serpapi_request({
                "engine": "google_scholar_author",
                "author_id": gs_id,
                "hl": "en",
                "sort": "pubdate",
                "num": _PAGE_SIZE,
                "start": start,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "google_scholar_papers: SerpAPI page start=%d failed: %s",
                start, exc,
            )
            break
        arts = data.get("articles") or []
        if not arts:
            break
        collected.extend(arts)

        # Early-break on a steady-state ledger. We only check when the
        # caller actually has a prior ledger (known_gs_ids truthy) —
        # skipping this for bootstrap guarantees the ceiling-depth
        # sweep on first ingest.
        if known_gs_ids:
            page_ids = {
                f"gs-{a.get('citation_id')}"
                for a in arts if a.get("citation_id")
            }
            if page_ids and page_ids.issubset(known_gs_ids):
                logger.info(
                    "google_scholar_papers: early-break at start=%d — "
                    "all %d results already in ledger",
                    start, len(arts),
                )
                early_break = True
                break

        if not (data.get("serpapi_pagination") or {}).get("next"):
            break
    return collected, early_break


# ══════════════════════════════════════════════════════════════════════
# Direct-scrape fallback
# ══════════════════════════════════════════════════════════════════════


_TR_RE = re.compile(r'<tr class="gsc_a_tr">([\s\S]*?)</tr>')
_TITLE_RE = re.compile(r'<a[^>]*class="gsc_a_at"[^>]*>([\s\S]*?)</a>')
# `<a>` attribute order varies — use two lookaheads so href and class
# can appear in either order inside the same tag.
_TITLE_HREF_RE = re.compile(
    r'<a\b(?=[^>]*class="gsc_a_at")[^>]*\bhref="([^"]+)"'
)
_GRAY_RE = re.compile(r'<div class="gs_gray">([\s\S]*?)</div>')
_CITES_RE = re.compile(r'<a[^>]*class="gsc_a_ac[^"]*"[^>]*>([0-9]*)</a>')
_YEAR_RE = re.compile(r'<span[^>]*class="gsc_a_h[^"]*"[^>]*>([0-9]{4})?</span>')


def _detag(s: str) -> str:
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


async def _fetch_direct_scrape(
    gs_id: str,
    *,
    known_gs_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Fallback: parse the citations HTML directly. Bot-sensitive,
    use sparingly (SerpAPI primary path covers most cases).

    Returns ``(articles, early_break)`` — same contract as
    ``_fetch_serpapi``.

    ``known_gs_ids`` drives the same early-break as ``_fetch_serpapi``
    so incremental scrapes on steady-state ledgers stop after the
    first fully-known page.
    """
    collected: list[dict[str, Any]] = []
    early_break = False
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _DIRECT_UA},
    ) as c:
        for cstart in range(0, _MAX_PAPERS, _PAGE_SIZE):
            url = (
                f"https://scholar.google.com/citations?user={gs_id}"
                f"&hl=en&cstart={cstart}&pagesize={_PAGE_SIZE}&sortby=pubdate"
            )
            try:
                r = await c.get(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "google_scholar_papers: scrape cstart=%d failed: %s",
                    cstart, exc,
                )
                break
            if r.status_code != 200:
                break
            rows = _TR_RE.findall(r.text)
            if not rows:
                break
            page_start_len = len(collected)
            for row in rows:
                title_m = _TITLE_RE.search(row)
                if not title_m:
                    continue
                title = _detag(title_m.group(1))
                href_m = _TITLE_HREF_RE.search(row)
                link = (
                    "https://scholar.google.com" + href_m.group(1)
                    if href_m else ""
                )
                grays = [_detag(g) for g in _GRAY_RE.findall(row)]
                authors_str = grays[0] if grays else ""
                publication = grays[1] if len(grays) >= 2 else ""
                cites_m = _CITES_RE.search(row)
                citations = int(cites_m.group(1)) if (
                    cites_m and cites_m.group(1)
                ) else 0
                year_m = _YEAR_RE.search(row)
                year = int(year_m.group(1)) if (
                    year_m and year_m.group(1)
                ) else None
                collected.append({
                    "title": title,
                    "link": link,
                    "authors": authors_str,
                    "publication": publication,
                    "cited_by": {"value": citations},
                    "year": str(year) if year else "",
                    # No citation_id from scrape — synthesize from link.
                    "citation_id": _extract_citation_id(link),
                })

            # Early-break mirrors the SerpAPI path: all newly-collected
            # GS ids on this page already known → stop.
            if known_gs_ids:
                page_items = collected[page_start_len:]
                page_ids = {
                    f"gs-{it.get('citation_id')}"
                    for it in page_items if it.get("citation_id")
                }
                if page_ids and page_ids.issubset(known_gs_ids):
                    logger.info(
                        "google_scholar_papers: scrape early-break at "
                        "cstart=%d — all %d results already in ledger",
                        cstart, len(page_items),
                    )
                    early_break = True
                    break
    return collected, early_break


def _extract_citation_id(link: str) -> str:
    """Pull ``citation_for_view=AAAA:BBBB`` out of a GS link."""
    if not link:
        return ""
    qs = parse_qs(urlparse(link).query)
    return (qs.get("citation_for_view") or [""])[0]


# ══════════════════════════════════════════════════════════════════════
# Shape adapter
# ══════════════════════════════════════════════════════════════════════


def _parse_venue(publication: str) -> str:
    """Strip trailing year fragment ``", 2026"`` from a publication
    string. GS returns e.g. ``"arXiv preprint arXiv:2604.06832, 2026"``;
    we want just the venue part.
    """
    if not publication:
        return ""
    return re.sub(r",?\s*(19|20)\d{2}\s*$", "", publication).strip()


def _split_authors(authors_str: str) -> list[dict[str, Any]]:
    """GS returns a comma-separated string, often truncated with
    ``"..."``. Split, drop placeholders, coerce to ``[{"name": ...}]``
    — the shape SS uses, minus ``authorId``."""
    if not authors_str:
        return []
    out: list[dict[str, Any]] = []
    for raw in authors_str.split(","):
        name = raw.strip()
        if not name or name == "...":
            continue
        out.append({"name": name})
    return out


_NAME_VARIANTS_CACHE: dict[str, list[str]] = {}
_NAME_VARIANTS_LOCK = threading.Lock()


def _name_variants(name: str) -> list[str]:
    """Return lowercase match variants for a scholar name.

    For ``"Song Han"`` returns
    ``["song han", "s han", "s. han", "han s", "han s."]`` — enough
    to match GS's common rendering styles.

    Cached per name across the process; thread-safe (heartbeat can
    refine multiple scholars concurrently).
    """
    if not name:
        return []
    with _NAME_VARIANTS_LOCK:
        cached = _NAME_VARIANTS_CACHE.get(name)
    if cached is not None:
        return cached

    parts = _WS_RE.sub(" ", name).strip().split(" ")
    parts = [p for p in parts if p]
    if not parts:
        out: list[str] = []
    else:
        last = parts[-1].lower()
        first = parts[0].lower()
        initial = first[0]
        variants = {
            f"{first} {last}",
            f"{initial} {last}",
            f"{initial}. {last}",
            f"{initial}.{last}",
            f"{last} {first}",
            f"{last} {initial}",
            f"{last} {initial}.",
        }
        out = sorted(variants)

    with _NAME_VARIANTS_LOCK:
        _NAME_VARIANTS_CACHE[name] = out
    return out


def _infer_position(
    authors_str: str, scholar_name: str,
) -> str | None:
    """Return ``"first" | "last" | "middle" | "sole" | None``.

    ``None`` only when the scholar is not findable in the visible
    portion of the author string (GS truncated the middle AND the
    scholar isn't in the visible head or tail). Callers that get
    ``None`` should count the paper toward raw totals but not toward
    first/last author buckets.

    Heuristic rules:

    - No ellipsis, single visible author that matches → ``"sole"``.
    - Scholar at index 0 of the visible tokens → ``"first"``.
    - No ellipsis, scholar at last index → ``"last"``.
    - With trailing ellipsis, scholar in the head (not index 0) →
      ``"middle"`` (can't tell if they're actually last since there
      may be more authors after the ``"..."``).
    - With trailing ellipsis, scholar in the tail (tokens after the
      last ellipsis) → ``"last"`` if the literal last token, else
      ``"middle"``.
    - Any middle match → ``"middle"``.
    """
    if not authors_str or not scholar_name:
        return None
    variants = _name_variants(scholar_name)
    if not variants:
        return None

    def _hit(token: str) -> bool:
        token = token.strip(". ").lower()
        if not token:
            return False
        for v in variants:
            if token == v:
                return True
            if token == v.replace(".", "").strip():
                return True
            if token.endswith(" " + v) or v.endswith(" " + token):
                return True
        return False

    has_ellipsis = "..." in authors_str

    if not has_ellipsis:
        tokens = [
            t.strip().lower() for t in authors_str.split(",") if t.strip()
        ]
        if not tokens:
            return None
        if len(tokens) == 1:
            return "sole" if _hit(tokens[0]) else None
        for idx, t in enumerate(tokens):
            if _hit(t):
                if idx == 0:
                    return "first"
                if idx == len(tokens) - 1:
                    return "last"
                return "middle"
        return None

    # Truncated list: scan visible head (before last "..."), then tail
    # (after last "..."). Anywhere in the head that isn't index 0
    # collapses to "middle" because the real last author is hidden
    # past the ellipsis.
    last_ellipsis_idx = authors_str.rfind("...")
    head = authors_str[:last_ellipsis_idx]
    tail = authors_str[last_ellipsis_idx + 3:]
    head_tokens = [
        t.strip().lower() for t in head.split(",")
        if t.strip() and t.strip() != "..."
    ]
    tail_tokens = [
        t.strip().lower() for t in tail.split(",") if t.strip()
    ]

    for idx, t in enumerate(head_tokens):
        if _hit(t):
            return "first" if idx == 0 else "middle"

    for idx, t in enumerate(tail_tokens):
        if _hit(t):
            if idx == len(tail_tokens) - 1 and tail_tokens:
                return "last"
            return "middle"

    return None


def _normalize_gs_article(
    article: dict[str, Any], scholar_name: str,
) -> dict[str, Any] | None:
    """SerpAPI / scrape article → papers.json row shape."""
    title = (article.get("title") or "").strip()
    if not title:
        return None
    year_raw = article.get("year") or ""
    try:
        year: int | None = int(str(year_raw).strip()) if year_raw else None
    except ValueError:
        year = None
    cited_by = article.get("cited_by") or {}
    citations = 0
    if isinstance(cited_by, dict):
        v = cited_by.get("value")
        if isinstance(v, (int, float)):
            citations = int(v)
        elif isinstance(v, str) and v.isdigit():
            citations = int(v)
    authors_str = article.get("authors") or ""
    publication = article.get("publication") or ""
    citation_id = article.get("citation_id") or ""
    link = article.get("link") or ""
    # ID stability matters for merge dedup + snapshot diffs. Use the
    # citation_id when present (unique per GS), else SHA1(title) —
    # deterministic across process restarts (Python's `hash()` is
    # randomized per interpreter, so it would flip the ID on every run).
    if citation_id:
        pid = f"gs-{citation_id}"
    else:
        pid = f"gs-t{hashlib.sha1(title.encode('utf-8')).hexdigest()[:12]}"
    return {
        "id": pid,
        "title": title,
        "authors": _split_authors(authors_str),
        "year": year,
        "venue": _parse_venue(publication),
        "citations": citations,
        "publication_date": None,  # GS doesn't expose ISO date
        "_source": "google_scholar",
        "_gs_citation_id": citation_id,
        "_gs_link": link,
        "_author_position": _infer_position(authors_str, scholar_name),
    }


# ══════════════════════════════════════════════════════════════════════
# run() — heartbeat entry point
# ══════════════════════════════════════════════════════════════════════


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    gs_id = _scholar_gs_id(scholar_id)
    if not gs_id:
        # Skip is not a failure — semantic_scholar_papers runs on its
        # own cadence and writes papers.json via the same merge helper.
        # Log the explicit fallback routing so operators reviewing
        # snapshots see the intent.
        sid = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={
                "mode": mode,
                "skipped": "no_gs_author_id",
                "fallback": "semantic_scholar_papers",
            },
        )
        return {
            "changed": False, "snapshot_id": sid,
            "error": "no_gs_author_id",
        }

    scholar_name = _scholar_name(scholar_id)

    # Read the ledger BEFORE fetching so we can short-circuit the
    # paginator once we've scanned past the newest known GS ids.
    # Bootstrap mode ignores the ledger and sweeps the full ceiling.
    papers_path = dossier_path(scholar_id) / "papers.json"
    prev = read_json(papers_path)
    prev_items = [
        normalize_ledger_row(p)
        for p in (prev.get("items") or []) if isinstance(p, dict)
    ]
    prev_gs_ids: set[str] = {
        p["id"] for p in prev_items
        if isinstance(p.get("id"), str) and p["id"].startswith("gs-")
    }
    early_break_ids = (
        prev_gs_ids if mode == "incremental" and prev_gs_ids else None
    )

    # Pass 1 — SerpAPI.
    serpapi_used = False
    fallback_used = False
    early_broke = False
    raw_articles: list[dict[str, Any]] = []
    if settings.SERPAPI_KEY:
        try:
            raw_articles, early_broke = await _fetch_serpapi(
                gs_id, known_gs_ids=early_break_ids,
            )
            serpapi_used = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "google_scholar_papers: SerpAPI path failed: %s", exc,
            )
            raw_articles = []

    # Pass 2 — direct scrape fallback.
    if not raw_articles:
        try:
            raw_articles, early_broke = await _fetch_direct_scrape(
                gs_id, known_gs_ids=early_break_ids,
            )
            fallback_used = True
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "google_scholar_papers: scrape fallback failed for %s",
                scholar_id,
            )
            sid = await record_snapshot(
                scholar_id, SOURCE_ID,
                detail={"mode": mode, "error": str(exc)},
            )
            return {
                "changed": False, "snapshot_id": sid, "error": str(exc),
            }

    if not raw_articles:
        sid = await record_snapshot(
            scholar_id, SOURCE_ID,
            detail={
                "mode": mode, "reason": reason,
                "serpapi_used": serpapi_used,
                "fallback_used": fallback_used,
                "paper_count": 0, "skipped": "no_articles",
            },
        )
        return {
            "changed": False, "snapshot_id": sid, "paper_count": 0,
        }

    gs_rows = [
        r for r in (
            _normalize_gs_article(a, scholar_name) for a in raw_articles
        ) if r is not None
    ]

    merged = _merge_papers_by_priority(
        gs_rows, prev_items, incoming_source="google_scholar",
    )

    # `changed` should fire when THIS source materially altered the
    # ledger. Because early-break may have stopped the fetch before
    # covering every known GS id, we compare the incoming set only
    # against the PREFIX of `prev_gs_ids` that overlaps with it — i.e.
    # flag as changed iff incoming introduces any id not already in
    # the ledger, or the merge grew/shrank the row count.
    new_ids = {r.get("id") for r in gs_rows if r.get("id")}
    changed = (
        bool(new_ids - prev_gs_ids)
        or len(merged) != len(prev_items)
    )

    write_json(papers_path, {"items": merged, "count": len(merged)})

    # Recompute metrics against the MERGED ledger so GS-only papers
    # participate via the _author_position heuristic.
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    ss_id = (
        ((profile or {}).get("identity") or {}).get("semantic_scholar")
        or {}
    ).get("id") or ""
    metrics = compute_attributed_metrics(merged, ss_id)
    write_json(
        dossier_path(scholar_id) / "attributed_metrics.json", metrics,
    )

    # Log new-paper events on incremental runs, capped at 20/run.
    # "New" means the GS row's id wasn't in the GS portion of the
    # ledger before this run. (Non-GS ids in prev don't count — they
    # aren't duplicates, they just aren't GS-sourced yet.)
    events_logged = 0
    if mode == "incremental" and prev_gs_ids:
        truly_new = [
            r for r in gs_rows
            if r.get("id") and r["id"] not in prev_gs_ids
        ]
        for p in truly_new[:20]:
            try:
                position = p.get("_author_position")
                paper_date = None
                if p.get("year"):
                    try:
                        paper_date = datetime(
                            int(p["year"]), 1, 1, tzinfo=timezone.utc,
                        )
                    except (ValueError, TypeError):
                        pass
                await log_event(
                    scholar_id,
                    event_type="new_paper",
                    title=f"New paper: {(p.get('title') or '?')[:100]}",
                    significance=paper_significance(
                        int(p.get("citations") or 0), position,
                    ),
                    event_date=paper_date,
                    payload={
                        "gs_citation_id": p.get("_gs_citation_id"),
                        "title": p.get("title"),
                        "year": p.get("year"),
                        "venue": p.get("venue"),
                        "citations": p.get("citations"),
                        "position": position,
                        "source": "google_scholar",
                    },
                )
                events_logged += 1
            except Exception:
                logger.warning(
                    "google_scholar_papers: log_event failed",
                    exc_info=True,
                )

    snapshot_id = await record_snapshot(
        scholar_id, SOURCE_ID,
        detail={
            "mode": mode, "reason": reason,
            "paper_count": len(gs_rows),
            "merged_count": len(merged),
            "changed": changed,
            "events_logged": events_logged,
            "serpapi_used": serpapi_used,
            "fallback_used": fallback_used,
            "early_break": early_broke,
        },
    )

    return {
        "changed": changed,
        "snapshot_id": snapshot_id,
        "paper_count": len(gs_rows),
        "merged_count": len(merged),
        "events_logged": events_logged,
    }
