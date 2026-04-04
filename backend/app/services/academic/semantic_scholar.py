"""
Semantic Scholar API client.

Authenticated tier (with API key): 1 request per second across all endpoints.
Free tier (no key): 100 requests / 5 min.
Docs: https://api.semanticscholar.org/api-docs/
"""

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = 30.0


class SemanticScholarService:
    # Class-level rate limiter — shared across all instances
    _last_call: float = 0.0
    _lock: asyncio.Lock | None = None

    def __init__(self, api_key: str = ""):
        self.headers: dict[str, str] = {}
        self._min_interval = 1.1  # 1 req/sec with small margin
        if api_key:
            self.headers["x-api-key"] = api_key

    async def _rate_limit(self) -> None:
        """Enforce 1 req/sec rate limit for authenticated API."""
        if SemanticScholarService._lock is None:
            SemanticScholarService._lock = asyncio.Lock()
        async with SemanticScholarService._lock:
            now = time.monotonic()
            elapsed = now - SemanticScholarService._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            SemanticScholarService._last_call = time.monotonic()

    async def _get_with_retry(
        self, client: httpx.AsyncClient, url: str, params: dict, max_retries: int = 3,
    ) -> httpx.Response:
        """GET with rate limiting + 429 retry backoff."""
        for attempt in range(max_retries):
            await self._rate_limit()
            r = await client.get(url, params=params)
            if r.status_code != 429:
                r.raise_for_status()
                return r
            wait = 2.0 * (attempt + 1)
            logger.warning("SS 429 on %s, retry %d in %.1fs", url, attempt + 1, wait)
            await asyncio.sleep(wait)
        r.raise_for_status()  # raise the last 429
        return r  # unreachable

    # ── Author search ──────────────────────────────────────────

    async def search_author(
        self,
        name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for authors by name.  Returns a list of candidate dicts."""
        fields = "name,affiliations,paperCount,citationCount,hIndex"
        params = {"query": name, "fields": fields, "limit": limit}

        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=self.headers) as c:
            try:
                r = await self._get_with_retry(c, f"{_BASE}/author/search", params)
                return [
                    {
                        "id": a.get("authorId"),
                        "name": a.get("name"),
                        "affiliations": a.get("affiliations", []),
                        "paper_count": a.get("paperCount", 0),
                        "citation_count": a.get("citationCount", 0),
                        "h_index": a.get("hIndex"),
                    }
                    for a in r.json().get("data", [])
                ]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("Semantic Scholar rate limit hit")
                else:
                    logger.error("Semantic Scholar author search error: %s", e)
                return []
            except Exception as e:
                logger.error("Semantic Scholar author search failed: %s", e)
                return []

    # ── Author details ─────────────────────────────────────────

    async def get_author_details(self, author_id: str) -> Optional[dict[str, Any]]:
        fields = "name,affiliations,homepage,paperCount,citationCount,hIndex"
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=self.headers) as c:
            try:
                r = await self._get_with_retry(c, f"{_BASE}/author/{author_id}", {"fields": fields})
                d = r.json()
                return {
                    "id": d.get("authorId"),
                    "name": d.get("name"),
                    "affiliations": d.get("affiliations", []),
                    "homepage": d.get("homepage"),
                    "paper_count": d.get("paperCount", 0),
                    "citation_count": d.get("citationCount", 0),
                    "h_index": d.get("hIndex"),
                }
            except Exception as e:
                logger.error("Semantic Scholar get author failed: %s", e)
                return None

    # ── Author papers ──────────────────────────────────────────

    async def get_author_papers(
        self,
        author_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        fields = (
            "title,authors,year,citationCount,influentialCitationCount,"
            "journal,venue,fieldsOfStudy,s2FieldsOfStudy,"
            "publicationTypes,publicationDate,externalIds"
        )
        papers: list[dict[str, Any]] = []
        offset = 0
        batch_size = min(100, limit)

        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=self.headers) as c:
            while len(papers) < limit:
                params = {
                    "fields": fields,
                    "limit": batch_size,
                    "offset": offset,
                }
                try:
                    r = await self._get_with_retry(c, f"{_BASE}/author/{author_id}/papers", params)
                    batch = r.json().get("data", [])
                    if not batch:
                        break

                    for p in batch:
                        # Preserve authorId alongside name for author-position analysis
                        authors_raw = p.get("authors", [])
                        authors = [
                            {"name": a.get("name"), "authorId": a.get("authorId")}
                            for a in authors_raw
                            if a.get("name")
                        ]

                        papers.append({
                            "id": p.get("paperId"),
                            "title": p.get("title"),
                            "authors": authors,
                            "year": p.get("year"),
                            "citations": p.get("citationCount", 0),
                            "influential_citations": p.get("influentialCitationCount", 0),
                            "journal": (
                                p.get("journal", {}).get("name")
                                if p.get("journal")
                                else None
                            ),
                            "venue": p.get("venue"),
                            "fields": p.get("fieldsOfStudy", []),
                            "s2_fields": p.get("s2FieldsOfStudy", []),
                            "publication_types": p.get("publicationTypes", []),
                            "publication_date": p.get("publicationDate"),
                            "external_ids": p.get("externalIds", {}),
                        })

                    offset += len(batch)
                    if len(batch) < batch_size:
                        break
                except Exception as e:
                    logger.error("Semantic Scholar get papers failed: %s", e)
                    break

        return papers[:limit]

    # ── Paper search ───────────────────────────────────────────

    async def search_papers(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for papers by title/keywords.  Returns a list of paper dicts."""
        fields = "title,authors,year,citationCount"
        params = {"query": query, "fields": fields, "limit": min(limit, 100)}

        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=self.headers) as c:
            try:
                r = await self._get_with_retry(c, f"{_BASE}/paper/search", params)
                results = []
                for p in r.json().get("data", []):
                    authors_raw = p.get("authors", [])
                    results.append({
                        "id": p.get("paperId"),
                        "title": p.get("title"),
                        "year": p.get("year"),
                        "citations": p.get("citationCount", 0),
                        "authors": [
                            {"name": a.get("name"), "authorId": a.get("authorId")}
                            for a in authors_raw if a.get("name")
                        ],
                    })
                return results
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("Semantic Scholar rate limit hit (paper search)")
                else:
                    logger.error("Semantic Scholar paper search error: %s", e)
                return []
            except Exception as e:
                logger.error("Semantic Scholar paper search failed: %s", e)
                return []
