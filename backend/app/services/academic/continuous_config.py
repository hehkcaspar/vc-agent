"""Loader + validator for the continuous-tasks config.

Architecture (post-2026-04-21 rewrite):

- **Seed** (``continuous_tasks_seed.json``, sibling of this module):
  canonical structure + defaults, tracked in git, ships with the
  backend image via ``COPY backend/`` in the Dockerfile. Read-only at
  runtime.
- **Overrides** (``ACADEMIC_CONFIG_DIR/continuous_tasks_overrides.json``):
  sparse dict of user deltas — the only mutable file. Managed by
  :mod:`continuous_overrides`. Missing file = no overrides.
- **Effective config** = ``deep_merge(seed, overrides)`` — computed
  in-memory on every load. No caching, no write-on-read.

Heartbeat re-reads on every tick (~60s); the merge is sub-millisecond.

One-time migration from the legacy ``continuous_tasks.json`` full-copy
format runs inside :func:`load_continuous_tasks` and
:func:`load_raw_continuous_tasks`: if the overrides file is absent but
the legacy file exists, the legacy values are diffed against the seed
and the deltas are persisted as overrides (see
:func:`continuous_overrides.migrate_legacy_runtime_if_needed`). The
legacy file is renamed with a ``.pre-overrides.bak`` suffix so it's
preserved but not re-read.

Unknown keys still fail loud per Concept 6 Rule 6.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import continuous_overrides

logger = logging.getLogger(__name__)


SEED_PATH = Path(__file__).parent / "continuous_tasks_seed.json"

# Fail loud at import time if the shipped seed is missing — the whole
# point of tracking it in-repo is to guarantee it's in the built image.
if not SEED_PATH.exists():
    raise RuntimeError(
        f"{SEED_PATH} is missing — this file is tracked in git and ships "
        f"with the backend image; something has gone very wrong with the "
        f"deploy or the source tree."
    )


# ── Pydantic schema (unchanged from prior version) ────────────────────


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


# ── Effective-config assembly ─────────────────────────────────────────


def _read_seed() -> dict[str, Any]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def _compute_effective(
    *,
    seed_path: Path | None = None,
    overrides_path: Path | None = None,
    legacy_path: Path | None = None,
) -> dict[str, Any]:
    """Seed + overrides merged in-memory. Pure read, apart from a
    one-time migration that runs iff the new overrides file is absent
    and a legacy runtime file is present.
    """
    continuous_overrides.migrate_legacy_runtime_if_needed(
        seed_path=seed_path or SEED_PATH,
        overrides_path=overrides_path,
        legacy_path=legacy_path,
    )
    seed = (
        json.loads(seed_path.read_text(encoding="utf-8"))
        if seed_path is not None else _read_seed()
    )
    overrides = continuous_overrides.load_overrides(overrides_path)
    return continuous_overrides.deep_merge(seed, overrides)


def load_continuous_tasks(
    seed_path: Path | None = None,
    *,
    overrides_path: Path | None = None,
) -> ContinuousTasksConfig:
    """Return the validated effective config (seed + overrides).

    Both ``seed_path`` and ``overrides_path`` default to the shipped /
    runtime locations. They're exposed as test-injection points —
    tests can point at tmp files to isolate config state.
    """
    raw = _compute_effective(
        seed_path=seed_path,
        overrides_path=overrides_path,
    )
    try:
        cfg = ContinuousTasksConfig.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"continuous_tasks config is invalid:\n{e}") from e
    cfg.validate_cross_refs()
    return cfg


def load_raw_continuous_tasks(
    seed_path: Path | None = None,
    *,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    """Return the merged raw dict (seed + overrides) without Pydantic
    coercion. Used by the router's GET/PATCH handlers so optional
    fields not declared in the schema still round-trip.
    """
    return _compute_effective(
        seed_path=seed_path,
        overrides_path=overrides_path,
    )


def validate_merged(data: dict[str, Any]) -> ContinuousTasksConfig:
    """Validate a merged-config dict against the schema + cross-refs.
    Router uses this post-PATCH to reject overrides that produce an
    invalid effective config BEFORE persisting the override.
    """
    cfg = ContinuousTasksConfig.model_validate(data)
    cfg.validate_cross_refs()
    return cfg


__all__ = [
    "SEED_PATH",
    "ContinuousTasksConfig",
    "SourceConfig",
    "DimensionTaskConfig",
    "NarrativeSynthesizerConfig",
    "PhaseClassifierConfig",
    "PriorityOverrides",
    "load_continuous_tasks",
    "load_raw_continuous_tasks",
    "validate_merged",
]
