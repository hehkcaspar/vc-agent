"""Asyncio-task registry + terminal-status helpers shared by chat jobs.

Mirrors the academic evaluation registry (``services/academic/evaluation_service.py``)
but keyed by an arbitrary string so multiple namespaces can coexist
(e.g. ``chat:<id>``, ``academic_chat:<id>``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Type

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


_running_tasks: dict[str, asyncio.Task[Any]] = {}


def launch_tracked_task(
    key: str,
    coro_factory: Callable[[], Awaitable[Any]],
) -> asyncio.Task[Any]:
    """Spawn a background asyncio task and register it under ``key``."""
    task = asyncio.create_task(coro_factory())
    _running_tasks[key] = task
    task.add_done_callback(
        lambda t, k=key: _running_tasks.pop(k, None)
        if _running_tasks.get(k) is t
        else None
    )
    return task


async def cancel_tracked_task(key: str, *, timeout: float = 1.0) -> bool:
    """Best-effort cancel. Returns True if a task was running and got cancelled.

    Short timeout because the cancel-endpoint caller has already flipped
    the DB row to ``cancelled`` before invoking us — the user-visible
    state is correct and waiting longer just stalls the HTTP response.
    """
    task = _running_tasks.get(key)
    if not task or task.done():
        return False
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    except Exception:
        logger.exception("cancel_tracked_task(%s): task raised during unwind", key)
    return True


async def mark_job_cancelled(
    session_factory: async_sessionmaker[Any],
    model: Type[Any],
    job_id: str,
) -> None:
    """Atomically flip a job row to ``cancelled`` iff not already terminal.

    Used from ``CancelledError`` handlers in chat / preset / academic-chat
    runners. The SQL ``WHERE status NOT IN (terminal)`` makes the write
    race-proof against the cancel endpoint that already wrote ``cancelled``.
    """
    async with session_factory() as db:
        await db.execute(
            update(model)
            .where(model.id == job_id)
            .where(model.status.notin_(TERMINAL_JOB_STATUSES))
            .values(status="cancelled", step_detail="Cancelled by user")
        )
        await db.commit()
