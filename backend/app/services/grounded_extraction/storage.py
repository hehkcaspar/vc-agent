"""Per-subject ledger storage abstraction used by ``refine_jsonl``.

Both academic (scholar dossiers) and portfolio (entity dossiers) use
JSONL ledgers keyed by ``{subject_id}/{category}.jsonl`` with the same
read / append / atomic-rewrite semantics, but with their own dossier
roots, lock primitives, and tombstone schemes. ``LedgerStorage``
captures only the bits ``refine_jsonl`` needs as a callable bag — much
lighter than a full Protocol class hierarchy.

Each domain (academic / portfolio) constructs one ``LedgerStorage``
once and passes it to ``refine_jsonl``.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


# Type aliases for clarity.
JsonlPathFn = Callable[[str, str], Path]
"""``(subject_id, category) -> Path`` for the on-disk jsonl ledger."""

WriteLockFn = Callable[[str], AbstractAsyncContextManager]
"""``(subject_id) -> async context manager`` serializing writes per
subject. ``async with storage.write_lock(subject_id): ...``"""

TombstoneFn = Callable[..., None]
"""``(subject_id, *, category, title, reason) -> None``. Optional —
domains without a tombstone ledger pass ``noop_tombstone``."""

AcceptIntoFn = Callable[..., Awaitable[dict[str, Any]]]
"""``(destination, subject_id, record, source_category) -> dict`` for
ROUTE actions. Optional — domains without destinations pass None and
ROUTE decisions degrade to DROP."""


def noop_tombstone(*args: Any, **kwargs: Any) -> None:
    """Default tombstone implementation for domains without a tombstone
    ledger. Silently ignores the call. Domains that DO want tombstones
    pass their own writer."""
    return None


@dataclass(frozen=True)
class LedgerStorage:
    """Bag of callables ``refine_jsonl`` calls into.

    Required: ``jsonl_path`` and ``write_lock``.
    Optional: ``write_tombstone`` (defaults to ``noop_tombstone``),
    ``accept_into`` (None disables routing).
    """
    jsonl_path: JsonlPathFn
    write_lock: WriteLockFn
    write_tombstone: TombstoneFn = field(default=noop_tombstone)
    accept_into: Optional[AcceptIntoFn] = None


__all__ = [
    "LedgerStorage",
    "JsonlPathFn",
    "WriteLockFn",
    "TombstoneFn",
    "AcceptIntoFn",
    "noop_tombstone",
]
