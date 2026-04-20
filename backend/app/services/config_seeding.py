"""Startup seed for universal config files.

Files that ship with the code (field archetypes, heartbeat schedule,
starter ranking presets) live under `backend/app/defaults/` and are
copied into `settings.ACADEMIC_CONFIG_DIR` on startup if the target is
missing. Existing files are never overwritten — user customisations via
the Settings UI persist across deploys.

This replaces the earlier deploy-time `gsutil cp data/config/* ...`
workaround that accidentally leaked a dev-machine weekly digest to prod.

Per-environment files (`funds.json`, `digests/*.md`) are intentionally
NOT seeded here — they must come from user action or runtime generation.

`dimensions.json` and `continuous_tasks.json` used to live here too but
migrated in 2026-04-20 to the in-package seed pattern
(`backend/app/services/academic/*_seed.json`). Their reader modules
(`dimensions.py`, `continuous_config.py`) self-seed the runtime file
from the sibling seed JSON on first read. Keeping two parallel seeding
mechanisms for the same file invited drift, so this module now only
handles configs whose reader doesn't implement self-seeding.

`legal_templates.json` and `legal_review_checklist.json` have their own
`ensure_*_seed()` helpers in their config modules (Pydantic-validated
inline Python defaults). This module only seeds flat files that are
uploaded wholesale from disk.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"

_FLAT_FILES: tuple[str, ...] = (
    "field_archetypes.json",
    "heartbeat.json",
)


def ensure_universal_configs_seeded() -> None:
    """Copy repo-embedded defaults into ACADEMIC_CONFIG_DIR where missing.

    Safe to call on every startup. No-ops for files that already exist.
    """
    target_dir = settings.ACADEMIC_CONFIG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    for name in _FLAT_FILES:
        src = _DEFAULTS_DIR / name
        dst = target_dir / name
        if not src.exists():
            logger.warning("default config missing from repo: %s", src)
            continue
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        logger.info("seeded %s → %s", src.name, dst)

    presets_src = _DEFAULTS_DIR / "ranking_presets"
    presets_dst = target_dir / "ranking_presets"
    if presets_src.exists():
        presets_dst.mkdir(parents=True, exist_ok=True)
        for preset in presets_src.glob("*.json"):
            dst = presets_dst / preset.name
            if dst.exists():
                continue
            shutil.copy2(preset, dst)
            logger.info("seeded ranking_presets/%s", preset.name)
