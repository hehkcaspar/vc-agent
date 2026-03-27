"""Single source of truth for UTC timestamps (DB vs JSON)."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Naive UTC for SQLAlchemy DateTime columns (SQLite stores UTC wall time)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now_iso() -> str:
    """ISO-8601 UTC with offset, for JSON blobs and on-disk metadata."""
    return datetime.now(timezone.utc).isoformat()
