"""
Academic Tracking v2 database — separate SQLite file from the portfolio DB.

v2 uses 3 SQL tables (scholars, scholar_events, channels) as a lightweight
index.  Full scholar state lives in JSON/JSONL/markdown files on disk.
See doc/ACADEMIC_TRACKING_V2_DESIGN.md §2.
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
academic_sync_engine = create_engine(
    settings.academic_database_url_sync,
    future=True,
    connect_args={"check_same_thread": False},
)

# Enable WAL mode for concurrent read/write (design doc §9.4)
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
        # Enable WAL mode for the async engine too
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(AcademicBase.metadata.create_all)


async def get_academic_db():
    """FastAPI dependency — yields an async session for the academic DB."""
    async with AcademicAsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
