"""Loader + validator for ``continuous_tasks.json``.

Heartbeat re-reads this on every tick (no caching) so changes take
effect without restart. Validation is fail-loud per Concept 6 Rule 6:
unknown keys are rejected.

Two files are involved — same split as ``dimensions.py``:

- **Seed** (``continuous_tasks_seed.json``, sibling of this module):
  canonical schedule tracked in git and shipped with the backend
  package via ``COPY backend/`` in the Dockerfile.
- **Runtime** (``ACADEMIC_CONFIG_DIR/continuous_tasks.json``):
  per-environment override state, gitignored. On first read after a
  deploy the runtime file is seeded from the shipped JSON so the
  heartbeat loop never starts with a missing-file error.

Historical note: this module previously raised ``FileNotFoundError``
and relied on ``ensure_universal_configs_seeded()`` (gc-deploy only)
to provision the file. On fresh-clone dev, tests, or main-branch CI
the heartbeat tick would spam a ``continuous_tasks.json failed to
load`` error every tick. Fixed 2026-04-19 by mirroring the dim-seed
pattern.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...config import settings


CONTINUOUS_TASKS_PATH = settings.ACADEMIC_CONFIG_DIR / "continuous_tasks.json"
SEED_PATH = Path(__file__).parent / "continuous_tasks_seed.json"

# Fail loud at import time if the shipped seed is missing — the whole
# point of tracking it in-repo is to guarantee it's in the built image.
if not SEED_PATH.exists():
    raise RuntimeError(
        f"{SEED_PATH} is missing — this file is tracked in git and ships "
        f"with the backend image; something has gone very wrong with the "
        f"deploy or the source tree."
    )


def _seed_runtime_config(path: Path) -> None:
    """Copy the shipped seed to the runtime path on first read.

    Parents are created as needed. Subsequent reads see the runtime
    file as-is — user customisations (e.g. via the Tasks view or
    ``PATCH /academic/continuous-tasks/...``) persist across restarts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SEED_PATH, path)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriorityOverrides(_Strict):
    high: int | None = None
    low: int | None = None


class SourceConfig(_Strict):
    layer: Literal[2]
    enabled: bool
    default_cadence_days: int = Field(ge=1)
    priority_overrides: PriorityOverrides | None = None
    rate_limit_per_minute: int | None = None
    on_failure: Literal["retry_next_tick", "skip"] | None = None
    description: str | None = None


class DimensionTaskConfig(_Strict):
    layer: Literal[3]
    enabled: bool
    default_cadence_days: int = Field(ge=1)
    required_sources: list[str]
    triage_model: str
    scoring_model: str


class NarrativeSynthesizerConfig(_Strict):
    layer: Literal[3]
    enabled: bool
    default_cadence_days: int = Field(ge=1)
    model: str
    on_demand_only: bool = False


class PhaseClassifierConfig(_Strict):
    layer: Literal[3]
    enabled: bool
    default_cadence_days: int = Field(ge=1)
    required_sources: list[str]
    triage_model: str
    classifier_model: str
    writes_to: str
    description: str | None = None


class ContinuousTasksConfig(_Strict):
    sources: dict[str, SourceConfig]
    dimensions: dict[str, DimensionTaskConfig]
    narrative_synthesizer: NarrativeSynthesizerConfig
    phase_classifier: PhaseClassifierConfig

    def validate_cross_refs(self) -> None:
        """Ensure every dim's required_sources exists in sources."""
        known = set(self.sources)
        for dim_id, dim in self.dimensions.items():
            for src in dim.required_sources:
                if src not in known:
                    raise ValueError(
                        f"dimension '{dim_id}' references unknown source '{src}'"
                    )
        for src in self.phase_classifier.required_sources:
            if src not in known:
                raise ValueError(
                    f"phase_classifier references unknown source '{src}'"
                )


def load_continuous_tasks(path: Path | None = None) -> ContinuousTasksConfig:
    """Read + validate the config file. Raises on malformed content.

    Seeds from the shipped ``continuous_tasks_seed.json`` if the
    runtime file is missing. Always re-reads from disk; do not cache.
    """
    p = path or CONTINUOUS_TASKS_PATH
    if not p.exists():
        _seed_runtime_config(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    try:
        cfg = ContinuousTasksConfig.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"continuous_tasks.json is invalid:\n{e}") from e
    cfg.validate_cross_refs()
    return cfg


def load_raw_continuous_tasks(path: Path | None = None) -> dict[str, Any]:
    """Return the raw JSON dict without Pydantic coercion.

    Used by the router PATCH handler so we can mutate a single slot
    in place and re-validate the whole file before writing. Keeping
    raw dict shape (vs dumping the Pydantic model) avoids dropping
    optional fields we didn't model explicitly.

    Seeds from the shipped seed if the runtime file is missing, for
    symmetry with ``load_continuous_tasks()``.
    """
    p = path or CONTINUOUS_TASKS_PATH
    if not p.exists():
        _seed_runtime_config(p)
    return json.loads(p.read_text(encoding="utf-8"))


def write_continuous_tasks(
    data: dict[str, Any],
    *,
    path: Path | None = None,
) -> None:
    """Validate *data* against the schema, then write atomically.

    The config file is never left in a broken state: if validation
    fails, ``ValidationError`` bubbles up and the file on disk is
    untouched. On success we write to a sibling ``.tmp`` file and
    ``os.replace()`` so concurrent readers (heartbeat) always see a
    consistent snapshot.
    """
    p = path or CONTINUOUS_TASKS_PATH
    # Validation round-trip — raises on any issue.
    cfg = ContinuousTasksConfig.model_validate(data)
    cfg.validate_cross_refs()

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, p)
