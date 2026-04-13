"""
Channel pollers for Academic Tracking v2 monitoring.

Deterministic pollers — no LLM for routine polling.  Fetch via SerpAPI
(Google Scholar, News, Patents) or direct API (Semantic Scholar, HTTP).
Only high-significance events trigger an agent investigation.

See design doc §5.2.
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx

from app.config import settings
from app.services.academic.tool_utils import is_top_venue

logger = logging.getLogger(__name__)

_SERPAPI_BASE = "https://serpapi.com/search.json"
_SERPAPI_TIMEOUT = 30.0


# ── Result type ──────────────────────────────────────────────


@dataclass
class PollResult:
    changed: bool = False
    snapshot: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


# ── Significance rules (deterministic, design doc §5.3) ──────


def assess_significance(event_type: str, payload: dict[str, Any]) -> str:
    """Deterministic significance assessment — no LLM."""
    if event_type == "patent_filed":
        return "high"
    if event_type == "career_change":
        return "high"
    if event_type == "new_paper":
        venue = payload.get("venue", "")
        pub_type = payload.get("publication_type", "")
        if is_top_venue(venue, pub_type):
            return "high"
        return "medium"
    if event_type == "metric_snapshot":
        h_old = payload.get("h_index", {}).get("old", 0)
        h_new = payload.get("h_index", {}).get("new", 0)
        if isinstance(h_old, (int, float)) and isinstance(h_new, (int, float)):
            if abs(h_new - h_old) > 3:
                return "high"
        return "low"
    if event_type == "news_mention":
        return "medium"
    if event_type == "website_updated":
        return "low"
    return "medium"


# ── SerpAPI helper ───────────────────────────────────────────


async def _serpapi_request(params: dict[str, Any]) -> dict[str, Any]:
    """Make a SerpAPI request with the configured API key."""
    key = settings.SERPAPI_KEY
    if not key:
        raise ValueError("SERPAPI_KEY is not set")
    params["api_key"] = key
    async with httpx.AsyncClient(timeout=_SERPAPI_TIMEOUT) as c:
        r = await c.get(_SERPAPI_BASE, params=params)
        r.raise_for_status()
        return r.json()


# ── Base poller ──────────────────────────────────────────────


class ChannelPoller(ABC):
    @abstractmethod
    async def poll(
        self,
        channel_type: str,
        url: str,
        last_snapshot: dict[str, Any],
        scholar_id: str,
    ) -> PollResult:
        ...


# ── Google Scholar Poller (SerpAPI) ──────────────────────────


class GoogleScholarPoller(ChannelPoller):
    """Poll a Google Scholar profile via SerpAPI google_scholar_author.

    Deterministic — no LLM.  Extracts h-index, i10-index, citations.
    Diff: h-index/citation changes → metric_snapshot events.
    """

    async def poll(self, channel_type, url, last_snapshot, scholar_id) -> PollResult:
        # Extract GS author ID from URL
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        gs_id = (qs.get("user") or [None])[0]
        if not gs_id:
            return PollResult(error=f"No user= param in GS URL: {url}")

        try:
            data = await _serpapi_request({
                "engine": "google_scholar_author",
                "author_id": gs_id,
            })
        except Exception as e:
            return PollResult(error=f"SerpAPI GS fetch failed: {e}")

        # Extract metrics from cited_by table
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

        if h_index is None:
            return PollResult(error="Could not extract h-index from SerpAPI response")

        # Diff against last snapshot
        events: list[dict] = []
        changed = False

        old_h = last_snapshot.get("h_index")
        old_cites = last_snapshot.get("total_citations")

        if old_h is not None and h_index != old_h:
            changed = True
            payload = {"h_index": {"old": old_h, "new": h_index}}
            events.append({
                "type": "metric_snapshot",
                "title": f"h-index {old_h}\u2192{h_index}",
                "significance": assess_significance("metric_snapshot", payload),
                "payload": payload,
            })

        if old_cites is not None and total_citations is not None and old_cites != total_citations:
            changed = True
            if not events:  # Don't duplicate if h-index already logged
                events.append({
                    "type": "metric_snapshot",
                    "title": f"Citations {old_cites:,}\u2192{total_citations:,}",
                    "significance": "low",
                    "payload": {"total_citations": {"old": old_cites, "new": total_citations}},
                })

        new_snapshot = {
            "h_index": h_index,
            "i10_index": i10_index,
            "total_citations": total_citations,
        }

        return PollResult(changed=changed, snapshot=new_snapshot, events=events)


# ── Semantic Scholar Poller (direct API) ─────────────────────


class SemanticScholarPoller(ChannelPoller):
    """Poll a Semantic Scholar author for new papers via SS API.

    Deterministic — no LLM.  Diff paper IDs → new_paper events.
    """

    async def poll(self, channel_type, url, last_snapshot, scholar_id) -> PollResult:
        from .semantic_scholar import SemanticScholarService

        # Extract SS author ID from URL
        parts = url.rstrip("/").split("/")
        ss_id = None
        for p in reversed(parts):
            if p.isdigit():
                ss_id = p
                break

        if not ss_id:
            return PollResult(error=f"No author ID in SS URL: {url}")

        ss = SemanticScholarService(api_key=settings.SEMANTIC_SCHOLAR_API_KEY)

        try:
            papers = await ss.get_author_papers(ss_id, limit=50)
        except Exception as e:
            return PollResult(error=f"SS API failed: {e}")

        # Diff: compare paper IDs
        known_ids = set(last_snapshot.get("known_paper_ids", []))
        new_papers = [p for p in papers if p.get("id") and p["id"] not in known_ids]

        events: list[dict] = []
        for p in new_papers:
            venue = p.get("venue", "")
            payload = {
                "title": p.get("title"),
                "year": p.get("year"),
                "venue": venue,
                "citations": p.get("citations", 0),
                "ss_paper_id": p.get("id"),
            }
            events.append({
                "type": "new_paper",
                "title": f"New paper: {p.get('title', '?')[:80]}",
                "significance": assess_significance("new_paper", payload),
                "payload": payload,
            })

        all_ids = known_ids | {p["id"] for p in papers if p.get("id")}
        new_snapshot = {
            "paper_count": len(papers),
            "known_paper_ids": list(all_ids),
        }

        return PollResult(
            changed=len(new_papers) > 0,
            snapshot=new_snapshot,
            events=events,
        )


# ── News Alert Poller (SerpAPI) ──────────────────────────────


class NewsAlertPoller(ChannelPoller):
    """Poll for news mentions via SerpAPI google_news.

    Deterministic — no LLM.  Diff by URL → news_mention / career_change events.
    Career keywords trigger high significance.
    """

    _CAREER_KEYWORDS = frozenset([
        "appointed", "joins", "founded", "startup", "ceo", "cto", "advisor",
        "award", "prize", "elected", "hired", "launched", "acquired", "funding",
    ])

    async def poll(self, channel_type, url, last_snapshot, scholar_id) -> PollResult:
        query = url  # For news channels, 'url' stores the search query
        if not query:
            return PollResult(error="No search query for news channel")

        try:
            data = await _serpapi_request({
                "engine": "google_news",
                "q": query,
                "gl": "us",
                "hl": "en",
            })
        except Exception as e:
            return PollResult(error=f"SerpAPI news fetch failed: {e}")

        news_results = data.get("news_results", [])
        seen_urls = set(last_snapshot.get("seen_urls", []))

        events: list[dict] = []
        new_urls: list[str] = []
        for item in news_results:
            item_url = item.get("link") or item.get("url") or ""
            if not item_url or item_url in seen_urls:
                continue

            new_urls.append(item_url)
            title = item.get("title", "News mention")
            source = item.get("source", {}).get("name", "")
            date = item.get("date", "")

            # Deterministic career keyword detection
            text_lower = title.lower()
            is_career = any(kw in text_lower for kw in self._CAREER_KEYWORDS)

            events.append({
                "type": "career_change" if is_career else "news_mention",
                "title": title[:120],
                "significance": "high" if is_career else "medium",
                "payload": {"url": item_url, "source": source, "date": date},
            })

        all_urls = seen_urls | set(new_urls)
        return PollResult(
            changed=len(events) > 0,
            snapshot={"seen_urls": list(all_urls)},
            events=events,
        )


# ── Personal Website Poller (HTTP + hash) ────────────────────


class WebsitePoller(ChannelPoller):
    """Poll a personal website for content changes via HTTP fetch + SHA256 hash.

    Fully deterministic — no LLM, no external API.
    """

    async def poll(self, channel_type, url, last_snapshot, scholar_id) -> PollResult:
        if not url:
            return PollResult(error="No URL for website channel")

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                content_hash = hashlib.sha256(r.content).hexdigest()
        except Exception as e:
            return PollResult(error=f"Website fetch failed: {e}")

        old_hash = last_snapshot.get("content_hash")
        if old_hash and old_hash == content_hash:
            return PollResult(changed=False, snapshot={"content_hash": content_hash})

        events = []
        if old_hash:  # Only emit event if we had a previous hash (not first poll)
            events.append({
                "type": "website_updated",
                "title": f"Website updated: {url[:60]}",
                "significance": "low",
                "payload": {"url": url, "old_hash": old_hash[:12], "new_hash": content_hash[:12]},
            })

        return PollResult(changed=True, snapshot={"content_hash": content_hash}, events=events)


# ── Patent Watch Poller (SerpAPI) ────────────────────────────


class PatentWatchPoller(ChannelPoller):
    """Poll for new patents via SerpAPI google_patents.

    Deterministic — no LLM.  Diff by patent ID → patent_filed events (always high).
    """

    async def poll(self, channel_type, url, last_snapshot, scholar_id) -> PollResult:
        query = url  # 'url' stores the search query for patent channels
        if not query:
            return PollResult(error="No search query for patent channel")

        try:
            data = await _serpapi_request({
                "engine": "google_patents",
                "q": query,
            })
        except Exception as e:
            return PollResult(error=f"SerpAPI patent fetch failed: {e}")

        results = data.get("organic_results", [])
        known_ids = set(last_snapshot.get("known_patent_ids", []))

        events: list[dict] = []
        new_ids: list[str] = []
        for p in results:
            pid = p.get("patent_id", "")
            if not pid or pid in known_ids:
                continue

            new_ids.append(pid)
            events.append({
                "type": "patent_filed",
                "title": f"Patent: {p.get('title', '?')[:80]}",
                "significance": "high",
                "payload": {
                    "title": p.get("title"),
                    "patent_id": pid,
                    "filing_date": p.get("filing_date"),
                    "publication_date": p.get("publication_date"),
                    "url": p.get("pdf", p.get("link", "")),
                },
            })

        all_ids = known_ids | set(new_ids)
        return PollResult(
            changed=len(events) > 0,
            snapshot={"known_patent_ids": list(all_ids), "total_patents": len(results)},
            events=events,
        )


# ── Poller registry ──────────────────────────────────────────


POLLERS: dict[str, ChannelPoller] = {
    "google_scholar_profile": GoogleScholarPoller(),
    "semantic_scholar_profile": SemanticScholarPoller(),
    "news_alert": NewsAlertPoller(),
    "personal_website": WebsitePoller(),
    "patent_watch": PatentWatchPoller(),
}


def get_poller(channel_type: str) -> Optional[ChannelPoller]:
    return POLLERS.get(channel_type)
