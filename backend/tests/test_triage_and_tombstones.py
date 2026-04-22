"""Unit tests for the pure triage decision function + tombstones file
I/O. No network, no Gemini calls.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.academic.item_triage import triage, TriageDecision
from app.services.academic.item_verification import VerifyResult
from app.services.academic import tombstones as ts


# ── triage ─────────────────────────────────────────────────────────────


def _vr(**kwargs) -> VerifyResult:
    return VerifyResult(**kwargs)


def test_triage_keeps_confirmed_items():
    item = {"title": "Real event"}
    vr = _vr(verdict="confirmed", category_correct=True)
    d = triage(item, vr, source_category="news")
    assert d.action == "keep"


def test_triage_drops_unconfirmed():
    item = {"title": "Hallucinated claim"}
    vr = _vr(verdict="unconfirmed",
             evidence="no search results matched this claim")
    d = triage(item, vr, source_category="news")
    assert d.action == "drop"
    assert "no search results" in d.reason


def test_triage_drops_category_mismatch_without_suggestion():
    # No suggested_category → drop (we don't know where to send it).
    item = {"title": "MCUNet", "patent_number": "US12345"}
    vr = _vr(verdict="confirmed", category_correct=False,
             correction_note="research paper, not a patent")
    d = triage(item, vr, source_category="patents")
    assert d.action == "drop"
    assert "paper" in d.reason.lower()


def test_triage_routes_when_destination_is_suggested():
    item = {"title": "MCUNet", "patent_number": "US12345"}
    vr = _vr(
        verdict="partial", category_correct=False,
        suggested_category="papers",
        correction_note="research paper, not a patent",
    )
    d = triage(item, vr, source_category="patents")
    assert d.action == "route"
    assert d.destination == "papers"
    assert "paper" in d.reason.lower()


def test_triage_does_not_route_to_unknown_destination():
    vr = _vr(
        verdict="partial", category_correct=False,
        suggested_category="something_weird",
        correction_note="weird category",
    )
    d = triage({"title": "x"}, vr, source_category="patents")
    assert d.action == "drop"


def test_triage_does_not_route_to_same_category():
    # If verify is confused and suggests the same category, still drop.
    vr = _vr(
        verdict="partial", category_correct=False,
        suggested_category="patents",
        correction_note="noise",
    )
    d = triage({"title": "x"}, vr, source_category="patents")
    assert d.action == "drop"


def test_triage_keeps_on_verify_error():
    # Flaky verify call should NOT tombstone — let the next sweep retry.
    item = {"title": "Some item"}
    vr = _vr(verdict="unconfirmed", error="timeout: deadline exceeded")
    d = triage(item, vr, source_category="news")
    assert d.action == "keep"
    assert "verify_error" in d.reason


def test_triage_partial_is_kept():
    # "partial" means the topic exists, one detail is off. Default KEEP —
    # the authoritative_url (if any) still routes the user to something
    # useful, and the content is correct about the scholar.
    item = {"title": "Partial match"}
    vr = _vr(verdict="partial", category_correct=True)
    d = triage(item, vr, source_category="news")
    assert d.action == "keep"


# ── tombstones ─────────────────────────────────────────────────────────


@pytest.fixture()
def scholar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    # Re-root dossier storage to tmp_path for the test.
    sid = "test_scholar"
    monkeypatch.setattr(
        "app.services.academic.tombstones.dossier_path",
        lambda s: tmp_path / s,
    )
    (tmp_path / sid).mkdir()
    return sid


def test_tombstone_write_then_load(scholar: str):
    ts.write_tombstone(
        scholar, category="patents",
        title="MCUNet: Tiny Deep Learning",
        reason="research paper, not a patent",
    )
    rows = ts.load_tombstones(scholar, category="patents")
    assert len(rows) == 1
    assert rows[0]["original_title"].startswith("MCUNet")
    assert "paper" in rows[0]["reason"]


def test_tombstone_dedup_by_normalized_title(scholar: str):
    ts.write_tombstone(scholar, category="patents",
                       title="MCUNet", reason="first note")
    ts.write_tombstone(scholar, category="patents",
                       title="  mcunet  ", reason="second note")
    rows = ts.load_tombstones(scholar, category="patents")
    # Same normalized title — last write wins after dedup.
    assert len(rows) == 1
    assert rows[0]["reason"] == "second note"


def test_tombstone_category_isolation(scholar: str):
    ts.write_tombstone(scholar, category="patents",
                       title="MCUNet", reason="paper")
    ts.write_tombstone(scholar, category="news",
                       title="Some event", reason="unconfirmed")
    assert len(ts.load_tombstones(scholar, category="patents")) == 1
    assert len(ts.load_tombstones(scholar, category="news")) == 1
    assert len(ts.load_tombstones(scholar)) == 2


def test_matches_tombstone_respects_category(scholar: str):
    ts.write_tombstone(scholar, category="patents",
                       title="MCUNet", reason="paper")
    rows = ts.load_tombstones(scholar)
    assert ts.matches_tombstone("MCUNet", rows, category="patents")
    assert not ts.matches_tombstone("MCUNet", rows, category="news")
    assert not ts.matches_tombstone("Different Thing", rows,
                                    category="patents")


def test_matches_tombstone_normalizes_whitespace_and_case(scholar: str):
    ts.write_tombstone(scholar, category="news",
                       title="A Clean Title", reason="")
    rows = ts.load_tombstones(scholar)
    assert ts.matches_tombstone("a  clean   title!", rows, category="news")


def test_format_for_prompt_empty():
    assert ts.format_for_prompt([]) == "(none)"


def test_format_for_prompt_renders_with_reason(scholar: str):
    ts.write_tombstone(scholar, category="patents",
                       title="MCUNet", reason="paper, not a patent")
    rows = ts.load_tombstones(scholar)
    out = ts.format_for_prompt(rows)
    assert "MCUNet" in out
    assert "REJECTED" in out
    assert "paper, not a patent" in out


def test_tombstone_empty_title_is_ignored(scholar: str):
    ts.write_tombstone(scholar, category="patents",
                       title="", reason="x")
    assert ts.load_tombstones(scholar) == []
