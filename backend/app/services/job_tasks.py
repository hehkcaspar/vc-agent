"""Generic asyncio-task registry for cancellable background jobs.

Used by chat/agent/preset jobs (portfolio + academic) so the frontend
Stop button can interrupt an in-flight run. Mirrors the academic
evaluation registry (``services/academic/evaluation_service.py``) but
keyed by an arbitrary string so multiple namespaces (e.g. ``chat:<id>``,
``academic_chat:<id>``) can coexist.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


_running_tasks: dict[str, asyncio.Task[Any]] = {}


def launch_tracked_task(
    key: str,
    coro_factory: Callable[[], Awaitable[Any]],
) -> asyncio.Task[Any]:
    """Spawn a background asyncio task and register it under ``key``.

    The task self-deregisters on completion via a done-callback so the
    registry doesn't leak. If a task is already registered under the
    same key, it is left in place — callers are responsible for keying
    uniquely (job ids are unique).
    """
    task = asyncio.create_task(coro_factory())
    _running_tasks[key] = task
    task.add_done_callback(
        lambda t, k=key: _running_tasks.pop(k, None)
        if _running_tasks.get(k) is t
        else None
    )
    return task


async def cancel_tracked_task(key: str, *, timeout: float = 5.0) -> bool:
    """Best-effort cancel of the task registered under ``key``.

    Returns True if a running task was cancelled, False if nothing was
    registered (or the task was already done). Waits up to ``timeout``
    seconds for the task to unwind so callers can rely on cleanup
    (e.g. SQL row writes inside ``finally`` blocks) having completed.
    All exceptions during cancel are swallowed — the goal is "best
    effort", never "block the cancel response on a stuck task".
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
