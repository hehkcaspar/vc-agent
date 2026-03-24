"""Resource scanning, classification, hash-based diff, and manifest persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class FileEntry(TypedDict):
    hash: str
    size: int
    file_type: str


ResourceManifest = Dict[str, FileEntry]  # relative_path → FileEntry


class ResourceDiff(TypedDict):
    added: List[str]
    modified: List[str]
    removed: List[str]
    unchanged: List[str]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_TYPE_MAP: Dict[str, str] = {}
for _ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
    _TYPE_MAP[_ext] = "image"
for _ext in (".docx",):
    _TYPE_MAP[_ext] = "word"
for _ext in (".doc",):
    _TYPE_MAP[_ext] = "word_legacy"  # python-docx cannot read .doc; marked separately
for _ext in (".pdf",):
    _TYPE_MAP[_ext] = "pdf"
for _ext in (".xls", ".xlsx"):
    _TYPE_MAP[_ext] = "excel"
for _ext in (".csv",):
    _TYPE_MAP[_ext] = "csv"
for _ext in (".txt", ".md", ".json", ".yaml", ".yml", ".jsonl", ".xml", ".html", ".htm"):
    _TYPE_MAP[_ext] = "text"


def classify_file(path: Path) -> str:
    return _TYPE_MAP.get(path.suffix.lower(), "other")


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

class Workspace:
    """Manages the resource directory: scan, diff, and snapshot persistence."""

    def __init__(self, root: Path, resources_dir: str = "resources", snapshots_dir: str = ".snapshots"):
        self.root = root.resolve()
        self.resources_path = self.root / resources_dir
        self.snapshots_path = self.root / snapshots_dir
        self._manifest_file = self.snapshots_path / "manifest.json"

    # -- scanning ----------------------------------------------------------

    def scan(self) -> ResourceManifest:
        """Walk all files under resources/ and build a manifest."""
        manifest: ResourceManifest = {}
        if not self.resources_path.exists():
            return manifest

        for path in sorted(self.resources_path.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.resources_path))
            manifest[rel] = FileEntry(
                hash=_sha256(path),
                size=path.stat().st_size,
                file_type=classify_file(path),
            )
        return manifest

    # -- diffing -----------------------------------------------------------

    @staticmethod
    def diff(current: ResourceManifest, previous: ResourceManifest) -> ResourceDiff:
        """Compare two manifests and return what changed."""
        cur_keys = set(current)
        prev_keys = set(previous)

        added = sorted(cur_keys - prev_keys)
        removed = sorted(prev_keys - cur_keys)
        common = cur_keys & prev_keys

        modified: List[str] = []
        unchanged: List[str] = []
        for key in sorted(common):
            if current[key]["hash"] != previous[key]["hash"]:
                modified.append(key)
            else:
                unchanged.append(key)

        return ResourceDiff(
            added=added,
            modified=modified,
            removed=removed,
            unchanged=unchanged,
        )

    # -- snapshots ---------------------------------------------------------

    def save_snapshot(self, manifest: ResourceManifest) -> None:
        self.snapshots_path.mkdir(parents=True, exist_ok=True)
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": manifest,
        }
        self._manifest_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_snapshot(self) -> Optional[ResourceManifest]:
        if not self._manifest_file.exists():
            return None
        raw = json.loads(self._manifest_file.read_text(encoding="utf-8"))
        return raw.get("files")

    # -- helpers -----------------------------------------------------------

    def format_diff_summary(self, diff: ResourceDiff) -> str:
        """Human-readable diff summary for injection into agent context."""
        parts: List[str] = []
        if diff["added"]:
            parts.append(f"New files ({len(diff['added'])}): " + ", ".join(diff["added"]))
        if diff["modified"]:
            parts.append(f"Modified files ({len(diff['modified'])}): " + ", ".join(diff["modified"]))
        if diff["removed"]:
            parts.append(f"Removed files ({len(diff['removed'])}): " + ", ".join(diff["removed"]))
        if not parts:
            parts.append("No changes since last run.")
        return "\n".join(parts)
