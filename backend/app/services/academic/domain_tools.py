"""
Academic Tracking v2 — domain tools for the scholar agent.

Tools are built via ``build_scholar_tools(scholar_id)`` which returns closures
with the scholar's dossier ID already bound — the agent never needs to pass
or know the UUID.  This matches the portfolio agent's closure pattern.

See design doc §4.3.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import tool

import httpx

from app.config import settings
from app.academic_database import AcademicSyncSessionLocal
from app.academic_models import Scholar, ScholarEvent

from .file_utils import dossier_path as _dossier, read_json as _read_json, write_json as _write_json
from .semantic_scholar import SemanticScholarService

_SERPAPI_BASE = "https://serpapi.com/search.json"
_SERPAPI_TIMEOUT = 30.0
from .tool_utils import (
    classify_urls as _classify_urls,
    compute_author_position,
    is_top_venue,
    names_match,
    norm_title,
    parse_json,
    safe_int,
    verify_ss_metrics,
)

logger = logging.getLogger(__name__)


def _ss() -> SemanticScholarService:
    return SemanticScholarService(api_key=settings.SEMANTIC_SCHOLAR_API_KEY)


def _gemini_client():
    from google import genai
    key = (settings.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise ValueError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=key)


def _gemini_model() -> str:
    return settings.ACADEMIC_GEMINI_MODEL


# ═══════════════════════════════════════════════════════════════
#  TOOL BUILDER — closure pattern (scholar_id pre-bound)
# ═══════════════════════════════════════════════════════════════


def build_scholar_tools(scholar_id: str) -> list:
    """Build all domain tools with scholar_id pre-bound in closures.

    The agent calls e.g. ``fetch_ss_papers(ss_author_id="12345")`` — it never
    needs to know or pass the dossier UUID.
    """
    dossier = _dossier(scholar_id)

    # ── Deterministic tools ──────────────────────────────────

    @tool
    def classify_urls(urls: list[str]) -> dict[str, Any]:
        """Extract known academic profile IDs from URLs deterministically.

        Handles Google Scholar (any TLD), Semantic Scholar, LinkedIn, DBLP.
        Results are ground truth — override any LLM extraction.
        """
        return _classify_urls(urls)

    @tool
    def compute_bibliometrics() -> dict[str, Any]:
        """Compute 15+ bibliometric metrics from this scholar's papers.json.

        Reads the full paper list from disk (not LLM context).
        Returns authorship breakdown, career years, citation growth, top venues, etc.
        Call this AFTER papers have been fetched.
        """
        logger.info("[tool] compute_bibliometrics() for scholar %s", scholar_id[:8])
        papers_data = _read_json(dossier / "papers.json")
        papers = papers_data.get("papers", [])

        if not papers:
            return {"error": "No papers found in papers.json. Fetch papers first."}

        profile = _read_json(dossier / "profile.json")
        gs_cpy: dict[str, int] = profile.get("metrics", {}).get("citations_per_year", {}) or {}

        current_year = datetime.now().year

        first_author = [p for p in papers if p.get("author_position") == "first"]
        last_author = [p for p in papers if p.get("author_position") == "last"]
        sole_author = [p for p in papers if p.get("author_position") == "sole"]
        middle_author = [p for p in papers if p.get("author_position") == "middle"]

        total_citations = sum(p.get("citations", 0) for p in papers)
        first_author_citations = sum(p.get("citations", 0) for p in first_author)
        first_author_citation_pct = (
            round(first_author_citations * 100 / total_citations) if total_citations > 0 else None
        )

        years = [p["year"] for p in papers if p.get("year")]
        recent_cutoff = current_year - 5
        recent_papers = [p for p in papers if p.get("year") and p["year"] >= recent_cutoff]
        recent_5yr_citations = sum(p.get("citations", 0) for p in recent_papers)

        career_years = (max(years) - min(years)) if len(years) >= 2 else None
        papers_per_year_avg = len(papers) / career_years if career_years and career_years > 0 else None
        papers_per_year_recent = len(recent_papers) / 5.0

        citation_growth_rate = None
        if gs_cpy:
            full_years = {int(y): c for y, c in gs_cpy.items() if int(y) < current_year}
            if len(full_years) >= 4:
                sorted_years = sorted(full_years.keys(), reverse=True)
                recent_yrs = sorted_years[:3]
                prior_yrs = sorted_years[3:6]
                if prior_yrs:
                    recent_avg = sum(full_years[y] for y in recent_yrs) / len(recent_yrs)
                    prior_avg = sum(full_years[y] for y in prior_yrs) / len(prior_yrs)
                    if prior_avg > 0:
                        citation_growth_rate = round((recent_avg / prior_avg - 1) * 100, 1)

        year_citations: Counter[int] = Counter()
        for p in papers:
            if p.get("year"):
                year_citations[p["year"]] += p.get("citations", 0)
        peak_citation_year = year_citations.most_common(1)[0][0] if year_citations else None

        all_coauthors: set[str] = set()
        for p in papers:
            for a in p.get("authors", []):
                name = a.get("name", "") if isinstance(a, dict) else str(a)
                if name:
                    all_coauthors.add(name.lower().strip())

        influential_count = sum(1 for p in papers if (p.get("influential_citations") or 0) > 0)
        top_venue_count = sum(1 for p in papers if is_top_venue(p.get("venue"), p.get("publication_type")))

        total_with_position = len(first_author) + len(last_author) + len(sole_author) + len(middle_author)
        authorship_summary = {
            "first_count": len(first_author), "last_count": len(last_author),
            "sole_count": len(sole_author), "middle_count": len(middle_author),
            "first_pct": round(len(first_author) * 100 / total_with_position) if total_with_position > 0 else None,
            "last_pct": round(len(last_author) * 100 / total_with_position) if total_with_position > 0 else None,
        }

        return {
            "first_author_papers": len(first_author), "last_author_papers": len(last_author),
            "sole_author_papers": len(sole_author), "first_author_citation_pct": first_author_citation_pct,
            "recent_5yr_papers": len(recent_papers), "recent_5yr_citations": recent_5yr_citations,
            "career_years": career_years,
            "papers_per_year_avg": round(papers_per_year_avg, 1) if papers_per_year_avg else None,
            "papers_per_year_recent": round(papers_per_year_recent, 1),
            "citation_growth_rate": citation_growth_rate, "peak_citation_year": peak_citation_year,
            "unique_coauthors": len(all_coauthors), "influential_paper_count": influential_count,
            "top_venue_papers": top_venue_count, "total_papers": len(papers),
            "total_citations": total_citations, "authorship_summary": authorship_summary,
        }

    @tool
    def append_event(event_type: str, title: str, significance: str = "medium", payload: Optional[dict] = None, event_date: Optional[str] = None) -> str:
        """Log an event to this scholar's events.jsonl + SQL index. Returns event ID.

        Args:
            event_type: Category (e.g. new_paper, career_change, patent_filed).
            title: Short description of the event.
            significance: high, medium, or low.
            payload: Optional extra data dict.
            event_date: ISO date string for when the event actually occurred
                (e.g. "2017-06-01" for a company founding). If omitted, defaults
                to the current time (i.e. event happened now).
        """
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Parse the agent-provided event_date, fall back to now
        actual_date = now
        if event_date:
            try:
                actual_date = datetime.fromisoformat(event_date)
                if actual_date.tzinfo is None:
                    actual_date = actual_date.replace(tzinfo=timezone.utc)
            except ValueError:
                actual_date = now

        event = {
            "id": event_id, "type": event_type,
            "date": actual_date.isoformat(),
            "discovered_at": now.isoformat(),
            "significance": significance, "title": title,
            "payload": payload or {}, "source": "agent",
        }

        from .file_utils import append_jsonl
        append_jsonl(dossier / "events.jsonl", event)

        with AcademicSyncSessionLocal() as db:
            db.add(ScholarEvent(
                id=event_id, scholar_id=scholar_id, event_type=event_type,
                significance=significance, title=title, is_read=False,
                event_date=actual_date,
            ))
            db.commit()

        return event_id

    @tool
    def sync_sql_index() -> str:
        """Rebuild SQL index rows from this scholar's document files."""
        from app.academic_models import Channel

        profile = _read_json(dossier / "profile.json")

        with AcademicSyncSessionLocal() as db:
            scholar = db.get(Scholar, scholar_id)
            if scholar:
                scholar.name = profile.get("name", scholar.name)
                tags = profile.get("tags")
                if tags:
                    scholar.tags = json.dumps(tags)
                scholar.updated_at = datetime.now(timezone.utc)

            db.query(ScholarEvent).filter(ScholarEvent.scholar_id == scholar_id).delete()
            events_path = dossier / "events.jsonl"
            if events_path.exists():
                for line in events_path.read_text(encoding="utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    evt = json.loads(line)
                    db.add(ScholarEvent(
                        id=evt.get("id", str(uuid.uuid4())),
                        scholar_id=scholar_id, event_type=evt.get("type", "unknown"),
                        significance=evt.get("significance", "medium"), title=evt.get("title"),
                        event_date=datetime.fromisoformat(evt["date"]) if evt.get("date") else None,
                    ))

            db.query(Channel).filter(Channel.scholar_id == scholar_id).delete()
            channels_data = _read_json(dossier / "channels.json")
            for ch in channels_data.get("channels", []):
                db.add(Channel(
                    id=ch.get("id", str(uuid.uuid4())),
                    scholar_id=scholar_id, channel_type=ch.get("type", "unknown"),
                    url=ch.get("url"), is_active=ch.get("is_active", True),
                    polling_interval_hours=ch.get("polling_interval_hours", 168),
                ))

            db.commit()

        return "SQL index rebuilt"

    # ── API + deterministic tools ────────────────────────────

    @tool
    async def search_semantic_scholar(
        name: str,
        expected_h_index: Optional[int] = None,
        expected_citations: Optional[int] = None,
    ) -> dict[str, Any]:
        """Search for a scholar on Semantic Scholar by name.

        Tier 1 resolution: API name search + score by h-index proximity + verify.
        Returns {ss_id, confidence, name} or {ss_id: null} if no match.
        """
        logger.info("[tool] search_semantic_scholar(name=%s, h=%s) for scholar %s", name, expected_h_index, scholar_id[:8])
        ss = _ss()
        candidates = await ss.search_author(name, limit=10)
        if not candidates:
            return {"ss_id": None, "reason": "no candidates from SS name search"}

        best_id, best_score, best_name = None, -1.0, ""
        for c in candidates:
            c_name, c_id = c.get("name", ""), c.get("id")
            if not c_id or not c_name or not names_match(name, c_name):
                continue
            c_h = c.get("h_index") or 0
            if expected_h_index and expected_h_index > 0:
                ratio = min(c_h, expected_h_index) / max(c_h, expected_h_index, 1)
                score = ratio * 100 + c.get("paper_count", 0) * 0.01
            else:
                score = c.get("citation_count", 0) * 0.001 + c.get("paper_count", 0) * 0.01
            if score > best_score:
                best_score, best_id, best_name = score, c_id, c_name

        min_score = 30.0 if expected_h_index and expected_h_index > 10 else 0.0
        if best_id and best_score >= min_score:
            details = await ss.get_author_details(best_id)
            if details:
                if not verify_ss_metrics(details.get("h_index") or 0, details.get("citation_count") or 0, expected_h_index, expected_citations):
                    return {"ss_id": None, "reason": f"metric verification failed for {best_id}"}
            return {"ss_id": best_id, "confidence": "high", "name": best_name}

        return {"ss_id": None, "reason": f"best score {best_score:.1f} below threshold {min_score}"}

    @tool
    async def search_ss_by_papers(
        name: str,
        research_area: str,
        expected_h_index: Optional[int] = None,
        expected_citations: Optional[int] = None,
    ) -> dict[str, Any]:
        """Find SS author by searching for their papers (Tier 2 — better for common names).

        Searches papers by name + research area, extracts author IDs, verifies each.
        """
        ss = _ss()
        queries = [name]
        if research_area:
            queries.append(f"{name} {research_area}")

        seen: set[str] = set()
        for query in queries:
            papers = await ss.search_papers(query, limit=10)
            if not papers:
                continue
            papers.sort(key=lambda p: p.get("citations", 0), reverse=True)
            for paper in papers:
                for author in paper.get("authors", []):
                    a_id, a_name = author.get("authorId"), author.get("name", "")
                    if not a_id or a_id in seen or not names_match(name, a_name):
                        continue
                    seen.add(a_id)
                    details = await ss.get_author_details(a_id)
                    if not details or not names_match(name, details.get("name", "")):
                        continue
                    if not verify_ss_metrics(details.get("h_index") or 0, details.get("citation_count") or 0, expected_h_index, expected_citations):
                        continue
                    return {"ss_id": a_id, "confidence": "high", "name": details.get("name")}

        return {"ss_id": None, "reason": "no verified match from paper search"}

    @tool
    async def fetch_ss_papers(ss_author_id: str, since_year: Optional[int] = None) -> dict[str, Any]:
        """Fetch papers from Semantic Scholar and write to this scholar's papers.json.

        Call this with the SS author ID obtained from search_semantic_scholar.
        Returns a summary (total count, new papers added).
        """
        logger.info("[tool] fetch_ss_papers(ss_id=%s) for scholar %s", ss_author_id, scholar_id[:8])
        ss = _ss()
        raw_papers = await ss.get_author_papers(ss_author_id, limit=500)

        papers_path = dossier / "papers.json"
        existing_data = _read_json(papers_path)
        existing_papers = existing_data.get("papers", [])
        existing_keys = {(norm_title(p["title"]), p.get("year")): i for i, p in enumerate(existing_papers)}

        new_count = 0
        for p in raw_papers:
            title = p.get("title") or ""
            if not title.strip():
                continue
            year = p.get("year")
            if since_year and year and year < since_year:
                continue

            position, total = compute_author_position(p.get("authors", []), ss_author_id)
            fields_of_study = p.get("fields") or p.get("s2_fields") or []
            pub_types = p.get("publication_types") or []

            paper_record = {
                "id": str(uuid.uuid4()), "title": title, "authors": p.get("authors", []),
                "year": year, "venue": p.get("venue"),
                "publication_type": pub_types[0] if pub_types else None,
                "citations": p.get("citations", 0),
                "influential_citations": p.get("influential_citations", 0),
                "fields_of_study": fields_of_study, "ss_paper_id": p.get("id"),
                "url": None, "source": "semantic_scholar",
                "author_position": position, "publication_date": p.get("publication_date"),
            }

            key = (norm_title(title), year)
            if key in existing_keys:
                idx = existing_keys[key]
                existing = existing_papers[idx]
                if not existing.get("author_position") and position:
                    existing["author_position"] = position
                if p.get("citations", 0) > (existing.get("citations") or 0):
                    existing["citations"] = p.get("citations", 0)
            else:
                existing_papers.append(paper_record)
                existing_keys[key] = len(existing_papers) - 1
                new_count += 1

        summary = _compute_papers_summary(existing_papers)
        _write_json(papers_path, {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary, "papers": existing_papers,
        })

        return {"total_papers": len(existing_papers), "new_papers_added": new_count, "summary": summary}

    # ── SerpAPI tools (deterministic, no LLM) ──────────────

    async def _serpapi(params: dict) -> dict:
        key = settings.SERPAPI_KEY
        if not key:
            raise ValueError("SERPAPI_KEY is not set")
        params["api_key"] = key
        async with httpx.AsyncClient(timeout=_SERPAPI_TIMEOUT) as c:
            r = await c.get(_SERPAPI_BASE, params=params)
            r.raise_for_status()
            return r.json()

    @tool
    async def fetch_gs_metrics(gs_id: str) -> dict[str, Any]:
        """Fetch Google Scholar metrics via SerpAPI (deterministic, no LLM).

        Returns h_index, i10_index, total_citations, name, affiliation,
        research_interests, citations_per_year.
        """
        logger.info("[tool] fetch_gs_metrics(gs_id=%s) for scholar %s", gs_id, scholar_id[:8])
        try:
            data = await _serpapi({"engine": "google_scholar_author", "author_id": gs_id})
        except Exception as e:
            logger.error("fetch_gs_metrics SerpAPI failed: %s", e)
            return {"error": str(e)}

        author = data.get("author", {})
        cited_by = data.get("cited_by", {})
        table = cited_by.get("table", [])

        h_index, i10_index, total_citations = None, None, None
        for row in table:
            if "citations" in row:
                total_citations = row["citations"].get("all")
            if "h_index" in row:
                h_index = row["h_index"].get("all")
            if "i10_index" in row:
                i10_index = row["i10_index"].get("all")

        # citations_per_year from the graph
        cpy = {}
        for item in cited_by.get("graph", []):
            year = item.get("year")
            cites = item.get("citations")
            if year and cites is not None:
                cpy[str(year)] = cites

        interests = [i.get("title") for i in author.get("interests", []) if i.get("title")]

        return {
            "h_index": h_index,
            "i10_index": i10_index,
            "total_citations": total_citations,
            "name": author.get("name"),
            "affiliation": author.get("affiliations"),
            "research_interests": interests,
            "citations_per_year": cpy,
        }

    # ── Gemini tool (needs LLM to interpret web pages) ───

    @tool
    async def crawl_url(url: str) -> dict[str, Any]:
        """Crawl a URL and extract outbound links and page content summary.

        Uses Gemini + Google Search — needed because arbitrary web pages
        require LLM to interpret structure and find profile links.
        """
        logger.info("[tool] crawl_url(%s) for scholar %s", url[:80], scholar_id[:8])
        from google.genai import types
        client = _gemini_client()
        prompt = f"""\
Visit this URL: {url}
Extract: name, role, institution, and all outbound links to academic profiles
(Google Scholar, LinkedIn, Semantic Scholar, DBLP, lab pages).
Return a JSON object:
{{"name": "...", "role": "...", "institution": "...",
  "outbound_links": {{"google_scholar": "url or null", "linkedin": "url or null",
    "semantic_scholar": "url or null", "dblp": "url or null", "lab_page": "url or null"}},
  "research_areas": ["area1", "area2"]}}
Return ONLY valid JSON. Do NOT fabricate URLs."""

        config = types.GenerateContentConfig(
            temperature=0.0, max_output_tokens=2048,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        try:
            response = await client.aio.models.generate_content(
                model=_gemini_model(), contents=prompt, config=config,
            )
            return parse_json(response.text or "")
        except Exception as e:
            logger.error("crawl_url failed for %s: %s", url, e)
            return {"error": str(e)}

    # ── SerpAPI tools continued ──────────────────────────

    @tool
    async def search_web(query: str) -> dict[str, Any]:
        """General web search via SerpAPI (deterministic).

        Returns organic results with titles, snippets, and links.
        The agent interprets the results — the tool just fetches.
        """
        try:
            data = await _serpapi({"engine": "google", "q": query, "num": 10})
        except Exception as e:
            return {"error": str(e)}

        results = []
        for item in data.get("organic_results", [])[:10]:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            })

        # Include knowledge graph if present
        kg = data.get("knowledge_graph", {})
        knowledge = {}
        if kg:
            knowledge = {
                "title": kg.get("title"),
                "description": kg.get("description"),
                "type": kg.get("type"),
            }

        return {"results": results, "knowledge_graph": knowledge}

    @tool
    async def search_patents(name: str, affiliation: Optional[str] = None) -> dict[str, Any]:
        """Search for patents via SerpAPI google_patents (deterministic).

        Returns structured patent records with IDs, titles, filing dates.
        """
        query = name
        if affiliation:
            query += f" {affiliation}"
        try:
            data = await _serpapi({"engine": "google_patents", "q": query})
        except Exception as e:
            return {"error": str(e)}

        patents = []
        for p in data.get("organic_results", []):
            patents.append({
                "title": p.get("title", ""),
                "patent_id": p.get("patent_id", ""),
                "filing_date": p.get("filing_date"),
                "publication_date": p.get("publication_date"),
                "url": p.get("pdf") or p.get("link", ""),
                "snippet": p.get("snippet", ""),
            })

        return {"patents": patents, "total_found": len(patents)}

    @tool
    async def search_news(query: str) -> dict[str, Any]:
        """Search for recent news via SerpAPI google_news (deterministic).

        Returns structured news items with titles, sources, dates.
        The agent interprets significance — the tool just fetches.
        """
        try:
            data = await _serpapi({"engine": "google_news", "q": query, "gl": "us", "hl": "en"})
        except Exception as e:
            return {"error": str(e)}

        items = []
        for n in data.get("news_results", [])[:20]:
            items.append({
                "title": n.get("title", ""),
                "source": n.get("source", {}).get("name", ""),
                "date": n.get("date", ""),
                "link": n.get("link", ""),
                "snippet": n.get("snippet", ""),
            })

        return {"news_items": items, "total_found": len(data.get("news_results", []))}

    return [
        classify_urls,
        compute_bibliometrics,
        append_event,
        sync_sql_index,
        search_semantic_scholar,
        search_ss_by_papers,
        fetch_ss_papers,
        fetch_gs_metrics,
        crawl_url,
        search_web,
        search_patents,
        search_news,
    ]


