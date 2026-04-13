"""
Pure utility functions for Academic Tracking v2.

No DB, no LLM, no I/O. Functions here are imported by
identity_resolver, channel_pollers, and attributed_metrics.
"""

import logging
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


# ── Name matching ────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """NFKD Unicode normalization — strips accents, cedillas, etc."""
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _name_tokens(name: str) -> list[str]:
    """Extract lowercase name tokens, ignoring short particles (len <= 1)."""
    normalized = _normalize_name(name.lower())
    return [t for t in normalized.split() if len(t) > 1]


def names_match(name_a: str, name_b: str) -> bool:
    """Check if two scholar names refer to the same person.

    Handles:
    - Exact token overlap ("Michael Bronstein" vs "Michael M. Bronstein")
    - Initials ("M. Bronstein" vs "Michael Bronstein")
    - Reversed order ("Fei-Fei Li" vs "Li Fei-Fei")
    - Abbreviated names ("B. Scholkopf" vs "Bernhard Scholkopf")
    - Prefix match ("Kate" vs "Katherine" via 3-char prefix)

    Threshold: 1 strong match (len>2) + min(2, shorter_list) total, OR ≥2 matches.
    Known limitation: "Bronstein" vs "Brown" false-positive on "bro" prefix —
    acceptable because metric verification is the real gate.
    """
    tokens_a = _name_tokens(name_a)
    tokens_b = _name_tokens(name_b)

    if not tokens_a or not tokens_b:
        return False

    # Strip dots/periods from tokens for initial matching
    clean_a = [t.rstrip(".") for t in tokens_a]
    clean_b = [t.rstrip(".") for t in tokens_b]

    # Count matches: exact, initial-to-full, or prefix
    matches = 0
    strong_matches = 0
    used_b: set[int] = set()
    for ta in clean_a:
        for j, tb in enumerate(clean_b):
            if j in used_b:
                continue
            if ta == tb:
                matches += 1
                if len(ta) > 2:
                    strong_matches += 1
                used_b.add(j)
                break
            # Initial match: "m" matches "michael", "b" matches "bernhard"
            if len(ta) <= 2 and tb.startswith(ta[0]):
                matches += 1
                used_b.add(j)
                break
            if len(tb) <= 2 and ta.startswith(tb[0]):
                matches += 1
                used_b.add(j)
                break
            # Prefix match: "kate"/"katie" matches "katherine" (common nicknames)
            if len(ta) >= 3 and len(tb) >= 3:
                shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
                if longer.startswith(shorter[:3]) and len(shorter) >= 4:
                    matches += 1
                    used_b.add(j)
                    break

    # Accept if: surname matches strongly AND at least some first-name signal
    if strong_matches >= 1 and matches >= min(2, min(len(tokens_a), len(tokens_b))):
        return True
    # Or: at least 2 matches total (for short names or all-initial cases)
    if matches >= 2:
        return True
    return False


# ── Metric verification ─────────────────────────────────────────


def verify_ss_metrics(
    ss_h_index: int,
    ss_citation_count: int,
    expected_h_index: Optional[int] = None,
    expected_citations: Optional[int] = None,
) -> bool:
    """Cheap pre-filter: is this SS candidate plausibly the same person?

    Used to reject obvious mismatches before the LLM verifier runs. A
    stronger semantic check (`identity_verifier.verify_source_candidate`)
    is the final gate; this is only here to avoid wasting LLM calls on
    candidates that fail a trivial numeric sanity check.

    Rules:
    - If no anchor is provided, pass through (no filtering). The LLM
      verifier will make the call.
    - With a strong anchor (`expected_h_index > 10` OR
      `expected_citations > 1000`), a candidate reporting **zero**
      on the corresponding axis is a mismatch. Zero metrics against a
      renowned scholar means either a wrong author or a stale SS profile
      — either way we don't trust it without the LLM reconfirming.
    - With nonzero candidate metrics, apply the ratio gates: h-index
      ratio ≥ 0.3, citation ratio ≥ 0.1.
    """
    if expected_h_index and expected_h_index > 10:
        if ss_h_index <= 0:
            logger.warning(
                "Metric mismatch: expected h~%d but candidate reports zero h-index",
                expected_h_index,
            )
            return False
        ratio = min(ss_h_index, expected_h_index) / max(ss_h_index, expected_h_index)
        if ratio < 0.3:
            logger.warning(
                "Metric mismatch: h-index expected ~%d, got %d (ratio=%.2f)",
                expected_h_index, ss_h_index, ratio,
            )
            return False

    if expected_citations and expected_citations > 1000:
        if ss_citation_count <= 0:
            logger.warning(
                "Metric mismatch: expected ~%d citations but candidate reports zero",
                expected_citations,
            )
            return False
        ratio = min(ss_citation_count, expected_citations) / max(ss_citation_count, expected_citations)
        if ratio < 0.1:
            logger.warning(
                "Metric mismatch: citations expected ~%d, got %d (ratio=%.2f)",
                expected_citations, ss_citation_count, ratio,
            )
            return False

    return True


