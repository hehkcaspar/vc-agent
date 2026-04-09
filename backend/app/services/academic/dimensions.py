"""
Scholar evaluation dimensions — unified, file-backed configuration.

All dimensions (both the original 7 and any user-added ones) live in one JSON
file and are treated equally. The file is seeded with sensible defaults on
first read. Prompts are built dynamically from this list so adding, removing,
or editing a dimension requires no code changes.
"""

from __future__ import annotations

import json
from typing import TypedDict

from ...config import settings


DIMENSIONS_PATH = settings.ACADEMIC_CONFIG_DIR / "dimensions.json"


class Dimension(TypedDict):
    key: str
    name: str
    prompt: str


DEFAULT_DIMENSIONS: list[Dimension] = [
    {
        "key": "research_impact",
        "name": "Research Impact",
        "prompt": (
            "Assess citation impact, h-index (relative to career stage and field), "
            "publication venue quality, and influence on the scholar's subfield."
        ),
    },
    {
        "key": "commercialization",
        "name": "Commercialization",
        "prompt": (
            "Assess commercial potential: patents filed, startups founded, "
            "industry collaborations, licensing activity, and applicability of research."
        ),
    },
    {
        "key": "career_trajectory",
        "name": "Career Trajectory",
        "prompt": (
            "Assess career momentum: recent promotions, lab growth, funding trajectory, "
            "and upward/downward trends in output and influence."
        ),
    },
    {
        "key": "collaboration_strength",
        "name": "Collaboration Strength",
        "prompt": (
            "Assess the strength and breadth of the scholar's collaboration network: "
            "co-author quality, cross-institution work, and interdisciplinary reach."
        ),
    },
    {
        "key": "field_position",
        "name": "Field Position",
        "prompt": (
            "Assess the scholar's standing within their primary field: "
            "percentile among peers, recognition, keynotes, editorial roles, awards."
        ),
    },
    {
        "key": "founder_potential",
        "name": "Founder Potential",
        "prompt": (
            "Assess founder potential: prior founding experience, industry exposure, "
            "product thinking, communication, and willingness to commercialise."
        ),
    },
    {
        "key": "public_profile",
        "name": "Public Profile",
        "prompt": (
            "Assess visibility: personal website, media coverage, social presence, "
            "talks, and ease of outreach."
        ),
    },
]


def read_dimensions() -> list[Dimension]:
    """Load dimensions from disk, seeding with defaults on first read."""
    if not DIMENSIONS_PATH.exists():
        write_dimensions(DEFAULT_DIMENSIONS)
        return list(DEFAULT_DIMENSIONS)
    try:
        data = json.loads(DIMENSIONS_PATH.read_text(encoding="utf-8"))
        dims = data.get("dimensions", [])
        if not isinstance(dims, list) or not dims:
            write_dimensions(DEFAULT_DIMENSIONS)
            return list(DEFAULT_DIMENSIONS)
        return dims
    except Exception:
        return list(DEFAULT_DIMENSIONS)


def write_dimensions(dims: list[Dimension]) -> None:
    DIMENSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIMENSIONS_PATH.write_text(
        json.dumps({"dimensions": dims}, indent=2), encoding="utf-8"
    )


def render_dimensions_schema_block(dims: list[Dimension]) -> str:
    """Render the JSON-shape example shown in the system prompt."""
    lines = ["  \"dimensions\": {"]
    for i, d in enumerate(dims):
        comma = "," if i < len(dims) - 1 else ""
        lines.append(
            f'    "{d["key"]}": {{ "score": 0, "explanation": "...", "evidence": ["..."] }}{comma}'
        )
    lines.append("  }")
    return "\n".join(lines)


def render_dimensions_rubric(dims: list[Dimension]) -> str:
    """Render the per-dimension scoring guidance block."""
    lines = []
    for d in dims:
        lines.append(f"- **{d['name']}** (`{d['key']}`): {d['prompt']}")
    return "\n".join(lines)
