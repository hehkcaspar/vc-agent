"""Unit tests for the continuous-tasks management surface.

Covers the two pieces of new logic:

1. `_health_from_log` — aggregate recent eval-log entries into
   per-task health metrics. Pure function, no I/O.
2. `write_continuous_tasks` — validate-then-write helper. Uses a
   tmpdir; verifies that a broken patch leaves the file untouched.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routers.academic import _health_from_log
from app.services.academic.continuous_config import (
    load_continuous_tasks,
    write_continuous_tasks,
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


class TestWriteContinuousTasks:
    """Validate-then-write helper must leave the file untouched on error."""

    @pytest.fixture
    def sample_config(self) -> dict:
        return copy.deepcopy(_MINIMAL_CONFIG)

    def test_write_and_reload_round_trip(
        self, tmp_path: Path, sample_config: dict
    ) -> None:
        target = tmp_path / "continuous_tasks.json"
        target.write_text(json.dumps(sample_config))
        mutated = copy.deepcopy(sample_config)
        mutated["sources"]["news_web"]["default_cadence_days"] = 3

        write_continuous_tasks(mutated, path=target)
        reloaded = load_continuous_tasks(path=target)
        assert reloaded.sources["news_web"].default_cadence_days == 3

    def test_invalid_patch_is_rejected_and_file_untouched(
        self, tmp_path: Path, sample_config: dict
    ) -> None:
        target = tmp_path / "continuous_tasks.json"
        original_text = json.dumps(sample_config, indent=2)
        target.write_text(original_text)

        broken = copy.deepcopy(sample_config)
        broken["sources"]["news_web"]["default_cadence_days"] = 0  # ge=1 fails

        with pytest.raises(ValidationError):
            write_continuous_tasks(broken, path=target)

        # File on disk is unchanged.
        assert target.read_text() == original_text

    def test_broken_cross_ref_rejected(
        self, tmp_path: Path, sample_config: dict
    ) -> None:
        target = tmp_path / "continuous_tasks.json"
        target.write_text(json.dumps(sample_config))

        broken = copy.deepcopy(sample_config)
        # Point an existing dim at a nonexistent source.
        broken["dimensions"]["academic_excellence"]["required_sources"] = [
            "this_source_does_not_exist"
        ]

        with pytest.raises(ValueError, match="unknown source"):
            write_continuous_tasks(broken, path=target)
