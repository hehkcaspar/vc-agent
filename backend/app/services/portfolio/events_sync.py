"""Entity timeline events — SQL single source of truth.

Mirrors ``services/academic/events_sync.py``. Every timeline-worthy
item discovered by a Layer 2 source (news, funding, partnership…)
writes through ``log_event`` into the ``entity_events`` table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


async def log_event(
    entity_id: str,
    *,
    event_type: str,
    title: str,
    significance: str = "medium",
    payload: dict[str, Any] | None = None,
    event_date: datetime | None = None,
) -> str:
    """Create an EntityEvent row. Returns the event id (UUID).

    Logs and re-raises on failure so callers can decide.
    """
    from ...database import AsyncSessionLocal
    from ...models import EntityEvent

    event_id = str(uuid.uuid4())
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                EntityEvent(
                    id=event_id,
                    entity_id=entity_id,
                    event_type=event_type,
                    significance=significance,
                    title=title,
                    is_read=False,
                    source_url=(payload or {}).get("url"),
                    payload_json=json.dumps(payload) if payload else None,
                    event_date=event_date,
                )
            )
            await db.commit()
    except Exception:
        logger.error(
            "log_event failed for %s / %s", entity_id, event_type, exc_info=True
        )
        raise

    return event_id


_COMMERCIAL_KEYWORDS = frozenset(
    [
        "acquired",
        "acquisition",
        "funding",
        "raised",
        "seed",
        "series a",
        "series b",
        "series c",
        "ipo",
        "exit",
        "partnership",
        "launched",
        "hired",
        "joins",
        "appointed",
        "ceo",
        "cto",
        "award",
    ]
)


def news_significance(title: str, category: str = "") -> str:
    """Classify a news item's significance.

    Portfolio-specific categories outrank keyword heuristics because
    commercial milestones are the main signal for portfolio tracking.
    """
    cat = (category or "").lower().strip()
    if cat in {"funding", "acquisition", "launch", "partnership", "award"}:
        return "high"
    if cat in {"other", "talk"}:
        return "low"
    text = f"{title} {category}".lower()
    if any(kw in text for kw in _COMMERCIAL_KEYWORDS):
        return "high"
    return "medium"
