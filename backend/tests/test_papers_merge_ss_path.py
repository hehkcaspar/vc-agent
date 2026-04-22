"""Unit tests for the SS path through ``_merge_papers_by_priority``.

Ensures the destination-autonomy guarantee holds: stubs planted by
``destinations.accept_into_papers`` survive or get enriched when
semantic_scholar_papers writes papers.json, and the SS pass correctly
enriches pre-existing GS rows without clobbering their recency fields.
"""
from __future__ import annotations

from app.services.academic.papers_merge import (
    _merge_papers_by_priority,
    _normalize_title,
)


def _merge_ss(ss, prev):
    """Local helper — every test here exercises the SS path."""
    return _merge_papers_by_priority(
        ss, prev, incoming_source="semantic_scholar",
    )


def test_normalize_title_matches_destinations_rules():
    assert _normalize_title("MCUNet: Tiny Deep Learning") == (
        "mcunet: tiny deep learning"
    )
    assert _normalize_title("  MCUnet   ") == "mcunet"
    assert _normalize_title("MCUnet!") == "mcunet"


def test_ss_paper_enriches_matching_stub():
    ss = [{
        "id": "ss-42",
        "title": "MCUNet: Tiny Deep Learning",
        "authors": [{"name": "Song Han", "authorId": "1"}],
        "year": 2020,
        "citations": 1500,
        "venue": "NeurIPS",
    }]
    prev = [{
        "id": "stub-abc",
        "title": "MCUNet: Tiny Deep Learning",
        "_stub": True,
        "_origin": "routed_from:patents",
        "_original_url": "https://example/fake-patent",
        "_routed_at": "2026-04-21T00:00:00Z",
    }]
    out = _merge_ss(ss, prev)
    assert len(out) == 1
    r = out[0]
    assert r["id"] == "ss-42"
    assert r["citations"] == 1500
    assert r.get("_stub") is not True
    assert r["_was_stub"] is True
    assert r["_origin"] == "routed_from:patents"
    assert r["_original_url"] == "https://example/fake-patent"


def test_unmatched_ss_paper_appended_normally():
    ss = [
        {"id": "ss-1", "title": "Paper One"},
        {"id": "ss-2", "title": "Paper Two"},
    ]
    out = _merge_ss(ss, [])
    assert [p["id"] for p in out] == ["ss-1", "ss-2"]


def test_unmatched_stub_survives():
    ss = [{"id": "ss-1", "title": "Completely Different Paper"}]
    prev = [{
        "id": "stub-xyz", "title": "Stub-Only Work",
        "_stub": True, "_origin": "routed_from:news",
    }]
    out = _merge_ss(ss, prev)
    titles = [(p["id"], p["title"]) for p in out]
    assert ("ss-1", "Completely Different Paper") in titles
    assert ("stub-xyz", "Stub-Only Work") in titles
    stub_out = next(p for p in out if p["id"] == "stub-xyz")
    assert stub_out.get("_stub") is True


def test_mix_of_matched_unmatched_ss_and_unmatched_stubs():
    ss = [
        {"id": "ss-1", "title": "MCUNet: Tiny Deep Learning", "year": 2020},
        {"id": "ss-2", "title": "Brand New SS Paper", "year": 2024},
    ]
    prev = [
        {"id": "stub-a", "title": "MCUNet: Tiny Deep Learning",
         "_stub": True, "_origin": "routed_from:patents"},
        {"id": "stub-b", "title": "Still-An-SS-Gap Paper",
         "_stub": True, "_origin": "routed_from:news"},
    ]
    out = _merge_ss(ss, prev)
    assert len(out) == 3
    by_id = {p["id"]: p for p in out}
    assert "ss-1" in by_id
    assert by_id["ss-1"]["_was_stub"] is True
    assert "ss-2" in by_id
    assert "stub-b" in by_id
    assert by_id["stub-b"]["_stub"] is True


def test_merge_is_case_insensitive_and_whitespace_tolerant():
    ss = [{"id": "ss-1",
           "title": "  MCUNET:   tiny deep   learning "}]
    prev = [{"id": "stub", "title": "MCUNet: Tiny Deep Learning",
             "_stub": True}]
    out = _merge_ss(ss, prev)
    assert len(out) == 1
    assert out[0]["id"] == "ss-1"
    assert out[0].get("_was_stub") is True


def test_no_previous_items_returns_ss_list_with_source_marker():
    ss = [{"id": "ss-1", "title": "A"}, {"id": "ss-2", "title": "B"}]
    out = _merge_ss(ss, [])
    assert [p["id"] for p in out] == ["ss-1", "ss-2"]
    assert all(p.get("_source") == "semantic_scholar" for p in out)


def test_ss_enriches_existing_gs_row_in_place():
    # Post-GS-primary case: SS runs AFTER a GS row is already on disk
    # and merges enrichment IN, leaving GS recency authority untouched.
    existing_gs = [{
        "id": "gs-A:B",
        "title": "MCUNet: Tiny Deep Learning",
        "authors": [{"name": "Song Han"}],
        "year": 2026,
        "citations": 10,
        "venue": "arXiv",
        "_source": "google_scholar",
        "_author_position": "last",
    }]
    incoming_ss = [{
        "id": "ss-42",
        "title": "MCUNet: Tiny Deep Learning",
        "authors": [
            {"name": "Song Han", "authorId": "ssid-1"},
            {"name": "X Y", "authorId": "ssid-2"},
        ],
        "year": 2020,
        "citations": 1500,
        "venue": "NeurIPS",
        "external_ids": {"DOI": "10.1/abc"},
        "influential_citations": 7,
    }]
    out = _merge_ss(incoming_ss, existing_gs)
    assert len(out) == 1
    row = out[0]
    # GS recency survives
    assert row["year"] == 2026
    assert row["citations"] == 10
    assert row["venue"] == "arXiv"
    assert row["_source"] == "google_scholar"
    assert row["_author_position"] == "last"
    # SS enrichment merged in
    assert row["external_ids"]["DOI"] == "10.1/abc"
    assert row["influential_citations"] == 7
    assert any(a.get("authorId") == "ssid-1" for a in row["authors"])
    assert row["_was_ss"] is True
