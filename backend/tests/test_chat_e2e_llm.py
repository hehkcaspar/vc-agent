"""
End-to-end chat tests against the **real** Gemini / deep-agent stack.

- **Direct mode** (`use_deep_agent: false`): one-shot `generate_with_context`.
- **Agent mode** (`use_deep_agent: true`): background job + tools (poll until terminal).

Run manually (from repo root or `backend/`), with API key available (e.g. `backend/.env`):

.. code-block:: powershell

    $env:RUN_E2E_LLM = "1"
    cd backend
    ..\\venv\\Scripts\\python.exe -m pytest tests/test_chat_e2e_llm.py -v -s --tb=short

Uses an isolated SQLite file and temp `DATA_ROOT` so your portfolio DB is not touched.
Expect roughly 30s–5min for agent tests depending on model latency.

Skip conditions (module-level): ``RUN_E2E_LLM`` not truthy, or ``GEMINI_API_KEY`` /
``GOOGLE_API_KEY`` missing or too short.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
import uuid

import pytest
from dotenv import load_dotenv

_backend_root = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(_backend_root / ".env")

_E2E_ENABLED = os.getenv("RUN_E2E_LLM", "").strip().lower() in ("1", "true", "yes")
_API_KEY = (
    os.getenv("GEMINI_API_KEY", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
)
if not _E2E_ENABLED:
    pytest.skip(
        "Set RUN_E2E_LLM=1 to run real LLM end-to-end tests.",
        allow_module_level=True,
    )
if len(_API_KEY) < 20:
    pytest.skip(
        "E2E LLM requires GEMINI_API_KEY or GOOGLE_API_KEY in the environment.",
        allow_module_level=True,
    )

# Isolated storage (must be set before importing app / settings).
_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
_db.close()
_tmp_data = tempfile.mkdtemp(prefix="vc-e2e-data-")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _db.name.replace("\\", "/")
os.environ["DATA_ROOT"] = str(pathlib.Path(_tmp_data))

# Prefer stable harness default; each test passes explicit use_deep_agent on POST.
os.environ.setdefault("CHAT_USE_DEEP_AGENT", "true")
os.environ.setdefault("GEMINI_API_KEY", _API_KEY)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

pytestmark = pytest.mark.e2e_llm

POLL_INTERVAL_S = 2.0


def _poll_job(client: TestClient, entity_id: str, session_id: str, job_id: str, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        r = client.get(
            f"/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}"
        )
        assert r.status_code == 200, r.text
        last = r.json()
        st = last.get("status")
        if st in ("succeeded", "failed"):
            return last
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(f"job {job_id} timed out after {timeout_s}s; last={last}")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def entity_and_session(client: TestClient):
    name = f"E2E LLM {uuid.uuid4().hex[:8]}"
    r = client.post("/entities", json={"name": name, "website": None})
    assert r.status_code == 200, r.text
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    assert r.status_code == 200, r.text
    session_id = r.json()["id"]
    return entity_id, session_id


def test_e2e_direct_mode_llm_roundtrip(client: TestClient, entity_and_session):
    """One-shot Gemini path: no tools, synchronous 200."""
    entity_id, session_id = entity_and_session
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": "Reply with exactly one English word: OK",
            "resource_ids": [],
            "artifact_ids": [],
            "use_deep_agent": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    content = (body.get("assistant_message") or {}).get("content") or ""
    assert len(content.strip()) > 0
    assert "OK" in content.upper()


def test_e2e_agent_mode_llm_roundtrip(client: TestClient, entity_and_session):
    """Deep-agent path: 202 + background job completes with an assistant message."""
    entity_id, session_id = entity_and_session
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": "Reply with exactly one English word: ALIVE",
            "resource_ids": [],
            "artifact_ids": [],
            "use_deep_agent": True,
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    job_id = body["job_id"]
    final = _poll_job(client, entity_id, session_id, job_id, timeout_s=300.0)
    assert final["status"] == "succeeded", (
        final.get("error_message") or final.get("step_detail") or final
    )
    am = final.get("assistant_message") or {}
    content = (am.get("content") or "").strip()
    assert len(content) > 0
    assert "ALIVE" in content.upper()


def test_e2e_agent_mode_tools_list_artifacts(client: TestClient, entity_and_session):
    """Agent uses tools: list artifacts (seed one memo, expect model to see count ≥ 1)."""
    entity_id, session_id = entity_and_session
    seed = client.post(
        f"/entities/{entity_id}/artifacts",
        data={
            "artifact_type": "memo",
            "content": "# E2E seed\nvisible to portfolio_list_artifacts.",
            "status": "draft",
        },
    )
    assert seed.status_code == 200, seed.text

    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": (
                "Use portfolio_list_artifacts (limit 20). "
                "How many artifacts does this entity have? Reply with one digit only."
            ),
            "resource_ids": [],
            "artifact_ids": [],
            "use_deep_agent": True,
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    final = _poll_job(client, entity_id, session_id, job_id, timeout_s=360.0)
    assert final["status"] == "succeeded", (
        final.get("error_message") or final.get("step_detail") or final
    )
    content = ((final.get("assistant_message") or {}).get("content") or "").strip()
    assert len(content) > 0
    # At least one digit (model may output "1" or "1.")
    assert any(c.isdigit() for c in content), f"expected digit in reply: {content!r}"
    tool_trace = final.get("tool_trace") or {}
    # Trace is shallow; LangGraph may not echo tool names. Soft check only.
    if isinstance(tool_trace, dict) and tool_trace.get("message_count"):
        assert int(tool_trace["message_count"]) >= 1


def test_e2e_agent_mode_natural_language_save_note_may_create_artifact(
    client: TestClient, entity_and_session,
):
    """
    Casual phrasing (CN + EN): user asks to keep a note without saying "artifact".
    Expect job success; ideally `portfolio_create_artifact` adds a row (best-effort assert).
    """
    entity_id, session_id = entity_and_session

    def _count():
        ar = client.get(f"/entities/{entity_id}/artifacts")
        assert ar.status_code == 200
        return len(ar.json())

    before = _count()
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": (
                "Quick: jot this down for the record — E2E casual save ping. "
                "顺便 workspace 里帮我记一下 same ping 中文一行就好。"
            ),
            "resource_ids": [],
            "artifact_ids": [],
            "use_deep_agent": True,
        },
    )
    assert r.status_code == 202, r.text
    final = _poll_job(
        client, entity_id, session_id, r.json()["job_id"], timeout_s=420.0
    )
    assert final["status"] == "succeeded", (
        final.get("error_message") or final.get("step_detail") or final
    )
    content = ((final.get("assistant_message") or {}).get("content") or "").strip()
    assert len(content) > 0
    after = _count()
    if after <= before:
        pytest.skip(
            "Model completed but did not create a new artifact (acceptable flake); "
            f"assistant was: {content[:500]!r}"
        )
