"""Per-entity asyncio write locks for serializing dossier mutations.

Mirrors ``services/academic/locks.py``. Single-process only; if we
ever scale out, swap for a file-based lock.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

_locks: dict[str, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()


async def _get_lock(entity_id: str) -> asyncio.Lock:
    async with _registry_lock:
        lock = _locks.get(entity_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[entity_id] = lock
        return lock


@asynccontextmanager
async def entity_write_lock(entity_id: str):
    """Async context manager holding the per-entity write lock."""
    lock = await _get_lock(entity_id)
    async with lock:
        yield
