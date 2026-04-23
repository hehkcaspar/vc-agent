"""Unit tests for the heuristic-position fallback path in
``compute_attributed_metrics``.

The authorId path still works (covered by existing tests). These new
tests exercise the fallback so GS-only papers (which lack authorId)
contribute to the metrics via their row-level ``_author_position``.
"""
from __future__ import annotations

from app.services.academic.attributed_metrics import (
    compute_attributed_metrics,
)


def test_authorid_path_unchanged():
    # Backward-compat check: when authorId is present, behaviour must
    # match what semantic_scholar_papers has produced all along.
    papers = [
        {
            "id": "ss-1", "title": "A",
            "citations": 100,
            "authors": [
                {"name": "Song Han", "authorId": "ssid-1"},
                {"name": "X Y", "authorId": "ssid-2"},
                {"name": "Z W", "authorId": "ssid-3"},
            ],
        },
    ]
    m = compute_attributed_metrics(papers, "ssid-1")
    # Song Han is first author of a 3-author paper.
    assert m["first_author_citations"] == 100
    assert m["attributed_citations"] == 100.0
    assert m["position_unknown_papers"] == 0


def test_heuristic_fallback_when_authorid_missing():
    papers = [
        {
            "id": "gs-1", "title": "A",
            "citations": 100,
            "authors": [{"name": "Song Han"}, {"name": "Y Z"}],
            "_author_position": "first",
        },
        {
            "id": "gs-2", "title": "B",
            "citations": 50,
            "authors": [{"name": "X Y"}, {"name": "Song Han"}],
            "_author_position": "last",
        },
        {
            "id": "gs-3", "title": "C",
            "citations": 20,
            "authors": [
                {"name": "A B"}, {"name": "Song Han"}, {"name": "C D"},
            ],
            "_author_position": "middle",
        },
    ]
    m = compute_attributed_metrics(papers, "ssid-1")
    assert m["first_author_citations"] == 100
    assert m["last_author_citations"] == 50
    # First+last = 150 × 1.0; middle = 20 × 0.1 = 2.0 → total 152.
    assert m["attributed_citations"] == 152.0
    assert m["position_unknown_papers"] == 0


def test_position_unknown_counted_separately():
    papers = [
        {
            "id": "gs-1", "title": "A",
            "citations": 99,
            "authors": [{"name": "X Y"}],       # no authorId
            "_author_position": None,           # heuristic failed
        },
    ]
    m = compute_attributed_metrics(papers, "ssid-1")
    # Raw citations still counted.
    assert m["total_citations_raw"] == 99
    # But attribution is zero — we don't know the scholar's role.
    assert m["attributed_citations"] == 0
    assert m["first_author_citations"] == 0
    assert m["last_author_citations"] == 0
    # Flagged for observability.
    assert m["position_unknown_papers"] == 1


def test_mixed_authorid_and_heuristic_rows():
    # SS-enriched paper (authorId) + pure-GS paper (heuristic) coexist.
    papers = [
        {
            "id": "ss-1", "title": "A",
            "citations": 200,
            "authors": [
                {"name": "Song Han", "authorId": "ssid-1"},
                {"name": "X Y", "authorId": "ssid-2"},
                {"name": "Z W", "authorId": "ssid-3"},
            ],
        },
        {
            "id": "gs-1", "title": "B",
            "citations": 40,
            "authors": [{"name": "Y Z"}, {"name": "Song Han"}],
            "_author_position": "last",
        },
    ]
    m = compute_attributed_metrics(papers, "ssid-1")
    assert m["first_author_citations"] == 200       # from SS row
    assert m["last_author_citations"] == 40         # from GS row heuristic
    assert m["attributed_citations"] == 240.0
    assert m["position_unknown_papers"] == 0


def test_no_authors_and_no_heuristic_still_counts_raw():
    papers = [
        {"id": "gs-1", "title": "A", "citations": 10, "authors": []},
    ]
    m = compute_attributed_metrics(papers, "ssid-1")
    assert m["total_citations_raw"] == 10
    assert m["attributed_citations"] == 0
    assert m["position_unknown_papers"] == 1
