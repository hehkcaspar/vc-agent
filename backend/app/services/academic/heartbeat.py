"""Heartbeat scheduler for Academic Tracking v2.

Single dispatcher that reads `data/config/continuous_tasks.json` on
every tick and runs due Layer 2 sources + Layer 3 tasks for every
active scholar. Legacy channel polling (Google Scholar / Semantic
Scholar / news) is kept in parallel so the signal feed keeps working
while the new pipeline takes over.

Runs as an asyncio task inside the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import json
import logging

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import Channel, Scholar

from .channel_pollers import get_poller
from .continuous_config import load_continuous_tasks
from .dim_runner import run_dim_eval
from .eval_log import log_step
from .fact_store import last_snapshot_for_source
from .events_sync import log_event
from .file_utils import dossier_path, latest_record, read_json, read_records
from .identity_resolver import resolve_identity
from .narrative_synthesizer import run_narrative_synthesizer
from .phase_classifier import run_phase_classifier
from .refresh_dispatcher import trigger_refresh

logger = logging.getLogger(__name__)

_TICK_SECONDS = 60


# Module-level pointer to the currently-running scheduler, set by
# `run()` on entry and cleared on exit. The Tasks view's liveness
# probe reads `last_tick_at` off this to prove the scheduler is
# actually ticking (a silently-dead lifespan task is a real failure
# mode and was invisible before).
_active: "HeartbeatScheduler | None" = None


def get_heartbeat_status() -> dict[str, Any]:
    """Snapshot of the live scheduler state for the Tasks view.

    Returns ``running=False`` when no scheduler has taken the module
    pointer (either the lifespan hasn't started yet or the task died).
    """
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


class HeartbeatScheduler:
    def __init__(self) -> None:
        self._legacy_last_tick: datetime | None = None
        self.last_tick_at: datetime | None = None

    async def run(self) -> None:
        global _active
        _active = self
        logger.info("Heartbeat v2 scheduler started")
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Heartbeat tick failed")
                await asyncio.sleep(_TICK_SECONDS)
        finally:
            if _active is self:
                _active = None

    # ── tick ──────────────────────────────────────────────────────

    async def _tick(self) -> None:
        self.last_tick_at = datetime.now(timezone.utc)
        try:
            cfg = load_continuous_tasks()
        except Exception:
            logger.exception("heartbeat: continuous_tasks.json failed to load")
            return

        # Only touch scholars in 'active' state. 'evaluating' means
        # another worker (manual bootstrap or a prior /evaluate call)
        # owns the slot via the atomic lock in evaluation_service —
        # don't race against it. 'paused' and 'archived' are
        # out-of-scope.
        async with AcademicAsyncSessionLocal() as db:
            result = await db.execute(
                select(Scholar).where(Scholar.status == "active")
            )
            scholars = result.scalars().all()

        # Per-scholar work claims the evaluating lock so a manual
        # `/evaluate` call cannot interleave with a heartbeat tick
        # that's already dispatching for the same scholar. The lock
        # is released in a try/finally; worst case after a crash,
        # the startup migration in main.lifespan resets any
        # still-evaluating rows.
        from .evaluation_service import claim_evaluating, release_evaluating

        for s in scholars:
            if not await claim_evaluating(s.id):
                # Another worker owns the slot — skip this tick for
                # this scholar. We'll pick it up on the next tick
                # after the other worker releases.
                continue
            try:
                await self._dispatch_scholar(s, cfg)
            except Exception:
                logger.exception("heartbeat: dispatch failed for %s", s.id)
            finally:
                await release_evaluating(s.id)

        # Legacy channel polling (for signal feed events).
        try:
            await self._poll_due_channels()
        except Exception:
            logger.exception("heartbeat: channel polling failed")

    # ── continuous dispatch ───────────────────────────────────────

    async def _dispatch_scholar(self, scholar: Scholar, cfg) -> None:
        priority = (scholar.tracking_priority or "medium").lower()
        now = datetime.now(timezone.utc)
        name = scholar.name

        # Identity guard — run the resolver once if we have neither GS
        # nor SS. Individual sources handle missing IDs gracefully
        # (record snapshot + return early), so having just one is enough.
        if not self._identity_complete(scholar.id):
            try:
                async with log_step(scholar.id, "identity", scholar_name=name):
                    await resolve_identity(scholar.id)
            except Exception:
                logger.exception(
                    "heartbeat: identity resolution failed for %s, skipping tick",
                    scholar.id,
                )
                return
            if not self._identity_complete(scholar.id):
                logger.info(
                    "heartbeat: %s still missing identity after resolver, skipping",
                    scholar.id,
                )
                return

        # Layer 2 sources.
        for source_id, src_cfg in cfg.sources.items():
            if not src_cfg.enabled:
                continue
            cadence_days = self._cadence_for_source(src_cfg, priority)
            if not self._is_source_due(scholar.id, source_id, cadence_days, now):
                continue
            try:
                async with log_step(
                    scholar.id, f"source/{source_id}", scholar_name=name
                ):
                    await trigger_refresh(
                        scholar.id,
                        source_id,
                        mode="incremental",
                        reason="heartbeat",
                    )
            except Exception:
                logger.exception(
                    "heartbeat: source %s failed for %s", source_id, scholar.id
                )

        # Layer 3 dims.
        for dim_id, dim_cfg in cfg.dimensions.items():
            if not dim_cfg.enabled:
                continue
            if not self._is_dim_due(
                scholar.id, dim_id, dim_cfg.default_cadence_days, now
            ):
                continue
            try:
                async with log_step(
                    scholar.id, f"dim/{dim_id}", scholar_name=name
                ) as ctx:
                    r = await run_dim_eval(scholar.id, dim_id, cfg=cfg)
                    if isinstance(r, dict) and "score" in r:
                        ctx.detail = {"score": r.get("score")}
            except Exception:
                logger.exception(
                    "heartbeat: dim %s failed for %s", dim_id, scholar.id
                )

        # Phase classifier.
        pc = cfg.phase_classifier
        if pc.enabled and self._is_phase_due(
            scholar.id, pc.default_cadence_days, now
        ):
            try:
                async with log_step(
                    scholar.id, "phase_classifier", scholar_name=name
                ):
                    await run_phase_classifier(scholar.id)
            except Exception:
                logger.exception(
                    "heartbeat: phase_classifier failed for %s", scholar.id
                )

        # Narrative synthesizer.
        ns = cfg.narrative_synthesizer
        if ns.enabled and not ns.on_demand_only and self._is_narrative_due(
            scholar.id, ns.default_cadence_days, now
        ):
            try:
                async with log_step(scholar.id, "narrative", scholar_name=name):
                    await run_narrative_synthesizer(scholar.id)
            except Exception:
                logger.exception(
                    "heartbeat: narrative failed for %s", scholar.id
                )

    def _identity_complete(self, scholar_id: str) -> bool:
        """Check whether the scholar has at least one usable identity.

        Requires at least one of GS or SS.  Individual Layer 2 sources
        already handle missing IDs gracefully (record snapshot + return
        early), so we don't need both to proceed.
        """
        profile = read_json(dossier_path(scholar_id) / "profile.json") or {}
        ident = profile.get("identity") or {}
        return bool(
            (ident.get("google_scholar") or {}).get("id")
            or (ident.get("semantic_scholar") or {}).get("id")
        )

    def _cadence_for_source(self, src_cfg, priority: str) -> int:
        if src_cfg.priority_overrides:
            o = src_cfg.priority_overrides
            if priority == "high" and o.high is not None:
                return o.high
            if priority == "low" and o.low is not None:
                return o.low
        return src_cfg.default_cadence_days

    def _is_source_due(
        self, scholar_id: str, source_id: str, cadence_days: int, now: datetime
    ) -> bool:
        snap = last_snapshot_for_source(scholar_id, source_id)
        if not snap:
            return True
        return self._older_than(snap.get("id", ""), cadence_days, now)

    def _is_dim_due(
        self, scholar_id: str, dim_id: str, cadence_days: int, now: datetime
    ) -> bool:
        recs = read_records(scholar_id, f"evaluations/{dim_id}")
        if not recs:
            return True
        return self._older_than(recs[-1].get("id", ""), cadence_days, now)

    def _is_phase_due(
        self, scholar_id: str, cadence_days: int, now: datetime
    ) -> bool:
        rec = latest_record(scholar_id, "peer_group")
        if not rec:
            return True
        return self._older_than(rec.get("id", ""), cadence_days, now)

    def _is_narrative_due(
        self, scholar_id: str, cadence_days: int, now: datetime
    ) -> bool:
        rec = latest_record(scholar_id, "narrative")
        if not rec:
            return True
        return self._older_than(rec.get("id", ""), cadence_days, now)

    @staticmethod
    def _older_than(iso_id: str, cadence_days: int, now: datetime) -> bool:
        """Parse a per-record ISO id and check if it's older than *cadence_days*.

        IDs look like `2026-04-09T06-28-56Z` (dashes instead of colons
        in the time portion — strptime with `%H-%M-%S` handles that
        directly). If the id is missing or malformed, treat the record
        as due so we never silently skip a scholar.
        """
        if not iso_id:
            return True
        try:
            d = datetime.strptime(iso_id[:19], "%Y-%m-%dT%H-%M-%S").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            return True
        return (now - d) >= timedelta(days=cadence_days)

    # ── Legacy channel polling (unchanged) ────────────────────────

    async def _poll_due_channels(self) -> None:
        now = datetime.now(timezone.utc)

        async with AcademicAsyncSessionLocal() as db:
            result = await db.execute(
                select(Channel).where(Channel.is_active == True)  # noqa: E712
            )
            channels = result.scalars().all()

        for ch in channels:
            if ch.last_polled_at:
                interval = timedelta(hours=ch.polling_interval_hours)
                if (now - ch.last_polled_at.replace(tzinfo=timezone.utc)) < interval:
                    continue
            try:
                await self._poll_channel(ch)
            except Exception:
                logger.exception(
                    "heartbeat: polling channel %s failed", ch.id
                )

    async def _poll_channel(self, ch: Channel) -> None:
        poller = get_poller(ch.channel_type)
        if not poller:
            return

        dossier = settings.ACADEMIC_SCHOLARS_DIR / ch.scholar_id
        channels_path = dossier / "channels.json"
        channels_data: dict[str, Any] = {}
        if channels_path.exists():
            channels_data = json.loads(channels_path.read_text(encoding="utf-8"))

        last_snapshot: dict[str, Any] = {}
        for c in channels_data.get("channels", []):
            if c.get("id") == ch.id:
                last_snapshot = c.get("last_snapshot", {})
                break

        result = await poller.poll(
            channel_type=ch.channel_type,
            url=ch.url or "",
            last_snapshot=last_snapshot,
            scholar_id=ch.scholar_id,
        )

        now = datetime.now(timezone.utc)
        async with AcademicAsyncSessionLocal() as db:
            channel = await db.get(Channel, ch.id)
            if not channel:
                return

            if result.error:
                channel.poll_error_count = (channel.poll_error_count or 0) + 1
                if channel.poll_error_count >= 5:
                    channel.is_active = False
                channel.last_polled_at = now
                await db.commit()
                return

            channel.poll_error_count = 0
            channel.last_polled_at = now
            if result.changed:
                channel.last_changed_at = now
                for evt in result.events:
                    try:
                        await log_event(
                            ch.scholar_id,
                            event_type=evt["type"],
                            title=evt.get("title", ""),
                            significance=evt.get("significance", "medium"),
                            payload=evt.get("payload", {}),
                        )
                    except Exception:
                        logger.warning("heartbeat: log_event failed for channel %s", ch.id)
                _update_channel_snapshot(channels_path, ch.id, result.snapshot)
            await db.commit()


def _update_channel_snapshot(
    channels_path: Path, channel_id: str, new_snapshot: dict[str, Any]
) -> None:
    data: dict[str, Any] = {}
    if channels_path.exists():
        data = json.loads(channels_path.read_text(encoding="utf-8"))
    channels_list = data.get("channels", [])
    for c in channels_list:
        if c.get("id") == channel_id:
            c["last_snapshot"] = new_snapshot
            c["last_polled_at"] = datetime.now(timezone.utc).isoformat()
            break
    data["channels"] = channels_list
    from .file_utils import write_json
    write_json(channels_path, data)
