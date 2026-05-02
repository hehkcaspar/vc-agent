"""Shared grounded-extraction pipeline.

The verify → triage → URL-fallback flow used by both academic scholar
tracking and portfolio entity tracking. Originally lived under
``services/academic/`` but the pipeline isn't scholar-specific — it
works on any "grounded-search produced this item, prove it's about our
subject" use case. Moved here 2026-05-02 to:

  1. Stop portfolio code reaching into academic internals.
  2. Make verify-prompt / triage-rule changes apply once, not twice.
  3. Give portfolio fire-and-forget refinement (matches academic UX).

Public API:

  from app.services.grounded_extraction import (
      verify_item, VerifyResult,
      triage, TriageDecision,
      apply_url_fallback,
      refine_jsonl,            # entity-agnostic refinement orchestrator
      LedgerStorage,           # protocol both academic + portfolio implement
  )

The leaf modules (``item_verification``, ``item_triage``,
``url_fallback``) are also re-exported under ``services/academic/`` as
thin shims for backward compatibility — existing internal imports keep
working unchanged.
"""

from .item_triage import TriageDecision, triage
from .item_verification import (
    DEFAULT_VERIFY_MODEL,
    VerifyResult,
    verify_item,
)
from .refinement import refine_jsonl
from .storage import LedgerStorage, noop_tombstone
from .url_fallback import VERTEX_REDIRECT_HOST, apply_url_fallback

__all__ = [
    "verify_item",
    "VerifyResult",
    "DEFAULT_VERIFY_MODEL",
    "triage",
    "TriageDecision",
    "apply_url_fallback",
    "VERTEX_REDIRECT_HOST",
    "refine_jsonl",
    "LedgerStorage",
    "noop_tombstone",
]
