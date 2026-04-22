"""Sparse user overrides for ``continuous_tasks_seed.json``.

Architecture:

- **Seed** (``continuous_tasks_seed.json``, in-repo) is the canonical
  source of STRUCTURE + DEFAULTS — read-only at runtime, shipped in
  the backend image.
- **Overrides** (``data/config/continuous_tasks_overrides.json``) is a
  sparse dict of user DELTAS only — the only mutable file. Stored shape
  mirrors the seed's top-level layout but each subtree carries only the
  fields the user has overridden.
- **Effective config** = ``deep_merge(seed, overrides)`` — computed in
  memory on every read, never persisted.

Read-path purity matters: ``load_continuous_tasks`` is called every
heartbeat tick (~60s) and on each router request. It must not write.

This module owns:

- ``load_overrides()`` — read the overrides file (empty dict if absent).
- ``set_override(kind, task_id, field, value)`` — patch a single leaf
  and atomically persist.
- ``deep_merge(seed, overrides)`` — pure merge used by the config loader.
- ``migrate_legacy_runtime_if_needed()`` — one-shot migration from the
  old ``continuous_tasks.json`` full-copy format.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ...config import settings

logger = logging.getLogger(__name__)

_OVERRIDES_FILENAME = "continuous_tasks_overrides.json"
_LEGACY_FILENAME = "continuous_tasks.json"
_LEGACY_BACKUP_SUFFIX = ".pre-overrides.bak"

OVERRIDES_PATH: Path = settings.ACADEMIC_CONFIG_DIR / _OVERRIDES_FILENAME
LEGACY_PATH: Path = settings.ACADEMIC_CONFIG_DIR / _LEGACY_FILENAME


# ── Atomic write ──────────────────────────────────────────────────────


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """tmp-write + rename so concurrent readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


# ── Deep merge ────────────────────────────────────────────────────────


