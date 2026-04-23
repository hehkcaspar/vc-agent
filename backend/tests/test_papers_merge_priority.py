"""Unit tests for the source-aware merge in
``papers_merge._merge_papers_by_priority``.

Covers the priority matrix:

    existing   | incoming=GS          | incoming=SS                    | incoming=stub
    -----------|----------------------|--------------------------------|--------------
    (none)     | append               | append                         | append
    GS         | refresh              | enrich (SS adds authorId etc)  | no-op
    SS         | GS wins recency, SS  | refresh                        | no-op
               | preserves enrichment |                                |
    stub       | wholesale + audit    | wholesale + audit              | no-op
"""
from __future__ import annotations

from app.services.academic.papers_merge import _merge_papers_by_priority


def _gs(**kw):
    base = {"title": kw.get("title", "T"),
            "authors": kw.get("authors", [{"name": "Song Han"}]),
            "year": kw.get("year", 2026),
            "citations": kw.get("citations", 10),
            "venue": kw.get("venue", "arXiv"),
            "_author_position": kw.get("_author_position", "last"),
            "id": kw.get("id", "gs-A:B"),
            "_source": "google_scholar"}
    base.update({k: v for k, v in kw.items() if k not in base})
    return base


def _ss(**kw):
    base = {"title": kw.get("title", "T"),
            "authors": kw.get("authors", [
                {"name": "Song Han", "authorId": "ssid-1"}
            ]),
            "year": kw.get("year", 2020),
            "citations": kw.get("citations", 500),
            "venue": kw.get("venue", "NeurIPS"),
            "id": kw.get("id", "ss-42"),
            "external_ids": kw.get(
                "external_ids", {"DOI": "10.1/abc"}
            ),
            "s2_fields": kw.get(
                "s2_fields", [{"category": "CS", "source": "s2"}]
            ),
            "influential_citations": kw.get("influential_citations", 7)}
    return base


def _stub(**kw):
    return {
        "id": kw.get("id", "stub-xyz"),
        "title": kw.get("title", "T"),
        "_stub": True,
        "_origin": "routed_from:patents",
        "_original_url": "https://example/fake-patent",
        "_routed_at": "2026-04-21T00:00:00Z",
    }


# ── (none) + incoming ──────────────────────────────────────────────────


def test_empty_existing_plus_gs_appends():
    out = _merge_papers_by_priority(
        [_gs()], [], incoming_source="google_scholar",
    )
    assert len(out) == 1
    assert out[0]["_source"] == "google_scholar"


def test_empty_existing_plus_ss_appends_with_source_marker():
    out = _merge_papers_by_priority(
        [_ss()], [], incoming_source="semantic_scholar",
    )
    assert len(out) == 1
    assert out[0]["_source"] == "semantic_scholar"


# ── GS existing + GS incoming = refresh ───────────────────────────────


def test_gs_refresh_overwrites_recency():
    existing = [_gs(citations=5)]
    incoming = [_gs(citations=100)]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    assert len(out) == 1
    assert out[0]["citations"] == 100


# ── GS existing + SS incoming = SS enriches ─────────────────────────


def test_ss_enriches_gs_row_in_place():
    existing = [_gs()]  # has _author_position="last"
    incoming = [_ss(year=2020, citations=500, venue="NeurIPS")]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="semantic_scholar",
    )
    assert len(out) == 1
    row = out[0]
    # GS keeps recency authority.
    assert row["year"] == 2026
    assert row["citations"] == 10
    assert row["venue"] == "arXiv"
    assert row["_author_position"] == "last"
    assert row["_source"] == "google_scholar"
    # SS enrichment fields were merged in.
    assert row["external_ids"]["DOI"] == "10.1/abc"
    assert row["s2_fields"][0]["category"] == "CS"
    assert row["influential_citations"] == 7
    # SS authors (with authorId) replace the GS no-authorId authors.
    assert any(a.get("authorId") == "ssid-1" for a in row["authors"])
    assert row["_was_ss"] is True


# ── SS existing + GS incoming = GS wins recency ────────────────────


def test_gs_over_ss_preserves_ss_enrichment_fields():
    existing = [_ss(year=2020, citations=500)]
    incoming = [_gs(year=2026, citations=10, _author_position="last")]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    assert len(out) == 1
    row = out[0]
    # GS wins on recency fields.
    assert row["year"] == 2026
    assert row["citations"] == 10
    assert row["_source"] == "google_scholar"
    # SS enrichment preserved.
    assert row["external_ids"]["DOI"] == "10.1/abc"
    assert row["s2_fields"][0]["category"] == "CS"
    assert row["influential_citations"] == 7
    # SS authors (with authorId) are preserved — attributed_metrics needs them.
    assert any(a.get("authorId") == "ssid-1" for a in row["authors"])
    # GS-derived _author_position stays on the row as a fallback.
    assert row["_author_position"] == "last"
    assert row["_was_ss"] is True


