"""
Heartbeat scheduler for Academic Tracking v2.

Reads ``data/config/heartbeat.json`` every 60 s and dispatches due actions.
Runs as an asyncio task inside the FastAPI lifespan.

See design doc §5.1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import Channel, Scholar, ScholarEvent

from .channel_pollers import get_poller

logger = logging.getLogger(__name__)


class HeartbeatScheduler:
    """Periodic scheduler that polls channels and dispatches maintenance tasks."""

    def __init__(self):
        self._last_run: dict[str, datetime] = {}

    async def run(self) -> None:
        """Main loop — runs until cancelled."""
        logger.info("Heartbeat scheduler started")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Heartbeat tick failed")
            await asyncio.sleep(60)

    async def _tick(self) -> None:
        config_path = settings.ACADEMIC_CONFIG_DIR / "heartbeat.json"
        if not config_path.exists():
            return

        config = json.loads(config_path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc)

        for check in config.get("checks", []):
            check_id = check.get("id", "")
            if not check.get("enabled", False):
                continue

            interval = timedelta(minutes=check.get("interval_minutes", 60))
            last = self._last_run.get(check_id)
            if last and (now - last) < interval:
                continue

            action = check.get("action", "")
            try:
                if action == "poll_due_channels":
                    await self._poll_due_channels()
                elif action == "refresh_stale_scholars":
                    await self._refresh_stale_scholars(check.get("filter", {}))
                elif action == "generate_digest":
                    await self._generate_digest()
                else:
                    logger.warning("Unknown heartbeat action: %s", action)
            except Exception:
                logger.exception("Heartbeat action '%s' failed", action)

            self._last_run[check_id] = now

    async def _refresh_stale_scholars(self, filter_cfg: dict) -> None:
        """Refresh scholars that are overdue based on their priority level."""
        stale_days = filter_cfg.get("stale_days", 7)
        priority = filter_cfg.get("tracking_priority")
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

        async with AcademicAsyncSessionLocal() as db:
            stmt = select(Scholar).where(Scholar.status == "active")
            if priority:
                stmt = stmt.where(Scholar.tracking_priority == priority)
            result = await db.execute(stmt)
            scholars = result.scalars().all()

        for s in scholars:
            # Check latest evaluation date from files
            evals_dir = settings.ACADEMIC_SCHOLARS_DIR / s.id / "evaluations"
            if not evals_dir.exists():
                continue
            files = sorted(evals_dir.glob("*.json"), reverse=True)
            if not files:
                continue
            # Check file modification time as proxy for eval date
            latest_mtime = datetime.fromtimestamp(files[0].stat().st_mtime, tz=timezone.utc)
            if latest_mtime < cutoff:
                logger.info("Scholar %s is stale (last eval %s), triggering refresh", s.id, latest_mtime.date())
                try:
                    from .scholar_agent import invoke_scholar_agent
                    from .scholar_prompts import GOAL_REFRESH
                    await invoke_scholar_agent(s.id, GOAL_REFRESH)
                except Exception:
                    logger.exception("Stale refresh failed for %s", s.id)

    async def _generate_digest(self) -> None:
        """Trigger digest generation."""
        try:
            from app.services.academic.digest_service import run_digest_generation
            await run_digest_generation()
        except Exception:
            logger.exception("Heartbeat digest generation failed")

    async def _poll_due_channels(self) -> None:
        """Find and poll all overdue channels."""
        now = datetime.now(timezone.utc)

        async with AcademicAsyncSessionLocal() as db:
            result = await db.execute(
                select(Channel).where(
                    Channel.is_active == True,  # noqa: E712
                )
            )
            channels = result.scalars().all()

        for ch in channels:
            # Check if due
            if ch.last_polled_at:
                interval = timedelta(hours=ch.polling_interval_hours)
                if (now - ch.last_polled_at.replace(tzinfo=timezone.utc)) < interval:
                    continue

            try:
                await self._poll_channel(ch)
            except Exception:
                logger.exception("Failed polling channel %s for scholar %s", ch.id, ch.scholar_id)

    async def _poll_channel(self, ch: Channel) -> None:
        """Poll a single channel and process the result."""
        poller = get_poller(ch.channel_type)
        if not poller:
            logger.debug("No poller for channel type: %s", ch.channel_type)
            return

        # Load last snapshot from channels.json
        dossier = settings.ACADEMIC_SCHOLARS_DIR / ch.scholar_id
        channels_path = dossier / "channels.json"
        channels_data = {}
        if channels_path.exists():
            channels_data = json.loads(channels_path.read_text(encoding="utf-8"))

        last_snapshot = {}
        for c in channels_data.get("channels", []):
            if c.get("id") == ch.id:
                last_snapshot = c.get("last_snapshot", {})
                break

        logger.info("Polling channel %s (%s) for scholar %s", ch.id, ch.channel_type, ch.scholar_id)

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
                logger.warning(
                    "Channel %s poll error (%d): %s",
                    ch.id, channel.poll_error_count, result.error,
                )
                # Auto-deactivate at 5 consecutive errors
                if channel.poll_error_count >= 5:
                    channel.is_active = False
                    logger.warning("Channel %s auto-deactivated after 5 errors", ch.id)
                    # Log warning event
                    db.add(ScholarEvent(
                        id=str(uuid.uuid4()),
                        scholar_id=ch.scholar_id,
                        event_type="channel_deactivated",
                        significance="medium",
                        title=f"Channel {ch.channel_type} deactivated after 5 poll errors",
                        event_date=now,
                    ))
                channel.last_polled_at = now
                await db.commit()
                return

            # Success — reset error count
            channel.poll_error_count = 0
            channel.last_polled_at = now

            if result.changed:
                channel.last_changed_at = now

                # Append events
                for evt in result.events:
                    event_id = str(uuid.uuid4())
                    # JSONL append
                    events_path = dossier / "events.jsonl"
                    events_path.parent.mkdir(parents=True, exist_ok=True)
                    jsonl_entry = {
                        "id": event_id,
                        "type": evt["type"],
                        "date": now.isoformat(),
                        "significance": evt.get("significance", "medium"),
                        "title": evt.get("title", ""),
                        "payload": evt.get("payload", {}),
                        "source": f"channel:{ch.id}",
                    }
                    from .file_utils import append_jsonl
                    append_jsonl(events_path, jsonl_entry)

                    # SQL event
                    db.add(ScholarEvent(
                        id=event_id,
                        scholar_id=ch.scholar_id,
                        event_type=evt["type"],
                        significance=evt.get("significance", "medium"),
                        title=evt.get("title"),
                        is_read=False,
                        event_date=now,
                    ))

                # Update snapshot in channels.json
                _update_channel_snapshot(channels_path, ch.id, result.snapshot)

                logger.info(
                    "Channel %s: %d events generated",
                    ch.id, len(result.events),
                )

                # Spawn agent investigation for high-significance events (design §5.2)
                high_events = [e for e in result.events if e.get("significance") == "high"]
                if high_events:
                    asyncio.create_task(
                        self._investigate_signal(ch.scholar_id, high_events[0])
                    )

            await db.commit()


    async def _investigate_signal(self, scholar_id: str, event: dict) -> None:
        """Spawn a scholar agent to investigate a high-significance signal."""
        try:
            from .scholar_agent import invoke_scholar_agent
            from .scholar_prompts import GOAL_SIGNAL_INVESTIGATION

            signal_desc = f"{event.get('type', 'unknown')}: {event.get('title', '')}"
            goal = GOAL_SIGNAL_INVESTIGATION.replace("{signal_description}", signal_desc)
            logger.info("Investigating signal for scholar %s: %s", scholar_id, signal_desc)
            await invoke_scholar_agent(scholar_id, goal)
        except Exception:
            logger.exception("Signal investigation failed for scholar %s", scholar_id)


def _update_channel_snapshot(
    channels_path: Path,
    channel_id: str,
    new_snapshot: dict[str, Any],
) -> None:
    """Update a channel's last_snapshot in channels.json."""
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
