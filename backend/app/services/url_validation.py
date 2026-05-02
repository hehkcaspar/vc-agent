"""Shared URL validation utility.

Two callers today:

1. **News pipeline** (academic + portfolio ``news_web``) — validates that an
   LLM-emitted news URL actually resolves AND the page content matches the
   claimed title. Catches the (a) 404-served-as-200 and (b) wrong-article-
   on-real-domain failure modes that pure HTTP-status checks miss.

2. **Founder LinkedIn URLs** (``extract_info`` post-merge) — validates the
   URL is a canonical ``linkedin.com/in/<slug>`` and that the host responds
   without a 4xx (LinkedIn's bot wall returns 403/999, which we treat as
   "format ok, server-side content unverifiable" rather than broken).

Single helper, no new third-party deps. The fuzzy-title match is a small
inline token-set-ratio implementation — enough signal to detect a
homepage-fallback or unrelated article without pulling in rapidfuzz.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


CANONICAL_LINKEDIN_RE = re.compile(
    r"^https?://(www\.)?linkedin\.com/in/[A-Za-z0-9._%~\-]+/?(\?.*)?$"
)


@dataclass
class URLValidationResult:
    """Result of a content-aware URL check.

    ``label`` is the canonical reason string callers persist (e.g. as
    ``_url_status`` on a news item). All other fields are diagnostic.
    """
    ok: bool
    label: str  # verified | title_mismatch | status_4xx | timeout | no_title_tag | blocked | invalid_url
    final_url: Optional[str]
    page_title: Optional[str]
    similarity: float  # 0.0..1.0; 0.0 when no comparison was made


_TITLE_TAG_RE = re.compile(
    r"<title[^>]*>(?P<t>.*?)</title>", re.IGNORECASE | re.DOTALL,
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](?P<t>[^"\']+)["\']',
    re.IGNORECASE,
)
_TWITTER_TITLE_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\'](?P<t>[^"\']+)["\']',
    re.IGNORECASE,
)


def _tokens(s: str) -> set[str]:
    """Lowercase alphanumeric token set, words ≥ 3 chars only.

    Short words ("a", "the", "of") and punctuation are noise — keeping
    them inflates the union, dragging the similarity metric down to
    nothing on perfectly matched titles.
    """
    if not s:
        return set()
    return {t for t in re.findall(r"[A-Za-z0-9]+", s.lower()) if len(t) >= 3}


def title_token_similarity(a: str, b: str) -> float:
    """Token-set ratio: |A ∩ B| / |A ∪ B|. Range 0.0..1.0.

    This is a tiny inline replacement for ``rapidfuzz.fuzz.token_set_ratio``
    — the project doesn't ship rapidfuzz today and the news + LinkedIn
    use-cases don't need anything subtler than "do most distinguishing
    tokens overlap?".
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def _extract_page_title(html: str, max_chars: int = 8192) -> Optional[str]:
    """Pull the most authoritative title from a (truncated) HTML response.

    Preference order: ``og:title`` → ``twitter:title`` → ``<title>``. We
    only inspect the first ``max_chars`` of the body — every site puts
    these in <head>, so reading further is wasted work.
    """
    head = html[:max_chars]
    for rx in (_OG_TITLE_RE, _TWITTER_TITLE_RE, _TITLE_TAG_RE):
        m = rx.search(head)
        if m:
            t = (m.group("t") or "").strip()
            # Decode the most common HTML entities by hand. We don't pull
            # `html.unescape` because it works fine but a few Unicode
            # whitespace cases still need normalising; this is enough.
            for k, v in (
                ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
            ):
                t = t.replace(k, v)
            return t.strip()
    return None


# Title-similarity bands. Calibrated against the known failure mode of a
# generic site title (e.g. "Penn State - News & Stories") served on a 404
# path: shares almost nothing with a specific article headline. A real
# match shares the entity name + ≥2 distinguishing tokens.
_VERIFY_THRESHOLD = 0.32


