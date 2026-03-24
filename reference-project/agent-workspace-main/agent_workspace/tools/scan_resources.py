"""Tool: scan_resources — list all files in the workspace with type and size."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from ..workspace import Workspace, classify_file


@tool
def scan_resources(workspace_root: str) -> str:
    """Scan the resources directory and list all available files with their types and sizes.

    Call this first to understand what files are available in the workspace.
    Returns a formatted list of files with relative paths, types, and sizes.

    Args:
        workspace_root: Absolute path to the workspace root directory.
    """
    ws = Workspace(Path(workspace_root))
    manifest = ws.scan()

    if not manifest:
        return "No files found in resources/."

    lines = [f"Found {len(manifest)} files in resources/:\n"]
    for rel_path, entry in sorted(manifest.items()):
        size_kb = entry["size"] / 1024
        lines.append(f"  {rel_path}  [{entry['file_type']}]  ({size_kb:.1f} KB)")

    return "\n".join(lines)
