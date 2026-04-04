"""
Shared utility functions for Academic Tracking v2 domain tools.

All functions here are pure (no DB, no LLM, no I/O) and extracted from
the v1 pipeline (academic_pipeline.py) and Gemini helpers (academic_gemini.py).
Algorithms are preserved exactly — see doc/ACADEMIC_TRACKING_V2_DESIGN.md §4.3.1.
"""

import json
import logging
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


# ── Name matching ────────────────────────────────────────────────


def normalize_name(name: str) -> str:
    """NFKD Unicode normalization — strips accents, cedillas, etc.

    Handles: Schölkopf → Scholkopf, de Rham → de Rham (unchanged ASCII).
    """
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def name_tokens(name: str) -> list[str]:
    """Extract lowercase name tokens, ignoring short particles (len ≤ 1)."""
    normalized = normalize_name(name.lower())
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
    tokens_a = name_tokens(name_a)
    tokens_b = name_tokens(name_b)

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
    """Check if Semantic Scholar metrics are plausible vs expected (from GS).

    h-index ratio ≥ 0.3 (3x divergence gate, only if expected_h > 10).
    Citation ratio ≥ 0.1 (10x divergence gate, only if expected_citations > 1000).
    """
    if expected_h_index and expected_h_index > 10 and ss_h_index > 0:
        ratio = min(ss_h_index, expected_h_index) / max(ss_h_index, expected_h_index)
        if ratio < 0.3:
            logger.warning(
                "Metric mismatch: h-index expected ~%d, got %d (ratio=%.2f)",
                expected_h_index, ss_h_index, ratio,
            )
            return False

    if expected_citations and expected_citations > 1000 and ss_citation_count > 0:
        ratio = min(ss_citation_count, expected_citations) / max(ss_citation_count, expected_citations)
        if ratio < 0.1:
            logger.warning(
                "Metric mismatch: citations expected ~%d, got %d (ratio=%.2f)",
                expected_citations, ss_citation_count, ratio,
            )
            return False

    return True


# ── URL classification ───────────────────────────────────────────


def classify_urls(urls: list[str]) -> dict[str, Any]:
    """Deterministic URL→ID extraction. Ground truth — overrides LLM output.

    Extracts:
    - Google Scholar user ID from scholar.google.* URLs (any TLD)
    - Semantic Scholar author ID (trailing numeric) from semanticscholar.org
    - LinkedIn and DBLP URLs
    """
    known: dict[str, Any] = {}
    for url in urls:
        lower = url.lower()
        parsed = urlparse(url)

        # Google Scholar profile (handles scholar.google.fr, .co.uk, etc.)
        if "scholar.google" in lower and "/citations" in lower:
            qs = parse_qs(parsed.query)
            user_ids = qs.get("user", [])
            if user_ids:
                known["google_scholar_id"] = user_ids[0]
                known["google_scholar_url"] = url

        # Semantic Scholar author page
        elif "semanticscholar.org/author/" in lower:
            parts = parsed.path.rstrip("/").split("/")
            if len(parts) >= 3 and parts[-1].isdigit():
                known["semantic_scholar_id"] = parts[-1]
                known["semantic_scholar_url"] = url

        # LinkedIn
        elif "linkedin.com/in/" in lower:
            known["linkedin_url"] = url

        # DBLP
        elif "dblp.org/" in lower:
            known["dblp_url"] = url

    return known


# ── Title normalization + dedup ──────────────────────────────────


def norm_title(title: str) -> str:
    """Normalize title for deduplication: lowercase + collapse whitespace."""
    return " ".join(title.lower().split())


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


# ── Author position ──────────────────────────────────────────────


def compute_author_position(
    authors: list[Any],
    scholar_ss_id: Optional[str],
) -> tuple[Optional[str], Optional[int]]:
    """Determine the scholar's position on a paper's author list.

    Returns (position, total_authors) where position is
    "sole", "first", "last", or "middle".
    """
    if not authors:
        return None, None

    total = len(authors)
    if total == 1:
        return "sole", 1

    if not scholar_ss_id:
        return None, total

    # Authors may be dicts with authorId or plain strings
    if isinstance(authors[0], dict):
        ids = [a.get("authorId") for a in authors]
    else:
        return None, total

    try:
        idx = ids.index(scholar_ss_id)
    except ValueError:
        return None, total

    if idx == 0:
        return "first", total
    elif idx == total - 1:
        return "last", total
    else:
        return "middle", total


# ── JSON parsing ─────────────────────────────────────────────────


def parse_json(text: str) -> dict[str, Any]:
    """Tolerantly parse JSON from LLM output (handles markdown fences, etc.)."""
    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip().rstrip("`")

    # Try full text first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find the outermost { ... } by brace matching (handles nested objects)
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    # Last resort: greedy regex
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse JSON from LLM response: %s", text[:200])
    return {}


# ── Safe type conversion ─────────────────────────────────────────


def safe_int(val: Any) -> Optional[int]:
    """Safely convert a value to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
