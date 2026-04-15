"""Cascade behaviour: accepting a discrepancy that mutates an array row's
selector key rewrites pending-sibling field_paths so their next accept still
targets the same (renamed) row."""

from __future__ import annotations

from app.services.fact_discrepancies import (
    accept_discrepancy,
    append_discrepancy,
    list_discrepancies,
)


def _seed(metadata: dict, field_path: str, proposed) -> str:
    """Helper to seed a pending discrepancy and return its id."""
    committed = append_discrepancy(metadata, {
        "detected_by": "legal_review",
        "field_path": field_path,
        "current_value": None,
        "proposed_value": proposed,
        "source_doc_node_id": "n1",
        "confidence": "high",
        "rationale": "test",
    })
    return committed["id"]


def test_accepting_selector_key_rewrites_sibling_paths() -> None:
    """Accept fund_id rename FIRST; siblings should still update the same row."""
    metadata = {
        "_positions": [{
            "fund_id": "taihill_v3_lp",
            "invested_amount": 500000,
            "round_at_entry": "Series Pre-A",
            "currency": "USD",
        }],
        "_fact_discrepancies": [],
    }
    a = _seed(metadata,
             "_positions[fund_id=taihill_v3_lp].fund_id",
             "taihill_venture_seed_iii_lp")
    b = _seed(metadata,
             "_positions[fund_id=taihill_v3_lp].invested_amount",
             300000)
    c = _seed(metadata,
             "_positions[fund_id=taihill_v3_lp].round_at_entry",
             "Series Angel-1")

    accept_discrepancy(metadata, a)

    # Siblings should now reference the new selector value.
    remaining = list_discrepancies(metadata, "pending")
    paths = sorted(d["field_path"] for d in remaining)
    assert paths == [
        "_positions[fund_id=taihill_venture_seed_iii_lp].invested_amount",
        "_positions[fund_id=taihill_venture_seed_iii_lp].round_at_entry",
    ], paths

    # Accepting them now updates the renamed row in-place (no phantom stubs).
    accept_discrepancy(metadata, b)
    accept_discrepancy(metadata, c)
    assert len(metadata["_positions"]) == 1
    pos = metadata["_positions"][0]
    assert pos["fund_id"] == "taihill_venture_seed_iii_lp"
    assert pos["invested_amount"] == 300000
    assert pos["round_at_entry"] == "Series Angel-1"
    assert pos["currency"] == "USD"  # untouched


def test_shorthand_selector_also_rewrites() -> None:
    """Siblings using the shorthand selector ``_positions[X]`` (no key= prefix)
    should rewrite too — _DEFAULT_ARRAY_KEYS maps _positions → fund_id."""
    metadata = {
        "_positions": [{"fund_id": "v3", "invested_amount": 100}],
        "_fact_discrepancies": [],
    }
    a = _seed(metadata, "_positions[v3].fund_id", "venture_seed_iii")
    b = _seed(metadata, "_positions[v3].invested_amount", 250)

    accept_discrepancy(metadata, a)
    remaining = list_discrepancies(metadata, "pending")
    assert remaining[0]["field_path"] == "_positions[venture_seed_iii].invested_amount"

    accept_discrepancy(metadata, b)
    assert len(metadata["_positions"]) == 1
    assert metadata["_positions"][0]["fund_id"] == "venture_seed_iii"
    assert metadata["_positions"][0]["invested_amount"] == 250


def test_non_selector_accept_leaves_siblings_untouched() -> None:
    """Accepting a leaf that's NOT the selector key must not touch siblings."""
    metadata = {
        "_positions": [{"fund_id": "x", "invested_amount": 100, "round_at_entry": "A"}],
        "_fact_discrepancies": [],
    }
    _seed(metadata, "_positions[fund_id=x].invested_amount", 200)
    c = _seed(metadata, "_positions[fund_id=x].round_at_entry", "B")
    b = list_discrepancies(metadata, "pending")[0]["id"]

    accept_discrepancy(metadata, b)
    # c's path must be unchanged.
    remaining = list_discrepancies(metadata, "pending")
    assert len(remaining) == 1
    assert remaining[0]["id"] == c
    assert remaining[0]["field_path"] == "_positions[fund_id=x].round_at_entry"


def test_cascade_only_rewrites_pending_not_accepted() -> None:
    """Already-accepted siblings must not be rewritten retroactively."""
    metadata = {
        "_positions": [{"fund_id": "x", "invested_amount": 100}],
        "_fact_discrepancies": [],
    }
    b = _seed(metadata, "_positions[fund_id=x].invested_amount", 200)
    accept_discrepancy(metadata, b)
    # b is now accepted. Seed a rename and accept it.
    a = _seed(metadata, "_positions[fund_id=x].fund_id", "y")
    accept_discrepancy(metadata, a)

    # b's field_path should remain historical (pre-rename).
    all_disc = list_discrepancies(metadata, "all")
    by_id = {d["id"]: d for d in all_disc}
    assert by_id[b]["field_path"] == "_positions[fund_id=x].invested_amount"
