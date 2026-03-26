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
        label="Extract info",
        description="Structured VC metadata (JSON) from selected sources",
        markdown_filename="extract_info.md",
        default_artifact_type="other",
        default_artifact_status="draft",
        artifact_title="extract_info",
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


def render_extract_info(entity_name: str, entity_website: Optional[str]) -> str:
    preset = PRESETS["extract_info"]
    body = load_preset_body(preset)
    return (
        body.replace("{{entity_name}}", entity_name)
        .replace("{{entity_website}}", entity_website or "(not provided)")
    )


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
