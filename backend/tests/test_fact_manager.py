"""Unit tests for fact_manager + hard_fact_catalog — pure-dict semantics.

DB-bound behaviour (record_fact, get_provenance) is exercised via the
end-to-end extract_info / legal_review retrofit tests — here we cover the
pure functions that don't need a session.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.services.fact_manager import (
    extract_hard_facts_from_payload,
    promote_proposed_to_active,
    read_flat_value,
    record_fact_in_metadata,
    record_proposed_for_discrepancy,
    reject_proposed,
)
from app.services.hard_fact_catalog import (
    evidence_tier,
    is_hard_fact,
)
from app.services.fact_ledger_schema import FactSource


# ---------------------------------------------------------------------------
# is_hard_fact
# ---------------------------------------------------------------------------


class TestIsHardFact:
    def test_scalar_identity_fields(self) -> None:
        assert is_hard_fact("website")
        assert is_hard_fact("company_name")
        assert is_hard_fact("legal_name")
        assert is_hard_fact("founded_date")
        assert is_hard_fact("incorporation_jurisdiction")

    def test_founder_verifiable_fields(self) -> None:
        assert is_hard_fact("founders[name=Joe Dow].name")
        assert is_hard_fact("founders[name=Joe Dow].title")
        assert is_hard_fact("founders[name=Joe Dow].linkedin_url")

    def test_founder_narrative_is_not_hard(self) -> None:
        # `background` is self-reported narrative → soft.
        assert not is_hard_fact("founders[name=Joe Dow].background")

    def test_key_team_mirror_founders(self) -> None:
        assert is_hard_fact("key_team[name=Na Li].name")
        assert is_hard_fact("key_team[name=Na Li].title")
        assert not is_hard_fact("key_team[name=Na Li].background")

    def test_prior_rounds_are_all_hard(self) -> None:
        # Prefix match: every leaf under a prior_rounds entry is hard.
        assert is_hard_fact("prior_rounds[round_name=Seed].amount")
        assert is_hard_fact("prior_rounds[round_name=Seed].lead_investor")
        assert is_hard_fact(
            "prior_rounds[round_name=Series A].safe_terms.valuation_cap"
        )
        assert is_hard_fact(
            "prior_rounds[round_name=Series A].governance.board_composition"
        )

    def test_positions_are_all_hard(self) -> None:
        assert is_hard_fact("_positions[fund_id=taihill_v3].amount")
        assert is_hard_fact(
            "_positions[fund_id=taihill_v3].valuation_at_investment"
        )

    def test_soft_claims_are_not_hard(self) -> None:
        assert not is_hard_fact("one_liner")
        assert not is_hard_fact("description")
        assert not is_hard_fact("industry_tags")
        assert not is_hard_fact("business_model")
        assert not is_hard_fact("priority_indicators")
        assert not is_hard_fact("red_flags")
        assert not is_hard_fact("competitors")

    def test_malformed_path_returns_false(self) -> None:
        assert not is_hard_fact("")
        assert not is_hard_fact("...")
        assert not is_hard_fact("founders[name=]")

    def test_raise_terms_are_hard(self) -> None:
        assert is_hard_fact("raise_amount")
        assert is_hard_fact("raise_currency")
        assert is_hard_fact("valuation_cap")
        assert is_hard_fact("pre_money_valuation")


# ---------------------------------------------------------------------------
# evidence_tier
# ---------------------------------------------------------------------------


class TestEvidenceTier:
    def test_ranking_is_correct(self) -> None:
        assert evidence_tier("cap_table") > evidence_tier("legal_doc")
        assert evidence_tier("legal_doc") > evidence_tier("upload")
        assert evidence_tier("upload") > evidence_tier("web")
        assert evidence_tier("user") > evidence_tier("upload")

    def test_unknown_source_is_weak(self) -> None:
        assert evidence_tier("never_heard_of_it") < evidence_tier("web")


# ---------------------------------------------------------------------------
# record_fact_in_metadata — core write semantics
# ---------------------------------------------------------------------------


def _fresh_metadata() -> Dict[str, Any]:
    return {
        "company_name": "Acme",
        "founders": [
            {"name": "Joe Dow", "title": "CEO", "background": "Ex-Google"},
        ],
    }


def _src(**overrides) -> FactSource:
    return FactSource(
        type=overrides.get("type", "upload"),
        ref=overrides.get("ref", "workspace://deck.pdf"),
        quote=overrides.get("quote"),
        preset=overrides.get("preset", "extract_info"),
        run_id=overrides.get("run_id", "run-001"),
    )


class TestRecordFact:
    def test_first_write_creates_active_entry(self) -> None:
        meta = _fresh_metadata()
        entry = record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CTO",
            source=_src(),
            confidence=0.9,
        )
        assert entry is not None
        assert entry.status == "active"
        assert entry.value == "CTO"
        assert entry.supersedes is None
        # Ledger has one entry at this path
        assert len(meta["_ledger"]) == 1
        # Flat field was updated
        assert read_flat_value(meta, "founders[name=Joe Dow].title") == "CTO"

    def test_soft_path_skipped(self) -> None:
        meta = _fresh_metadata()
        entry = record_fact_in_metadata(
            meta,
            fact_path="one_liner",
            value="A great company",
            source=_src(),
        )
        assert entry is None
        assert meta.get("_ledger", []) == []
        # Soft claim not touched
        assert "one_liner" not in meta

    def test_idempotent_noop_same_value_same_source(self) -> None:
        meta = _fresh_metadata()
        record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source=_src(type="upload", ref="workspace://deck.pdf"),
        )
        # Second identical write: no new ledger entry
        second = record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source=_src(type="upload", ref="workspace://deck.pdf"),
        )
        assert second is not None
        assert len(meta["_ledger"]) == 1

    def test_supersession_on_value_change(self) -> None:
        meta = _fresh_metadata()
        first = record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CTO",
            source=_src(),
        )
        assert first is not None
        second = record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CEO",
            source=_src(ref="workspace://linkedin.pdf"),
        )
        assert second is not None
        assert second.status == "active"
        assert second.supersedes == first.entry_id

        # Ledger has both; first is now superseded.
        ledger = meta["_ledger"]
        assert len(ledger) == 2
        assert ledger[0]["status"] == "superseded"
        assert ledger[1]["status"] == "active"

        # Flat field reflects the latest
        assert read_flat_value(
            meta, "founders[name=Joe Dow].title"
        ) == "CEO"

    def test_corroboration_same_value_different_source(self) -> None:
        meta = _fresh_metadata()
        record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source=_src(type="upload", ref="workspace://deck.pdf"),
        )
        corrob = record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source=_src(type="web", ref="https://acme.com"),
        )
        assert corrob is not None
        assert corrob.status == "verified"
        assert len(meta["_ledger"]) == 2
        # First entry stays active (same value); second is the verification.
        assert meta["_ledger"][0]["status"] == "active"
        assert meta["_ledger"][1]["status"] == "verified"

    def test_corroboration_does_not_set_supersedes(self) -> None:
        """Regression: `verified` entries should NOT point supersedes at
        the still-active prior entry — they coexist as independent attestations."""
        meta = _fresh_metadata()
        record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source=_src(type="upload", ref="workspace://deck.pdf"),
        )
        corrob = record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source=_src(type="web", ref="https://acme.com"),
        )
        assert corrob is not None
        assert corrob.supersedes is None, (
            "verified corroboration must not supersede — the prior entry "
            "stays active"
        )

    def test_proposed_status_does_not_touch_flat_field(self) -> None:
        meta = _fresh_metadata()
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CTO",
            source=_src(),
            status="active",
        )
        # Propose a different value from a web source — awaits user adjudication
        proposed = record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="Advisor",
            source=_src(type="web", ref="https://linkedin.com/in/joedow"),
            status="proposed",
        )
        assert proposed is not None
        assert proposed.status == "proposed"
        # Flat field NOT updated
        assert read_flat_value(
            meta, "founders[name=Joe Dow].title"
        ) == "CTO"
        # Prior active entry NOT marked superseded
        assert meta["_ledger"][0]["status"] == "active"

    def test_nested_term_block_writes_through(self) -> None:
        meta: Dict[str, Any] = {}
        entry = record_fact_in_metadata(
            meta,
            fact_path=(
                "prior_rounds[round_name=Series A]"
                ".safe_terms.valuation_cap"
            ),
            value="15000000",
            source=_src(type="legal_doc", ref="workspace://safe.pdf"),
            confidence=0.95,
        )
        assert entry is not None
        assert read_flat_value(
            meta,
            "prior_rounds[round_name=Series A].safe_terms.valuation_cap",
        ) == "15000000"
        # Prior_rounds array row was lazily created with the selector key
        rounds = meta["prior_rounds"]
        assert rounds[0]["round_name"] == "Series A"
        assert rounds[0]["safe_terms"]["valuation_cap"] == "15000000"

    def test_position_row_lazy_create(self) -> None:
        meta: Dict[str, Any] = {}
        record_fact_in_metadata(
            meta,
            fact_path="_positions[fund_id=taihill_v3].amount",
            value=500000,
            source=_src(type="cap_table", ref="workspace://captable.xlsx"),
        )
        record_fact_in_metadata(
            meta,
            fact_path="_positions[fund_id=taihill_v3].valuation_at_investment",
            value=12000000,
            source=_src(type="cap_table", ref="workspace://captable.xlsx"),
        )
        positions = meta["_positions"]
        assert len(positions) == 1
        assert positions[0]["fund_id"] == "taihill_v3"
        assert positions[0]["amount"] == 500000
        assert positions[0]["valuation_at_investment"] == 12000000
        # Each write appended a ledger entry.
        assert len(meta["_ledger"]) == 2

    def test_record_from_dict_source_payload(self) -> None:
        meta = _fresh_metadata()
        entry = record_fact_in_metadata(
            meta,
            fact_path="website",
            value="https://acme.com",
            source={
                "type": "upload",
                "ref": "workspace://deck.pdf",
                "quote": "Visit us at acme.com",
                "preset": "extract_info",
            },
        )
        assert entry is not None
        assert entry.source.type == "upload"
        assert entry.source.quote == "Visit us at acme.com"


# ---------------------------------------------------------------------------
# read_flat_value
# ---------------------------------------------------------------------------


class TestReadFlatValue:
    def test_reads_existing_field(self) -> None:
        meta = _fresh_metadata()
        assert read_flat_value(meta, "company_name") == "Acme"
        assert (
            read_flat_value(meta, "founders[name=Joe Dow].title") == "CEO"
        )

    def test_missing_path_returns_none(self) -> None:
        meta = _fresh_metadata()
        assert read_flat_value(meta, "founders[name=Ghost].title") is None
        assert read_flat_value(meta, "nonexistent") is None

    def test_returns_deep_copy(self) -> None:
        meta: Dict[str, Any] = {
            "prior_rounds": [{"round_name": "Seed", "amount": "$2M"}]
        }
        got = read_flat_value(meta, "prior_rounds[round_name=Seed]")
        assert isinstance(got, dict)
        got["amount"] = "MUTATED"
        # Original unchanged
        assert meta["prior_rounds"][0]["amount"] == "$2M"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_existing_investors_prefix_is_hard(self) -> None:
        # existing_investors is a list of strings — the prefix catches any
        # path under it (rare but allowed).
        assert is_hard_fact("existing_investors")

    def test_ledger_survives_multiple_rewrites_same_path(self) -> None:
        meta = _fresh_metadata()
        # Burst of writes at the same path with different values
        values = ["A", "B", "C", "D"]
        for i, v in enumerate(values):
            record_fact_in_metadata(
                meta,
                fact_path="website",
                value=f"https://{v.lower()}.com",
                source=_src(ref=f"workspace://doc-{i}.pdf"),
            )
        # Ledger grows monotonically
        assert len(meta["_ledger"]) == 4
        # Only the last is active; the rest superseded.
        statuses = [e["status"] for e in meta["_ledger"]]
        assert statuses == ["superseded", "superseded", "superseded", "active"]
        # Flat reflects last
        assert meta["website"] == "https://d.com"

    def test_discrepancy_shim_full_lifecycle(self) -> None:
        """propose → accept: proposed entry flips to active and supersedes prior."""
        meta = _fresh_metadata()
        # Agent recorded a hard fact from the deck.
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CEO",
            source=_src(type="upload", ref="workspace://deck.pdf"),
        )
        assert meta["founders"][0]["title"] == "CEO"

        # Later: LinkedIn contradicts; agent surfaces a discrepancy.
        proposed = record_proposed_for_discrepancy(
            meta,
            discrepancy_id="disc-001",
            fact_path="founders[name=Joe Dow].title",
            proposed_value="Advisor",
            source=_src(type="web", ref="https://linkedin.com/in/joedow"),
            confidence=0.7,
        )
        assert proposed is not None
        assert proposed.status == "proposed"
        assert proposed.linked_discrepancy_id == "disc-001"
        # Flat field NOT touched by proposal alone.
        assert meta["founders"][0]["title"] == "CEO"

        # User accepts the discrepancy.
        promoted = promote_proposed_to_active(meta, "disc-001")
        assert promoted is not None
        assert promoted.status == "active"
        # Prior active entry superseded.
        statuses_by_path: Dict[str, list] = {}
        for e in meta["_ledger"]:
            statuses_by_path.setdefault(e["fact_path"], []).append(e["status"])
        assert statuses_by_path["founders[name=Joe Dow].title"] == [
            "superseded", "active"
        ]
        # Flat field now reflects LinkedIn.
        assert meta["founders"][0]["title"] == "Advisor"

    def test_discrepancy_shim_reject_path(self) -> None:
        meta = _fresh_metadata()
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CEO",
            source=_src(type="upload"),
        )
        record_proposed_for_discrepancy(
            meta,
            discrepancy_id="disc-002",
            fact_path="founders[name=Joe Dow].title",
            proposed_value="Chairman",
            source=_src(type="web"),
        )
        # Reject the proposal.
        rejected = reject_proposed(meta, "disc-002")
        assert rejected is not None
        assert rejected.status == "rejected"
        # Flat field stayed on the deck value.
        assert meta["founders"][0]["title"] == "CEO"

    def test_promote_returns_none_for_pre_shim_discrepancy(self) -> None:
        """Discrepancies filed before the shim have no ledger mirror."""
        meta = _fresh_metadata()
        promoted = promote_proposed_to_active(meta, "never-seen-before")
        assert promoted is None

    def test_soft_path_discrepancy_not_mirrored(self) -> None:
        """Soft paths in _fact_discrepancies[] don't get a ledger entry."""
        meta = _fresh_metadata()
        mirrored = record_proposed_for_discrepancy(
            meta,
            discrepancy_id="disc-003",
            fact_path="one_liner",
            proposed_value="new tagline",
            source=_src(type="upload"),
        )
        assert mirrored is None
        assert meta.get("_ledger", []) == []

    def test_new_active_retires_matching_proposed(self) -> None:
        """When record_fact writes a new active value that matches an open
        proposal, the proposal flips to 'superseded' — not left dangling."""
        meta = _fresh_metadata()
        # Prior active from the deck.
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CEO",
            source=_src(type="upload"),
        )
        # Agent raised a discrepancy proposing "CTO" from web.
        record_proposed_for_discrepancy(
            meta,
            discrepancy_id="disc-retire-match",
            fact_path="founders[name=Joe Dow].title",
            proposed_value="CTO",
            source=_src(type="web"),
        )
        # A subsequent extract_info run lands on "CTO" directly (e.g. deck
        # updated). That write should retire the proposal.
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CTO",
            source=_src(type="upload", ref="workspace://deck_v2.pdf"),
        )
        statuses = [
            e["status"] for e in meta["_ledger"]
            if e["fact_path"] == "founders[name=Joe Dow].title"
        ]
        # CEO:superseded, CTO(proposed):superseded, CTO(active):active
        assert statuses.count("active") == 1
        assert statuses.count("proposed") == 0, (
            "matching-value proposal should be retired when the new active "
            "captures the same claim"
        )
        assert statuses.count("superseded") == 2

    def test_new_active_rejects_nonmatching_proposed(self) -> None:
        """When record_fact writes a new active value that DIFFERS from an
        open proposal, the proposal flips to 'rejected' — the claim is stale."""
        meta = _fresh_metadata()
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="CEO",
            source=_src(type="upload"),
        )
        record_proposed_for_discrepancy(
            meta,
            discrepancy_id="disc-retire-nomatch",
            fact_path="founders[name=Joe Dow].title",
            proposed_value="Chairman",
            source=_src(type="web"),
        )
        # Agent lands on an unrelated new value.
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Joe Dow].title",
            value="Co-Founder",
            source=_src(type="upload", ref="workspace://announcement.pdf"),
        )
        ledger = [
            e for e in meta["_ledger"]
            if e["fact_path"] == "founders[name=Joe Dow].title"
        ]
        # The "Chairman" proposal should be flipped to rejected.
        chairman = next((e for e in ledger if e["value"] == "Chairman"), None)
        assert chairman is not None
        assert chairman["status"] == "rejected"

    def test_extract_hard_facts_from_real_elastro_payload(self) -> None:
        """Validate the flattener against a real-shape extract_info payload."""
        payload = {
            "company_name": "Elastro",
            "legal_name": "Elastro, Inc.",
            "one_liner": "AI-controlled electronics…",    # soft
            "description": "Spun out of Harvard…",         # soft
            "industry_tags": ["BCI", "AI"],               # soft
            "business_model": "Device + SaaS",             # soft
            "hq_location": "Watertown, MA",
            "website": "www.elastro.com",
            "founded_date": "2025-01-02",
            "incorporation_jurisdiction": "Delaware",
            "incorporation_entity_type": "Corporation",
            "founders": [
                {"name": "Michael Schrader", "title": "Co-Founder & CEO",
                 "background": "Ex-Vaxess CEO"},
                {"name": "Ren Liu", "title": "Co-Founder & VP R&D",
                 "linkedin_url": None, "background": "…"},
            ],
            "key_team": [
                {"name": "Na Li", "title": "Advisor", "background": "Harvard Prof"},
            ],
            "investment_stage": "pre_seed",
            "raise_amount": "$1M - $1.5M",
            "raise_currency": "USD",
            "existing_investors": ["Charles Hierschler", "Ulu Venture"],
            "prior_rounds": [],
            "priority_indicators": ["Elite founding team"],  # soft
            "red_flags": [],                                  # soft
            "competitors": [],                                # soft
        }
        facts = extract_hard_facts_from_payload(payload)
        paths = {p for p, _ in facts}

        # Hard fields present
        assert "website" in paths
        assert "company_name" in paths
        assert "legal_name" in paths
        assert "hq_location" in paths
        assert "founded_date" in paths
        assert "incorporation_jurisdiction" in paths
        assert "incorporation_entity_type" in paths
        assert "investment_stage" in paths
        assert "raise_amount" in paths
        assert "raise_currency" in paths
        assert "founders[name=Michael Schrader].title" in paths
        assert "founders[name=Ren Liu].title" in paths
        assert "key_team[name=Na Li].title" in paths
        assert "existing_investors" in paths

        # Soft fields absent
        assert "one_liner" not in paths
        assert "description" not in paths
        assert "industry_tags" not in paths
        assert "business_model" not in paths
        assert "priority_indicators" not in paths
        assert "red_flags" not in paths
        assert "competitors" not in paths
        assert "founders[name=Michael Schrader].background" not in paths

        # Empty-ish values skipped
        assert "founders[name=Ren Liu].linkedin_url" not in paths

    def test_extract_hard_facts_with_prior_round_term_blocks(self) -> None:
        payload = {
            "prior_rounds": [
                {
                    "round_name": "Series Seed",
                    "amount": "$2M",
                    "currency": "USD",
                    "lead_investor": "Acme Ventures",
                    "effective_date": "2025-06-15",
                    "instrument_type": "safe",
                    "safe_terms": {
                        "valuation_cap": 15_000_000,
                        "discount_rate": None,   # empty — skip
                        "mfn": True,
                    },
                    "governance": {
                        "board_seats": 1,
                    },
                },
            ],
        }
        facts = extract_hard_facts_from_payload(payload)
        paths = {p for p, _ in facts}
        assert "prior_rounds[round_name=Series Seed].amount" in paths
        assert "prior_rounds[round_name=Series Seed].currency" in paths
        assert "prior_rounds[round_name=Series Seed].lead_investor" in paths
        assert "prior_rounds[round_name=Series Seed].effective_date" in paths
        assert "prior_rounds[round_name=Series Seed].instrument_type" in paths
        assert (
            "prior_rounds[round_name=Series Seed].safe_terms.valuation_cap" in paths
        )
        assert "prior_rounds[round_name=Series Seed].safe_terms.mfn" in paths
        assert (
            "prior_rounds[round_name=Series Seed].governance.board_seats" in paths
        )
        # Empty leaf skipped
        assert (
            "prior_rounds[round_name=Series Seed].safe_terms.discount_rate"
            not in paths
        )

    def test_extract_hard_facts_positions(self) -> None:
        payload = {
            "_positions": [
                {
                    "fund_id": "taihill_v3",
                    "amount": 500_000,
                    "valuation_at_investment": 12_000_000,
                    "date": "2025-09-01",
                },
            ],
        }
        facts = extract_hard_facts_from_payload(payload)
        paths = {p for p, _ in facts}
        assert "_positions[fund_id=taihill_v3].amount" in paths
        assert "_positions[fund_id=taihill_v3].valuation_at_investment" in paths
        assert "_positions[fund_id=taihill_v3].date" in paths

    def test_multiple_founders_isolated(self) -> None:
        meta: Dict[str, Any] = {}
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Alice].title",
            value="CEO",
            source=_src(),
        )
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Bob].title",
            value="CTO",
            source=_src(),
        )
        # Superseding Alice's title does NOT touch Bob's.
        record_fact_in_metadata(
            meta,
            fact_path="founders[name=Alice].title",
            value="Chair",
            source=_src(ref="workspace://board-doc.pdf"),
        )
        by_name = {f["name"]: f for f in meta["founders"]}
        assert by_name["Alice"]["title"] == "Chair"
        assert by_name["Bob"]["title"] == "CTO"
        # 3 ledger entries, Bob's still active.
        statuses_by_path = {}
        for e in meta["_ledger"]:
            statuses_by_path.setdefault(e["fact_path"], []).append(e["status"])
        assert statuses_by_path["founders[name=Alice].title"] == [
            "superseded", "active"
        ]
        assert statuses_by_path["founders[name=Bob].title"] == ["active"]
