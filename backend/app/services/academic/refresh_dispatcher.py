"""In-flight-deduped dispatcher for Layer 2 source refreshes.

Layer 3 dim agents are NOT allowed to hit external APIs directly. They
can only call `trigger_refresh(source, scope, reason, scholar_id)`
which routes through this dispatcher. Heartbeat uses the same
dispatcher when scheduling periodic source runs. Two concurrent
triggers for the same `(scholar_id, source_id)` share the same
underlying task; the second call awaits the first.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Literal

from . import sources as _sources

logger = logging.getLogger(__name__)

Mode = Literal["bootstrap", "incremental"]

# (scholar_id, source_id) -> running Future
_in_flight: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
_registry_lock = asyncio.Lock()


async def trigger_refresh(
    scholar_id: str,
    source_id: str,
    *,
    mode: Mode = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    """Run the named source against the scholar; dedupe in-flight calls.

    Returns whatever dict the source `run()` returns (at minimum
    `{"snapshot_id": str, "changed": bool}`).
    """
    key = (scholar_id, source_id)

    async with _registry_lock:
        existing = _in_flight.get(key)
        if existing is not None and not existing.done():
            logger.info(
                "refresh_dispatcher: piggy-back on in-flight %s for %s",
                source_id,
                scholar_id,
            )
            return await existing

        runner = _resolve_runner(source_id)
        if runner is None:
            raise ValueError(f"Unknown source '{source_id}'")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        _in_flight[key] = fut

    try:
        result = await runner(scholar_id, mode=mode, reason=reason)
        fut.set_result(result)
        return result
    except Exception as e:
        logger.exception(
            "refresh_dispatcher: source %s failed for %s", source_id, scholar_id
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
    """Look up `sources.{source_id}.run` lazily so unit tests can patch."""
    mod = getattr(_sources, source_id, None)
    if mod is None:
        return None
    return getattr(mod, "run", None)