def deep_merge(seed: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict: seed values with overrides layered on top.

    Semantics:

    - Scalars and lists in ``overrides`` REPLACE the corresponding
      seed value wholesale. (Lists are not recursively merged — the
      PATCH API doesn't expose list fields today, so a simple replace
      matches user intuition if list edits ever land.)
    - Dicts recurse: nested dicts are merged, so setting
      ``priority_overrides.high=1`` doesn't wipe ``priority_overrides.low``.
    - Keys present only in ``overrides`` but not in ``seed`` land in
      the output as-is. The caller's Pydantic validation catches
      structurally-invalid overrides (e.g. an override for a source
      not declared in the seed).
    - Neither input is mutated.
    """
    out: dict[str, Any] = {}
    for k, v in seed.items():
        if k in overrides:
            ov = overrides[k]
            if isinstance(v, dict) and isinstance(ov, dict):
                out[k] = deep_merge(v, ov)
            else:
                out[k] = ov
        else:
            out[k] = v
    for k, v in overrides.items():
        if k not in seed:
            out[k] = v
    return out


# ── File I/O ──────────────────────────────────────────────────────────


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    """Read the overrides JSON. Missing or empty file → empty dict."""
    p = path or OVERRIDES_PATH
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        logger.exception(
            "continuous_overrides: %s is not valid JSON; "
            "treating as empty overrides", p,
        )
        return {}
    return raw if isinstance(raw, dict) else {}


def save_overrides(
    overrides: dict[str, Any], path: Path | None = None,
) -> None:
    _atomic_write_json(path or OVERRIDES_PATH, overrides)


# ── One-leaf write ────────────────────────────────────────────────────


_TOP_LEVEL_SECTIONS = ("sources", "dimensions")
_FLAT_KINDS = ("phase_classifier", "narrative_synthesizer")


def set_override(
    kind: str,
    task_id: str,
    field: str,
    value: Any,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Persist a single ``field=value`` override for one task.

    - For ``kind in {"source", "dimension"}``: writes under
      ``overrides[<kind>s][task_id][field]``.
    - For flat kinds (``phase_classifier``, ``narrative_synthesizer``):
      ``task_id`` must equal the kind name; writes under
      ``overrides[kind][field]``.

    Returns the updated overrides dict. Pydantic validation of the
    merged result is the caller's responsibility — this module only
    knows about storage shape.
    """
    current = load_overrides(path)

    if kind == "source":
        section = current.setdefault("sources", {})
        entry = section.setdefault(task_id, {})
        entry[field] = value
    elif kind == "dimension":
        section = current.setdefault("dimensions", {})
        entry = section.setdefault(task_id, {})
        entry[field] = value
    elif kind in _FLAT_KINDS:
        if task_id != kind:
            raise ValueError(
                f"for kind='{kind}' task_id must equal '{kind}' "
                f"(got '{task_id}')"
            )
        section = current.setdefault(kind, {})
        section[field] = value
    else:
        raise ValueError(f"unknown kind: {kind!r}")

    save_overrides(current, path)
    return current


# ── One-time migration ───────────────────────────────────────────────


def _diff_leaves(seed: Any, legacy: Any) -> Any:
    """Return a sparse dict of legacy values that differ from seed.

    - Dict branches recurse; an empty sub-result is dropped.
    - List leaves: a list that is a REORDERING of the seed list (same
      elements, assuming hashable) is NOT treated as an override —
      this is almost always a seed-update artifact (we appended a new
      entry to the canonical order) rather than a user edit. If the
      content sets differ, we keep the legacy list as an override so
      the user's genuine list edit (add/remove) is preserved.
    - Scalar leaves: keep iff legacy != seed.
    - Keys in legacy but not in seed are kept verbatim — the user may
      have extended the config; Pydantic validation decides legality.
    """
    if isinstance(seed, dict) and isinstance(legacy, dict):
        out: dict[str, Any] = {}
        for k, lv in legacy.items():
            if k not in seed:
                out[k] = lv
                continue
            sub = _diff_leaves(seed[k], lv)
            if isinstance(sub, dict):
                if sub:
                    out[k] = sub
            elif sub is not _SENTINEL_SKIP:
                out[k] = sub
        return out
    if isinstance(seed, list) and isinstance(legacy, list):
        try:
            if set(seed) == set(legacy):
                # Same set, possibly different order → treat as seed
                # owned, not a user override.
                return _SENTINEL_SKIP
        except TypeError:
            # Unhashable elements (list of dicts) — fall through to
            # strict equality below.
            pass
        return legacy if legacy != seed else _SENTINEL_SKIP
    if legacy == seed:
        return _SENTINEL_SKIP
    return legacy


# Sentinel to signal "no difference, drop this leaf" without colliding
# with any real legacy value (including None, 0, "", []).
class _Skip:
    __slots__ = ()


_SENTINEL_SKIP = _Skip()


def migrate_legacy_runtime_if_needed(
    *,
    seed_path: Path,
    overrides_path: Path | None = None,
    legacy_path: Path | None = None,
) -> bool:
    """If the new overrides file is absent but the legacy full-copy
    runtime file exists, diff the legacy against the seed and persist
    just the deltas as overrides. Legacy file is renamed to
    ``.pre-overrides.bak``.

    Returns True if a migration ran, False if no action was needed.
    Safe to call on every read — subsequent calls take the
    "overrides already exist" branch.
    """
    op = overrides_path or OVERRIDES_PATH
    lp = legacy_path or LEGACY_PATH

    if op.exists():
        return False
    if not lp.exists():
        # Fresh install: write an empty overrides file so subsequent
        # loads short-circuit here and never re-enter the migration.
        save_overrides({}, op)
        return False

    try:
        legacy = json.loads(lp.read_text(encoding="utf-8"))
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "continuous_overrides: legacy-migration load failed — "
            "leaving legacy file in place", exc,
        )
        return False

    overrides = _diff_leaves(seed, legacy)
    if not isinstance(overrides, dict):
        overrides = {}

    save_overrides(overrides, op)
    backup = lp.with_suffix(lp.suffix + _LEGACY_BACKUP_SUFFIX)
    try:
        os.replace(lp, backup)
    except OSError as exc:
        logger.warning(
            "continuous_overrides: legacy rename failed (%s); "
            "overrides file still written so next load will not "
            "re-migrate", exc,
        )
    logger.info(
        "continuous_overrides: migrated legacy runtime config → "
        "overrides.json with %d top-level keys; original saved to %s",
        len(overrides), backup,
    )
    return True


__all__ = [
    "OVERRIDES_PATH",
    "LEGACY_PATH",
    "deep_merge",
    "load_overrides",
    "save_overrides",
    "set_override",
    "migrate_legacy_runtime_if_needed",
]
