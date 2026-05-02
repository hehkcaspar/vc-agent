"""Backward-compat shim — the canonical module moved to
``app.services.grounded_extraction.item_triage`` on 2026-05-02
(Tier 2 refactor, see ``services/grounded_extraction/__init__.py``).

Existing internal imports keep working unchanged via this re-export.
New code should import from ``grounded_extraction`` directly.
"""
from app.services.grounded_extraction.item_triage import (
    Action,
    TriageDecision,
    triage,
)

__all__ = ["triage", "TriageDecision", "Action"]