# ── SS existing + SS incoming = refresh (historical behaviour) ────────


def test_ss_refresh_overwrites():
    existing = [_ss(citations=100)]
    incoming = [_ss(citations=999)]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="semantic_scholar",
    )
    assert len(out) == 1
    assert out[0]["citations"] == 999


# ── stub existing + GS incoming ────────────────────────────────────────


def test_gs_wins_wholesale_over_stub_preserves_audit():
    existing = [_stub()]
    incoming = [_gs(year=2026, citations=10)]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    assert len(out) == 1
    row = out[0]
    assert row["_source"] == "google_scholar"
    assert row.get("_stub") is None
    assert row["_was_stub"] is True
    # Routing audit preserved from the stub.
    assert row["_origin"] == "routed_from:patents"
    assert row["_original_url"] == "https://example/fake-patent"
    assert row["_routed_at"] == "2026-04-21T00:00:00Z"


# ── stub existing + SS incoming ───────────────────────────────────────


def test_ss_wins_wholesale_over_stub_preserves_audit():
    existing = [_stub()]
    incoming = [_ss()]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="semantic_scholar",
    )
    assert len(out) == 1
    row = out[0]
    assert row["_source"] == "semantic_scholar"
    assert row.get("_stub") is None
    assert row["_was_stub"] is True
    assert row["_origin"] == "routed_from:patents"


# ── stub existing + stub incoming = no-op ────────────────────────────


def test_stub_incoming_is_noop_when_existing():
    existing = [_stub()]
    incoming = [_stub()]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="stub",
    )
    assert len(out) == 1
    # Still a stub; no wholesale overwrite.
    assert out[0]["_stub"] is True


# ── Title-normalized matching ─────────────────────────────────────────


def test_match_is_case_insensitive_and_whitespace_tolerant():
    existing = [_gs(title="MCUNet: Tiny Deep Learning")]
    incoming = [_ss(title="  mcunet:   Tiny   Deep   Learning  ")]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="semantic_scholar",
    )
    assert len(out) == 1  # matched, enriched in place
    assert out[0]["_was_ss"] is True


# ── Unmatched incoming gets appended at the end ──────────────────────


def test_unmatched_incoming_appended():
    existing = [_gs(title="A")]
    incoming = [_gs(title="B")]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    assert [r["title"] for r in out] == ["A", "B"]


# ── Rows with missing title are skipped, not appended ────────────────


def test_rows_missing_title_are_skipped():
    existing = [_gs()]
    incoming = [{"authors": [], "citations": 0, "year": 2020}]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    assert len(out) == 1  # existing row unchanged


def test_normalize_ledger_row_stamps_legacy_ss_marker():
    # Pre-refactor papers.json rows lack `_source`. The normalize
    # helper — which source modules invoke on prev_items before merge —
    # stamps them as semantic_scholar so downstream provenance is
    # uniform.
    from app.services.academic.papers_merge import normalize_ledger_row

    legacy = {"id": "ss-legacy", "title": "Old Paper", "citations": 100}
    stub = {"id": "stub-a", "title": "Stubbed", "_stub": True,
            "_origin": "routed_from:patents"}
    gs = {"id": "gs-x", "title": "Already marked", "_source": "google_scholar"}

    out_legacy = normalize_ledger_row(legacy)
    assert out_legacy["_source"] == "semantic_scholar"
    assert out_legacy is not legacy  # fresh dict, not mutated

    out_stub = normalize_ledger_row(stub)
    assert "_source" not in out_stub  # stubs keep their `_stub` classifier
    assert out_stub is stub  # unchanged rows pass through

    out_gs = normalize_ledger_row(gs)
    assert out_gs is gs  # already marked → no-op


def test_merge_is_pure_does_not_backfill_source_on_existing():
    # Regression guard: merge trusts callers to normalize before
    # calling. Unmarked rows in `existing` pass through verbatim.
    existing = [{"id": "ss-legacy", "title": "Unmarked", "citations": 100}]
    incoming = [_gs(title="A Brand New GS Paper")]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    legacy = next(r for r in out if r["id"] == "ss-legacy")
    assert "_source" not in legacy  # merge left it alone


def test_duplicate_titles_in_same_incoming_batch_are_collapsed():
    existing: list[dict] = []
    incoming = [
        _gs(id="gs-1", title="Same Title", citations=5),
        _gs(id="gs-2", title="Same Title", citations=99),
    ]
    out = _merge_papers_by_priority(
        incoming, existing, incoming_source="google_scholar",
    )
    # Two items with the same normalized title collapse; last write
    # wins (GS-over-GS refresh semantics).
    assert len(out) == 1
    assert out[0]["citations"] == 99
