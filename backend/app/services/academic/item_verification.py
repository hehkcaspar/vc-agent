"""Backward-compat shim — the canonical module moved to
``app.services.grounded_extraction.item_verification`` on 2026-05-02
(Tier 2 refactor, see ``services/grounded_extraction/__init__.py``).

Existing internal imports keep working unchanged via this re-export.
New code should import from ``grounded_extraction`` directly.
"""
from app.services.grounded_extraction.item_verification import (
    DEFAULT_VERIFY_MODEL,
    VerifyResult,
    verify_item,
)

__all__ = ["verify_item", "VerifyResult", "DEFAULT_VERIFY_MODEL"]
