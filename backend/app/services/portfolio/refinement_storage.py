"""Portfolio-side ``LedgerStorage`` for the shared
``grounded_extraction.refine_jsonl`` orchestrator.

Mirrors ``services.academic.refinement.SCHOLAR_STORAGE``: maps
``(entity_id, category)`` to the dossier jsonl path, wraps
``entity_write_lock`` as the write-lock context manager, and supplies
no-op tombstone + no destinations (portfolio doesn't have a tombstone
ledger yet, and there are no cross-category routing destinations on the
portfolio side).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from app.services.grounded_extraction import LedgerStorage

from .file_utils import dossier_path
from .locks import entity_write_lock


def _entity_jsonl_path(entity_id: str, category: str) -> Path:
    return dossier_path(entity_id) / f"{category}.jsonl"


@asynccontextmanager
async def _entity_lock_ctx(entity_id: str):
    """Adapter from ``entity_write_lock`` to the ``LedgerStorage``
    write_lock contract (a single-arg async context manager)."""
    async with entity_write_lock(entity_id):
        yield


PORTFOLIO_STORAGE = LedgerStorage(
    jsonl_path=_entity_jsonl_path,
    write_lock=_entity_lock_ctx,
    # No tombstone ledger on portfolio side yet — refine_jsonl falls
    # through to the default no-op. Add a portfolio tombstone helper
    # here if hallucinated story re-emission becomes a recurring
    # problem.
    # No accept_into — portfolio has no cross-ledger routing
    # destinations, so triage `route` decisions degrade to drop.
)


__all__ = ["PORTFOLIO_STORAGE"]
