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
    "legal_review": PresetDefinition(
        id="legal_review",
        label="Legal Review",
        description=(
            "Review selected legal documents for unusual terms, red flags, "
            "and (for existing positions) position/rights changes"
        ),
        markdown_filename="legal_review.md",
        default_artifact_type="report",
        default_artifact_status="draft",
        artifact_title="Legal Review",
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


# ---------------------------------------------------------------------------
# Legal Review — renderer + helpers
# ---------------------------------------------------------------------------


def _format_template_catalog(cfg) -> str:
    """Format the Tier R1 reference-template catalog as a compact markdown table.

    Agent uses this to pick which raw template to fetch via `legal_template_read`
    when a term looks unusual and warrants precision comparison.
    """
    if not cfg.templates:
        return "_(no reference templates catalogued)_"

    lines = [
        "| id | label | category | round | when to use |",
        "|---|---|---|---|---|",
    ]
    for t in cfg.templates:
        lines.append(
            f"| `{t.id}` | {t.label} | {t.category} | {t.round_type} | {t.description} |"
        )
    lines.append("")
    lines.append(
        "Fetch any row's raw text by calling `legal_template_read(template_id=\"...\")`."
    )
    return "\n".join(lines)


def _format_checklist(cfg) -> str:
    """Format the Tier R2 review checklist for injection into the prompt.

    Emits a hierarchical markdown outline (category → item → properties) that's
    compact but complete enough for the agent to use as its primary rubric.
    """
    if not cfg.categories:
        return "_(no review checklist configured — results will be shallow)_"

    import json as _json

    out: list[str] = []
    for cat in cfg.categories:
        out.append(f"### {cat.label}  (`{cat.id}`)")
        if cat.description:
            out.append(f"_{cat.description}_")
        out.append("")
        for item in cat.items:
            head = f"- **{item.label}**  (`{item.id}`)"
            out.append(head)
            if item.applies_to_instruments:
                out.append(f"  - applies_to: {', '.join(item.applies_to_instruments)}")
            if item.standard_value:
                out.append(f"  - standard_value: {item.standard_value}")
            if item.why_matters:
                out.append(f"  - why_matters: {item.why_matters}")
            if item.red_flag_patterns:
                out.append("  - red_flag_patterns:")
                for rfp in item.red_flag_patterns:
                    note = f" — {rfp.note}" if rfp.note else ""
                    out.append(
                        f"    - `{rfp.pattern}` (severity: {rfp.severity}){note}"
                    )
            if item.scenario_focus:
                sf = item.scenario_focus.model_dump(exclude_none=True)
                if sf:
                    out.append(
                        f"  - scenario_focus: `{_json.dumps(sf, ensure_ascii=False)}`"
                    )
        out.append("")
    return "\n".join(out).rstrip()


def render_legal_review(
    entity_name: str,
    entity_website: Optional[str],
    entity_positions: Optional[list[dict]] = None,
    existing_legal_reviews: Optional[list[dict]] = None,
    existing_prior_rounds: Optional[list[dict]] = None,
) -> str:
    """Render the legal_review preset body with:
    - Tier R1 template catalog pointer (agent fetches raw text on demand)
    - Tier R2 full review checklist (primary rubric)
    - Prior-state context for scenario detection + incremental merge
    """
    preset = PRESETS["legal_review"]
    body = load_preset_body(preset)
    result = (
        body.replace("{{entity_name}}", entity_name)
        .replace("{{entity_website}}", entity_website or "(not provided)")
    )

    # Tier R1: catalog pointer only, not content
    from app.services.legal_templates_config import load_legal_templates_config
    tpl_cfg = load_legal_templates_config()
    result = result.replace(
        "{{template_catalog}}",
        _format_template_catalog(tpl_cfg),
    )

    # Tier R2: full checklist injected inline — this is the agent's rubric
    from app.services.legal_review_checklist_config import (
        load_legal_review_checklist,
    )
    checklist_cfg = load_legal_review_checklist()
    result = result.replace(
        "{{review_checklist}}",
        _format_checklist(checklist_cfg),
    )
    result = result.replace(
        "{{checklist_version}}",
        str(checklist_cfg.version),
    )

    # Incremental / scenario context
    has_any_context = bool(
        (existing_legal_reviews or [])
        or (entity_positions or [])
        or (existing_prior_rounds or [])
    )
    if has_any_context:
        import json
        ctx = {
            "legal_reviews": existing_legal_reviews or [],
            "_positions": entity_positions or [],
            "prior_rounds": existing_prior_rounds or [],
        }
        result += (
            "\n\n---\n\n## Prior-state context\n\n"
            "The system has the following knowledge about this company. Use it to "
            "(a) detect the **scenario** for each round you review "
            "(`new_investment` vs `follow_on` vs `retrospective`), "
            "(b) produce the **complete updated `legal_reviews` array** "
            "(preserve prior rounds' entries verbatim unless new docs contradict them), "
            "(c) avoid re-reviewing rounds that haven't changed.\n\n"
            f"```json\n{json.dumps(ctx, indent=2, ensure_ascii=False)}\n```\n"
        )
    return result
