"""Tool: extract_content — extract text/images from specific files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from langchain_core.tools import tool

from ..config import ExtractionConfig, load_workspace_config
from ..extractor import extract_file
from ..workspace import classify_file


@tool
def extract_content(workspace_root: str, file_paths: List[str]) -> str:
    """Extract text content from specific files in the resources directory.

    Use this after scanning to read the actual content of files you need to analyze.
    Provide relative paths (as returned by scan_resources).

    Args:
        workspace_root: Absolute path to the workspace root directory.
        file_paths: List of relative file paths within the resources/ directory to extract.
    """
    ws_root = Path(workspace_root)
    cfg = load_workspace_config(ws_root)
    resources = ws_root / cfg.resources_dir

    results = []
    for rel_path in file_paths:
        full_path = resources / rel_path
        if not full_path.exists():
            results.append({"path": rel_path, "error": "File not found"})
            continue
        file_type = classify_file(full_path)
        extracted = extract_file(full_path, file_type, cfg.extraction)
        results.append(extracted)

    # Format for LLM consumption
    parts = []
    for item in results:
        path = item.get("path", "unknown")
        if "error" in item:
            parts.append(f"--- {path} ---\n[Error: {item['error']}]")
        elif item.get("type") == "image":
            parts.append(f"--- {path} ---\n{item.get('text', '[Image]')}")
        else:
            parts.append(f"--- {path} ---\n{item.get('text', '[No content]')}")

    return "\n\n".join(parts)
