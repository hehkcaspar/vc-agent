"""Scholar timeline events — SQL single source of truth.

All event creation goes through `log_event()` which writes a
`ScholarEvent` row. The SQL table powers the timeline, signal feed,
mark-as-read, and digest queries. No JSONL duplication.

Layer 2 sources that discover timeline-worthy items (papers, news,
patents, etc.) should call `log_event` with the real `event_date`
parsed from upstream data. `event_date=None` means unknown.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalise to naive-UTC to match the DateTime column storage.

    The rest of the app uses `datetime_support.utc_now()` which is
    naive-UTC by convention. SQLite silently coerced tz-aware sources
    (e.g. news_web's `_parse_date` which tags UTC); Postgres rejects
    the insert with "can't subtract offset-naive and offset-aware
    datetimes". Convert to UTC then strip tzinfo.
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def log_event(
    scholar_id: str,
    *,
    event_type: str,
    title: str,
    significance: str = "medium",
    payload: dict[str, Any] | None = None,
    event_date: datetime | None = None,
) -> str:
    """Create a ScholarEvent row. Returns the event id (UUID).

    Logs and re-raises on failure so callers can decide whether to
    swallow or propagate the error.
    """
    from ...academic_database import AcademicAsyncSessionLocal
    from ...academic_models import ScholarEvent

    event_id = str(uuid.uuid4())
    try:
        async with AcademicAsyncSessionLocal() as db:
            db.add(
                ScholarEvent(
                    id=event_id,
                    scholar_id=scholar_id,
                    event_type=event_type,
                    significance=significance,
                    title=title,
                    is_read=False,
                    source_url=(payload or {}).get("url"),
                    event_date=_to_naive_utc(event_date),
                )
            )
            await db.commit()
    except Exception:
        logger.error(
            "log_event failed for %s / %s", scholar_id, event_type, exc_info=True
        )
        raise

    return event_id


_CAREER_KEYWORDS = frozenset(
    [
        "appointed",
        "joins",
        "founded",
        "startup",
        "ceo",
        "cto",
        "advisor",
        "award",
        "prize",
        "elected",
        "hired",
        "launched",
        "acquired",
        "acquisition",
        "funding",
        "raised",
        "seed",
        "series a",
        "series b",
        "ipo",
        "exit",
    ]
)


def news_significance(title: str, category: str = "") -> str:
    """Classify a news item's significance from its title + category.

    Careerish keywords → `high`. Everything else → `medium` unless
    the category is a mundane `other`/`talk`, in which case `low`.
    """
    text = f"{title} {category}".lower()
    if any(kw in text for kw in _CAREER_KEYWORDS):
        return "high"
    if category in {"funding", "launch", "partnership", "award", "appointment"}:
        return "high"
    if category in {"other", "talk"}:
        return "low"
    return "medium"


def paper_significance(citations: int, position: str | None = None) -> str:
    """Classify a newly-discovered paper by citations + position.

    - first/last author + any citations → medium
    - middle author → low
    - no position info → medium (default)
    """
    if position in {"first", "last"}:
        return "medium"
    if position == "middle":
        return "low"
    return "medium"
