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

    # Gemini (google-genai). API key from https://aistudio.google.com/apikey
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = _DEFAULT_GEMINI_CHAT_MODEL
    GEMINI_METADATA_EXTRACTION_MODEL: str = _DEFAULT_GEMINI_METADATA_EXTRACTION_MODEL
    CHAT_MAX_HISTORY_MESSAGES: int = 40
    CHAT_MAX_ATTACHMENT_BYTES: int = 20 * 1024 * 1024
    CHAT_ENABLE_GOOGLE_SEARCH: bool = True

    # Deep Agents harness (LangChain). When False, chat uses direct google-genai / Kimi path.
    CHAT_USE_DEEP_AGENT: bool = False
    # Tri-state mode: "one_shot" | "react" | "deep_agent". Used when client
    # does not send an explicit agent_mode or use_deep_agent.
    CHAT_DEFAULT_AGENT_MODE: str = "one_shot"
    CHAT_AGENT_RECURSION_LIMIT: int = 100
    CHAT_DEFAULT_MODEL_PROFILE: str = "gemini_google"

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

    @property
    def database_url_sync(self) -> str:
        """SQLAlchemy sync URL for the same SQLite file (agent tools / sync session)."""
        u = self.DATABASE_URL
        if u.startswith("sqlite+aiosqlite:///"):
            return "sqlite:///" + u.removeprefix("sqlite+aiosqlite:///")
        return u

    @property
    def academic_database_url_sync(self) -> str:
        """Sync URL for the academic SQLite file."""
        u = self.ACADEMIC_DATABASE_URL
        if u.startswith("sqlite+aiosqlite:///"):
            return "sqlite:///" + u.removeprefix("sqlite+aiosqlite:///")
        return u


settings = Settings()
