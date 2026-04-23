import asyncio
from contextlib import asynccontextmanager
import logging
import os
import re
import secrets
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.academic_database import init_academic_db
from app.database import init_db
from app.routers import (
    academic,
    chat,
    discrepancies,
    entities,
    entity_news,
    fact_ledger,
    ingest,
    parkinglot,
    settings as settings_router,
    workspace,
)

logger = logging.getLogger(__name__)


# Redact secrets that vendor SDKs (httpx, requests, etc.) often
# print at INFO level inside full request URLs. SerpAPI requires
# `api_key` as a query param so it always shows up in logs unless
# we filter. Add other key names here if/when needed.
_SECRET_QUERY_RE = re.compile(
    r"(api_key|x-api-key|key|token|access_token)=[^&\s\"']+",
    re.IGNORECASE,
)


class _SecretRedactor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _SECRET_QUERY_RE.sub(r"\1=***REDACTED***", str(record.msg))
            if record.args:
                record.args = tuple(
                    _SECRET_QUERY_RE.sub(r"\1=***REDACTED***", str(a))
                    if isinstance(a, str)
                    else a
                    for a in record.args
                )
        except Exception:
            pass
        return True


def _install_secret_redactor() -> None:
    f = _SecretRedactor()
    for name in ("httpx", "httpcore", "urllib3", "uvicorn.access"):
        logging.getLogger(name).addFilter(f)
    logging.getLogger().addFilter(f)


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
    _install_secret_redactor()
    _guard_langsmith_tracing()
    if not settings.APP_PASSWORD:
        logger.warning(
            "APP_PASSWORD is empty — shared-password gate is DISABLED. "
            "Set APP_PASSWORD in the environment to require auth."
        )
    await init_db()
    await init_academic_db()

    # V2 migration — ensure `last_interaction_id` column exists on
    # academic_chat_sessions (added by the scholar evaluation framework
    # rewrite). Single ALTER TABLE, idempotent.
    try:
        from sqlalchemy import text
        from app.academic_database import academic_engine
        async with academic_engine.begin() as conn:
            res = await conn.exec_driver_sql(
                "PRAGMA table_info(academic_chat_sessions)"
            )
            cols = {row[1] for row in res.fetchall()}
            if "last_interaction_id" not in cols:
                await conn.exec_driver_sql(
                    "ALTER TABLE academic_chat_sessions "
                    "ADD COLUMN last_interaction_id TEXT"
                )
                logger.info("Added last_interaction_id column to academic_chat_sessions")
    except Exception:
        logger.warning("v2 migration (last_interaction_id) failed", exc_info=True)

    # Add agent_mode column to chat_completion_jobs (react vs deep_agent dispatch)
    try:
        from app.database import engine as portfolio_engine
        async with portfolio_engine.begin() as conn:
            res = await conn.exec_driver_sql(
                "PRAGMA table_info(chat_completion_jobs)"
            )
            cols = {row[1] for row in res.fetchall()}
            if "agent_mode" not in cols:
                await conn.exec_driver_sql(
                    "ALTER TABLE chat_completion_jobs "
                    "ADD COLUMN agent_mode TEXT DEFAULT 'deep_agent'"
                )
                logger.info("Added agent_mode column to chat_completion_jobs")
    except Exception:
        logger.warning("agent_mode migration failed", exc_info=True)

    # Add metadata_json + deal_stage columns to entities
    try:
        from app.database import engine as portfolio_engine  # noqa: F811
        async with portfolio_engine.begin() as conn:
            res = await conn.exec_driver_sql(
                "PRAGMA table_info(entities)"
            )
            cols = {row[1] for row in res.fetchall()}
            if "metadata_json" not in cols:
                await conn.exec_driver_sql(
                    "ALTER TABLE entities ADD COLUMN metadata_json TEXT"
                )
                logger.info("Added metadata_json column to entities")
            if "deal_stage" not in cols:
                await conn.exec_driver_sql(
                    "ALTER TABLE entities ADD COLUMN deal_stage TEXT "
                    "NOT NULL DEFAULT 'diligence'"
                )
                logger.info("Added deal_stage column to entities")
    except Exception:
        logger.warning("entities column migration failed", exc_info=True)

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

    # Trim the evaluation log if it has grown past its bounds — cheap
    # startup check, no effect during steady-state operation.
    try:
        from app.services.academic.eval_log import rotate_if_needed
        rotate_if_needed()
    except Exception:
        logger.warning("eval_log rotation check failed", exc_info=True)

    # Seed legal-review config files if they don't exist yet. Both files are
    # idempotent — no-op when already present.
    try:
        from app.services.legal_templates_config import ensure_legal_templates_seed
        from app.services.legal_review_checklist_config import (
            ensure_legal_review_checklist_seed,
        )
        ensure_legal_templates_seed()
        ensure_legal_review_checklist_seed()
    except Exception:
        logger.warning("legal_review config seeding failed", exc_info=True)

    # Start academic heartbeat scheduler
    from app.services.academic.heartbeat import HeartbeatScheduler
    scheduler = HeartbeatScheduler()
    scheduler_task = asyncio.create_task(scheduler.run())

    # Start portfolio news scheduler (per-entity cadence-driven news_web ticks)
    from app.services.portfolio.news_scheduler import PortfolioNewsScheduler
    news_scheduler = PortfolioNewsScheduler()
    news_scheduler_task = asyncio.create_task(news_scheduler.run())

    yield

    # Shutdown
    for t in (scheduler_task, news_scheduler_task):
        t.cancel()
        try:
            await t
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


# Shared-password gate. When APP_PASSWORD is unset, pass-through (local dev).
# When set, every request except liveness + CORS preflight must carry an
# X-Access-Password header matching it (constant-time compare).
_AUTH_EXEMPT_PATHS = frozenset({"/", "/health"})


@app.middleware("http")
async def shared_password_gate(request: Request, call_next):
    expected = settings.APP_PASSWORD
    if not expected:
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    provided = request.headers.get("X-Access-Password", "")
    if not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# Include routers
app.include_router(entities.router)
app.include_router(chat.router)
app.include_router(parkinglot.router)
app.include_router(ingest.router)
app.include_router(workspace.router)
app.include_router(academic.router)
app.include_router(settings_router.router)
app.include_router(discrepancies.router)
app.include_router(fact_ledger.router)
app.include_router(entity_news.router)


@app.get("/")
async def root():
    return {
        "message": "VC Portfolio Manager API",
        "version": "0.1.0"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/auth/verify")
async def auth_verify():
    # Reaching this route means the shared-password middleware accepted the
    # header. The SPA's LoginGate calls this to validate before persisting.
    return {"ok": True}
