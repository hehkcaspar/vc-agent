"""3-tier URL fallback for grounded-search items.

The refinement pipeline calls ``apply_url_fallback(item)`` once per
KEEP-ed item to guarantee the stored URL is clickable and stable:

    Tier 1 — GROUNDING
        Vertex redirect (``vertexaisearch.cloud.google.com/…``) →
        follow once to canonical target.

    Tier 2 — LLM-VALIDATED
        Model-emitted URL → HEAD check; keep if ``status < 400`` or
        403 (bot-walled but the page exists).

    Tier 3 — GOOGLE SEARCH
        ``google.com/search?q=<title/name/claim>`` — guaranteed
        clickable fallback when everything else fails.

Pure-ish: one HTTP client per call, no global state. Mutates the item
in place and sets ``_url_source`` for auditability.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .llm_client import URL_FIELDS, active_url_field, google_search_url

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
    - HEAD-validate the model's URL (T2).
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
    url = (item.get(field) or "").strip()
    src = item.get("_url_source")

    # Case 1: grounding URL (possibly a vertex redirect) — resolve it.
    if src == "grounding" and VERTEX_REDIRECT_HOST in url:
        resolved = await _resolve_vertex(client, url)
        if resolved and VERTEX_REDIRECT_HOST not in resolved:
            item[field] = resolved
            item["_url_resolved"] = True
            return
        # Redirect couldn't be resolved — drop to T3.
        _set_search_fallback(item, field)
        return

    # Case 2: grounding URL already direct — leave alone.
    if src == "grounding":
        return

    # Case 3: no grounding. Try LLM URL via HEAD, else search fallback.
    if url.startswith(("http://", "https://")):
        ok, final = await _head_validate(client, url)
        if ok:
            item[field] = final or url
            item["_url_source"] = "llm_validated"
            return

    _set_search_fallback(item, field)


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


async def _head_validate(
    client: httpx.AsyncClient, url: str,
) -> tuple[bool, str]:
    """Return (is_real, final_url). Real = status < 400 or 403."""
    status: int | None = None
    final = url
    try:
        resp = await client.head(url)
        status = resp.status_code
        final = str(resp.url)
        if status in (403, 405, 501):
            # Sites that block HEAD often serve GET.
            resp = await client.get(url, headers={"Range": "bytes=0-0"})
            status = resp.status_code
            final = str(resp.url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("url_fallback: HEAD validate failed %s (%s)",
                     url[:80], exc)
        return False, url
    ok = status is not None and (status < 400 or status == 403)
    return ok, final


def _set_search_fallback(item: dict[str, Any], field: str) -> None:
    gs = google_search_url(item)
    if gs:
        item[field] = gs
        item["_url_source"] = "google_search"
    else:
        item[field] = ""
        item["_url_source"] = "no_anchor"


__all__ = ["apply_url_fallback", "URL_FIELDS", "VERTEX_REDIRECT_HOST"]
