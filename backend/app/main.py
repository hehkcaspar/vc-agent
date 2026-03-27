from contextlib import asynccontextmanager
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import entities, parkinglot, ingest, chat

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
    yield
    # Shutdown


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


@app.get("/")
async def root():
    return {
        "message": "VC Portfolio Manager API",
        "version": "0.1.0"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
