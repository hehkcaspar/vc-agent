"""Shared tracking-state helper for portfolio news_web.

Both the router (manual ``POST /news/refresh``) and the scheduler
(``PortfolioNewsScheduler._tick``) need to update
``Entity.metadata._news_tracking`` after a run completes. Centralising
the write here avoids two call sites drifting — and crucially avoids
the v1 bug where scheduler-fired runs never updated ``last_run_at``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


_DEFAULT_CADENCE_DAYS = 3


def default_tracking(enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "cadence_days": _DEFAULT_CADENCE_DAYS,
        "last_bootstrapped_at": None,
        "last_run_at": None,
        "last_error": None,
    }


def read_metadata(entity) -> dict[str, Any]:
    if not entity.metadata_json:
        return {}
    try:
        return json.loads(entity.metadata_json) or {}
    except (TypeError, ValueError):
        return {}


def write_metadata(entity, metadata: dict[str, Any]) -> None:
    entity.metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)


def get_tracking(entity) -> dict[str, Any] | None:
    """Return current ``_news_tracking`` dict or None if absent."""
    metadata = read_metadata(entity)
    trk = metadata.get("_news_tracking")
    return trk if isinstance(trk, dict) else None


async def apply_run_result_to_tracking(
    entity_id: str,
    *,
    mode_used: str,
    error: str | None,
) -> None:
    """Update ``Entity.metadata._news_tracking`` after a news_web run.

    Safe to call from any caller (router, scheduler, hook). Creates the
    tracking dict lazily if missing — a run can legitimately complete
    before tracking is explicitly toggled on (e.g. the auto-bootstrap
    hook sets the dict then kicks the run).

    ``mode_used`` should be the mode the source actually executed
    (bootstrap vs incremental), not the mode the caller requested —
    use the ``mode_used`` key from the source's return dict.
    """
    from app.database import AsyncSessionLocal
    from app.models import Entity

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if entity is None:
            return
        metadata = read_metadata(entity)
        trk = metadata.get("_news_tracking")
        if not isinstance(trk, dict):
            trk = default_tracking()
        now_iso = datetime.now(timezone.utc).isoformat()
        trk["last_run_at"] = now_iso
        trk["last_error"] = error
        if mode_used == "bootstrap" and not error:
            trk["last_bootstrapped_at"] = now_iso
        metadata["_news_tracking"] = trk
        write_metadata(entity, metadata)
        await db.commit()
