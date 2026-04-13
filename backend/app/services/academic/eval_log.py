"""Structured evaluation log for manual + continuous tasks.

Single append-only JSONL file at `data/logs/evaluation.jsonl`, one line per
step event. Every step emits a pair of events:

    {"ts": …, "scholar_id": …, "step": "source/news_web", "status": "start", …}
    {"ts": …, "scholar_id": …, "step": "source/news_web", "status": "done",
     "duration_s": 12.4, …}

The public surface is intentionally small:

    log_eval(...)           — primitive one-shot write
    log_step(...)           — async context manager (preferred)
    read_tail_jsonl(...)    — constant-memory tail reader for the API
    rotate_if_needed()      — called once on startup to cap the file

Terminal statuses: ``done`` (success), ``error`` (raised Exception),
``cancelled`` (task was ``asyncio.cancel()``-ed). Any ``start`` without a
matching terminal for the same ``(scholar_id, step)`` counts as
"currently running" to the frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from ...config import settings

logger = logging.getLogger(__name__)

LOG_DIR = Path(settings.ACADEMIC_SCHOLARS_DIR).parent / "logs"
LOG_PATH = LOG_DIR / "evaluation.jsonl"

# Rotation bounds: cap file at MAX_BYTES or MAX_LINES — whichever
# triggers first — by trimming in place to KEEP_LINES most recent.
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_LINES = 20_000
KEEP_LINES = 5_000


def _ensure_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_eval(
    scholar_id: str,
    step: str,
    status: str = "ok",
    *,
    detail: Any = None,
    duration_s: float | None = None,
    scholar_name: str | None = None,
) -> None:
    """Append one log entry. Never raises — logging must not break work."""
    try:
        _ensure_dir()
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scholar_id": scholar_id,
            "step": step,
            "status": status,
        }
        if scholar_name:
            entry["scholar_name"] = scholar_name
        if duration_s is not None:
            entry["duration_s"] = round(duration_s, 2)
        if detail is not None:
            entry["detail"] = detail
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass  # intentionally swallowed


@dataclass
class StepContext:
    """Mutable handle yielded by `log_step` so callers can attach `detail`.

    Assign a dict / scalar to `detail` before the `async with` block exits
    and it will be written onto the `done` entry. Useful for capturing
    scores, counts, or other per-run facts that belong next to timing.
    """

    detail: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@asynccontextmanager
async def log_step(
    scholar_id: str,
    step: str,
    *,
    scholar_name: str | None = None,
) -> AsyncIterator[StepContext]:
    """Log ``start`` on entry and ``done`` / ``error`` / ``cancelled`` on exit.

    Distinguishes ``asyncio.CancelledError`` from other exceptions so the
    frontend can render cancelled steps separately from real failures.
    Re-raises every exception (including CancelledError) so normal flow
    control is unchanged.
    """
    ctx = StepContext()
    log_eval(scholar_id, step, "start", scholar_name=scholar_name)
    t0 = time.monotonic()
    try:
        yield ctx
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        msg = str(exc) or type(exc).__name__
        log_eval(
            scholar_id,
            step,
            status,
            duration_s=time.monotonic() - t0,
            detail=msg,
            scholar_name=scholar_name,
        )
        raise
    log_eval(
        scholar_id,
        step,
        "done",
        duration_s=time.monotonic() - t0,
        detail=ctx.detail,
        scholar_name=scholar_name,
    )


def rotate_if_needed() -> None:
    """Trim the log file in place if it exceeds size or line bounds.

    Called once on startup. Keeps the last ``KEEP_LINES`` entries. Writes
    to a sibling tmp file and ``os.replace``s atomically so concurrent
    readers never see a half-written file.
    """
    try:
        if not LOG_PATH.exists():
            return
        size = LOG_PATH.stat().st_size
        if size < MAX_BYTES:
            # Cheap size check passed; only count lines if we're close.
            return
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            tail = deque(f, maxlen=KEEP_LINES)
        tmp = LOG_PATH.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(tail)
        tmp.replace(LOG_PATH)
        logger.info(
            "eval_log rotated: kept %d lines (was %d bytes)", len(tail), size
        )
    except Exception:  # noqa: BLE001
        logger.warning("eval_log rotation failed", exc_info=True)


def read_tail_jsonl(
    limit: int = 200,
    scholar_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the most recent log entries, newest first.

    Uses a sliding `deque` window so memory stays bounded even if the
    file is large. When filtering by `scholar_id`, we keep a wider
    window than `limit` so the filter is unlikely to under-return.
    """
    if not LOG_PATH.exists():
        return []
    window = max(limit * 10, limit) if scholar_id else limit
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            recent = deque(f, maxlen=window)
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for line in reversed(recent):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if scholar_id and entry.get("scholar_id") != scholar_id:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out
