"""Single source of truth for UTC timestamps (DB vs JSON)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    """Timezone-aware UTC for SQLAlchemy columns using :class:`UtcDateTime`.

    Every model's timestamp column uses :class:`UtcDateTime`, which
    round-trips tz-aware UTC on both Postgres and SQLite. Returning
    naive here would mean the ORM writes naive, Postgres errors out,
    and the SQLite round-trip looks deceptively fine. Always aware.
    """
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """ISO-8601 UTC with offset, for JSON blobs and on-disk metadata."""
    return datetime.now(timezone.utc).isoformat()


class UtcDateTime(TypeDecorator):
    """DateTime column that always round-trips as tz-aware UTC.

    Postgres has native ``TIMESTAMP WITH TIME ZONE`` so round-trip is
    cheap and exact. SQLite stores datetimes as ISO strings without
    honouring ``timezone=True`` — it strips offsets on write and
    returns naive objects on read. That inconsistency is what let the
    scholar events pipeline mix naive + aware and crash on first
    Postgres deploy.

    This TypeDecorator normalises both directions:

    - Writes: if a naive datetime arrives, tag it as UTC (matches the
      project-wide convention). If aware, convert to UTC (drops any
      non-UTC offset before storage — we always store UTC).
    - Reads: if the underlying dialect handed back a naive value
      (SQLite), re-attach ``tzinfo=UTC``. Postgres-shaped reads pass
      through.

    Declaring this as the column type everywhere means downstream code
    can rely on "the timestamp I get from the DB is aware UTC" without
    per-callsite guards.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
