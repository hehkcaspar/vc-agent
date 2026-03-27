from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Sync engine for LangChain tool callbacks (same SQLite file as async engine).
sync_engine = create_engine(
    settings.database_url_sync,
    future=True,
    connect_args={"check_same_thread": False},
)
SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def _ensure_sqlite_artifact_title(connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    result = connection.execute(text("PRAGMA table_info(artifacts)"))
    columns = {row[1] for row in result.fetchall()}
    if "title" not in columns:
        connection.execute(text("ALTER TABLE artifacts ADD COLUMN title VARCHAR"))


def _ensure_sqlite_resource_metadata_json(connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    result = connection.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='resources'"
        )
    )
    if result.fetchone() is None:
        return
    result = connection.execute(text("PRAGMA table_info(resources)"))
    columns = {row[1] for row in result.fetchall()}
    if "metadata_json" not in columns:
        connection.execute(
            text("ALTER TABLE resources ADD COLUMN metadata_json TEXT")
        )


def _ensure_sqlite_artifact_metadata_json(connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    result = connection.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifacts'"
        )
    )
    if result.fetchone() is None:
        return
    result = connection.execute(text("PRAGMA table_info(artifacts)"))
    columns = {row[1] for row in result.fetchall()}
    if "metadata_json" not in columns:
        connection.execute(
            text("ALTER TABLE artifacts ADD COLUMN metadata_json TEXT")
        )


# SQLite `create_all` does not ALTER existing tables — add any missing columns here.
_SQLITE_ARTIFACT_EDIT_EVENTS_ADDS: tuple[tuple[str, str], ...] = (
    ("correlation_id", "VARCHAR"),
    ("entity_id", "VARCHAR"),
    ("session_id", "VARCHAR"),
    ("artifact_id", "VARCHAR"),
    ("requested_mode", "VARCHAR"),
    ("resolved_mode", "VARCHAR"),
    ("state", "VARCHAR"),
    ("intent_summary", "TEXT"),
    ("tool_context_json", "TEXT"),
    ("validation_result_json", "TEXT"),
    ("before_checksum", "VARCHAR"),
    ("after_checksum", "VARCHAR"),
    ("error_message", "TEXT"),
    ("run_id", "VARCHAR"),
    ("pipeline_version", "VARCHAR"),
    ("created_at", "DATETIME"),
)


def _ensure_sqlite_artifact_edit_events(connection) -> None:
    """Bring pre-Option-B artifact_edit_events tables up to current ArtifactEditEvent columns."""
    if connection.dialect.name != "sqlite":
        return
    result = connection.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='artifact_edit_events'"
        )
    )
    if result.fetchone() is None:
        return
    result = connection.execute(text("PRAGMA table_info(artifact_edit_events)"))
    columns = {row[1] for row in result.fetchall()}
    for col_name, sql_type in _SQLITE_ARTIFACT_EDIT_EVENTS_ADDS:
        if col_name not in columns:
            connection.execute(
                text(f"ALTER TABLE artifact_edit_events ADD COLUMN {col_name} {sql_type}")
            )
    # Backfill rows touched by incremental ADD COLUMN (all new cols are NULL at first).
    connection.execute(
        text(
            "UPDATE artifact_edit_events SET correlation_id = id "
            "WHERE correlation_id IS NULL OR correlation_id = ''"
        )
    )
    connection.execute(
        text(
            "UPDATE artifact_edit_events SET state = 'migrated' "
            "WHERE state IS NULL OR state = ''"
        )
    )
    connection.execute(
        text(
            "UPDATE artifact_edit_events SET pipeline_version = 'option_b' "
            "WHERE pipeline_version IS NULL OR pipeline_version = ''"
        )
    )
    connection.execute(
        text(
            "UPDATE artifact_edit_events SET created_at = CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL"
        )
    )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_sqlite_artifact_title)
        await conn.run_sync(_ensure_sqlite_resource_metadata_json)
        await conn.run_sync(_ensure_sqlite_artifact_metadata_json)
        await conn.run_sync(_ensure_sqlite_artifact_edit_events)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
