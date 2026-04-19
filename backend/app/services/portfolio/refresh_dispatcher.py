"""In-flight-deduped dispatcher for portfolio Layer 2 source refreshes.

Mirrors ``services/academic/refresh_dispatcher.py``. Two concurrent
triggers for the same ``(entity_id, source_id)`` share the same
running task.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Literal

from . import sources as _sources

logger = logging.getLogger(__name__)

Mode = Literal["bootstrap", "incremental"]

# (entity_id, source_id) -> running Future
_in_flight: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
_registry_lock = asyncio.Lock()


async def trigger_refresh(
    entity_id: str,
    source_id: str,
    *,
    mode: Mode = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    """Run the named source against the entity; dedup in-flight calls."""
    key = (entity_id, source_id)

    async with _registry_lock:
        existing = _in_flight.get(key)
        if existing is not None and not existing.done():
            logger.info(
                "portfolio.refresh_dispatcher: piggy-back on in-flight %s for %s",
                source_id,
                entity_id,
            )
            return await existing

        runner = _resolve_runner(source_id)
        if runner is None:
            raise ValueError(f"Unknown portfolio source '{source_id}'")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        _in_flight[key] = fut

    try:
        result = await runner(entity_id, mode=mode, reason=reason)
        fut.set_result(result)
        return result
    except Exception as e:
        logger.exception(
            "portfolio.refresh_dispatcher: source %s failed for %s",
            source_id,
            entity_id,
        )
        fut.set_exception(e)
        raise
    finally:
        async with _registry_lock:
            if _in_flight.get(key) is fut:
                del _in_flight[key]


def _resolve_runner(
    source_id: str,
) -> Callable[..., Awaitable[dict[str, Any]]] | None:
    """Look up ``sources.{source_id}.run`` lazily so tests can patch."""
    mod = getattr(_sources, source_id, None)
    if mod is None:
        return None
    return getattr(mod, "run", None)
