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


# Appended to every dim prompt so the LLM knows how to signal
# "insufficient evidence" via score=0 sentinel.
_SCOREABLE_GUIDANCE = (
    "\n\nScoring zero: If there is genuinely insufficient evidence to "
    "evaluate this dimension (e.g., zero commercial activity for "
    "tech-transfer, no data whatsoever), set score to 0. Reserve this "
    "for cases where the dimension is fundamentally inapplicable to "
    "this scholar's profile — not merely because data is sparse."
)

# NOTE: these are fallback seeds used only when `data/config/dimensions.json`
# is missing or empty. The real source of truth is the JSON file. The seeds
# here mirror the 4 MECE dims from the scholar evaluation framework doc;
# when we update `dimensions.json` with richer prompts those override
# these defaults on every read.
DEFAULT_DIMENSIONS: list[Dimension] = [
    {
        "key": "academic_excellence",
        "name": "Academic Excellence",
        "prompt": (
            "Holistic judgment of scientific contribution + peer standing. "
            "Use author-position-weighted citations, field recognition "
            "(awards, editorial roles, keynotes), and collaboration quality. "
            "Calibrate top bands against billion-dollar-outcome potential."
        ),
    },
    {
        "key": "tech_transfer_experience",
        "name": "Tech-transfer Experience",
        "prompt": (
            "Historical track record of moving research to market. Score "
            "against commercial peers. Weight revenue > investor funding > "
            "grants. Verify IP ownership structure. Judge ventures, patents, "
            "licensing, and partnerships holistically."
        ),
    },
    {
        "key": "founder_potential",
        "name": "Founder Potential",
        "prompt": (
            "Predict founder success probability. Core signals: "
            "founder-market fit (including domain dominance), determination, "
            "commitment/bridging, team-attracting ability, public presence, "
            "and prior operating experience (bonus, not required)."
        ),
    },
    {
        "key": "growth_trajectory",
        "name": "Growth Trajectory",
        "prompt": (
            "Slope across scientific / commercial / operator axes over the "
            "recent past. Multi-axis acceleration > single-axis spike. "
            "Phase-sensitive: flat is fine at R4, concerning at R3a. "
            "Recency weighted; last 24 months dominate."
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


    # NOTE: v1 had render_dimensions_schema_block() and
    # render_dimensions_rubric() here — they were used by the deleted
    # scholar_prompts.py to interpolate dim prompts into a monolithic
    # system prompt. In v2, each dim's prompt is read directly from
    # dimensions.json and passed to generate_structured() by
    # dim_runner.py. The render functions are no longer needed.
