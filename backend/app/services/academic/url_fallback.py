"""Backward-compat shim — the canonical module moved to
``app.services.grounded_extraction.url_fallback`` on 2026-05-02
(Tier 2 refactor, see ``services/grounded_extraction/__init__.py``).

Existing internal imports keep working unchanged via this re-export.
New code should import from ``grounded_extraction`` directly.
"""
from app.services.grounded_extraction.url_fallback import (
    VERTEX_REDIRECT_HOST,
    apply_url_fallback,
)
from app.services.academic.llm_client import URL_FIELDS  # noqa: F401

__all__ = ["apply_url_fallback", "URL_FIELDS", "VERTEX_REDIRECT_HOST"]
