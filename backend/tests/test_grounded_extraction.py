"""Unit tests for the grounded_extraction module — the bits without
network deps. The full verify+triage+url_fallback orchestrator is
covered by live diagnostic runs against real entities; here we
exercise pure functions and dataclass semantics.

The async tests use ``asyncio.run`` directly rather than
``pytest-asyncio`` because the project's pytest config doesn't
register the async marker — matches the convention in other tests
that drive async code via ``TestClient``.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.services.academic.llm_client import _byte_to_char_index
from app.services.grounded_extraction import (
    LedgerStorage,
    apply_url_fallback,
    refine_jsonl,
    triage,
)
from app.services.grounded_extraction.item_verification import VerifyResult
from app.services.grounded_extraction.storage import noop_tombstone


# ── byte→char index converter (covers the CJK / en-dash bug) ─────────


def test_byte_to_char_ascii_identity():
    text = "hello world"
    assert _byte_to_char_index(text, 0) == 0
    assert _byte_to_char_index(text, 5) == 5
    assert _byte_to_char_index(text, 11) == 11


def test_byte_to_char_endash_offset():
    # The Euro 2024 case that proved indices are byte offsets:
    # text contains an en-dash (3 bytes, 1 char). end_index reported by
    # the API was len(text)+2 — only matches if interpreted as bytes.
    text = "Spain won 2–1"  # 13 chars, 15 bytes
    assert len(text) == 13
    assert len(text.encode("utf-8")) == 15
    # Byte index at end-of-text == char len(text)
    assert _byte_to_char_index(text, 15) == 13
    # Byte 11 is right after the en-dash (which spans bytes 10..12)
    # text[0:9] = "Spain won "
    # text[9] = "2", text[10] = "–" (3 bytes), text[11] = "1"
    assert _byte_to_char_index(text, 13) == 11  # after "2" + en-dash


def test_byte_to_char_cjk():
    # 赛 = 3 bytes, 源 = 3 bytes
    text = "赛源 ai"
    assert len(text) == 5
    assert len(text.encode("utf-8")) == 9
    assert _byte_to_char_index(text, 0) == 0
    assert _byte_to_char_index(text, 3) == 1   # after 赛
    assert _byte_to_char_index(text, 6) == 2   # after 赛源
    assert _byte_to_char_index(text, 7) == 3   # after 赛源 + space
    assert _byte_to_char_index(text, 9) == 5   # end


def test_byte_to_char_mid_codepoint_rounds_down():
    # If the API ever hands us an index landing mid-multibyte
    # sequence, we round down to the previous valid boundary.
    text = "赛源"  # 6 bytes
    # Byte 1 lands inside the first 3-byte 赛; should round down to 0.
    assert _byte_to_char_index(text, 1) == 0
    # Byte 4 lands inside the second 3-byte 源; should round down to 1.
    assert _byte_to_char_index(text, 4) == 1


def test_byte_to_char_overflow_clamps():
    text = "abc"
    # Byte index past end → return len(text)
    assert _byte_to_char_index(text, 100) == 3


# ── LedgerStorage dataclass semantics ────────────────────────────────


@asynccontextmanager
async def _fake_lock(_subject_id: str):
    yield


def _fake_path(_sid: str, cat: str) -> Path:
    return Path(f"/tmp/{cat}.jsonl")


def test_ledger_storage_default_tombstone_is_noop():
    """noop_tombstone is callable, returns None, and accepts the
    refinement-pipeline call shape."""
    storage = LedgerStorage(jsonl_path=_fake_path, write_lock=_fake_lock)
    assert storage.write_tombstone is noop_tombstone
    # Refinement calls it as `(subject_id, category=..., title=..., reason=...)`.
    result = storage.write_tombstone(
        "subj-1", category="news", title="title", reason="r",
    )
    assert result is None
    assert storage.accept_into is None


def test_ledger_storage_is_frozen():
    """LedgerStorage is hashable + frozen — module-level singletons
    can't be accidentally mutated by callers."""
    storage = LedgerStorage(jsonl_path=_fake_path, write_lock=_fake_lock)
    with pytest.raises(Exception):  # FrozenInstanceError
        storage.accept_into = lambda: None  # type: ignore[misc]


