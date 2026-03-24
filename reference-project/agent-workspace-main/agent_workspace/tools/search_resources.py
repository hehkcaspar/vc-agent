"""Tool: search_resources — keyword search across resources and artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from langchain_core.tools import tool

from ..config import load_workspace_config

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".yaml", ".yml", ".jsonl",
    ".xml", ".html", ".htm", ".log",
}


@tool
def search_resources(workspace_root: str, query: str, max_results: int = 20) -> str:
    """Search for a keyword or phrase across all text-based resources and artifacts.

    Use this to find specific information without extracting every file.
    Searches file contents for case-insensitive matches and returns snippets.

    Args:
        workspace_root: Absolute path to the workspace root directory.
        query: The search term or phrase to look for (case-insensitive).
        max_results: Maximum number of matching snippets to return.
    """
    ws_root = Path(workspace_root)
    cfg = load_workspace_config(ws_root)

    search_dirs = [
        ws_root / cfg.resources_dir,
        ws_root / cfg.artifacts_dir,
    ]

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    hits: List[str] = []

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for path in sorted(search_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    rel = str(path.relative_to(ws_root))
                    snippet = line.strip()[:200]
                    hits.append(f"  {rel}:{i}  {snippet}")
                    if len(hits) >= max_results:
                        break
            if len(hits) >= max_results:
                break

    if not hits:
        return f"No matches found for '{query}'."

    return f"Found {len(hits)} matches for '{query}':\n" + "\n".join(hits)
