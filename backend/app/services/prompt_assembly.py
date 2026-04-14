"""Assemble system instructions for portfolio chat (reference-style sections)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EntityBrief:
    entity_id: str
    name: str
    website: Optional[str]


def _format_gp_identity_block() -> str:
    """Render the Taihill fund registry as a "GP identity" section.

    Reads `data/config/funds.json` via `funds_config.load_funds()` and
    formats each fund as `id → display name`. Returns an empty string
    (no section) when the config is missing or the registry is empty,
    so prompts stay clean for deployments that haven't populated it.

    The block is injected into every portfolio chat/agent system prompt
    so the LLM can (a) recognise which signatory on a cap table or SPA
    represents "us", and (b) populate fields like `our_position.investor_entity`
    by display name without guessing from slugs.
    """
    try:
        from app.services.funds_config import load_funds
        cfg = load_funds()
    except Exception:
        logger.warning("Failed to load funds.json for GP identity block", exc_info=True)
        return ""
    if not cfg.funds:
        return ""

    lines = [
        "## GP identity — funds you are acting on behalf of",
        "",
        "You represent the following Taihill-controlled investment vehicles. "
        "When any cap table, stock purchase agreement, side letter, signatory "
        "block, or position record references one of these entities (match "
        "loosely on display name — small spelling / naming variations are "
        "still a match), that counterparty is **us**.",
        "",
    ]
    for f in cfg.funds:
        lines.append(f"- id `{f.id}` → display name **{f.name}**")
    lines.append("")
    lines.append(
        "**When populating any schema that tracks \"our\" position (e.g. "
        "`our_position`, `holder` / `investor` fields, position entries):**"
    )
    lines.append(
        "- Set the display-name field (e.g. `investor_entity`, `holder`, "
        "`name`) to the matched fund's display name verbatim."
    )
    lines.append(
        "- Set the id field (e.g. `fund_id`) to the matched fund's id from "
        "the list above."
    )
    lines.append(
        "- If the document references a Taihill-named entity that is NOT in "
        "the list above, still populate the display name but leave the id "
        "`null` and note the discrepancy in the narrative."
    )
    lines.append("")
    return "\n".join(lines)


def build_portfolio_system_prompt(
    entity: EntityBrief,
    diff_summary: Optional[str],
    task_block: str,
) -> str:
    web = entity.website or "(not provided)"
    diff_section = ""
    if diff_summary:
        diff_section = f"\n## File context\n{diff_summary}\n"

    gp_block = _format_gp_identity_block()
    gp_section = f"\n{gp_block}" if gp_block else ""

    return f"""You are an AI assistant for a VC portfolio entity workspace.

## Capabilities
- You may receive attached files (PDF, images, text) from this entity's workspace.
- You have access to Google Search when enabled to verify external facts.
- Cite which file you relied on when making claims.

## Entity
- **Entity id:** {entity.entity_id}
- **Name:** {entity.name}
- **Website:** {web}
{gp_section}{diff_section}
## Instructions for this turn
{task_block}

## Rules
- Do not fabricate quotes or data from documents you were not given.
- If information is missing, say what is missing and what you would need.
- Prefer concise, actionable answers for investment workflows.
"""


def build_agent_system_prompt(
    entity: EntityBrief,
    *,
    extras: str,
) -> str:
    web = entity.website or "(not provided)"
    gp_block = _format_gp_identity_block()
    gp_section = f"\n{gp_block}" if gp_block else ""
    return f"""You are an AI assistant for a VC portfolio entity workspace (Deep Agent harness).

## Entity
- **Entity id:** {entity.entity_id}
- **Name:** {entity.name}
- **Website:** {web}
{gp_section}
## Workspace Tools
You have 13 workspace tools to browse, read, write, and organize files.

**Reading:** Use workspace_read_file(path) with paths from the tree context.
Use workspace_search_files(query, folder) when the tree doesn't have what you need.

**User-selected files:** When the user selects specific files for a task, they appear as a
pointer list in the user message with path, type, size, and description. Use
workspace_read_file(path) to read the files relevant to the task. You do not need to read
all of them — triage by metadata first.

**Creating deliverables:** Write to Deliverables/ folder:
  - Memos: Deliverables/Memos/{{title}}.md
  - Reports: Deliverables/Reports/{{title}}.md
  - Factsheets: Deliverables/Factsheets/{{title}}.md
Set metadata {{"deliverable_type": "...", "status": "draft|final"}} on write.

**Editing:** Write to the same path. Old version is automatically preserved.

**Write zones:**
  - You CAN freely create/edit files you created (in Deliverables/ or elsewhere)
  - You CAN move/rename any file to organize — that's always safe
  - You CANNOT overwrite or delete user-uploaded files (the system will block you)
  - If you need to analyze an uploaded file, create a derivative:
    "Data Room/pitch-deck.pdf" → create "Deliverables/pitch-deck-analysis.md"

**Annotating:** After reading a file, use workspace_annotate(path, description).
Descriptions appear in the tree context for future conversations.

**Workspace notes:** After cross-referencing files or learning non-obvious context,
update WORKSPACE_NOTES.md. Focus on cross-file dependencies, data quality issues,
process context, and information gaps. Keep concise. Delete stale notes.

## Web search
When search is enabled, use it for external facts; cite sources briefly.

## Session instructions
{extras}

## Rules
- Do not fabricate quotes or data from materials you did not read via tools or this message.
- If information is missing, say what is missing.
- Prefer concise, actionable answers for investment workflows.
"""
