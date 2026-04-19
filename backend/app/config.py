from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


# Get project root directory (parent of backend)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Gemini model defaults (override with env / .env: GEMINI_MODEL, GEMINI_METADATA_EXTRACTION_MODEL)
_DEFAULT_GEMINI_CHAT_MODEL = "gemini-3.1-pro-preview"
_DEFAULT_GEMINI_METADATA_EXTRACTION_MODEL = "gemini-3.1-flash-lite-preview"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATA_ROOT: Path = PROJECT_ROOT / "data" / "entities"
    DATABASE_URL: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'vc_portfolio.db'}"

    # Object-store bucket for workspace blobs. When set (prod on Cloud Run
    # with GCS FUSE mount), storage adapter issues v4 signed PUT URLs so
    # the browser uploads bytes directly to GCS, bypassing Cloud Run's
    # 32 MB request-body limit. When empty (local dev), adapter falls
    # back to direct-POST uploads through the backend.
    GCS_BUCKET: str = ""

    # Gemini (google-genai). API key from https://aistudio.google.com/apikey
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = _DEFAULT_GEMINI_CHAT_MODEL
    GEMINI_METADATA_EXTRACTION_MODEL: str = _DEFAULT_GEMINI_METADATA_EXTRACTION_MODEL
    # ── Chat: shared settings ──
    CHAT_MAX_HISTORY_MESSAGES: int = 40
    CHAT_DEFAULT_MODEL_PROFILE: str = "gemini_google"
    CHAT_ENABLE_GOOGLE_SEARCH: bool = True

    # ── Chat: mode selection ──
    # "one_shot" — synchronous single Gemini/Kimi call; selected files are
    #   inlined into the prompt (capped by MAX_ATTACHMENTS / MAX_TEXT_CHARS
    #   in gemini_context.py). Fast, no tools.
    # "react" (Agent) — async background job via LangChain ReAct agent with
    #   13 workspace tools. Files NOT inlined; agent reads on demand via
    #   workspace_read_file. No file-count limit.
    # "deep_agent" — legacy compat (removable). Same as react but adds
    #   9 SDK built-in tools via deepagents.
    CHAT_DEFAULT_AGENT_MODE: str = "one_shot"
    CHAT_USE_DEEP_AGENT: bool = False          # legacy; overridden by agent_mode

    # ── Chat: one-shot-only limits ──
    # These apply ONLY to one_shot mode. Agent mode has no attachment limits.
    CHAT_MAX_ATTACHMENT_BYTES: int = 20 * 1024 * 1024  # 20 MB per file
    # File count + text-char limits are hardcoded in gemini_context.py:
    #   MAX_ATTACHMENTS = 10, MAX_TEXT_CHARS = 200_000

    # ── Chat: agent-only settings ──
    CHAT_AGENT_RECURSION_LIMIT: int = 100      # LangGraph recursion limit (react/deep_agent only)

    # Gemini Interactions API: TTL in days (paid tier retains 55 days; 0 = free tier / disabled)
    GEMINI_INTERACTION_TTL_DAYS: int = 50

    # Workspace
    WORKSPACE_MAX_FILE_BYTES: int = 50 * 1024 * 1024        # 50 MB per file
    WORKSPACE_MAX_ZIP_BYTES: int = 500 * 1024 * 1024        # 500 MB per zip upload
    WORKSPACE_VERSION_RETENTION_DAYS: int = 30
    WORKSPACE_INTAKE_SAMPLE_SIZE: int = 5                   # Path B needs_sampling budget
    # Kimi / Moonshot OpenAI-compatible API (/v1/chat/completions).
    # - Open Platform (console API keys): https://api.moonshot.ai/v1 or https://api.moonshot.cn/v1
    # - Kimi Code platform (/login "Kimi Code" in kimi-cli): https://api.kimi.com/coding/v1
    # If only KIMI_CODE_API_KEY is set (MOONSHOT_API_KEY empty), code uses KIMI_CODE_BASE_URL by default.
    MOONSHOT_API_KEY: str = ""
    KIMI_CODE_API_KEY: str = ""
    MOONSHOT_BASE_URL: str = "https://api.moonshot.ai/v1"
    KIMI_CODE_BASE_URL: str = "https://api.kimi.com/coding/v1"
    # Optional: force OpenAI-compatible base URL (overrides Kimi Code vs Open Platform auto-pick).
    KIMI_OPENAI_BASE_URL: str = ""
    MOONSHOT_MODEL: str = "kimi-k2.5"
    # Model id for api.kimi.com/coding/v1. Moonshot lists kimi-k2.5 on Open Platform; coding endpoint accepts the same id (and alias kimi-for-coding).
    KIMI_CODE_MODEL: str = "kimi-k2.5"
    KIMI_CODE_HTTP_USER_AGENT: str = "KimiCLI/1.6"
    # Kimi K2.5: disable extended thinking on OpenAI-compatible calls (default True). Thinking
    # requires reasoning_content on prior tool messages; LangChain agent history omits it → HTTP 400.
    KIMI_DISABLE_THINKING_FOR_SEARCH: bool = True

    # Portfolio settings — fund registry for our positions
    FUNDS_CONFIG_PATH: Path = PROJECT_ROOT / "data" / "config" / "funds.json"

    # Portfolio — legal-review preset
    # Tier R1: raw template corpus shipped with the codebase (YC SAFE, NVCA, etc.)
    LEGAL_TEMPLATES_DIR: Path = PROJECT_ROOT / "backend" / "app" / "legal_templates"
    LEGAL_TEMPLATES_CONFIG_PATH: Path = PROJECT_ROOT / "data" / "config" / "legal_templates.json"
    # Tier R2: distilled review checklist (user-tunable rubric)
    LEGAL_REVIEW_CHECKLIST_CONFIG_PATH: Path = PROJECT_ROOT / "data" / "config" / "legal_review_checklist.json"

    # Academic Tracking v2 — separate DB + document-oriented storage
    ACADEMIC_DATABASE_URL: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'academic.db'}"
    ACADEMIC_SCHOLARS_DIR: Path = PROJECT_ROOT / "data" / "scholars"
    ACADEMIC_CONFIG_DIR: Path = PROJECT_ROOT / "data" / "config"
    SERPAPI_KEY: str = ""
    SEMANTIC_SCHOLAR_API_KEY: str = ""  # optional; free tier works without key
    ACADEMIC_GEMINI_MODEL: str = "gemini-3-flash-preview"

    # LangSmith tracing (project-level runtime config).
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "vc-portfolio-agent"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    # CORS — comma-separated list of allowed origins, or "*" for any (dev default).
    # In production, set to the deployed frontend URL(s), e.g. the Firebase
    # Hosting domain. Multiple origins accepted comma-separated.
    CORS_ORIGINS: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.CORS_ORIGINS.strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @staticmethod
    def _to_sync_url(url: str) -> str:
        """Map an async SQLAlchemy URL to its sync counterpart.

        aiosqlite → sqlite (stdlib), asyncpg → psycopg v3. Any other prefix
        is returned unchanged (treated as already-sync).
        """
        if url.startswith("sqlite+aiosqlite:///"):
            return "sqlite:///" + url.removeprefix("sqlite+aiosqlite:///")
        if url.startswith("postgresql+asyncpg://"):
            return "postgresql+psycopg://" + url.removeprefix("postgresql+asyncpg://")
        return url

    @property
    def database_url_sync(self) -> str:
        """Sync URL for the portfolio DB (LangChain workspace tools use this)."""
        return self._to_sync_url(self.DATABASE_URL)

    @property
    def academic_database_url_sync(self) -> str:
        """Sync URL for the academic DB."""
        return self._to_sync_url(self.ACADEMIC_DATABASE_URL)

    @staticmethod
    def _is_sqlite_url(url: str) -> bool:
        return url.startswith("sqlite")

    @property
    def portfolio_is_sqlite(self) -> bool:
        return self._is_sqlite_url(self.DATABASE_URL)

    @property
    def academic_is_sqlite(self) -> bool:
        return self._is_sqlite_url(self.ACADEMIC_DATABASE_URL)


settings = Settings()
