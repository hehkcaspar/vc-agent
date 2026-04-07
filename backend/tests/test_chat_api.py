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


def _stub_gemini_interaction(**kwargs) -> tuple[str, str]:
    """Stub for generate_with_interaction — returns (reply_text, interaction_id)."""
    return ("Assistant stub reply.", "stub-interaction-id")


def _stub_gemini_json(**kwargs) -> str:
    return json.dumps(
        {
            "company_name": {"value": "StubCo", "confidence": "high"},
            "founders": [],
        }
    )


@pytest.fixture
def client(monkeypatch):
    # Local .env may set CHAT_USE_DEEP_AGENT=true; tests expect stubbed direct path unless overridden.
    monkeypatch.setattr(settings, "CHAT_USE_DEEP_AGENT", False)
    monkeypatch.setattr(
        "app.routers.chat.generate_one_shot",
        _stub_gemini,
    )
    monkeypatch.setattr(
        "app.routers.chat.generate_with_interaction",
        _stub_gemini_interaction,
    )
    monkeypatch.setattr(
        "app.routers.chat.generate_json_one_shot",
        _stub_gemini_json,
    )
    with TestClient(app) as c:
        yield c


def test_post_message_deep_agent_override_off_uses_direct(
    client: TestClient, monkeypatch
):
    """Client can force direct Gemini even when server default is harness."""
    monkeypatch.setattr(settings, "CHAT_USE_DEEP_AGENT", True)
    monkeypatch.setattr(
        "app.routers.chat.generate_with_interaction",
        _stub_gemini_interaction,
    )
    r = client.post("/entities", json={"name": "Override Co"})
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    session_id = r.json()["id"]
    r = client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={
            "text": "Hi",
            "node_ids": [],
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
            "node_ids": [],
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
        json={"text": "Hi", "node_ids": [], },
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
        json={"text": "Hello", "node_ids": [], },
    )
    assert r.status_code == 200
    body = r.json()
    assert "assistant_message" in body
    assert body["assistant_message"]["role"] == "assistant"

    r = client.get(f"/entities/{entity_id}/chat/sessions/{session_id}")
    assert len(r.json()["messages"]) == 2


def test_preset_run_creates_deliverable(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.routers.chat.generate_one_shot",
        lambda **kw: "# Red team report\n\nStub markdown.",
    )
    r = client.post("/entities", json={"name": "Beta Inc"})
    entity_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/chat/presets/red_team/run",
        json={"node_ids": []},
    )
    assert r.status_code == 200
    nid = r.json()["node_id"]
    assert uuid.UUID(nid).version == 4

    node = client.get(f"/entities/{entity_id}/workspace/node/{nid}").json()
    assert "risk_analyze" in node["name"]
    assert node["path"].startswith("Deliverables/")


def test_preset_run_session_message_is_deliverable_card(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.routers.chat.generate_one_shot",
        lambda **kw: "# Report\n\nShort.",
    )
    r = client.post("/entities", json={"name": "Gamma LLC"})
    entity_id = r.json()["id"]
    r = client.post(f"/entities/{entity_id}/chat/sessions", json={})
    session_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/chat/presets/red_team/run",
        json={"node_ids": [], "session_id": session_id},
    )
    assert r.status_code == 200
    nid = r.json()["node_id"]

    detail = client.get(f"/entities/{entity_id}/chat/sessions/{session_id}").json()
    msgs = detail["messages"]
    assert len(msgs) == 1
    data = json.loads(msgs[0]["content"])
    assert data["_vc_chat"] == "artifact_card"
    assert data["node_id"] == nid
    assert data["preset_label"] == "Red team diligence"


def test_workspace_file_upload_and_download(client: TestClient):
    import io as _io
    r = client.post("/entities", json={"name": "Epsilon Co"})
    entity_id = r.json()["id"]

    files = {"file": ("data.json", _io.BytesIO(b'{"a": 1}'), "application/json")}
    r = client.post(
        f"/entities/{entity_id}/workspace/file?path=Inbox/data.json",
        files=files,
    )
    assert r.status_code == 200
    nid = r.json()["id"]

    r = client.get(f"/entities/{entity_id}/workspace/file/{nid}")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["a"] == 1


def test_extract_info_preset_creates_json_deliverable(client: TestClient):
    r = client.post("/entities", json={"name": "Delta LLC", "website": "https://delta.test"})
    entity_id = r.json()["id"]

    r = client.post(
        f"/entities/{entity_id}/chat/presets/extract_info/run",
        json={"node_ids": []},
    )
    assert r.status_code == 200
    nid = r.json()["node_id"]

    node = client.get(f"/entities/{entity_id}/workspace/node/{nid}").json()
    assert node["name"].endswith(".json")
    assert node["path"].startswith("Deliverables/")

    r = client.get(f"/entities/{entity_id}/workspace/file/{nid}")
    assert r.status_code == 200
    parsed = json.loads(r.content)
    assert parsed["company_name"]["value"] == "StubCo"
