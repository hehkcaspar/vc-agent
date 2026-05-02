"""Shared file I/O helpers for portfolio entity tracking.

Per-entity dossier lives at ``data/entities/{entity_id}/`` alongside
the workspace/ directory. All JSONL appends go through
``append_record`` for ordering + monotonic ISO-timestamp ids.

The shape mirrors ``services/academic/file_utils.py`` so knowledge of
the scholar module transfers directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

from .locks import entity_write_lock

ENTITIES_DIR = settings.DATA_ROOT

# Monotonic-id memory per entity within one process.
_last_id_seen: dict[str, str] = {}


def dossier_path(entity_id: str) -> Path:
    """Return the entity dossier directory (parent of workspace/)."""
    return ENTITIES_DIR / entity_id


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning ``{}`` if missing."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _record_path(entity_id: str, record_name: str) -> Path:
    return dossier_path(entity_id) / f"{record_name}.jsonl"


def _new_iso_id(entity_id: str) -> str:
    """Allocate a monotonically-increasing per-entity id.

    The lexicographic comparison ``now <= last`` was buggy when ``last``
    already had a microsecond suffix (e.g. ``2026-05-02T05-01-28-940901Z``)
    and ``now`` was the bare-second form (``2026-05-02T05-01-28Z``).
    The bare-second string sorts AFTER the suffixed string because ``Z``
    (0x5A) > ``-`` (0x2D), so the guard let the bare-second id through
    even though it represented the same wall-clock second as the prior
    id — producing duplicate row ids and React duplicate-key warnings.

    Fix: when the current bare-second id is the same prefix as ``last``
    (with or without ``last``'s microsecond suffix), always escalate to
    a microsecond-suffixed id. Compare prefixes, not lex order.
    """
    bare = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    last = _last_id_seen.get(entity_id)
    # Both candidates have the same per-second prefix when the leading
    # 19 chars (YYYY-MM-DDTHH-MM-SS) match. Use that, not lex compare.
    same_second = (
        last is not None
        and len(last) >= 19
        and last[:19] == bare[:19]
    )
    if same_second:
        micro = datetime.now(timezone.utc).strftime("%f")
        new_id = f"{bare[:-1]}-{micro}Z"
    else:
        new_id = bare
    _last_id_seen[entity_id] = new_id
    return new_id


async def append_record(
    entity_id: str,
    record_name: str,
    obj: dict[str, Any],
) -> str:
    """Append a record to ``{record_name}.jsonl``; return assigned id."""
    async with entity_write_lock(entity_id):
        record_id = _new_iso_id(entity_id)
        obj = {**obj, "id": record_id}
        path = _record_path(entity_id, record_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        return record_id


async def rewrite_records(
    entity_id: str,
    record_name: str,
    records: list[dict[str, Any]],
) -> None:
    """Atomically replace ``{record_name}.jsonl`` with ``records``.

    Use sparingly — the file is otherwise append-only. Intended for
    in-place mutations of existing rows (e.g., backfilling a new field
    like ``_url_status`` after the validator was added). Caller MUST
    preserve ``id`` on each record so downstream consumers don't see
    fresh ids reshuffle history.

    Atomic semantics: write to ``{path}.tmp`` then ``os.replace``, both
    inside the entity write-lock so concurrent appends serialize.
    """
    async with entity_write_lock(entity_id):
        path = _record_path(entity_id, record_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        tmp.replace(path)


def read_records(entity_id: str, record_name: str) -> list[dict[str, Any]]:
    """Read all records from ``{record_name}.jsonl`` in file order."""
    path = _record_path(entity_id, record_name)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def latest_record(entity_id: str, record_name: str) -> dict[str, Any] | None:
    recs = read_records(entity_id, record_name)
    return recs[-1] if recs else None


# ── Snapshot bookkeeping ──────────────────────────────────────────────


async def record_snapshot(
    entity_id: str,
    source_id: str,
    detail: dict[str, Any] | None = None,
) -> str:
    """Append a snapshot marker to snapshot_log.jsonl."""
    return await append_record(
        entity_id,
        "snapshot_log",
        {
            "source": source_id,
            "detail": detail or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def last_snapshot_for_source(
    entity_id: str, source_id: str
) -> dict[str, Any] | None:
    for rec in reversed(read_records(entity_id, "snapshot_log")):
        if rec.get("source") == source_id:
            return rec
    return None
