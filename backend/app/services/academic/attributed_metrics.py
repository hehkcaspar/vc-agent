"""Pure compute of author-position-weighted citation metrics.

Concept 4 from the framework doc. Called by
`sources/semantic_scholar_papers.py` after papers.json is updated, and
writes `attributed_metrics.json` to the fact store. Dim agents read
these values; they never recompute.
"""

from __future__ import annotations

from typing import Any


# Per Concept 4.
FIRST_OR_LAST = 1.0
SECOND = 0.4
SECOND_TO_LAST = 0.3
MIDDLE = 0.1
CONSORTIUM = 0.05

CONSORTIUM_THRESHOLD = 20
# If a single paper contributes more than this fraction of attributed
# citations we emit a concentration inflation flag.
CONCENTRATION_FLAG_RATIO = 0.40


def _position_of(paper: dict[str, Any], scholar_ss_id: str | None) -> int | None:
    """Return 0-indexed position of the scholar in the paper's author list.

    Match by `authorId` if we know the scholar's SS id; fall back to a
    case-insensitive name match if we don't (best effort).
    """
    authors = paper.get("authors") or []
    if not authors:
        return None
    if scholar_ss_id:
        for i, a in enumerate(authors):
            if str(a.get("authorId") or "") == str(scholar_ss_id):
                return i
    return None


def _weight_for(pos: int, n_authors: int) -> tuple[float, str]:
    """Return (weight, role_label) for the scholar at position *pos*
    in a paper with *n_authors* total authors.
    """
    if n_authors >= CONSORTIUM_THRESHOLD:
        if pos == 0:
            return FIRST_OR_LAST, "first"
        if pos == n_authors - 1 and n_authors >= 3:
            return FIRST_OR_LAST, "last"
        return CONSORTIUM, "consortium"
    if pos == 0:
        return FIRST_OR_LAST, "first"
    if n_authors >= 3 and pos == n_authors - 1:
        return FIRST_OR_LAST, "last"
    if pos == 1:
        return SECOND, "second"
    if n_authors >= 4 and pos == n_authors - 2:
        return SECOND_TO_LAST, "second_to_last"
    return MIDDLE, "middle"


def _h_index(citation_counts: list[int]) -> int:
    """Standard h-index over a list of citation counts."""
    sorted_c = sorted(citation_counts, reverse=True)
    h = 0
    for i, c in enumerate(sorted_c, start=1):
        if c >= i:
            h = i
        else:
            break
    return h


def compute_attributed_metrics(
    papers: list[dict[str, Any]],
    scholar_ss_id: str | None,
) -> dict[str, Any]:
    """Compute the full attributed_metrics block from a paper list.

    Shape matches the example in Concept 4 of the framework doc.

    If `scholar_ss_id` is missing we CANNOT reliably identify the
    scholar's author position on any paper, so every paper would
    fall into the `unknown_position` bucket and get weight 0.1 —
    silently producing garbage metrics. In that case we return a
    minimal block that reports the raw citation total, zero
    attribution, and a `missing_data` flag so downstream dims can
    downgrade their confidence instead of scoring on noise.
    """
    total_raw = sum(int(p.get("citations") or 0) for p in papers)

    if not scholar_ss_id:
        return {
            "total_citations_raw": total_raw,
            "attributed_citations": 0,
            "attribution_ratio": 0.0,
            "first_author_citations": 0,
            "last_author_citations": 0,
            "first_last_h_index": 0,
            "top5_first_or_last": [],
            "inflation_flags": [],
            "scholar_ss_id_used": None,
            "paper_count_considered": len(papers),
            "missing_data": [
                "scholar has no identity.semantic_scholar.id — cannot "
                "compute author-position-weighted metrics; raw citation "
                "count is shown but attribution is zero",
            ],
        }

    attributed = 0.0
    first_author_cites = 0
    last_author_cites = 0
    first_or_last_cites: list[int] = []
    per_paper_contrib: list[tuple[dict[str, Any], float, str]] = []

    for p in papers:
        cites = int(p.get("citations") or 0)
        authors = p.get("authors") or []
        n = len(authors)
        pos = _position_of(p, scholar_ss_id)
        if pos is None or n == 0:
            # Scholar's author id not found on this paper — skip it
            # entirely rather than counting it as middle-author. With
            # a known SS id, "not found" means the paper really
            # doesn't involve the scholar (noise in the feed) or the
            # authors list is empty.
            per_paper_contrib.append((p, 0.0, "not_present"))
            continue
        weight, role = _weight_for(pos, n)
        contrib = weight * cites
        attributed += contrib
        per_paper_contrib.append((p, contrib, role))
        if role == "first":
            first_author_cites += cites
            first_or_last_cites.append(cites)
        elif role == "last":
            last_author_cites += cites
            first_or_last_cites.append(cites)

    attribution_ratio = (attributed / total_raw) if total_raw > 0 else 0.0

    # Top 5 first/last-author papers by citations.
    top5 = sorted(
        [
            {
                "paper_id": p.get("id"),
                "title": p.get("title"),
                "position": role,
                "citations": int(p.get("citations") or 0),
                "year": p.get("year"),
                "venue": p.get("venue") or p.get("journal"),
            }
            for (p, _c, role) in per_paper_contrib
            if role in ("first", "last")
        ],
        key=lambda r: r["citations"],
        reverse=True,
    )[:5]

    # Concentration inflation flag.
    inflation_flags: list[str] = []
    if attributed > 0 and per_paper_contrib:
        top_contrib = max(c for (_p, c, _r) in per_paper_contrib)
        if top_contrib / attributed >= CONCENTRATION_FLAG_RATIO:
            top_paper = max(per_paper_contrib, key=lambda x: x[1])[0]
            share = top_contrib / attributed * 100
            inflation_flags.append(
                f"concentrated: {share:.0f}% of attributed citations from one paper"
                f" ({(top_paper.get('title') or '?')[:80]})"
            )

    return {
        "total_citations_raw": total_raw,
        "attributed_citations": round(attributed, 1),
        "attribution_ratio": round(attribution_ratio, 3),
        "first_author_citations": first_author_cites,
        "last_author_citations": last_author_cites,
        "first_last_h_index": _h_index(first_or_last_cites),
        "top5_first_or_last": top5,
        "inflation_flags": inflation_flags,
        "scholar_ss_id_used": scholar_ss_id,
        "paper_count_considered": len(papers),
    }