# ── triage decision logic ────────────────────────────────────────────


def test_triage_drops_unconfirmed():
    vr = VerifyResult(
        verdict="unconfirmed", subject_match=False,
        evidence="story is about a different entity sharing the name",
    )
    decision = triage({"title": "x"}, vr, source_category="news")
    assert decision.action == "drop"
    assert "different entity" in decision.reason


def test_triage_keeps_confirmed():
    vr = VerifyResult(verdict="confirmed", subject_match=True)
    decision = triage({"title": "x"}, vr, source_category="news")
    assert decision.action == "keep"


def test_triage_routes_when_category_wrong_with_known_destination():
    vr = VerifyResult(
        verdict="confirmed",
        category_correct=False,
        suggested_category="papers",
        correction_note="this is a paper PDF",
    )
    decision = triage({"title": "x"}, vr, source_category="news")
    assert decision.action == "route"
    assert decision.destination == "papers"


def test_triage_drops_when_category_wrong_no_destination():
    vr = VerifyResult(
        verdict="confirmed",
        category_correct=False,
        suggested_category="something_unknown",
        correction_note="not a news article",
    )
    decision = triage({"title": "x"}, vr, source_category="news")
    assert decision.action == "drop"


def test_triage_keeps_on_verify_error():
    """A flaky verify call shouldn't tombstone the item — keep it
    pending for the next sweep."""
    vr = VerifyResult(verdict="unconfirmed", error="network timeout")
    decision = triage({"title": "x"}, vr, source_category="news")
    assert decision.action == "keep"
    assert "verify_error" in decision.reason


# ── VerifyResult subject-match defence-in-depth ──────────────────────
#
# These are unit tests for the parsing branch in verify_item that
# forces verdict→unconfirmed when subject_match=False. The full
# verify_item function makes a network call so we can't test it here
# without mocking; this exercises the post-parse logic directly.


def test_verify_result_default_subject_match():
    """Legacy callers that don't pass subject_match get True (preserve
    behavior for absent field)."""
    vr = VerifyResult(verdict="confirmed")
    assert vr.subject_match is True


def test_verify_result_subject_match_carries_through():
    vr = VerifyResult(verdict="unconfirmed", subject_match=False)
    assert vr.subject_match is False


# ── verify_item subject-match defence-in-depth ───────────────────────
#
# If the model returns verdict=confirmed but subject_match=False (it
# acknowledges the subject mismatch but forgot to flip the verdict),
# we force the verdict to unconfirmed at parse time. This is what
# stopped Override Co's wrong-subject collisions from leaking through.


def test_verify_item_forces_unconfirmed_on_subject_mismatch(monkeypatch):
    """Smoke test of the parse-time defence-in-depth without making
    a real network call. Mocks ``genai_client`` to return a response
    where the model said verdict=confirmed but subject_match=False;
    verify_item should override to verdict=unconfirmed."""
    from unittest.mock import AsyncMock, MagicMock

    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "verdict": "confirmed",
        "subject_match": False,
        "category_correct": True,
        "evidence": "story is about a different entity sharing the name",
    })
    fake_resp.candidates = []  # no grounding chunks needed for this test

    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(return_value=fake_resp)

    monkeypatch.setattr(
        "app.services.grounded_extraction.item_verification.genai_client",
        lambda: fake_client,
    )

    from app.services.grounded_extraction.item_verification import verify_item

    async def _run():
        return await verify_item(
            {"title": "fake story"},
            context="A real company at HQ X with founders Y and Z",
            source_category="news",
        )
    vr = asyncio.run(_run())

    assert vr.verdict == "unconfirmed"
    assert vr.subject_match is False
    assert "different entity" in vr.evidence


