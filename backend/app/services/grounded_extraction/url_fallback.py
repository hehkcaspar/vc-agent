"""3-tier URL fallback for grounded-search items.

The refinement pipeline calls ``apply_url_fallback(item)`` once per
KEEP-ed item to guarantee the stored URL is clickable and stable:

    Tier 1 — GROUNDING
        Vertex redirect (``vertexaisearch.cloud.google.com/…``) →
        follow once to canonical target.

    Tier 2 — LLM-VALIDATED
        Model-emitted URL → GET + content-match (page title fuzzy-
        matches the item's claimed title). Pure HTTP-status checks
        miss real-domain-with-fake-path 404s served as 200, and
        homepage-fallback redirects on real domains.

    Tier 3 — GOOGLE SEARCH
        ``google.com/search?q=<title/name/claim>`` — guaranteed
        clickable fallback when everything else fails.

Pure-ish: one HTTP client per call, no global state. Mutates the item
in place. Sets:
- ``_url_source`` ∈ {grounding, llm_validated, google_search, no_anchor}
- ``_url_status`` ∈ {verified, title_mismatch, status_4xx, blocked,
  timeout, no_title_tag, fallback_search} for downstream UI badging.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.academic.llm_client import (
    URL_FIELDS,
    active_url_field,
    google_search_url,
)
from app.services.url_validation import validate_url_content

logger = logging.getLogger(__name__)

VERTEX_REDIRECT_HOST = "vertexaisearch.cloud.google.com"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


async def apply_url_fallback(
    item: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Upgrade one item's URL through the 3-tier fallback.

    - Resolve Vertex redirects (T1 grounding).
    - Content-validate the model's URL (T2).
    - Fall back to Google search on the item's title (T3).

    Mutates ``item`` in place. Sets ``_url_source`` to
    ``"grounding" | "llm_validated" | "google_search" | "no_anchor"``.

    Pass a shared ``httpx.AsyncClient`` for concurrency; a temporary
    client is created if none is provided.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0),
            follow_redirects=True,
            headers={"User-Agent": _UA},
        )
    try:
        await _apply(item, client)
    finally:
        if own_client:
            await client.aclose()


async def _apply(item: dict[str, Any], client: httpx.AsyncClient) -> None:
    field = active_url_field(item)

    # Pull the title/claim so we can content-match.
    expected_title = (
        item.get("title")
        or item.get("name")
        or item.get("claim")
        or ""
    )

    # Build the candidate URL list in preference order. We try each, taking
    # the first one whose page title fuzzy-matches the item title.
    #
    #   1. LLM-emitted URL (``_llm_url``) — typically the most specific
    #      article URL. Often correct when the LLM cited a real source.
    #   2. Grounding chunk URLs (``_grounding_chunk_urls``) — what Google
    #      cited as evidence. Often coarser (homepage, listing page).
    #   3. The current ``url`` field — covers the no-grounding case where
    #      the LLM URL is the only thing we have.
    #
    # Vertex redirects in the chunk-URL list get resolved before content
    # match so we compare against the real article page, not the redirect
    # response. If every candidate fails, _set_search_fallback rewrites
    # the URL to a Google search for the title.

    candidates: list[tuple[str, str]] = []  # (url, origin)
    seen_urls: set[str] = set()

    def _add(u: str | None, origin: str) -> None:
        if not u:
            return
        u = u.strip()
        if not u.startswith(("http://", "https://")):
            return
        if u in seen_urls:
            return
        seen_urls.add(u)
        candidates.append((u, origin))

    _add(item.get("_llm_url"), "llm_validated")
    chunk_urls = item.get("_grounding_chunk_urls") or item.get("_all_grounding_urls") or []
    if isinstance(chunk_urls, list):
        for u in chunk_urls:
            _add(u, "grounding")
    # Field URL — only if not already covered by _llm_url / chunks.
    _add(item.get(field), "llm_validated")

    last_label = "no_anchor"
    last_resolved: str | None = None
    for cand, origin in candidates:
        cand_url = cand
        # If candidate is a Vertex redirect, resolve it first so we
        # content-check against the real article page.
        if VERTEX_REDIRECT_HOST in cand_url:
            resolved = await _resolve_vertex(client, cand_url)
            if not resolved or VERTEX_REDIRECT_HOST in resolved:
                last_label = "timeout"
                continue
            cand_url = resolved

        res = await validate_url_content(
            cand_url,
            expected_title=expected_title or None,
            client=client,
            timeout=6.0,
        )
        last_label = res.label
        last_resolved = cand_url
        if res.ok:
            item[field] = res.final_url or cand_url
            item["_url_source"] = origin
            item["_url_status"] = "verified"
            return

    # No candidate validated. Surface diagnostic + Google search fallback.
    if last_resolved:
        item["_url_attempted"] = last_resolved
    item["_url_status_diagnostic"] = last_label
    _set_search_fallback(item, field, status="fallback_search")


async def _resolve_vertex(client: httpx.AsyncClient, url: str) -> str:
    try:
        resp = await client.head(url)
        final = str(resp.url)
        if VERTEX_REDIRECT_HOST in final:
            resp = await client.get(url, headers={"Range": "bytes=0-0"})
            final = str(resp.url)
        return final
    except Exception as exc:  # noqa: BLE001
        logger.debug("url_fallback: vertex resolve failed %s (%s)",
                     url[:80], exc)
        return ""


def _set_search_fallback(
    item: dict[str, Any], field: str, *, status: str = "fallback_search",
) -> None:
    gs = google_search_url(item)
    if gs:
        item[field] = gs
        item["_url_source"] = "google_search"
        item["_url_status"] = status
    else:
        item[field] = ""
        item["_url_source"] = "no_anchor"
        item["_url_status"] = "no_anchor"


__all__ = ["apply_url_fallback", "URL_FIELDS", "VERTEX_REDIRECT_HOST"]
