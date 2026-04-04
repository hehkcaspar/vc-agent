import asyncio
from contextlib import asynccontextmanager
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.academic_database import init_academic_db
from app.database import init_db
from app.routers import entities, parkinglot, ingest, chat, academic

logger = logging.getLogger(__name__)


def _guard_langsmith_tracing() -> None:
    """
    Normalize LangSmith / LangChain tracing env for this process.

    - When disabled: no outbound trace uploads (LANGSMITH_TRACING / LANGCHAIN_TRACING_V2 false).
    - When enabled but API key missing: force off and log (predictable, no failed client calls).
    """
    tracing_enabled = settings.LANGSMITH_TRACING
    api_key = settings.LANGSMITH_API_KEY.strip()
    project = settings.LANGSMITH_PROJECT.strip()
    endpoint = settings.LANGSMITH_ENDPOINT.strip() or "https://api.smith.langchain.com"

    if tracing_enabled and not api_key:
        tracing_enabled = False
        logger.warning(
            "Tracing was enabled but no LangSmith API key was provided; "
            "disabled external LangSmith tracing for this process."
        )

    tracing_flag = "true" if tracing_enabled else "false"
    os.environ["LANGSMITH_TRACING"] = tracing_flag
    # Older LangChain stacks still consult LANGCHAIN_TRACING_V2; keep in sync.
    os.environ["LANGCHAIN_TRACING_V2"] = tracing_flag

    if tracing_enabled and api_key:
        os.environ["LANGSMITH_API_KEY"] = api_key
    if tracing_enabled and project:
        os.environ["LANGSMITH_PROJECT"] = project
    if tracing_enabled:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    _guard_langsmith_tracing()
    await init_db()
    await init_academic_db()

    # Reset any scholars stuck in "evaluating" (no background tasks survive restart)
    try:
        from sqlalchemy import update
        from app.academic_database import AcademicAsyncSessionLocal
        from app.academic_models import Scholar
        async with AcademicAsyncSessionLocal() as db:
            result = await db.execute(
                update(Scholar)
                .where(Scholar.status == "evaluating")
                .values(status="active")
            )
            if result.rowcount:
                await db.commit()
                logger.info("Reset %d stuck 'evaluating' scholars to 'active'", result.rowcount)
    except Exception:
        logger.warning("Could not reset stuck scholar statuses", exc_info=True)

    # Start academic heartbeat scheduler
    from app.services.academic.heartbeat import HeartbeatScheduler
    scheduler = HeartbeatScheduler()
    scheduler_task = asyncio.create_task(scheduler.run())

    yield

    # Shutdown
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="VC Portfolio Manager",
    description="Entity-Canonical, Parking-Lot Ingestion API",
    version="0.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(entities.router)
app.include_router(chat.router)
app.include_router(parkinglot.router)
app.include_router(ingest.router)
app.include_router(academic.router)


@app.get("/")
async def root():
    return {
        "message": "VC Portfolio Manager API",
        "version": "0.1.0"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
