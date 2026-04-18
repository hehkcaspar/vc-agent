"""
Academic Tracking v2 database — separate from the portfolio DB.

Works on both SQLite (local dev default) and Postgres (production). Schema
itself is driver-portable; the two divergences (WAL journaling, sqlite-only
connect_args) are guarded by settings.academic_is_sqlite.

v2 uses 3 SQL tables (scholars, scholar_events, channels) as a lightweight
index. Full scholar state lives in JSON/JSONL/markdown files on disk.
See docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

AcademicBase = declarative_base()

academic_engine = create_async_engine(
    settings.ACADEMIC_DATABASE_URL,
    echo=False,
    future=True,
)

AcademicAsyncSessionLocal = async_sessionmaker(
    academic_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine for agent tool callbacks that run outside async context.
_sync_connect_args = {"check_same_thread": False} if settings.academic_is_sqlite else {}
academic_sync_engine = create_engine(
    settings.academic_database_url_sync,
    future=True,
    connect_args=_sync_connect_args,
)

# Enable WAL mode on SQLite (design doc §9.4 — improves concurrent read/write).
# No-op on Postgres, which has MVCC built in.
if settings.academic_is_sqlite:
    @event.listens_for(academic_sync_engine, "connect")
    def _set_wal_sync(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


AcademicSyncSessionLocal = sessionmaker(
    bind=academic_sync_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


async def init_academic_db():
    """Create all academic v2 tables (idempotent)."""
    import app.academic_models  # noqa: F401

    async with academic_engine.begin() as conn:
        if settings.academic_is_sqlite:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(AcademicBase.metadata.create_all)


async def get_academic_db():
    """FastAPI dependency — yields an async session for the academic DB."""
    async with AcademicAsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
