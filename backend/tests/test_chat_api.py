"""Chat API smoke tests with Gemini calls mocked."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid

import pytest
from fastapi.testclient import TestClient

_db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
_db_file.close()
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///" + _db_file.name.replace("\\", "/")
)
os.environ.setdefault("GEMINI_API_KEY", "test-key-for-mock")

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def _stub_gemini(**kwargs) -> str:
    return "Assistant stub reply."


def _stub_gemini_json(**kwargs) -> str:
    return json.dumps(
        {
            "company_name": {"value": "StubCo", "confidence": "high"},
            "founders": [],
        }
    )


@pytest.fixture
def client(monkeypatch):
    # Local .env may set CHAT_USE_DEEP_AGENT=true; tests expect stubbed legacy path unless overridden.
    monkeypatch.setattr(settings, "CHAT_USE_DEEP_AGENT", False)
    monkeypatch.setattr(
        "app.routers.chat.generate_with_context",
        _stub_gemini,
    )
    monkeypatch.setattr(
        "app.routers.chat.generate_json_with_context",
        _stub_gemini_json,
    )
    with TestClient(app) as c:
        yield c


def test_post_message_deep_agent_override_off_uses_legacy(
    client: TestClient, monkeypatch
):
    """Client can force one-shot Gemini even when server default is harness."""
    monkeypatch.setattr(settings, "CHAT_USE_DEEP_AGENT", True)
    monkeypatch.setattr(
        "app.routers.chat.generate_with_context",
        _stub_gemini,
    )
    r = client.post("/entities", json={"name": "Override Co"})
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    session_id = r.json()["id"]
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": "Hi",
            "resource_ids": [],
            "artifact_ids": [],
            "use_deep_agent": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["assistant_message"]["content"] == "Assistant stub reply."


def test_post_message_deep_agent_override_on_uses_harness(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "CHAT_USE_DEEP_AGENT", False)

    class _FakeAgent:
        def invoke(self, *args, **kwargs):
            from langchain_core.messages import AIMessage

            return {"messages": [AIMessage(content="Harness from override")]}

    monkeypatch.setattr(
        "app.routers.chat.create_portfolio_agent",
        lambda **kw: _FakeAgent(),
    )
    r = client.post("/entities", json={"name": "Override Harness Co"})
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    session_id = r.json()["id"]
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": "Hi",
            "resource_ids": [],
            "artifact_ids": [],
            "use_deep_agent": True,
        },
    )
    assert r.status_code == 202
    accepted = r.json()
    job_id = accepted["job_id"]
    st = {"status": "pending"}
    for _ in range(100):
        jr = client.get(
            f"/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}"
        )
        assert jr.status_code == 200
        st = jr.json()
        if st["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.02)
    assert st["status"] == "succeeded"
    assert st["assistant_message"]["content"] == "Harness from override"


def test_post_message_uses_deep_agent_when_enabled(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_USE_DEEP_AGENT", True)

    class _FakeAgent:
        def invoke(self, *args, **kwargs):
            from langchain_core.messages import AIMessage

            return {"messages": [AIMessage(content="Harness reply")]}

    monkeypatch.setattr(
        "app.routers.chat.create_portfolio_agent",
        lambda **kw: _FakeAgent(),
    )
    r = client.post("/entities", json={"name": "Deep Co"})
    assert r.status_code == 200
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    session_id = r.json()["id"]
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={"text": "Hi", "resource_ids": [], "artifact_ids": []},
    )
    assert r.status_code == 202
    accepted = r.json()
    job_id = accepted["job_id"]
    assert accepted["user_message"]["role"] == "user"
    st = {"status": "pending"}
    for _ in range(100):
        jr = client.get(
            f"/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}"
        )
        assert jr.status_code == 200
        st = jr.json()
        if st["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.02)
    assert st["status"] == "succeeded"
    assert st["assistant_message"]["content"] == "Harness reply"
    assert st.get("run_id")
    assert st.get("tool_trace")


def test_chat_session_flow(client: TestClient):
    r = client.post("/entities", json={"name": "Acme Corp", "website": "https://acme.test"})
    assert r.status_code == 200
    entity_id = r.json()["id"]

    r = client.get(f"/entities/{entity_id}/chat/presets")
    assert r.status_code == 200
    presets = r.json()
    assert any(p["id"] == "red_team" for p in presets)
    assert any(p["id"] == "extract_info" for p in presets)

    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    assert r.status_code == 200
    session_id = r.json()["id"]

    r = client.get(f"/entities/{entity_id}/chat/sessions")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    r = client.get(f"/entities/{entity_id}/chat/sessions/{session_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["session"]["id"] == session_id
    assert detail["messages"] == []

    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={"text": "Hello", "resource_ids": [], "artifact_ids": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert "assistant_message" in body
    assert body["assistant_message"]["role"] == "assistant"

    r = client.get(f"/entities/{entity_id}/chat/sessions/{session_id}")
    assert len(r.json()["messages"]) == 2


def test_preset_run_creates_artifact(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.routers.chat.generate_with_context",
        lambda **kw: "# Red team report\n\nStub markdown.",
    )
    r = client.post("/entities", json={"name": "Beta Inc"})
    entity_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/chat/presets/red_team/run",
        json={"resource_ids": [], "artifact_ids": []},
    )
    assert r.status_code == 200
    aid = r.json()["artifact_id"]
    assert uuid.UUID(aid).version == 4

    arts = client.get(f"/entities/{entity_id}/artifacts").json()
    created = next(a for a in arts if a["id"] == aid)
    assert created["title"] == "risk_analyze"


def test_preset_run_session_message_is_artifact_card_json(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.routers.chat.generate_with_context",
        lambda **kw: "# Report\n\nShort.",
    )
    r = client.post("/entities", json={"name": "Gamma LLC"})
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    session_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/chat/presets/red_team/run",
        json={"resource_ids": [], "artifact_ids": [], "session_id": session_id},
    )
    assert r.status_code == 200
    aid = r.json()["artifact_id"]

    detail = client.get(f"/entities/{entity_id}/chat/sessions/{session_id}").json()
    msgs = detail["messages"]
    assert len(msgs) == 1
    data = json.loads(msgs[0]["content"])
    assert data["_vc_chat"] == "artifact_card"
    assert data["artifact_id"] == aid
    assert data["preset_label"] == "Red team diligence"


def test_update_artifact_json_content(client: TestClient):
    r = client.post("/entities", json={"name": "Epsilon Co"})
    entity_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/artifacts",
        data={
            "artifact_type": "other",
            "content": '{"a": 1}',
            "status": "draft",
        },
    )
    assert r.status_code == 200
    aid = r.json()["id"]

    r = client.put(
        f"/entities/{entity_id}/artifacts/{aid}/content",
        json={"a": 2, "b": ["x", "y"]},
    )
    assert r.status_code == 200

    r = client.get(f"/entities/{entity_id}/artifacts/{aid}/view")
    assert r.status_code == 200
    body = json.loads(r.json()["content"])
    assert body["a"] == 2
    assert body["b"] == ["x", "y"]


def test_extract_info_preset_creates_json_artifact(client: TestClient):
    r = client.post("/entities", json={"name": "Delta LLC", "website": "https://delta.test"})
    entity_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/chat/presets/extract_info/run",
        json={"resource_ids": [], "artifact_ids": []},
    )
    assert r.status_code == 200
    aid = r.json()["artifact_id"]

    arts = client.get(f"/entities/{entity_id}/artifacts").json()
    created = next(a for a in arts if a["id"] == aid)
    assert created["title"] == "extract_info"
    assert created["relative_path"].endswith(".json")

    view = client.get(f"/entities/{entity_id}/artifacts/{aid}/view").json()
    parsed = json.loads(view["content"])
    assert parsed["company_name"]["value"] == "StubCo"
