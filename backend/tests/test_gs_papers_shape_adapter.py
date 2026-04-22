"""Unit tests for the GS→papers.json shape adapter and name-match
position heuristic in ``google_scholar_papers.py``.

Pure-function tests, no network calls.
"""
from __future__ import annotations

from app.services.academic.sources.google_scholar_papers import (
    _infer_position,
    _normalize_gs_article,
    _parse_venue,
    _split_authors,
    _name_variants,
)


# ── venue parsing ──────────────────────────────────────────────────────


def test_parse_venue_strips_trailing_year():
    assert _parse_venue("arXiv preprint arXiv:2604.06832, 2026") == (
        "arXiv preprint arXiv:2604.06832"
    )
    assert _parse_venue("Proceedings of the 31st ACM Conference, 2026") == (
        "Proceedings of the 31st ACM Conference"
    )


def test_parse_venue_no_year_is_unchanged():
    assert _parse_venue("NeurIPS") == "NeurIPS"
    assert _parse_venue("Journal of Foo") == "Journal of Foo"


def test_parse_venue_empty():
    assert _parse_venue("") == ""


# ── authors split ──────────────────────────────────────────────────────


def test_split_authors_drops_ellipsis_tokens():
    out = _split_authors("C Wu, S Lan, Y Fu, ..., P Luo, ...")
    names = [a["name"] for a in out]
    assert names == ["C Wu", "S Lan", "Y Fu", "P Luo"]


def test_split_authors_handles_empty():
    assert _split_authors("") == []


def test_split_authors_trims_whitespace():
    out = _split_authors("  Song Han ,  Jane Doe  ")
    assert [a["name"] for a in out] == ["Song Han", "Jane Doe"]


# ── name variants ──────────────────────────────────────────────────────


def test_name_variants_generate_common_shortenings():
    variants = set(_name_variants("Song Han"))
    assert "song han" in variants
    assert "s han" in variants
    assert "s. han" in variants


def test_name_variants_empty_name():
    assert _name_variants("") == []


# ── position inference ─────────────────────────────────────────────────


def test_infer_position_first():
    assert _infer_position("Song Han, A B, C D", "Song Han") == "first"
    assert _infer_position("S Han, A B, C D", "Song Han") == "first"


def test_infer_position_last():
    assert _infer_position("A B, C D, Song Han", "Song Han") == "last"
    assert _infer_position("A B, C D, S. Han", "Song Han") == "last"


def test_infer_position_middle():
    assert _infer_position("A B, Song Han, C D", "Song Han") == "middle"


def test_infer_position_truncated_scholar_fully_elided_returns_none():
    # Scholar not in the visible head nor tail — everything they might
    # have appeared in is inside the elided "...".
    assert _infer_position(
        "A B, C D, ..., E F", "Song Han"
    ) is None


def test_infer_position_after_ellipsis_at_tail():
    # Scholar shown in the tail after ellipsis → last.
    assert _infer_position(
        "A B, C D, ..., Song Han", "Song Han"
    ) == "last"


def test_infer_position_visible_head_collapses_to_middle_under_truncation():
    # Scholar in the visible head but not index 0, trailing "..." —
    # they're neither "first" nor necessarily "last" (some unseen
    # authors come after), so the only safe label is "middle".
    assert _infer_position(
        "A B, Song Han, C D, ...", "Song Han"
    ) == "middle"


def test_infer_position_tail_middle_under_truncation():
    # Scholar appears in the tail but NOT the last tail token → middle.
    assert _infer_position(
        "A B, ..., Song Han, X Y", "Song Han"
    ) == "middle"


def test_infer_position_sole_author():
    assert _infer_position("Song Han", "Song Han") == "sole"
    assert _infer_position("  S Han  ", "Song Han") == "sole"


def test_infer_position_sole_author_no_hit():
    assert _infer_position("A B", "Song Han") is None


def test_infer_position_scholar_absent():
    assert _infer_position("A B, C D", "Song Han") is None


def test_infer_position_empty_inputs():
    assert _infer_position("", "Song Han") is None
    assert _infer_position("A B", "") is None


# ── full normalize ────────────────────────────────────────────────────


def test_normalize_gs_article_full_serpapi_shape():
    article = {
        "title": "Fast-dVLM: Efficient Block-Diffusion VLM",
        "link": (
            "https://scholar.google.com/citations?view_op=view_citation"
            "&hl=en&user=ABC&citation_for_view=ABC:XYZ"
        ),
        "citation_id": "ABC:XYZ",
        "authors": "C Wu, S Lan, ..., Song Han",
        "publication": "arXiv preprint arXiv:2604.06832, 2026",
        "cited_by": {"value": 12},
        "year": "2026",
    }
    row = _normalize_gs_article(article, "Song Han")
    assert row is not None
    assert row["id"] == "gs-ABC:XYZ"
    assert row["title"] == "Fast-dVLM: Efficient Block-Diffusion VLM"
    assert row["year"] == 2026
    assert row["venue"] == "arXiv preprint arXiv:2604.06832"
    assert row["citations"] == 12
    assert row["_source"] == "google_scholar"
    assert row["_author_position"] == "last"
    assert row["_gs_citation_id"] == "ABC:XYZ"
    # Authors are structured dicts (no authorId from GS).
    author_names = [a["name"] for a in row["authors"]]
    assert "Song Han" in author_names
    assert "..." not in author_names


def test_normalize_gs_article_missing_title_returns_none():
    assert _normalize_gs_article({"title": ""}, "Song Han") is None
    assert _normalize_gs_article({}, "Song Han") is None


def test_normalize_gs_article_null_citations():
    article = {
        "title": "Recent preprint",
        "citation_id": "AAA:BBB",
        "authors": "Song Han, J Doe",
        "publication": "arXiv preprint, 2026",
        "cited_by": {"value": None},
        "year": "2026",
    }
    row = _normalize_gs_article(article, "Song Han")
    assert row["citations"] == 0
    assert row["_author_position"] == "first"


def test_normalize_gs_article_malformed_year():
    article = {
        "title": "T",
        "citation_id": "A:B",
        "authors": "Song Han",
        "publication": "V",
        "cited_by": {"value": 0},
        "year": "not-a-year",
    }
    row = _normalize_gs_article(article, "Song Han")
    assert row["year"] is None


def test_normalize_gs_article_stable_id_without_citation_id():
    """GS sometimes lacks citation_id on scraped rows; the fallback
    must produce the same id every time (not Python's randomized
    hash())."""
    article = {
        "title": "Same Exact Title",
        "citation_id": "",
        "authors": "Song Han",
        "publication": "V",
        "cited_by": {"value": 0},
        "year": "2026",
    }
    row1 = _normalize_gs_article(article, "Song Han")
    row2 = _normalize_gs_article(article, "Song Han")
    assert row1["id"] == row2["id"]
    assert row1["id"].startswith("gs-t")


def test_normalize_gs_article_sole_author():
    article = {
        "title": "Solo Paper",
        "citation_id": "S:S",
        "authors": "Song Han",
        "publication": "arXiv, 2026",
        "cited_by": {"value": 0},
        "year": "2026",
    }
    row = _normalize_gs_article(article, "Song Han")
    assert row["_author_position"] == "sole"
