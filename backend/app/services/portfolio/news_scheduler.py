"""Portfolio news scheduler — per-entity cadence-driven news_web ticks.

Runs as an asyncio task in the FastAPI lifespan alongside the academic
heartbeat. Much simpler than ``academic/heartbeat.py``: one source
(``news_web``), one gating rule (``Entity.status == 'active'`` AND
``metadata._news_tracking.enabled == True``), one cadence (per-entity
``cadence_days`` in the tracking dict, default 3).

Snapshot recency drives due-ness via
``portfolio.file_utils.last_snapshot_for_source`` — the same mechanism
news_web itself uses to pick bootstrap vs incremental.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Entity

from .file_utils import last_snapshot_for_source
from .refresh_dispatcher import trigger_refresh
from .sources.news_web import SOURCE_ID as NEWS_SOURCE_ID
from .tracking import apply_run_result_to_tracking

logger = logging.getLogger(__name__)

_TICK_SECONDS = 60
_DEFAULT_CADENCE_DAYS = 3

# Module-level pointer to the live scheduler (mirrors academic heartbeat).
_active: "PortfolioNewsScheduler | None" = None


def get_scheduler_status() -> dict[str, Any]:
    sched = _active
    if sched is None:
        return {
            "running": False,
            "last_tick_at": None,
            "tick_interval_s": _TICK_SECONDS,
        }
    return {
        "running": True,
        "last_tick_at": sched.last_tick_at.isoformat(timespec="seconds")
        if sched.last_tick_at
        else None,
        "tick_interval_s": _TICK_SECONDS,
    }


class PortfolioNewsScheduler:
    def __init__(self) -> None:
        self.last_tick_at: datetime | None = None

    async def run(self) -> None:
        global _active
        _active = self
        logger.info("Portfolio news scheduler started")
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("portfolio news: tick failed")
                await asyncio.sleep(_TICK_SECONDS)
        finally:
            if _active is self:
                _active = None

    async def _tick(self) -> None:
        self.last_tick_at = datetime.now(timezone.utc)
        now = self.last_tick_at

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Entity).where(Entity.status == "active")
            )
            entities = result.scalars().all()

        due_count = 0
        for entity in entities:
            tracking = _read_tracking(entity)
            if not tracking or not tracking.get("enabled"):
                continue
            cadence_days = int(
                tracking.get("cadence_days") or _DEFAULT_CADENCE_DAYS
            )
            if not self._is_due(entity.id, cadence_days, now):
                continue
            due_count += 1
            logger.info(
                "portfolio news_web: refreshing %s (%s) — cadence=%sd",
                entity.name,
                entity.id,
                cadence_days,
            )
            mode_used = "incremental"
            error: str | None = None
            try:
                run_result = await trigger_refresh(
                    entity.id,
                    NEWS_SOURCE_ID,
                    mode="incremental",
                    reason="heartbeat",
                )
                if isinstance(run_result, dict):
                    mu = run_result.get("mode_used")
                    if isinstance(mu, str) and mu:
                        mode_used = mu
                    src_err = run_result.get("error")
                    if isinstance(src_err, str) and src_err:
                        error = src_err
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "portfolio news: refresh failed for %s", entity.id
                )
                error = str(e)

            try:
                await apply_run_result_to_tracking(
                    entity.id, mode_used=mode_used, error=error,
                )
            except Exception:
                logger.warning(
                    "portfolio news: tracking update failed for %s",
                    entity.id,
                    exc_info=True,
                )

        if due_count:
            logger.info(
                "portfolio news_web tick: %s/%s entities refreshed",
                due_count,
                len(entities),
            )

    def _is_due(
        self, entity_id: str, cadence_days: int, now: datetime
    ) -> bool:
        snap = last_snapshot_for_source(entity_id, NEWS_SOURCE_ID)
        if not snap:
            return True
        created = snap.get("created_at") or snap.get("id") or ""
        return _older_than(created, cadence_days, now)


def _read_tracking(entity: Entity) -> dict[str, Any] | None:
    if not entity.metadata_json:
        return None
    try:
        metadata = json.loads(entity.metadata_json) or {}
    except (TypeError, ValueError):
        return None
    trk = metadata.get("_news_tracking")
    return trk if isinstance(trk, dict) else None


def _older_than(iso_str: str, cadence_days: int, now: datetime) -> bool:
    """Parse an ISO timestamp and check if older than cadence_days.

    Accepts either full ISO 8601 (``2026-04-18T22:48:24.707749+00:00``)
    or the JSONL id format (``2026-04-18T22-48-24Z``). If unparseable,
    treat as due.
    """
    if not iso_str:
        return True
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.strptime(iso_str[:19], "%Y-%m-%dT%H-%M-%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return True
    return (now - dt) >= timedelta(days=cadence_days)
