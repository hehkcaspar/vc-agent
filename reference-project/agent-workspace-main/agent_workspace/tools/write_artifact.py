"""Tool: write_artifact — create or update an artifact file."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from ..config import load_workspace_config


@tool
def write_artifact(workspace_root: str, artifact_type: str, name: str, content: str) -> str:
    """Write an artifact file to the workspace.

    Use this to persist any output: reports, memory notes, data files, etc.
    The file is written to artifacts/{artifact_type}/{name}.

    Args:
        workspace_root: Absolute path to the workspace root directory.
        artifact_type: Category of artifact — one of: reports, memory, skills, settings.
        name: Filename including extension (e.g. 'summary.md', 'scores.json').
        content: The full content to write.
    """
    ws_root = Path(workspace_root)
    cfg = load_workspace_config(ws_root)
    artifacts_dir = ws_root / cfg.artifacts_dir / artifact_type

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = (artifacts_dir / name).resolve()

    # Path traversal protection: ensure target stays within artifacts/
    artifacts_base = (ws_root / cfg.artifacts_dir).resolve()
    if not str(target).startswith(str(artifacts_base)):
        return f"Error: path traversal detected. '{name}' would write outside artifacts/."

    target.write_text(content, encoding="utf-8")

    rel = f"artifacts/{artifact_type}/{name}"
    return f"Artifact written: {rel} ({len(content)} chars)"
