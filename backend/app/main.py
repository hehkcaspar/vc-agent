import asyncio
from contextlib import asynccontextmanager
import logging
import os
import re
import secrets
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect as sa_inspect

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
    initial_screening_actions,
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

    # Idempotent column-adds. Driver-agnostic: sqlalchemy.inspect works on
    # both SQLite and Postgres. ALTER TABLE ... ADD COLUMN is ANSI-standard
    # and accepted unchanged by both (TEXT type, DEFAULT literals all work).

    async def _missing_columns(engine_async, table: str, *needed: str) -> set[str]:
        """Return the subset of `needed` that don't exist on `table`."""
        async with engine_async.connect() as conn:
            present = await conn.run_sync(
                lambda sc: {c["name"] for c in sa_inspect(sc).get_columns(table)}
            )
        return {c for c in needed if c not in present}

    # V2 migration — last_interaction_id on academic_chat_sessions.
    try:
        from app.academic_database import academic_engine
        if "last_interaction_id" in await _missing_columns(
            academic_engine, "academic_chat_sessions", "last_interaction_id"
        ):
            async with academic_engine.begin() as conn:
                await conn.exec_driver_sql(
                    "ALTER TABLE academic_chat_sessions "
                    "ADD COLUMN last_interaction_id TEXT"
                )
            logger.info("Added last_interaction_id column to academic_chat_sessions")
    except Exception:
        logger.warning("v2 migration (last_interaction_id) failed", exc_info=True)

    # agent_mode on chat_completion_jobs (react vs deep_agent dispatch).
    try:
        from app.database import engine as portfolio_engine
        if "agent_mode" in await _missing_columns(
            portfolio_engine, "chat_completion_jobs", "agent_mode"
        ):
            async with portfolio_engine.begin() as conn:
                await conn.exec_driver_sql(
                    "ALTER TABLE chat_completion_jobs "
                    "ADD COLUMN agent_mode TEXT DEFAULT 'deep_agent'"
                )
            logger.info("Added agent_mode column to chat_completion_jobs")
    except Exception:
        logger.warning("agent_mode migration failed", exc_info=True)

    # metadata_json + deal_stage on entities.
    try:
        from app.database import engine as portfolio_engine  # noqa: F811
        missing = await _missing_columns(
            portfolio_engine, "entities", "metadata_json", "deal_stage"
        )
        if missing:
            async with portfolio_engine.begin() as conn:
                if "metadata_json" in missing:
                    await conn.exec_driver_sql(
                        "ALTER TABLE entities ADD COLUMN metadata_json TEXT"
                    )
                    logger.info("Added metadata_json column to entities")
                if "deal_stage" in missing:
                    await conn.exec_driver_sql(
                        "ALTER TABLE entities ADD COLUMN deal_stage TEXT "
                        "NOT NULL DEFAULT 'diligence'"
                    )
                    logger.info("Added deal_stage column to entities")
    except Exception:
        logger.warning("entities column migration failed", exc_info=True)

    # Upgrade entity-referring FKs to ON DELETE CASCADE.
    # SQLite doesn't enforce FKs by default so this class of bug only bites
    # on Postgres: DELETE entity with children in workspace_nodes (self-FK
    # on parent_id) or workspace_ops (no ORM relationship) fails with
    # `update or delete … violates foreign key constraint "…_fkey"`.
    # pg_constraint.confdeltype: 'c' = CASCADE, 'a' = NO ACTION. Loop
    # covers the four child tables plus the self-referential
    # workspace_nodes.parent_id. Only runs on Postgres; SQLite paths
    # untouched.
    _FK_CASCADE_SPEC: tuple[tuple[str, str, str, str], ...] = (
        # (table, constraint_name, column, references)
        ("workspace_nodes", "workspace_nodes_parent_id_fkey", "parent_id", "workspace_nodes(id)"),
        ("workspace_nodes", "workspace_nodes_entity_id_fkey", "entity_id", "entities(id)"),
        ("workspace_ops", "workspace_ops_entity_id_fkey", "entity_id", "entities(id)"),
        ("conversation_sessions", "conversation_sessions_entity_id_fkey", "entity_id", "entities(id)"),
        ("chat_completion_jobs", "chat_completion_jobs_entity_id_fkey", "entity_id", "entities(id)"),
    )
    try:
        if not settings.portfolio_is_sqlite:
            from app.database import engine as portfolio_engine  # noqa: F811
            async with portfolio_engine.begin() as conn:
                for table, constraint, column, references in _FK_CASCADE_SPEC:
                    res = await conn.exec_driver_sql(
                        f"SELECT confdeltype FROM pg_constraint "
                        f"WHERE conname = '{constraint}'"
                    )
                    row = res.fetchone()
                    if row and row[0] != "c":
                        await conn.exec_driver_sql(
                            f'ALTER TABLE {table} DROP CONSTRAINT "{constraint}"'
                        )
                        await conn.exec_driver_sql(
                            f'ALTER TABLE {table} '
                            f'ADD CONSTRAINT "{constraint}" '
                            f'FOREIGN KEY ({column}) REFERENCES {references} '
                            f'ON DELETE CASCADE'
                        )
                        logger.info(
                            "Upgraded %s.%s FK to ON DELETE CASCADE", table, column
                        )
    except Exception:
        logger.warning("FK cascade migration failed", exc_info=True)

    # Upgrade every `TIMESTAMP WITHOUT TIME ZONE` column to
    # `TIMESTAMP WITH TIME ZONE` so the new `UtcDateTime` TypeDecorator
    # round-trips cleanly on Postgres. Existing naive values were
    # stored as UTC wall-time by `utc_now()`; the `AT TIME ZONE 'UTC'`
    # clause tells Postgres to reinterpret them as UTC instants.
    # Discovery is dynamic (information_schema) so any new DateTime
    # column gets migrated on next boot without code changes. Scope is
    # limited to the `public` schema to avoid touching Postgres
    # internals or extensions. SQLite-backed instances skip entirely.
    async def _upgrade_timestamp_columns(engine, label: str) -> None:
        async with engine.begin() as conn:
            res = await conn.exec_driver_sql(
                "SELECT table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND data_type = 'timestamp without time zone'"
            )
            rows = res.fetchall()
            for table, column in rows:
                await conn.exec_driver_sql(
                    f'ALTER TABLE "{table}" '
                    f'ALTER COLUMN "{column}" '
                    f'TYPE TIMESTAMP WITH TIME ZONE '
                    f'USING "{column}" AT TIME ZONE \'UTC\''
                )
                logger.info(
                    "TZ migration (%s): %s.%s -> TIMESTAMPTZ",
                    label, table, column,
                )

    try:
        if not settings.portfolio_is_sqlite:
            from app.database import engine as portfolio_engine  # noqa: F811
            await _upgrade_timestamp_columns(portfolio_engine, "portfolio")
    except Exception:
        logger.warning("TZ migration (portfolio) failed", exc_info=True)
    try:
        if not settings.academic_is_sqlite:
            from app.academic_database import academic_engine  # noqa: F811
            await _upgrade_timestamp_columns(academic_engine, "academic")
    except Exception:
        logger.warning("TZ migration (academic) failed", exc_info=True)

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

    # Seed universal config files (schema, D1-D4 prompts, heartbeat schedule,
    # starter ranking presets, legal review). All ensure-functions are
    # idempotent and never overwrite existing user-customised files.
    # Per-environment files (funds.json, digests/*) are intentionally NOT
    # seeded — they must come from user action or runtime generation.
    try:
        from app.services.legal_templates_config import ensure_legal_templates_seed
        from app.services.legal_review_checklist_config import (
            ensure_legal_review_checklist_seed,
        )
        from app.services.config_seeding import ensure_universal_configs_seeded
        ensure_legal_templates_seed()
        ensure_legal_review_checklist_seed()
        ensure_universal_configs_seeded()
    except Exception:
        logger.warning("config seeding failed", exc_info=True)

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


# Strip a leading '/api' from inbound paths. Vite dev proxy already rewrites
# '/api/**' → backend without the prefix; Firebase Hosting rewrites do not
# strip, so this normalises production to match dev. Routers stay
# prefix-free.
@app.middleware("http")
async def _strip_api_prefix(request, call_next):
    path = request.url.path
    if path == "/api":
        request.scope["path"] = "/"
        request.scope["raw_path"] = b"/"
    elif path.startswith("/api/"):
        new_path = path[len("/api"):]
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode("utf-8")
    return await call_next(request)


# CORS middleware. Origins configurable via CORS_ORIGINS env var (comma-separated).
# Note: allow_credentials=True is incompatible with allow_origins=["*"] per the
# CORS spec — Starlette silently drops credentials in that case. When a concrete
# origin list is provided, credentials work normally.
_cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
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
app.include_router(initial_screening_actions.router)


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