async def validate_url_content(
    url: str,
    *,
    expected_title: Optional[str] = None,
    expected_keywords: Optional[list[str]] = None,
    client: httpx.AsyncClient,
    timeout: float = 6.0,
) -> URLValidationResult:
    """GET-validate a URL and (optionally) confirm content matches the
    expected title or keyword set.

    Resolution semantics:
    - 4xx response (incl. 403) → ``status_4xx``. Note 403 is NOT treated as
      verified — the page might exist but we cannot confirm the article
      content matches our claim, which is the bug we're fixing.
    - 5xx / network error → ``timeout``.
    - 2xx with parseable HTML title:
        - ``expected_title`` provided AND token-similarity ≥ threshold →
          ``verified``.
        - ``expected_keywords`` provided and ≥ 1 distinguishing keyword
          token appears in the page title → ``verified``.
        - Otherwise → ``title_mismatch`` with the page title for debug.
    - 2xx with no title tag → ``no_title_tag`` (caller decides whether to
      treat as verified — for LinkedIn-style auth walls the absence of an
      article title is the expected signal).

    The ``ok`` flag is True only for ``verified``. Callers that want to
    accept "unverifiable but probably ok" cases (LinkedIn) inspect
    ``label`` and decide.
    """
    if not url or not url.startswith(("http://", "https://")):
        return URLValidationResult(
            ok=False, label="invalid_url",
            final_url=None, page_title=None, similarity=0.0,
        )
    try:
        resp = await client.get(
            url,
            timeout=timeout,
            follow_redirects=True,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.debug(
            "url_validation: GET timeout/network %s (%s)", url[:80], exc,
        )
        return URLValidationResult(
            ok=False, label="timeout",
            final_url=None, page_title=None, similarity=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "url_validation: GET failed %s (%s)", url[:80], exc,
        )
        return URLValidationResult(
            ok=False, label="timeout",
            final_url=None, page_title=None, similarity=0.0,
        )

    final_url = str(resp.url)
    status = resp.status_code

    if status >= 400:
        return URLValidationResult(
            ok=False,
            label="blocked" if status in (401, 403, 999) else "status_4xx",
            final_url=final_url, page_title=None, similarity=0.0,
        )

    # 2xx — try to extract a title. Use the first 16 KB of the body to
    # stay cheap on huge HTML responses.
    body_text = ""
    try:
        body_bytes = resp.content[:16 * 1024]
        body_text = body_bytes.decode(resp.encoding or "utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        body_text = ""
    page_title = _extract_page_title(body_text)
    if not page_title:
        return URLValidationResult(
            ok=False, label="no_title_tag",
            final_url=final_url, page_title=None, similarity=0.0,
        )

    sim = 0.0
    if expected_title:
        sim = title_token_similarity(expected_title, page_title)
        if sim >= _VERIFY_THRESHOLD:
            return URLValidationResult(
                ok=True, label="verified",
                final_url=final_url, page_title=page_title, similarity=sim,
            )
    if expected_keywords:
        page_tokens = _tokens(page_title)
        if any(kw.lower() in page_tokens for kw in expected_keywords if kw):
            return URLValidationResult(
                ok=True, label="verified",
                final_url=final_url, page_title=page_title, similarity=sim,
            )
    if expected_title is None and expected_keywords is None:
        # No content-match constraint — any 2xx with a title counts.
        return URLValidationResult(
            ok=True, label="verified",
            final_url=final_url, page_title=page_title, similarity=0.0,
        )
    return URLValidationResult(
        ok=False, label="title_mismatch",
        final_url=final_url, page_title=page_title, similarity=sim,
    )


def is_canonical_linkedin_url(url: str) -> bool:
    """True if the URL matches the canonical
    ``https://(www.)?linkedin.com/in/<slug>`` pattern.

    Off-pattern URLs (linkedin.com/pub/..., shortened linkedin.com/in/,
    or non-linkedin domains) → False. ``extract_info`` post-merge nulls
    fields that fail this check rather than render a broken link.
    """
    if not isinstance(url, str):
        return False
    return bool(CANONICAL_LINKEDIN_RE.match(url.strip()))


__all__ = [
    "URLValidationResult",
    "validate_url_content",
    "title_token_similarity",
    "is_canonical_linkedin_url",
]
