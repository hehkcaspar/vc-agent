"""System prompts, task refinement, and template resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional


SYSTEM_PROMPT = """\
You are a workspace agent. Your job is to complete the task described in your \
instructions by scanning and reasoning about the available resources.

## Your capabilities
- Scan the resource directory for available files (scan_resources)
- Extract text content from documents, spreadsheets, PDFs (extract_content)
- Search across all resources and artifacts for specific information (search_resources)
- Read artifacts from previous runs — reports, memory, settings (read_artifact)
- Write new artifacts — reports, memory notes, data files (write_artifact)

## Current context
- Workspace: {workspace_root}
{diff_section}\
{memory_section}\

## Instructions
{task_content}

## Rules
- Always scan resources before making conclusions.
- Cite specific files as evidence for any claims.
- If information is insufficient, say so — do not fabricate.
- Write artifacts for anything that should persist beyond this run.
- Update memory (write to artifacts/memory/) with key observations for future reference.
"""


def build_system_prompt(
    workspace_root: str,
    task_content: str,
    diff_summary: Optional[str] = None,
    memory_content: Optional[str] = None,
) -> str:
    """Assemble the system prompt with available context."""
    diff_section = ""
    if diff_summary:
        diff_section = f"- Resource changes since last run:\n{diff_summary}\n"

    memory_section = ""
    if memory_content:
        memory_section = f"- Memory notes:\n{memory_content}\n"

    return SYSTEM_PROMPT.format(
        workspace_root=workspace_root,
        diff_section=diff_section,
        memory_section=memory_section,
        task_content=task_content,
    )


def resolve_template(
    templates_dir: Path,
    template_name: str,
    variables: Optional[Dict[str, str]] = None,
) -> str:
    """Load a template from instructions/templates/{name}.md and substitute variables.

    Variables use {variable_name} syntax in the template markdown.
    """
    # Try with and without .md extension
    candidates = [
        templates_dir / template_name,
        templates_dir / f"{template_name}.md",
    ]

    template_path: Optional[Path] = None
    for c in candidates:
        if c.exists():
            template_path = c
            break

    if template_path is None:
        available = [f.name for f in templates_dir.iterdir() if f.is_file()] if templates_dir.exists() else []
        raise FileNotFoundError(
            f"Template '{template_name}' not found in {templates_dir}. "
            f"Available: {available}"
        )

    content = template_path.read_text(encoding="utf-8")

    if variables:
        for key, value in variables.items():
            content = content.replace(f"{{{key}}}", value)

    return content
