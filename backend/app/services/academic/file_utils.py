"""Shared file I/O helpers for academic tracking.

Centralises dossier path resolution, JSON read/write, and the four
record primitives that back every JSONL log under
`data/scholars/{id}/`. All JSONL appends MUST go through
`append_record` so they hold the per-scholar write lock and get a
monotonic ISO timestamp id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import settings

from .locks import scholar_write_lock

SCHOLARS_DIR = settings.ACADEMIC_SCHOLARS_DIR

# Module-level state to guarantee monotonic ISO timestamp ids even if
# two appends fall in the same second within a single process.
_last_id_seen: dict[str, str] = {}


def dossier_path(scholar_id: str) -> Path:
    """Return the dossier directory for a scholar."""
    return SCHOLARS_DIR / scholar_id


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning ``{}`` if it doesn't exist."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as pretty-printed JSON, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    """Append a single JSON object as a line to a JSONL file.

    Legacy helper kept for the old code paths during the rewrite.
    New code should use `append_record` instead, which holds the
    per-scholar write lock and assigns an ISO timestamp id.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


# ── Concept 5 Rule 6 — shared primitives ──────────────────────────────


def _record_path(scholar_id: str, record_name: str) -> Path:
    """Resolve the JSONL file for a given record name.

    `record_name` may include a leading subdirectory (e.g.
    'evaluations/academic_excellence') for per-dim eval logs.
    """
    return dossier_path(scholar_id) / f"{record_name}.jsonl"


def _new_iso_id(scholar_id: str) -> str:
    """Mint a fresh ISO-timestamp id, monotonic per scholar in-process."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    last = _last_id_seen.get(scholar_id)
    if last is not None and now <= last:
        # Same second collision: bump by appending a microsecond suffix.
        # Lexical order remains correct because the suffix is fixed-width.
        micro = datetime.now(timezone.utc).strftime("%f")
        now = f"{last[:-1]}-{micro}Z"
    _last_id_seen[scholar_id] = now
    return now


async def append_record(
    scholar_id: str,
    record_name: str,
    obj: dict[str, Any],
) -> str:
    """Append a record to `{record_name}.jsonl`, return the assigned id.

    - Holds the per-scholar write lock.
    - Mints an ISO-timestamp `id` (monotonic in-process).
    - The caller's `obj` may carry an `id` already; it is overwritten.
    """
    async with scholar_write_lock(scholar_id):
        record_id = _new_iso_id(scholar_id)
        obj = {**obj, "id": record_id}
        path = _record_path(scholar_id, record_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        return record_id


def read_records(scholar_id: str, record_name: str) -> list[dict[str, Any]]:
    """Read all records from `{record_name}.jsonl` in file order."""
    path = _record_path(scholar_id, record_name)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def fold_records(
    scholar_id: str,
    record_name: str,
    reducer: Callable[[Any, dict[str, Any]], Any],
    initial: Any = None,
) -> Any:
    """Replay every record through `reducer(state, record) -> state`.

    Mirrors functools.reduce; used for event-projection state per
    Concept 5 Rule 5 (red flags etc.).
    """
    state = initial
    for rec in read_records(scholar_id, record_name):
        state = reducer(state, rec)
    return state


def latest_record(scholar_id: str, record_name: str) -> dict[str, Any] | None:
    """Return the most recently appended record (last line), or None."""
    recs = read_records(scholar_id, record_name)
    return recs[-1] if recs else None
