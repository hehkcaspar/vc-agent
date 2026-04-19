"""Per-entity news feed + tracking controls.

Three endpoints power the News tab:

* ``GET /entities/{id}/news``      — current ledger + tracking state.
* ``POST /entities/{id}/news/refresh`` — kick a bootstrap / incremental run.
* ``PATCH /entities/{id}/news/tracking`` — edit enabled / cadence.

Tracking state lives at ``metadata_json._news_tracking`` on the entity
so it's colocated with other entity state and user-editable via the
existing metadata edit path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Entity
from app.services.portfolio.file_utils import (
    last_snapshot_for_source,
    read_records,
)
from app.services.portfolio.refresh_dispatcher import trigger_refresh
from app.services.portfolio.sources.news_web import SOURCE_ID as NEWS_SOURCE_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entities/{entity_id}/news", tags=["entity-news"])


# ── Tracking defaults ─────────────────────────────────────────────────

_DEFAULT_CADENCE_DAYS = 3


def _default_tracking(enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "cadence_days": _DEFAULT_CADENCE_DAYS,
        "last_bootstrapped_at": None,
        "last_run_at": None,
        "last_error": None,
    }


def _read_metadata(entity: Entity) -> dict[str, Any]:
    if not entity.metadata_json:
        return {}
    try:
        return json.loads(entity.metadata_json) or {}
    except (TypeError, ValueError):
        return {}


def _write_metadata(entity: Entity, metadata: dict[str, Any]) -> None:
    entity.metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)


def _get_tracking(entity: Entity) -> dict[str, Any] | None:
    """Return current ``_news_tracking`` dict or None if absent."""
    metadata = _read_metadata(entity)
    trk = metadata.get("_news_tracking")
    return trk if isinstance(trk, dict) else None


async def _ensure_tracking(
    db: AsyncSession,
    entity: Entity,
    *,
    enabled: bool = True,
    cadence_days: int | None = None,
) -> dict[str, Any]:
    """Initialize ``_news_tracking`` if missing; return the current state."""
    metadata = _read_metadata(entity)
    trk = metadata.get("_news_tracking")
    if not isinstance(trk, dict):
        trk = _default_tracking(enabled=enabled)
        if cadence_days is not None:
            trk["cadence_days"] = cadence_days
        metadata["_news_tracking"] = trk
        _write_metadata(entity, metadata)
        await db.commit()
    return trk


async def _update_tracking(
    db: AsyncSession,
    entity: Entity,
    patch: dict[str, Any],
) -> dict[str, Any]:
    metadata = _read_metadata(entity)
    trk = metadata.get("_news_tracking")
    if not isinstance(trk, dict):
        trk = _default_tracking()
    trk = {**trk, **patch}
    metadata["_news_tracking"] = trk
    _write_metadata(entity, metadata)
    await db.commit()
    return trk


# ── Response schemas ──────────────────────────────────────────────────


class NewsTrackingState(BaseModel):
    enabled: bool
    cadence_days: int
    last_bootstrapped_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None


class NewsItem(BaseModel):
    id: str
    title: str
    url: Optional[str] = None
    source: Optional[str] = None
    summary: Optional[str] = None
    published_date: Optional[str] = None
    category: Optional[str] = None


class NewsSnapshot(BaseModel):
    created_at: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)


class NewsFeedResponse(BaseModel):
    entity_id: str
    tracking: Optional[NewsTrackingState] = None
    items: list[NewsItem]
    total: int
    last_snapshot: Optional[NewsSnapshot] = None


class RefreshRequest(BaseModel):
    mode: Optional[Literal["bootstrap", "incremental"]] = None


class RefreshResponse(BaseModel):
    status: Literal["queued", "running"]
    mode: str
    message: str


class TrackingPatchRequest(BaseModel):
    enabled: Optional[bool] = None
    cadence_days: Optional[int] = Field(default=None, ge=1, le=90)


# ── Helpers ───────────────────────────────────────────────────────────


async def _get_entity_or_404(db: AsyncSession, entity_id: str) -> Entity:
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


def _sort_news_newest_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda r: (r.get("published_date") or "", r.get("id") or ""),
        reverse=True,
    )


# ── Background runner ─────────────────────────────────────────────────


async def _run_news_refresh(
    entity_id: str,
    *,
    mode: str,
    reason: str,
) -> None:
    """Kick trigger_refresh and update tracking timestamps."""
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        await trigger_refresh(
            entity_id, NEWS_SOURCE_ID, mode=mode, reason=reason or "manual",
        )
        error: str | None = None
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "news refresh failed for %s (mode=%s)", entity_id, mode
        )
        error = str(e)

    from app.database import AsyncSessionLocal  # local to avoid cycle

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if entity is None:
            return
        metadata = _read_metadata(entity)
        trk = metadata.get("_news_tracking")
        if not isinstance(trk, dict):
            trk = _default_tracking()
        now_iso = datetime.now(timezone.utc).isoformat()
        trk["last_run_at"] = now_iso
        trk["last_error"] = error
        if mode == "bootstrap" and not error:
            trk["last_bootstrapped_at"] = now_iso
        metadata["_news_tracking"] = trk
        _write_metadata(entity, metadata)
        await db.commit()


# ── GET feed ──────────────────────────────────────────────────────────


@router.get("", response_model=NewsFeedResponse)
async def get_news_feed(
    entity_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> NewsFeedResponse:
    entity = await _get_entity_or_404(db, entity_id)
    trk = _get_tracking(entity)

    records = read_records(entity_id, "news")
    total = len(records)
    records = _sort_news_newest_first(records)[: max(1, min(limit, 500))]

    items = [
        NewsItem(
            id=r.get("id") or "",
            title=(r.get("title") or "").strip(),
            url=r.get("url"),
            source=r.get("source"),
            summary=r.get("summary"),
            published_date=r.get("published_date"),
            category=r.get("category"),
        )
        for r in records
        if r.get("title")
    ]

    snap = last_snapshot_for_source(entity_id, NEWS_SOURCE_ID)
    last_snapshot = (
        NewsSnapshot(
            created_at=(snap.get("created_at") if snap else None),
            detail=(snap.get("detail") or {}) if snap else {},
        )
        if snap
        else None
    )

    tracking = NewsTrackingState(**trk) if trk else None
    return NewsFeedResponse(
        entity_id=entity_id,
        tracking=tracking,
        items=items,
        total=total,
        last_snapshot=last_snapshot,
    )


# ── POST refresh ──────────────────────────────────────────────────────


@router.post("/refresh", response_model=RefreshResponse, status_code=202)
async def refresh_news(
    entity_id: str,
    body: RefreshRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    entity = await _get_entity_or_404(db, entity_id)

    # Initialize tracking if not present — manual refresh implies the
    # user wants to start tracking this entity.
    trk = await _ensure_tracking(db, entity, enabled=True)

    requested_mode = body.mode
    has_snapshot = last_snapshot_for_source(entity_id, NEWS_SOURCE_ID) is not None
    if requested_mode is None:
        mode_used = "bootstrap" if not has_snapshot else "incremental"
    else:
        mode_used = requested_mode

    background_tasks.add_task(
        _run_news_refresh,
        entity_id,
        mode=mode_used,
        reason="manual",
    )

    return RefreshResponse(
        status="queued",
        mode=mode_used,
        message=f"news_web refresh queued (mode={mode_used})",
    )


# ── PATCH tracking ────────────────────────────────────────────────────


@router.patch("/tracking", response_model=NewsTrackingState)
async def patch_tracking(
    entity_id: str,
    body: TrackingPatchRequest,
    db: AsyncSession = Depends(get_db),
) -> NewsTrackingState:
    entity = await _get_entity_or_404(db, entity_id)
    patch: dict[str, Any] = {}
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if body.cadence_days is not None:
        patch["cadence_days"] = body.cadence_days
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Ensure tracking exists before patching.
    await _ensure_tracking(db, entity, enabled=True)
    updated = await _update_tracking(db, entity, patch)
    return NewsTrackingState(**updated)


# ── Bootstrap hook (called from preset completion) ────────────────────


async def maybe_bootstrap_after_preset(
    entity_id: str,
    *,
    trigger_preset: str,
) -> None:
    """Auto-start news tracking after extract_info / initial_screening.

    Idempotent — no-op when ``_news_tracking`` already exists. Runs the
    bootstrap non-blocking via asyncio.create_task so preset completion
    isn't delayed.
    """
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if entity is None:
            return
        if entity.status != "active":
            return
        metadata = _read_metadata(entity)
        existing = metadata.get("_news_tracking")
        if isinstance(existing, dict):
            # Already initialized by a prior preset run — bootstrap once total.
            return
        metadata["_news_tracking"] = _default_tracking(enabled=True)
        _write_metadata(entity, metadata)
        await db.commit()

    logger.info(
        "news_web: auto-bootstrapping entity %s after %s", entity_id, trigger_preset
    )
    asyncio.create_task(
        _run_news_refresh(entity_id, mode="bootstrap", reason=f"auto:{trigger_preset}")
    )
