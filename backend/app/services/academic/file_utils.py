"""Shared file I/O helpers for academic tracking.

Centralises dossier path resolution, JSON read/write, and JSONL append
so that both the router and domain tools use the same functions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings

SCHOLARS_DIR = settings.ACADEMIC_SCHOLARS_DIR


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
    """Append a single JSON object as a line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
