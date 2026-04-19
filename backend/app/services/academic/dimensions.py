"""
Scholar evaluation dimensions — JSON-backed, deploy-tracked configuration.

Two files are involved:

- **Seed** (``dimensions_seed.json``, sibling of this module): the canonical
  prompt content. Tracked in git, ships with the backend package via the
  existing ``COPY backend/`` in the Dockerfile, so every deploy carries the
  latest prompts into the image. Edit this file when iterating on prompt
  content — changes propagate to prod on redeploy.
- **Runtime** (``ACADEMIC_CONFIG_DIR/dimensions.json``): per-environment
  override state. Gitignored (lives under ``data/`` locally, under the GCS
  FUSE mount in prod). On first read after a deploy the runtime file is
  seeded from ``dimensions_seed.json``; the Settings UI's CRUD endpoints
  then write user customizations back here.

Why the split: the runtime file was previously the ONLY source of rich
prompts — but it's gitignored, so the rich content never reached prod on
first deploy. Pulling the seed into the tracked package solves that
systematically. (Fixed 2026-04-19; see commit log for context.)

To update prompts on an existing prod deploy whose bucket already has a
``dimensions.json``, either edit via the Settings UI (persists in the
bucket) or ``gsutil rm gs://$GCS_BUCKET/config/dimensions.json`` before
redeploying to force a re-seed from the shipped file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from ...config import settings


DIMENSIONS_PATH = settings.ACADEMIC_CONFIG_DIR / "dimensions.json"
SEED_PATH = Path(__file__).parent / "dimensions_seed.json"


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


def _load_seed() -> list[Dimension]:
    """Load the shipped seed. Fail loud if it's missing or malformed —
    the whole point of shipping it in-repo is to guarantee it exists."""
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    dims = data.get("dimensions")
    if not isinstance(dims, list) or not dims:
        raise RuntimeError(
            f"{SEED_PATH} is missing or contains no dimensions — "
            f"this file is tracked in git and ships with the image; "
            f"something has gone very wrong with the deploy."
        )
    return dims


# Loaded once at module import. Every deploy ships the current seed.
DEFAULT_DIMENSIONS: list[Dimension] = _load_seed()


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
    """Atomically replace ``dimensions.json``.

    Concurrent readers (``dim_runner`` on every heartbeat tick) must
    never see a half-written file. Write to a sibling ``.tmp`` and
    ``os.replace`` — mirrors ``continuous_config.write_continuous_tasks``.
    """
    DIMENSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DIMENSIONS_PATH.with_suffix(DIMENSIONS_PATH.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"dimensions": dims}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, DIMENSIONS_PATH)


    # NOTE: v1 had render_dimensions_schema_block() and
    # render_dimensions_rubric() here — they were used by the deleted
    # scholar_prompts.py to interpolate dim prompts into a monolithic
    # system prompt. In v2, each dim's prompt is read directly from
    # dimensions.json and passed to generate_structured() by
    # dim_runner.py. The render functions are no longer needed.
