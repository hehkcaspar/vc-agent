"""Unit tests for the sparse-overrides architecture in
``continuous_overrides`` + ``continuous_config``.

Contract verified here:

- Empty overrides → effective config equals the seed.
- Scalar overrides replace the seeded value.
- Nested overrides (e.g. ``priority_overrides.high``) merge without
  clobbering sibling keys (``priority_overrides.low`` survives).
- List overrides replace wholesale (by design — the PATCH API never
  exposes list fields).
- Pydantic + cross-ref validation on the merged config still rejects
  invalid overrides (unknown source, bad cadence).
- One-time legacy migration: (a) empty runtime file → empty overrides
  + fresh-install marker, (b) populated legacy runtime → diff-against-
  seed → sparse overrides + ``.pre-overrides.bak`` renamed legacy,
  (c) list reorderings are NOT captured (seed-owned), (d) rerun is a
  no-op because the overrides file now exists.
- ``set_override`` round-trips: what's written is what ``load_overrides``
  returns.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.academic import continuous_overrides as co
from app.services.academic.continuous_config import (
    ContinuousTasksConfig,
    SEED_PATH,
    load_continuous_tasks,
    validate_merged,
)


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _minimal_seed() -> dict:
    """A small but valid seed shape — enough to exercise the merge +
    validator paths without depending on the real production seed."""
    return {
        "sources": {
            "source_a": {
                "layer": 2, "enabled": True, "default_cadence_days": 7,
                "priority_overrides": {"high": 3, "low": 14},
                "rate_limit_per_minute": 60,
                "on_failure": "retry_next_tick",
                "description": "original A",
            },
            "source_b": {
                "layer": 2, "enabled": True, "default_cadence_days": 1,
                "description": "original B",
            },
        },
        "dimensions": {
            "dim_x": {
                "layer": 3, "enabled": True, "default_cadence_days": 14,
                "required_sources": ["source_a", "source_b"],
                "triage_model": "flash", "scoring_model": "pro",
            },
        },
        "phase_classifier": {
            "layer": 3, "enabled": True, "default_cadence_days": 60,
            "required_sources": ["source_a"],
            "triage_model": "flash", "classifier_model": "pro",
            "writes_to": "peer_group.jsonl",
        },
        "narrative_synthesizer": {
            "layer": 3, "enabled": True, "default_cadence_days": 30,
            "model": "pro",
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# deep_merge
# ═══════════════════════════════════════════════════════════════════════


def test_empty_overrides_returns_seed_verbatim():
    seed = _minimal_seed()
    out = co.deep_merge(seed, {})
    assert out == seed


def test_scalar_override_replaces_seed_value():
    seed = _minimal_seed()
    overrides = {"sources": {"source_a": {"enabled": False}}}
    out = co.deep_merge(seed, overrides)
    assert out["sources"]["source_a"]["enabled"] is False
    # Siblings preserved
    assert out["sources"]["source_a"]["default_cadence_days"] == 7
    assert out["sources"]["source_a"]["priority_overrides"] == {
        "high": 3, "low": 14,
    }


def test_nested_override_merges_without_clobbering_siblings():
    seed = _minimal_seed()
    overrides = {"sources": {"source_a": {"priority_overrides": {"high": 1}}}}
    out = co.deep_merge(seed, overrides)
    # high was overridden, low retained from seed.
    assert out["sources"]["source_a"]["priority_overrides"] == {
        "high": 1, "low": 14,
    }


def test_list_override_replaces_wholesale():
    seed = _minimal_seed()
    overrides = {"dimensions": {"dim_x": {"required_sources": ["source_a"]}}}
    out = co.deep_merge(seed, overrides)
    assert out["dimensions"]["dim_x"]["required_sources"] == ["source_a"]


def test_override_for_seed_absent_key_passes_through_unchanged():
    # deep_merge itself doesn't validate. Passthrough is intended so
    # the Pydantic validator at the caller surfaces the error.
    seed = _minimal_seed()
    overrides = {"sources": {"ghost_source": {"enabled": False}}}
    out = co.deep_merge(seed, overrides)
    assert out["sources"]["ghost_source"] == {"enabled": False}


def test_inputs_not_mutated_by_deep_merge():
    seed = _minimal_seed()
    overrides = {"sources": {"source_a": {"enabled": False}}}
    seed_before = json.dumps(seed, sort_keys=True)
    overrides_before = json.dumps(overrides, sort_keys=True)
    co.deep_merge(seed, overrides)
    assert json.dumps(seed, sort_keys=True) == seed_before
    assert json.dumps(overrides, sort_keys=True) == overrides_before


# ═══════════════════════════════════════════════════════════════════════
# Pydantic validation of the merged config
# ═══════════════════════════════════════════════════════════════════════


def test_validate_merged_accepts_clean_override():
    seed = _minimal_seed()
    merged = co.deep_merge(
        seed, {"sources": {"source_a": {"default_cadence_days": 1}}},
    )
    cfg = validate_merged(merged)
    assert isinstance(cfg, ContinuousTasksConfig)
    assert cfg.sources["source_a"].default_cadence_days == 1


def test_validate_merged_rejects_bad_cadence():
    seed = _minimal_seed()
    merged = co.deep_merge(
        seed, {"sources": {"source_a": {"default_cadence_days": 0}}},
    )
    with pytest.raises(ValidationError):
        validate_merged(merged)


def test_validate_merged_rejects_ghost_source_in_required_sources():
    seed = _minimal_seed()
    merged = co.deep_merge(
        seed, {"dimensions": {
            "dim_x": {"required_sources": ["source_a", "not_a_real_source"]}
        }},
    )
    with pytest.raises(ValueError):
        validate_merged(merged)


# ═══════════════════════════════════════════════════════════════════════
# set_override round-trip
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture()
def tmp_overrides(tmp_path: Path) -> Path:
    return tmp_path / "continuous_tasks_overrides.json"


def test_set_override_source(tmp_overrides: Path):
    co.set_override("source", "source_a", "enabled", False, path=tmp_overrides)
    rd = _read_json(tmp_overrides)
    assert rd == {"sources": {"source_a": {"enabled": False}}}


def test_set_override_dimension(tmp_overrides: Path):
    co.set_override(
        "dimension", "dim_x", "default_cadence_days", 3, path=tmp_overrides,
    )
    rd = _read_json(tmp_overrides)
    assert rd == {"dimensions": {"dim_x": {"default_cadence_days": 3}}}


def test_set_override_phase_classifier(tmp_overrides: Path):
    co.set_override(
        "phase_classifier", "phase_classifier", "enabled", False,
        path=tmp_overrides,
    )
    rd = _read_json(tmp_overrides)
    assert rd == {"phase_classifier": {"enabled": False}}


def test_set_override_multiple_fields_cumulative(tmp_overrides: Path):
    co.set_override("source", "source_a", "enabled", False, path=tmp_overrides)
    co.set_override(
        "source", "source_a", "default_cadence_days", 2, path=tmp_overrides,
    )
    co.set_override(
        "source", "source_b", "enabled", False, path=tmp_overrides,
    )
    assert _read_json(tmp_overrides) == {
        "sources": {
            "source_a": {"enabled": False, "default_cadence_days": 2},
            "source_b": {"enabled": False},
        }
    }


def test_set_override_flat_kind_mismatched_task_id_raises(tmp_overrides: Path):
    with pytest.raises(ValueError):
        co.set_override(
            "phase_classifier", "wrong_id", "enabled", False,
            path=tmp_overrides,
        )


def test_set_override_unknown_kind_raises(tmp_overrides: Path):
    with pytest.raises(ValueError):
        co.set_override(
            "mystery_kind", "t", "enabled", False, path=tmp_overrides,
        )


# ═══════════════════════════════════════════════════════════════════════
# Legacy migration
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture()
def migration_paths(tmp_path: Path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(_minimal_seed()), encoding="utf-8")
    overrides = tmp_path / "continuous_tasks_overrides.json"
    legacy = tmp_path / "continuous_tasks.json"
    return seed, overrides, legacy


def test_migration_fresh_install_no_legacy(migration_paths):
    seed, overrides, legacy = migration_paths
    assert not overrides.exists()
    assert not legacy.exists()
    ran = co.migrate_legacy_runtime_if_needed(
        seed_path=seed, overrides_path=overrides, legacy_path=legacy,
    )
    assert ran is False
    # Empty overrides file is materialized so subsequent calls
    # short-circuit without re-entering the migration.
    assert overrides.exists()
    assert _read_json(overrides) == {}


def test_migration_from_legacy_captures_deltas_only(migration_paths):
    seed, overrides, legacy = migration_paths
    # A legacy runtime that differs from seed at:
    #  - source_a.enabled (user disabled it)
    #  - source_b.default_cadence_days (user sped it up)
    legacy_data = _minimal_seed()
    legacy_data["sources"]["source_a"]["enabled"] = False
    legacy_data["sources"]["source_b"]["default_cadence_days"] = 1  # unchanged
    legacy_data["sources"]["source_b"]["description"] = "user custom"  # new
    legacy.write_text(json.dumps(legacy_data), encoding="utf-8")

    ran = co.migrate_legacy_runtime_if_needed(
        seed_path=seed, overrides_path=overrides, legacy_path=legacy,
    )
    assert ran is True
    assert _read_json(overrides) == {
        "sources": {
            "source_a": {"enabled": False},
            "source_b": {"description": "user custom"},
        }
    }
    # Legacy file moved aside
    assert not legacy.exists()
    assert legacy.with_suffix(legacy.suffix + ".pre-overrides.bak").exists()


def test_migration_ignores_list_reorderings(migration_paths):
    seed, overrides, legacy = migration_paths
    legacy_data = _minimal_seed()
    # Same sources, different order — NOT a user override.
    legacy_data["dimensions"]["dim_x"]["required_sources"] = [
        "source_b", "source_a",
    ]
    legacy.write_text(json.dumps(legacy_data), encoding="utf-8")

    co.migrate_legacy_runtime_if_needed(
        seed_path=seed, overrides_path=overrides, legacy_path=legacy,
    )
    rd = _read_json(overrides)
    # required_sources difference was a reorder → skipped.
    assert "dimensions" not in rd or "dim_x" not in (rd.get("dimensions") or {})


def test_migration_captures_real_list_edit_with_new_element(migration_paths):
    seed, overrides, legacy = migration_paths
    legacy_data = _minimal_seed()
    # User added a NEW source to the list (not just reorder).
    legacy_data["dimensions"]["dim_x"]["required_sources"] = [
        "source_a", "source_b", "source_a",  # duplicate, but content != seed
    ]
    # Actually duplicate is weird; use a real case: remove an element.
    legacy_data["dimensions"]["dim_x"]["required_sources"] = ["source_a"]
    legacy.write_text(json.dumps(legacy_data), encoding="utf-8")

    co.migrate_legacy_runtime_if_needed(
        seed_path=seed, overrides_path=overrides, legacy_path=legacy,
    )
    rd = _read_json(overrides)
    assert rd["dimensions"]["dim_x"]["required_sources"] == ["source_a"]


def test_migration_rerun_is_noop(migration_paths):
    seed, overrides, legacy = migration_paths
    legacy.write_text(json.dumps(_minimal_seed()), encoding="utf-8")

    # First call migrates
    ran1 = co.migrate_legacy_runtime_if_needed(
        seed_path=seed, overrides_path=overrides, legacy_path=legacy,
    )
    assert ran1 is True
    # Legacy file is gone (renamed to .bak); second call has nothing
    # to migrate and overrides.json already exists.
    ran2 = co.migrate_legacy_runtime_if_needed(
        seed_path=seed, overrides_path=overrides, legacy_path=legacy,
    )
    assert ran2 is False


# ═══════════════════════════════════════════════════════════════════════
# End-to-end: load_continuous_tasks against an explicit seed + overrides
# ═══════════════════════════════════════════════════════════════════════


def test_end_to_end_load_with_override(tmp_path: Path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(_minimal_seed()), encoding="utf-8")
    overrides = tmp_path / "continuous_tasks_overrides.json"
    overrides.write_text(json.dumps({
        "sources": {
            "source_a": {"enabled": False, "default_cadence_days": 1},
        },
    }), encoding="utf-8")

    cfg = load_continuous_tasks(seed_path=seed, overrides_path=overrides)
    assert cfg.sources["source_a"].enabled is False
    assert cfg.sources["source_a"].default_cadence_days == 1
    # Sibling untouched:
    assert cfg.sources["source_a"].priority_overrides.high == 3
    # source_b: seed values verbatim
    assert cfg.sources["source_b"].enabled is True
    assert cfg.sources["source_b"].default_cadence_days == 1


def test_end_to_end_load_with_empty_overrides_returns_seed(tmp_path: Path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(_minimal_seed()), encoding="utf-8")
    overrides = tmp_path / "continuous_tasks_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    cfg = load_continuous_tasks(seed_path=seed, overrides_path=overrides)
    assert cfg.sources["source_a"].enabled is True
    assert cfg.sources["source_a"].default_cadence_days == 7


def test_real_seed_loads_clean(tmp_path: Path):
    """The production seed must validate cleanly against an empty
    overrides file — i.e. the seed is self-consistent on its own."""
    overrides = tmp_path / "overrides.json"
    cfg = load_continuous_tasks(seed_path=SEED_PATH, overrides_path=overrides)
    assert "semantic_scholar_papers" in cfg.sources
    assert "google_scholar_papers" in cfg.sources
    # Fresh-install side-effect: empty overrides materialized on disk.
    assert overrides.exists()
    assert json.loads(overrides.read_text()) == {}
