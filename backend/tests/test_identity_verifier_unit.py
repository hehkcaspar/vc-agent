"""Pure-Python unit tests for identity verification primitives.

No network, no DB. Covers:
- `verify_ss_metrics` — the cheap numeric pre-filter, specifically
  the zero-metric edge case that caused the Hannah Ritchie bug.
- `identity_verifier` — LLM verdict routing with a patched
  `llm_client.generate_structured`: accept / reject / low-confidence.
- `rejected_identity` round-trip — `is_rejected`, `append_rejection`
  dedup, and `build_rejection_entry`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.academic.identity_verifier import (
    CONFIDENCE_VERIFIED_THRESHOLD,
    IdentityVerdict,
    IdentityVerifier,
    ScholarContext,
    append_rejection,
    build_rejection_entry,
    commit_label,
    is_rejected,
)
from app.services.academic.tool_utils import (
    HIGH_SIGNAL_IDENTITY_SOURCES,
    KNOWN_IDENTITY_SOURCES,
    verify_ss_metrics,
)


# ── verify_ss_metrics ───────────────────────────────────────────────


class TestVerifySsMetrics:
    """The numeric pre-filter. Hannah Ritchie's fix lives here."""

    def test_no_anchor_passes_any_candidate(self) -> None:
        # Without an anchor the cheap filter defers to the LLM.
        assert verify_ss_metrics(0, 0) is True
        assert verify_ss_metrics(100, 50_000) is True

    def test_weak_anchor_passes_through(self) -> None:
        # h=10 is exactly at the threshold — not "strong enough" to
        # reject on divergence alone.
        assert verify_ss_metrics(0, 0, expected_h_index=10) is True
        assert verify_ss_metrics(5, 500, expected_h_index=10) is True

    def test_strong_anchor_rejects_zero_h_index(self) -> None:
        """The Hannah Ritchie regression: zero h against h=77 is a mismatch."""
        assert verify_ss_metrics(0, 0, expected_h_index=77, expected_citations=45_000) is False

    def test_strong_anchor_rejects_zero_citations(self) -> None:
        # Some SS profiles report nonzero h but zero citations, which
        # is still incoherent against a strong citation anchor.
        assert verify_ss_metrics(10, 0, expected_h_index=12, expected_citations=50_000) is False

    def test_strong_anchor_rejects_wide_h_divergence(self) -> None:
        # Ratio 5/77 ≈ 0.06 < 0.3 threshold.
        assert verify_ss_metrics(5, 10_000, expected_h_index=77, expected_citations=45_000) is False

    def test_strong_anchor_accepts_nonzero_within_ratio(self) -> None:
        # Ratio 60/77 ≈ 0.78 > 0.3 — same person, slightly different index.
        assert verify_ss_metrics(60, 30_000, expected_h_index=77, expected_citations=45_000) is True

    def test_citation_anchor_skipped_when_below_threshold(self) -> None:
        # expected_citations <= 1000 means the citation gate doesn't fire.
        assert verify_ss_metrics(0, 0, expected_h_index=8, expected_citations=500) is True


# ── Known source sets ──────────────────────────────────────────────


class TestSourceSets:
    def test_known_sources_is_nonempty_and_immutable(self) -> None:
        assert isinstance(KNOWN_IDENTITY_SOURCES, frozenset)
        assert "google_scholar" in KNOWN_IDENTITY_SOURCES
        assert "semantic_scholar" in KNOWN_IDENTITY_SOURCES
        assert len(KNOWN_IDENTITY_SOURCES) == 10

    def test_high_signal_is_subset_of_known(self) -> None:
        assert HIGH_SIGNAL_IDENTITY_SOURCES.issubset(KNOWN_IDENTITY_SOURCES)
        assert "google_scholar" in HIGH_SIGNAL_IDENTITY_SOURCES
        assert "semantic_scholar" in HIGH_SIGNAL_IDENTITY_SOURCES
        assert "orcid" in HIGH_SIGNAL_IDENTITY_SOURCES
        assert "homepage" in HIGH_SIGNAL_IDENTITY_SOURCES
        assert "linkedin" not in HIGH_SIGNAL_IDENTITY_SOURCES


# ── rejected_identity round-trip ───────────────────────────────────


class TestRejectionList:
    def test_is_rejected_empty(self) -> None:
        assert is_rejected({}, "semantic_scholar", "123") is False

    def test_is_rejected_match(self) -> None:
        rejected = {"semantic_scholar": [{"id": "123", "reason": "test"}]}
        assert is_rejected(rejected, "semantic_scholar", "123") is True
        assert is_rejected(rejected, "semantic_scholar", "456") is False

    def test_is_rejected_other_source(self) -> None:
        rejected = {"google_scholar": [{"id": "abc"}]}
        assert is_rejected(rejected, "semantic_scholar", "abc") is False

    def test_append_rejection_dedupes(self) -> None:
        rejected: dict[str, list[dict[str, Any]]] = {}
        entry = {"id": "2071092470", "reason": "sleep researcher"}
        append_rejection(rejected, "semantic_scholar", entry)
        append_rejection(rejected, "semantic_scholar", entry)
        assert len(rejected["semantic_scholar"]) == 1

    def test_append_rejection_skips_entries_without_id(self) -> None:
        # Without a stable id we can't dedupe, so the helper drops it.
        rejected: dict[str, list[dict[str, Any]]] = {}
        append_rejection(rejected, "semantic_scholar", {"url": "x", "reason": "y"})
        assert rejected.get("semantic_scholar", []) == []

    def test_build_rejection_entry_shape(self) -> None:
        verdict = IdentityVerdict(
            match=False, confidence=0.1, reason="different person"
        )
        entry = build_rejection_entry({"id": "abc", "url": "https://x"}, verdict)
        assert entry["id"] == "abc"
        assert entry["url"] == "https://x"
        assert entry["reason"] == "different person"
        assert entry["rejected_by"] == "llm_verifier"
        assert "rejected_at" in entry  # timestamp present