def _compute_papers_summary(papers: list[dict]) -> dict[str, Any]:
    """Compute summary header for papers.json."""
    by_position: Counter[str] = Counter()
    by_decade: Counter[str] = Counter()
    for p in papers:
        pos = p.get("author_position")
        if pos:
            by_position[pos] += 1
        year = p.get("year")
        if year:
            by_decade[f"{(year // 10) * 10}s"] += 1

    sorted_by_cites = sorted(papers, key=lambda x: x.get("citations", 0), reverse=True)
    top_cited = [
        {"title": p["title"], "year": p.get("year"), "citations": p.get("citations", 0), "venue": p.get("venue")}
        for p in sorted_by_cites[:5]
    ]

    sorted_by_year = sorted([p for p in papers if p.get("year")], key=lambda x: (x["year"], x.get("citations", 0)), reverse=True)
    recent_5 = [
        {"title": p["title"], "year": p["year"], "citations": p.get("citations", 0), "venue": p.get("venue"), "position": p.get("author_position")}
        for p in sorted_by_year[:5]
    ]

    return {"total": len(papers), "by_position": dict(by_position), "by_decade": dict(by_decade), "top_cited": top_cited, "recent_5": recent_5}


# Keep DOMAIN_TOOLS for backward compat (channel_pollers, etc.) but it's not used by the agent
DOMAIN_TOOLS = []  # Use build_scholar_tools(scholar_id) instead
