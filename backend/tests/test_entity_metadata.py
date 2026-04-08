"""Workspace node metadata (upload + PATCH + tree listing)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_upload_and_patch_node_metadata(client: TestClient):
    r = client.post("/entities", json={"name": "Meta Co"})
    assert r.status_code == 200
    entity_id = r.json()["id"]

    # Upload file via workspace API
    files = {"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post(
        f"/entities/{entity_id}/workspace/file?path=Inbox/note.txt",
        files=files,
    )
    assert r.status_code == 200
    node_id = r.json()["id"]
    assert r.json().get("metadata") is None or r.json()["metadata"] == {}

    # Patch metadata
    r = client.patch(
        f"/entities/{entity_id}/workspace/node/{node_id}",
        json={"metadata": {"summary": "short", "ocr_status": "pending"}},
    )
    assert r.status_code == 200
    got = r.json()
    assert got["metadata"]["summary"] == "short"
    assert got["metadata"]["ocr_status"] == "pending"

    # Verify via tree
    r = client.get(f"/entities/{entity_id}/workspace/tree")
    assert r.status_code == 200

    # Clear metadata
    r = client.patch(
        f"/entities/{entity_id}/workspace/node/{node_id}",
        json={"metadata": None},
    )
    assert r.status_code == 200
    assert r.json()["metadata"] is None


def test_patch_metadata_rejects_non_object(client: TestClient):
    r = client.post("/entities", json={"name": "Reject Co"})
    entity_id = r.json()["id"]

    files = {"file": ("f.txt", io.BytesIO(b"x"), "text/plain")}
    r = client.post(
        f"/entities/{entity_id}/workspace/file?path=Inbox/f.txt",
        files=files,
    )
    node_id = r.json()["id"]

    r = client.patch(
        f"/entities/{entity_id}/workspace/node/{node_id}",
        json={"metadata": "not-an-object"},
    )
    assert r.status_code == 400
