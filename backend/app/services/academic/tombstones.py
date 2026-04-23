"""Per-scholar tombstone ledger.

When the refinement triage drops an item, we don't just delete it —
we record a one-line tombstone under
``data/scholars/{scholar_id}/_tombstones.jsonl``. The existing dedup
Layer 1 (prompt guard) reads these tombstones and tells Gemini
"these have been rejected before — do not re-emit".

Each tombstone is a single JSON line:

    {"category": "patents", "normalized_title": "mcunet: ...",
     "reason": "research paper, not a patent", "at": "2026-04-21T..."}

Append-only. Duplicate lines (same category + normalized_title) are
tolerated; readers de-dup on load.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .file_utils import dossier_path
from .papers_merge import _normalize_title as _normalize

logger = logging.getLogger(__name__)

_TOMBSTONE_FILE = "_tombstones.jsonl"


def tombstone_path(scholar_id: str) -> Path:
    return dossier_path(scholar_id) / _TOMBSTONE_FILE


def write_tombstone(
    scholar_id: str,
    *,
    category: str,
    title: str,
    reason: str,
) -> None:
    """Append one tombstone row. Silent if ``title`` is empty."""
    norm = _normalize(title)
    if not norm:
        return
    path = tombstone_path(scholar_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "category": category,
        "normalized_title": norm,
        "original_title": title,
        "reason": (reason or "")[:300],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("tombstone write failed for %s: %s", scholar_id, exc)


def load_tombstones(
    scholar_id: str,
    *,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Return deduplicated tombstones for a scholar. Latest reason wins
    per (category, normalized_title) pair.
    """
    path = tombstone_path(scholar_id)
    if not path.exists():
        return []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            cat = row.get("category") or ""
            if category is not None and cat != category:
                continue
            key = (cat, row.get("normalized_title") or "")
            if not key[1]:
                continue
            by_key[key] = row
    except OSError as exc:
        logger.warning("tombstone read failed for %s: %s", scholar_id, exc)
        return []
    return list(by_key.values())


def format_for_prompt(
    tombstones: Iterable[dict[str, Any]],
    *,
    limit: int = 30,
) -> str:
    """Render tombstones as a Layer-1 prompt block. Returns '(none)' when
    empty so prompt format strings never emit a stray blank line.
    """
    rows = [t for t in tombstones if t.get("original_title")]
    if not rows:
        return "(none)"
    rows = rows[-limit:]
    lines = []
    for r in rows:
        t = r.get("original_title") or r.get("normalized_title") or ""
        reason = r.get("reason") or ""
        lines.append(f"- {t}  — REJECTED: {reason}" if reason else f"- {t}")
    return "\n".join(lines)


def matches_tombstone(
    title: str,
    tombstones: Iterable[dict[str, Any]],
    *,
    category: str | None = None,
) -> dict[str, Any] | None:
    """Post-search rule dedup: if a new candidate's title matches a
    tombstone, return the tombstone so the caller can skip + log.
    """
    needle = _normalize(title)
    if not needle:
        return None
    for t in tombstones:
        if category is not None and (t.get("category") or "") != category:
            continue
        if (t.get("normalized_title") or "") == needle:
            return t
    return None


__all__ = [
    "write_tombstone",
    "load_tombstones",
    "format_for_prompt",
    "matches_tombstone",
    "tombstone_path",
]
