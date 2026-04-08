"""Shared pytest configuration.

This file MUST be imported by pytest before any test module so it can set the
test database URL before `app.config.settings` is instantiated. Per-file env
mucking races each other and breaks the full pytest sweep.

Responsibilities:
- Point DATABASE_URL (and academic equivalents) at a shared per-session temp
  directory so every test file sees the same engine.
- Skip the standalone academic __main__ scripts that pytest would otherwise
  try to collect (and trip over their own env mucking + missing API keys).
- Reset the workspace tables between tests so per-test entity creation stays
  isolated even though the engine is shared.
"""

from __future__ import annotations

import os
import tempfile

# ── Step 1: set env BEFORE any app import ────────────────────────────
_TEST_DIR = tempfile.mkdtemp(prefix="vc_pytest_")
_TEST_DB = os.path.join(_TEST_DIR, "portfolio.db")
_TEST_ACADEMIC_DB = os.path.join(_TEST_DIR, "academic.db")
_TEST_SCHOLARS_DIR = os.path.join(_TEST_DIR, "scholars")
_TEST_CONFIG_DIR = os.path.join(_TEST_DIR, "config")
_TEST_DATA_ROOT = os.path.join(_TEST_DIR, "entities")

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"
os.environ["ACADEMIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_ACADEMIC_DB}"
os.environ["ACADEMIC_SCHOLARS_DIR"] = _TEST_SCHOLARS_DIR
os.environ["ACADEMIC_CONFIG_DIR"] = _TEST_CONFIG_DIR
os.environ["DATA_ROOT"] = _TEST_DATA_ROOT
os.environ.setdefault("GEMINI_API_KEY", "test-key-for-mock")
os.environ.setdefault("LANGSMITH_TRACING", "false")

os.makedirs(_TEST_SCHOLARS_DIR, exist_ok=True)
os.makedirs(_TEST_CONFIG_DIR, exist_ok=True)
os.makedirs(_TEST_DATA_ROOT, exist_ok=True)

# ── Step 2: tell pytest to ignore standalone academic scripts ────────
# These files have no `test_*` functions — they are __main__ scripts that
# require real Gemini/SerpAPI calls and run end-to-end via direct invocation.
collect_ignore = [
    "test_academic_e2e.py",
    "test_academic_feifei.py",
    "test_academic_randomized.py",
    "test_chat_e2e_llm.py",  # gated on RUN_E2E_LLM=1, real Gemini
]

# ── Step 3: per-test DB reset ────────────────────────────────────────
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_portfolio_db():
    """Drop + recreate portfolio tables before each test for isolation.

    Uses the sync engine to avoid event-loop entanglement with TestClient,
    which spins its own anyio loop per request.
    """
    from app.database import sync_engine
    from app.models import Base

    Base.metadata.drop_all(bind=sync_engine)
    Base.metadata.create_all(bind=sync_engine)
    yield
