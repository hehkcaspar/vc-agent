"""Regression tests for merge_prior_round_facts — legacy-shape handling."""

from __future__ import annotations

from app.services.legal_review_facts import merge_prior_round_facts


def test_legacy_round_key_merges_with_new_round_name() -> None:
    """Legacy entries with `round` must migrate to `round_name` and merge
    with incoming deep rows, not create duplicates."""
    existing = [
        {"round": "Series Pre-A", "amount": "$6M", "date": "2026-01-19",
         "lead_investor": "Future Capital"},
        {"round": "Series Angel", "amount": "$2M", "date": "2025-01-24"},
    ]
    incoming = [
        {
            "round_name": "Series Pre-A",
            "instrument_type": "priced_round",
            "scenario": "retrospective",
            "priced_round_terms": {"liquidation_preference_multiple": "1x"},
            "company_terms": {"new_money_amount": "6000000"},
        },
        {
            "round_name": "Series Pre-A+",
            "instrument_type": "priced_round",
            "scenario": "new_investment",
            "priced_round_terms": {"liquidation_preference_multiple": "1x"},
        },
    ]
    merged = merge_prior_round_facts(existing, incoming)

    by_name = {r["round_name"]: r for r in merged}
    assert set(by_name) == {"Series Pre-A", "Series Angel", "Series Pre-A+"}

    # Legacy + deep join into one row
    pa = by_name["Series Pre-A"]
    assert pa["lead_investor"] == "Future Capital"       # preserved from legacy
    assert pa["amount"] == "$6M"                          # preserved
    assert pa["effective_date"] == "2026-01-19"           # migrated from `date`
    assert pa["priced_round_terms"]["liquidation_preference_multiple"] == "1x"
    assert pa["company_terms"]["new_money_amount"] == "6000000"

    # Untouched legacy entry gets migrated
    angel = by_name["Series Angel"]
    assert angel["effective_date"] == "2025-01-24"
    assert angel["amount"] == "$2M"

    # No duplicate round_name
    names = [r["round_name"] for r in merged]
    assert len(names) == len(set(names)), names


def test_empty_existing_with_incoming() -> None:
    merged = merge_prior_round_facts(
        None, [{"round_name": "Series A", "amount": "$5M"}],
    )
    assert len(merged) == 1
    assert merged[0]["round_name"] == "Series A"


def test_empty_incoming_preserves_existing() -> None:
    existing = [{"round": "Series A", "amount": "$5M"}]
    merged = merge_prior_round_facts(existing, [])
    assert len(merged) == 1
    assert merged[0]["round_name"] == "Series A"
    assert merged[0]["amount"] == "$5M"


def test_deep_merge_preserves_nested_fields() -> None:
    """Nested term blocks from existing are preserved when incoming has a
    different nested key (e.g., existing has safe_terms, incoming has
    priced_round_terms for the same round)."""
    existing = [{
        "round_name": "Series Seed",
        "safe_terms": {"valuation_cap": "$10M"},
    }]
    incoming = [{
        "round_name": "Series Seed",
        "priced_round_terms": {"liquidation_preference_multiple": "1x"},
    }]
    merged = merge_prior_round_facts(existing, incoming)
    assert len(merged) == 1
    r = merged[0]
    assert r["safe_terms"]["valuation_cap"] == "$10M"
    assert r["priced_round_terms"]["liquidation_preference_multiple"] == "1x"