# ── Known identity source types ─────────────────────────────────
#
# Canonical set of source_id values the resolver understands. Mirrors
# the shapes parsed by `classify_urls` below. Used by the API router
# to validate user upserts and by the frontend as an enum.
KNOWN_IDENTITY_SOURCES: frozenset[str] = frozenset(
    {
        "google_scholar",
        "semantic_scholar",
        "orcid",
        "dblp",
        "arxiv",
        "openreview",
        "linkedin",
        "github",
        "twitter",
        "homepage",
    }
)

# Sources that get LLM-verified during identity resolution. Other
# sources are committed with heuristic confidence and flagged in the
# UI for user review. Keep this in sync with
# `identity_verifier.HIGH_SIGNAL_SOURCES`.
HIGH_SIGNAL_IDENTITY_SOURCES: frozenset[str] = frozenset(
    {"google_scholar", "semantic_scholar", "orcid", "homepage"}
)


# ── URL classification ───────────────────────────────────────────


_STATIC_ASSET_SUFFIXES = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".webm",
    ".mov",
    ".pdf",
    ".zip",
)


def classify_urls(urls: list[str]) -> dict[str, Any]:
    """Deterministic URL→ID extraction. Ground truth — overrides LLM output.

    Extracts credible-source identifiers from any URL shape we know.
    Returns a dict where the first URL seen for a given kind wins
    (subsequent ones of the same kind are ignored).

    Supported sources:
    - Google Scholar (`scholar.google.*/citations?user=...`)
    - Semantic Scholar author page (trailing numeric id)
    - ORCID (`orcid.org/XXXX-XXXX-XXXX-XXXX`)
    - DBLP (`dblp.org/pid/...` or `/pers/...`)
    - arXiv author page (`arxiv.org/a/surname_i_Y`)
    - OpenReview (`openreview.net/profile?id=~Name`)
    - LinkedIn (`linkedin.com/in/...`)
    - GitHub user (`github.com/username`)
    - Twitter / X (`twitter.com/handle`, `x.com/handle`)
    - Homepage (any other http(s) URL — kept as `homepage_url`
      fallback if no stronger category matches)
    """
    import re

    known: dict[str, Any] = {}
    homepage_fallback: str | None = None

    for url in urls:
        if not url:
            continue
        lower = url.lower()
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()

        matched = False

        # Google Scholar profile (handles scholar.google.fr, .co.uk, etc.)
        if "scholar.google" in lower and "/citations" in lower:
            qs = parse_qs(parsed.query)
            user_ids = qs.get("user", [])
            if user_ids and "google_scholar_id" not in known:
                known["google_scholar_id"] = user_ids[0]
                known["google_scholar_url"] = url
            matched = True

        # Semantic Scholar author page
        elif "semanticscholar.org/author/" in lower:
            parts = parsed.path.rstrip("/").split("/")
            # Accept /author/Name/12345 or /author/12345
            for part in reversed(parts):
                if part.isdigit():
                    if "semantic_scholar_id" not in known:
                        known["semantic_scholar_id"] = part
                        known["semantic_scholar_url"] = url
                    matched = True
                    break

        # ORCID — canonical 19-char form XXXX-XXXX-XXXX-XXXX (16 digits + 3 dashes)
        elif "orcid.org/" in lower:
            m = re.search(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dxX])\b", url)
            if m and "orcid_id" not in known:
                known["orcid_id"] = m.group(1).upper()
                known["orcid_url"] = f"https://orcid.org/{m.group(1).upper()}"
            matched = True

        # DBLP — /pid/NN/NNNN[-S] or /pers/hd/*
        # NOTE: the trailing `-S` disambiguates between multiple
        # authors sharing the same numeric id (e.g. `80/806-3` is a
        # different person than `80/806`). Preserving the suffix is
        # critical for common names like "Song Han".
        elif "dblp.org/" in lower:
            m = re.search(r"/pid/(\d+/\d+(?:-\d+)?)", parsed.path) or re.search(
                r"/pers/hd/[a-z]/([^/.]+)", parsed.path
            )
            if m and "dblp_id" not in known:
                known["dblp_id"] = m.group(1)
            if "dblp_url" not in known:
                known["dblp_url"] = url
            matched = True

        # arXiv author page (arxiv.org/a/surname_i_Y)
        elif "arxiv.org/a/" in lower:
            slug = parsed.path.rstrip("/").split("/")[-1]
            if slug and "arxiv_author" not in known:
                known["arxiv_author"] = slug
                known["arxiv_url"] = url
            matched = True

        # OpenReview
        elif "openreview.net" in lower and "profile" in lower:
            qs = parse_qs(parsed.query)
            pid = (qs.get("id") or [None])[0]
            if pid and "openreview_id" not in known:
                known["openreview_id"] = pid
                known["openreview_url"] = url
            matched = True

        # LinkedIn
        elif "linkedin.com/in/" in lower:
            handle = parsed.path.rstrip("/").split("/")[-1]
            if handle and "linkedin_handle" not in known:
                known["linkedin_handle"] = handle
                known["linkedin_url"] = url
            matched = True

        # GitHub user (not a repo)
        elif host.endswith("github.com"):
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 1 and "github_user" not in known:
                known["github_user"] = parts[0]
                known["github_url"] = url
            matched = True

        # Twitter / X handle
        elif "twitter.com" in host or host == "x.com" or host.endswith(".x.com"):
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 1 and "twitter_handle" not in known:
                handle = parts[0].lstrip("@")
                if handle and handle not in {"home", "search", "i"}:
                    known["twitter_handle"] = handle
                    known["twitter_url"] = url
            matched = True

        # Homepage fallback (first unmatched http URL, but only if the
        # URL looks like an actual page — not a static asset, not a
        # CDN, and path is reasonable).
        if not matched and parsed.scheme in ("http", "https"):
            path_lower = (parsed.path or "").lower()
            if path_lower.endswith(_STATIC_ASSET_SUFFIXES):
                continue
            if any(x in host for x in ("cdn.", "fonts.", "googleapis.com", "cloudfront", "jsdelivr", "unpkg")):
                continue
            # Skip mailto:, tel:, javascript:, etc. (already filtered
            # by scheme check) and anchor-only fragments.
            if parsed.path in ("", "/") and not parsed.netloc:
                continue
            if homepage_fallback is None:
                homepage_fallback = url

    if homepage_fallback and "homepage_url" not in known:
        known["homepage_url"] = homepage_fallback

    return known


# ── Venue quality ────────────────────────────────────────────────


TOP_VENUES: set[str] = {
    # CS/AI
    "neurips", "nips", "icml", "iclr", "cvpr", "iccv", "eccv", "aaai",
    "acl", "emnlp", "naacl", "sigir", "kdd", "www", "icse", "fse",
    "osdi", "sosp", "sigcomm", "sigmod", "vldb", "stoc", "focs",
    # Science
    "nature", "science", "cell", "the lancet", "new england journal of medicine",
    "nejm", "pnas", "nature medicine", "nature biotechnology",
    "nature methods", "nature machine intelligence",
    "nature communications", "science robotics", "science advances",
    # Physics
    "physical review letters", "physical review x",
    # Math
    "annals of mathematics", "inventiones mathematicae",
    # General high-impact
    "jama", "bmj",
}


def is_top_venue(venue: Optional[str], publication: Optional[str]) -> bool:
    """Check if a paper is from a top-tier venue (~80 venues, case-insensitive substring)."""
    for v in (venue, publication):
        if v and v.lower().strip() in TOP_VENUES:
            return True
        if v:
            v_lower = v.lower()
            for top in TOP_VENUES:
                if top in v_lower:
                    return True
    return False