def test_verify_item_keeps_confirmed_when_subject_matches(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "verdict": "confirmed",
        "subject_match": True,
        "category_correct": True,
        "evidence": "matches",
    })
    fake_resp.candidates = []

    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(return_value=fake_resp)

    monkeypatch.setattr(
        "app.services.grounded_extraction.item_verification.genai_client",
        lambda: fake_client,
    )

    from app.services.grounded_extraction.item_verification import verify_item

    async def _run():
        return await verify_item(
            {"title": "real story"},
            context="ctx",
            source_category="news",
        )
    vr = asyncio.run(_run())
    assert vr.verdict == "confirmed"
    assert vr.subject_match is True


# ── refine_jsonl orchestrator (with a fake LedgerStorage) ────────────


def test_refine_jsonl_no_pending_records_is_noop():
    """When there are no `_refinement_status: pending` records, the
    orchestrator is a fast no-op — it doesn't even touch the network."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "news.jsonl"
            path.write_text(
                json.dumps({"id": "1", "title": "already-finalized",
                            "_refinement_status": "finalized"}) + "\n",
                encoding="utf-8",
            )
            storage = LedgerStorage(
                jsonl_path=lambda _sid, _cat: path,
                write_lock=_fake_lock,
            )
            return await refine_jsonl(
                "subj", "news", context="x", storage=storage,
            )
    result = asyncio.run(_run())
    assert result == {"refined": 0, "kept": 0, "dropped": 0}


def test_refine_jsonl_missing_file_is_noop():
    """When the ledger file doesn't exist, return zero-counts without
    touching anything."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "news.jsonl"  # never written
            storage = LedgerStorage(
                jsonl_path=lambda _sid, _cat: path,
                write_lock=_fake_lock,
            )
            return await refine_jsonl(
                "subj", "news", context="x", storage=storage,
            )
    result = asyncio.run(_run())
    assert result == {"refined": 0, "kept": 0, "dropped": 0}


# ── apply_url_fallback candidate ordering ────────────────────────────


def test_apply_url_fallback_no_anchor_falls_back_to_search():
    """Item with no LLM URL, no chunk URLs, no field URL → falls back
    to a Google search URL on the title (since title is set, the
    search-url helper has something to query)."""
    async def _run():
        item: dict[str, Any] = {"title": "An item"}
        await apply_url_fallback(item)
        return item
    item = asyncio.run(_run())
    assert item.get("_url_source") == "google_search"
    assert item.get("_url_status") == "fallback_search"
    assert (item.get("url") or "").startswith("https://www.google.com/search")


def test_apply_url_fallback_empty_item_has_no_anchor():
    """No title, no URL → no_anchor."""
    async def _run():
        item: dict[str, Any] = {}
        await apply_url_fallback(item)
        return item
    item = asyncio.run(_run())
    assert item.get("_url_source") == "no_anchor"
    assert item.get("_url_status") == "no_anchor"


# ── _new_iso_id same-second escalation (regression for the lex-compare bug) ─


def test_new_iso_id_escalates_to_microsecond_within_same_second():
    """Three calls within the same wall-clock second must produce three
    distinct ids. The original lex-compare was buggy: after the second
    id picked up a microsecond suffix, a third bare-second id would
    lex-sort AFTER it (because 'Z' > '-') and pass the monotonicity
    guard, colliding with the first id. Fixed by comparing per-second
    prefix instead of full lex order."""
    from app.services.portfolio import file_utils as portfolio_fu

    portfolio_fu._last_id_seen.clear()
    ids = [portfolio_fu._new_iso_id("test-eid") for _ in range(5)]
    assert len(ids) == len(set(ids)), (
        f"_new_iso_id produced duplicates: {ids}"
    )

    # Same regression test on the academic side — both file_utils share
    # the bug shape and the fix.
    from app.services.academic import file_utils as academic_fu

    academic_fu._last_id_seen.clear()
    ids2 = [academic_fu._new_iso_id("test-sid") for _ in range(5)]
    assert len(ids2) == len(set(ids2)), (
        f"academic _new_iso_id produced duplicates: {ids2}"
    )


__all__: list[str] = []