# ── commit_label ────────────────────────────────────────────────────


class TestCommitLabel:
    def test_high_confidence_match_is_verified(self) -> None:
        verdict = IdentityVerdict(match=True, confidence=0.9, reason="clear match")
        conf, verified_by = commit_label(verdict, "semantic_scholar")
        assert conf == "verified"
        assert verified_by == "llm_verified:semantic_scholar"

    def test_threshold_boundary_is_verified(self) -> None:
        # Exactly at the threshold — should still count as verified.
        verdict = IdentityVerdict(
            match=True,
            confidence=CONFIDENCE_VERIFIED_THRESHOLD,
            reason="borderline",
        )
        conf, verified_by = commit_label(verdict, "google_scholar")
        assert conf == "verified"
        assert verified_by == "llm_verified:google_scholar"

    def test_low_confidence_match_is_flagged(self) -> None:
        verdict = IdentityVerdict(
            match=True,
            confidence=0.4,
            reason="thin evidence but name + field align",
        )
        conf, verified_by = commit_label(verdict, "orcid")
        assert conf == "low"
        assert verified_by == "llm_low_confidence:orcid"

    def test_commit_label_rejects_non_match(self) -> None:
        verdict = IdentityVerdict(match=False, confidence=0.1, reason="wrong person")
        with pytest.raises(ValueError):
            commit_label(verdict, "semantic_scholar")


# ── IdentityVerifier LLM routing ───────────────────────────────────


def _make_ctx() -> ScholarContext:
    return ScholarContext(
        name="Hannah Ritchie",
        aliases=[],
        affiliation_current="University of Oxford",
        affiliation_department="Oxford Martin School",
        research_areas=["climate change", "global development", "food systems"],
    )


def test_verifier_accepts_match() -> None:
    accept = IdentityVerdict(
        match=True, confidence=0.9, reason="Oxford climate researcher — matches."
    )
    mock = AsyncMock(return_value=accept)

    async def run() -> IdentityVerdict:
        verifier = IdentityVerifier(_make_ctx())
        return await verifier.verify(
            "semantic_scholar",
            {"id": "real_ss_id", "url": "https://semanticscholar.org/author/1"},
            {"top_papers": [{"title": "Greenhouse gas emissions"}]},
        )

    with patch(
        "app.services.academic.identity_verifier.generate_structured", mock
    ):
        verdict = asyncio.run(run())

    assert verdict.match is True
    assert verdict.confidence == 0.9
    mock.assert_awaited_once()


def test_verifier_rejects_mismatch() -> None:
    reject = IdentityVerdict(
        match=False,
        confidence=0.15,
        reason="Sleep-research papers, wrong field and h=0.",
    )
    mock = AsyncMock(return_value=reject)

    async def run() -> IdentityVerdict:
        verifier = IdentityVerifier(_make_ctx())
        return await verifier.verify(
            "semantic_scholar",
            {"id": "2071092470", "url": "https://semanticscholar.org/author/2071092470"},
            {"top_papers": [{"title": "Circadian biology and sleep"}]},
        )

    with patch(
        "app.services.academic.identity_verifier.generate_structured", mock
    ):
        verdict = asyncio.run(run())

    assert verdict.match is False
    assert "sleep" in verdict.reason.lower()


def test_verifier_caches_repeat_calls() -> None:
    hit = IdentityVerdict(match=True, confidence=0.8, reason="ok")
    mock = AsyncMock(return_value=hit)

    async def run() -> None:
        verifier = IdentityVerifier(_make_ctx())
        await verifier.verify(
            "semantic_scholar", {"id": "abc", "url": "x"}, {"x": 1}
        )
        await verifier.verify(
            "semantic_scholar", {"id": "abc", "url": "x"}, {"x": 2}
        )

    with patch(
        "app.services.academic.identity_verifier.generate_structured", mock
    ):
        asyncio.run(run())

    # Second call should come from cache — only one LLM invocation.
    assert mock.await_count == 1


def test_verifier_soft_fails_on_llm_error() -> None:
    """LLM errors must not crash the resolver — return a reject verdict."""
    mock = AsyncMock(side_effect=RuntimeError("API down"))

    async def run() -> IdentityVerdict:
        verifier = IdentityVerifier(_make_ctx())
        return await verifier.verify(
            "semantic_scholar", {"id": "abc", "url": "x"}, {}
        )

    with patch(
        "app.services.academic.identity_verifier.generate_structured", mock
    ):
        verdict = asyncio.run(run())

    assert verdict.match is False
    assert "llm_unavailable" in verdict.reason
