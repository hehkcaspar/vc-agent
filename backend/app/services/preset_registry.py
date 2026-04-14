"""Shortcut presets for chat (markdown templates under app/prompts/)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True)
class PresetDefinition:
    id: str
    label: str
    description: str
    markdown_filename: str
    default_artifact_type: Literal["memo", "factsheet", "report", "other"]
    default_artifact_status: Literal["draft", "final"]
    artifact_title: str
    output_kind: Literal["markdown", "json"] = "markdown"


PRESETS: dict[str, PresetDefinition] = {
    "red_team": PresetDefinition(
        id="red_team",
        label="Red team diligence",
        description="Forensic internal review + external search-backed red team report",
        markdown_filename="red_team.md",
        default_artifact_type="report",
        default_artifact_status="draft",
        artifact_title="risk_analyze",
        output_kind="markdown",
    ),
    "extract_info": PresetDefinition(
        id="extract_info",
        label="Extract Info",
        description="Browse workspace and extract structured company metadata",
        markdown_filename="extract_info.md",
        default_artifact_type="other",
        default_artifact_status="draft",
        artifact_title="Company Profile",
        output_kind="json",
    ),
}


def list_presets() -> list[PresetDefinition]:
    return list(PRESETS.values())


def get_preset(preset_id: str) -> Optional[PresetDefinition]:
    return PRESETS.get(preset_id)


def load_preset_body(preset: PresetDefinition) -> str:
    path = _PROMPTS_DIR / preset.markdown_filename
    if not path.exists():
        raise FileNotFoundError(f"Preset template missing: {path}")
    return path.read_text(encoding="utf-8")


def load_file_lookup_preprocess_instruction() -> str:
    """System instruction for row-level metadata pre-process (file lookup index, not VC extract)."""
    path = _PROMPTS_DIR / "file_lookup_preprocess.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def load_inbox_grouping_instruction() -> str:
    """Path A Pass 2: synoptic grouping + destination-aware routing for loose Inbox files."""
    path = _PROMPTS_DIR / "inbox_grouping.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def load_inbox_folder_routing_instruction() -> str:
    """Path B Step B1: fast folder routing from structure signal alone."""
    path = _PROMPTS_DIR / "inbox_folder_routing.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def render_extract_info(
    entity_name: str,
    entity_website: Optional[str],
    existing_metadata: Optional[dict] = None,
) -> str:
    preset = PRESETS["extract_info"]
    body = load_preset_body(preset)
    result = (
        body.replace("{{entity_name}}", entity_name)
        .replace("{{entity_website}}", entity_website or "(not provided)")
    )

    # Inject incremental context when a previous extraction exists
    if existing_metadata and existing_metadata.get("_extracted_at"):
        extracted_at = existing_metadata["_extracted_at"]
        files_examined = existing_metadata.get("_files_examined") or []

        # Accept both shapes: ["path"] (new) or [{"path": "..."}] (legacy)
        def _fmt(item: object) -> str:
            if isinstance(item, str):
                return f"  - {item}"
            if isinstance(item, dict):
                return f"  - {item.get('path', '?')}"
            return f"  - {item!r}"

        file_list = (
            "\n".join(_fmt(f) for f in files_examined)
            if files_examined else "  (none recorded)"
        )

        import json
        incremental_block = (
            f"\n\n---\n\n## Previous extraction context\n\n"
            f"A previous extraction was run at **{extracted_at}** and examined "
            f"**{len(files_examined)}** file(s):\n{file_list}\n\n"
            f"**Current metadata snapshot:**\n```json\n"
            f"{json.dumps(existing_metadata, indent=2, ensure_ascii=False)}\n```\n\n"
            f"This is an **incremental run**. Focus on files not in the list above "
            f"or files modified after the previous extraction. Only re-read previously "
            f"examined files if new information suggests something was missed or is wrong. "
            f"Produce a complete updated JSON (full state, not a delta) that merges "
            f"your new findings with the existing metadata."
        )
        result += incremental_block

    return result


def render_red_team(
    startup_name: str,
    industry: Optional[str] = None,
    stage: Optional[str] = None,
) -> str:
    preset = PRESETS["red_team"]
    body = load_preset_body(preset)
    return (
        body.replace("{{startup_name}}", startup_name)
        .replace(
            "{{industry}}",
            industry or "Not specified — infer cautiously from provided materials only.",
        )
        .replace(
            "{{stage}}",
            stage or "Not specified — infer cautiously from provided materials only.",
        )
    )
