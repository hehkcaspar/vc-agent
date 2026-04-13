"""Per-scholar asyncio write locks for serializing dossier mutations.

Used by every JSONL append in `file_utils.append_record` and by any other
writer that needs to keep ordering consistent for a single scholar.

LIMITATION — single-process only:
    These locks live in process memory. Under a multi-worker / multi-
    process FastAPI deployment they do NOT serialize cross-process
    appends, and `peer_group.jsonl` / `red_flags.jsonl` could interleave
    writes. The rest of this app is single-process per CLAUDE.md, so
    this is acceptable for v1. If we ever scale beyond one process,
    replace with a file-based lock (`fcntl.flock` / `portalocker`) or
    move append-ordering into SQLite.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

_locks: dict[str, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()


async def _get_lock(scholar_id: str) -> asyncio.Lock:
    async with _registry_lock:
        lock = _locks.get(scholar_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[scholar_id] = lock
        return lock


@asynccontextmanager
async def scholar_write_lock(scholar_id: str):
    """Async context manager that holds the per-scholar write lock."""
    lock = await _get_lock(scholar_id)
    async with lock:
        yield
