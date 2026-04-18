from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine for LangChain workspace tools (sync sessions used inside
# threadpool-executed tool callbacks).
# `check_same_thread=False` is a SQLite-only arg (pysqlite default is single-
# thread); Postgres drivers reject unknown connect_args.
_sync_connect_args = {"check_same_thread": False} if settings.portfolio_is_sqlite else {}
sync_engine = create_engine(
    settings.database_url_sync,
    future=True,
    connect_args=_sync_connect_args,
)
SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
