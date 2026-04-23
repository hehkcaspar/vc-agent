"""Unit tests for the continuous-tasks management surface.

Covers two pieces of logic:

1. ``_health_from_log`` — aggregate recent eval-log entries into
   per-task health metrics. Pure function, no I/O.
2. Overrides-backed read/write path — exercise the seed + sparse
   overrides architecture end-to-end: ``set_override`` writes,
   ``load_continuous_tasks`` merges, ``validate_merged`` rejects
   invalid patches before they'd hit disk.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routers.academic import _health_from_log
from app.services.academic import continuous_overrides as co
from app.services.academic.continuous_config import (
    load_continuous_tasks,
    validate_merged,
)


_NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)


def _entry(step: str, status: str, hours_ago: int, duration_s: float | None = None,
           detail=None) -> dict:
    ts = (_NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    out = {"ts": ts, "step": step, "status": status, "scholar_id": "x"}
    if duration_s is not None:
        out["duration_s"] = duration_s
    if detail is not None:
        out["detail"] = detail
    return out


class TestHealthFromLog:
    def test_empty_log_returns_zeroed_shape(self) -> None:
        h = _health_from_log("source/news_web", [], _NOW)
        assert h == {
            "runs_7d": 0,
            "success_rate_7d": None,
            "avg_duration_s_7d": None,
            "last_run_ts": None,
            "last_status": None,
            "last_error": None,
        }

    def test_skips_start_entries_and_other_steps(self) -> None:
        entries = [
            _entry("source/news_web", "start", hours_ago=1),
            _entry("source/other", "done", hours_ago=1, duration_s=1.0),
        ]
        h = _health_from_log("source/news_web", entries, _NOW)
        assert h["runs_7d"] == 0
        assert h["last_status"] is None

    def test_counts_successes_and_failures_within_7d(self) -> None:
        entries = [
            _entry("source/news_web", "done", hours_ago=2, duration_s=1.0),
            _entry("source/news_web", "done", hours_ago=24, duration_s=2.0),
            _entry("source/news_web", "error", hours_ago=48, detail="boom"),
        ]
        h = _health_from_log("source/news_web", entries, _NOW)
        assert h["runs_7d"] == 3
        assert h["success_rate_7d"] == round(2 / 3, 3)
        # avg only counts entries with duration_s
        assert h["avg_duration_s_7d"] == 1.5

    def test_drops_runs_older_than_7d(self) -> None:
        entries = [
            _entry("source/news_web", "done", hours_ago=2, duration_s=1.0),
            _entry("source/news_web", "done", hours_ago=24 * 10, duration_s=99.0),
        ]
        h = _health_from_log("source/news_web", entries, _NOW)
        assert h["runs_7d"] == 1
        assert h["avg_duration_s_7d"] == 1.0

    def test_newest_entry_populates_last_status(self) -> None:
        # Entries arrive newest-first from read_tail_jsonl.
        entries = [
            _entry("source/news_web", "error", hours_ago=1, detail="connection reset"),
            _entry("source/news_web", "done", hours_ago=2, duration_s=1.0),
        ]
        h = _health_from_log("source/news_web", entries, _NOW)
        assert h["last_status"] == "error"
        assert h["last_error"] == "connection reset"

    def test_last_error_not_set_for_done_status(self) -> None:
        entries = [
            _entry("source/news_web", "done", hours_ago=1, duration_s=1.0, detail={"ok": True}),
        ]
        h = _health_from_log("source/news_web", entries, _NOW)
        assert h["last_status"] == "done"
        assert h["last_error"] is None

    def test_cancelled_counts_as_non_success(self) -> None:
        entries = [
            _entry("source/news_web", "cancelled", hours_ago=1, detail="cancel"),
            _entry("source/news_web", "done", hours_ago=2, duration_s=1.0),
        ]
        h = _health_from_log("source/news_web", entries, _NOW)
        assert h["runs_7d"] == 2
        assert h["success_rate_7d"] == 0.5


_MINIMAL_CONFIG: dict = {
    "sources": {
        "news_web": {
            "layer": 2,
            "enabled": True,
            "default_cadence_days": 1,
            "description": "test source",
        },
        "patents_web": {
            "layer": 2,
            "enabled": True,
            "default_cadence_days": 30,
        },
    },
    "dimensions": {
        "academic_excellence": {
            "layer": 3,
            "enabled": True,
            "default_cadence_days": 14,
            "required_sources": ["news_web"],
            "triage_model": "gemini-3-flash-preview",
            "scoring_model": "gemini-3.1-pro-preview",
        }
    },
    "narrative_synthesizer": {
        "layer": 3,
        "enabled": True,
        "default_cadence_days": 30,
        "model": "gemini-3.1-pro-preview",
        "on_demand_only": False,
    },
    "phase_classifier": {
        "layer": 3,
        "enabled": True,
        "default_cadence_days": 60,
        "required_sources": ["news_web"],
        "triage_model": "gemini-3-flash-preview",
        "classifier_model": "gemini-3.1-pro-preview",
        "writes_to": "peer_group.jsonl",
    },
}


class TestOverridesBackedRoundTrip:
    """Exercise the seed + sparse-overrides architecture end-to-end.

    Setup: a tmp seed file (full valid config) + a tmp overrides file
    that starts empty. ``set_override`` writes deltas; reloads merge;
    ``validate_merged`` is the gate that catches bad values before
    they'd ever get persisted (the router uses the same gate).
    """

    @pytest.fixture
    def seed_and_overrides(self, tmp_path: Path) -> tuple[Path, Path]:
        seed = tmp_path / "seed.json"
        seed.write_text(
            json.dumps(copy.deepcopy(_MINIMAL_CONFIG)), encoding="utf-8",
        )
        overrides = tmp_path / "continuous_tasks_overrides.json"
        return seed, overrides

    def test_override_round_trip(
        self, seed_and_overrides: tuple[Path, Path]
    ) -> None:
        seed, overrides = seed_and_overrides
        co.set_override(
            "source", "news_web", "default_cadence_days", 3,
            path=overrides,
        )
        reloaded = load_continuous_tasks(
            seed_path=seed, overrides_path=overrides,
        )
        assert reloaded.sources["news_web"].default_cadence_days == 3
        # Seed sibling fields survive
        assert reloaded.sources["news_web"].enabled is True

    def test_invalid_merged_config_rejected_without_persist(
        self, seed_and_overrides: tuple[Path, Path]
    ) -> None:
        seed, overrides = seed_and_overrides
        # Build what the merged config WOULD look like after a bad
        # patch. The router calls validate_merged BEFORE set_override,
        # so an invalid patch never hits disk.
        seed_raw = json.loads(seed.read_text())
        pending_overrides = {
            "sources": {"news_web": {"default_cadence_days": 0}}  # ge=1 fails
        }
        merged = co.deep_merge(seed_raw, pending_overrides)
        with pytest.raises(ValidationError):
            validate_merged(merged)
        # Overrides file untouched (validator is called before write).
        assert not overrides.exists()

    def test_broken_cross_ref_rejected(
        self, seed_and_overrides: tuple[Path, Path]
    ) -> None:
        seed, _overrides = seed_and_overrides
        seed_raw = json.loads(seed.read_text())
        pending_overrides = {
            "dimensions": {
                "academic_excellence": {
                    "required_sources": ["this_source_does_not_exist"],
                }
            }
        }
        merged = co.deep_merge(seed_raw, pending_overrides)
        with pytest.raises(ValueError, match="unknown source"):
            validate_merged(merged)
