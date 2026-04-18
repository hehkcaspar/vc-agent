"""Unit tests for ``workspace_tools._infer_source_type``.

This classifier picks the fact-ledger source tier (``upload`` / ``legal_doc``
/ ``cap_table``) for each workspace path that lands as a discrepancy source.
Heuristics drive evidence-tier comparisons downstream, so the matching rules
need to err on the side of *upload* (generic) rather than falsely promoting
a deck to ``legal_doc`` tier.
"""

from __future__ import annotations

from app.services.workspace_tools import _infer_source_type


class TestInferSourceType:
    def test_cap_table_by_folder(self) -> None:
        assert _infer_source_type(
            "Data Room/Cap Table/captable.xlsx",
        ) == "cap_table"

    def test_cap_table_by_basename(self) -> None:
        assert _infer_source_type("data/captable_2025.xlsx") == "cap_table"
        assert _infer_source_type("cap-table-live.xlsx") == "cap_table"

    def test_legal_folder(self) -> None:
        assert _infer_source_type(
            "Data Room/Legal/SPA executed.pdf",
        ) == "legal_doc"
        assert _infer_source_type(
            "Data Room/Elastro Data Room/Legal/Side Letter.pdf",
        ) == "legal_doc"

    def test_legal_by_basename_prefix(self) -> None:
        assert _infer_source_type("Inbox/SPA - executed.pdf") == "legal_doc"
        assert _infer_source_type("SAFE - YC Post-Money.pdf") == "legal_doc"
        assert _infer_source_type(
            "Side Letter - Fund V.docx",
        ) == "legal_doc"

    def test_legal_numbered_prefix(self) -> None:
        # "1. SPA - executed.pdf" style — common in legal closing binders.
        assert _infer_source_type(
            "Data Room/Legal Binder/1. SPA - executed.pdf",
        ) == "legal_doc"

    def test_deck_with_safe_in_name_is_not_legal(self) -> None:
        # Regression for the earlier heuristic: a deck whose filename has
        # "safe" as a substring should NOT be promoted to legal_doc.
        assert _infer_source_type(
            "Data Room/Deck/Elastro Safe Mobility.pdf",
        ) == "upload"
        assert _infer_source_type(
            "Data Room/Product/unsafe_behavior_demo.pdf",
        ) == "upload"

    def test_generic_upload(self) -> None:
        assert _infer_source_type("Inbox/pitch.pdf") == "upload"
        assert _infer_source_type(
            "Data Room/Financials/MRR Cohort.xlsx",
        ) == "upload"

    def test_empty_path(self) -> None:
        assert _infer_source_type("") == "upload"
        assert _infer_source_type("   ") == "upload"
