"""Tool: read_artifact — read a previously generated artifact."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from ..config import load_workspace_config


@tool
def read_artifact(workspace_root: str, artifact_path: str) -> str:
    """Read a previously generated artifact from the workspace.

    Use this to read prior reports, memory notes, or other artifacts.

    Args:
        workspace_root: Absolute path to the workspace root directory.
        artifact_path: Relative path within artifacts/ (e.g. 'reports/summary.md' or 'memory/observations.md').
    """
    ws_root = Path(workspace_root)
    cfg = load_workspace_config(ws_root)
    target = (ws_root / cfg.artifacts_dir / artifact_path).resolve()

    # Path traversal protection: ensure target stays within artifacts/
    artifacts_base = (ws_root / cfg.artifacts_dir).resolve()
    if not str(target).startswith(str(artifacts_base)):
        return f"Error: path traversal detected. '{artifact_path}' would read outside artifacts/."

    if not target.exists():
        return f"Artifact not found: artifacts/{artifact_path}"

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = target.read_text(encoding="latin-1")

    return f"--- artifacts/{artifact_path} ---\n{content}"
