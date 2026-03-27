"""Resource and artifact metadata_json (REST list + PATCH)."""

from __future__ import annotations

import io
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
_db_file.close()
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///" + _db_file.name.replace("\\", "/")
)

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_patch_and_list_resource_metadata(client: TestClient):
    r = client.post("/entities", json={"name": "Meta Co"})
    assert r.status_code == 200
    entity_id = r.json()["id"]

    files = [("files", ("note.txt", io.BytesIO(b"hello"), "text/plain"))]
    data = {"entity_id": entity_id}
    r = client.post("/ingest/resources", files=files, data=data)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    resource_id = body["resources"][0]["id"]
    assert body["resources"][0].get("metadata") is None

    r = client.patch(
        f"/entities/{entity_id}/resources/{resource_id}",
        json={"metadata": {"summary": "short", "ocr_status": "pending"}},
    )
    assert r.status_code == 200
    got = r.json()
    assert got["metadata"] == {"summary": "short", "ocr_status": "pending"}

    r = client.get(f"/entities/{entity_id}/resources")
    assert r.status_code == 200
    listed = r.json()
    match = next(x for x in listed if x["id"] == resource_id)
    assert match["metadata"] == {"summary": "short", "ocr_status": "pending"}

    r = client.patch(
        f"/entities/{entity_id}/resources/{resource_id}",
        json={"metadata": None},
    )
    assert r.status_code == 200
    assert r.json()["metadata"] is None


def test_patch_and_list_artifact_metadata(client: TestClient):
    r = client.post("/entities", json={"name": "Arti Meta Co"})
    assert r.status_code == 200
    entity_id = r.json()["id"]

    form = {
        "artifact_type": "memo",
        "content": "# Hello",
        "status": "draft",
    }
    r = client.post(
        f"/entities/{entity_id}/artifacts",
        data=form,
    )
    assert r.status_code == 200
    artifact_id = r.json()["id"]
    assert r.json().get("metadata") is None

    r = client.patch(
        f"/entities/{entity_id}/artifacts/{artifact_id}",
        json={"metadata": {"blurb": "one-liner", "tokens": 12}},
    )
    assert r.status_code == 200
    assert r.json()["metadata"] == {"blurb": "one-liner", "tokens": 12}

    r = client.get(f"/entities/{entity_id}/artifacts")
    assert r.status_code == 200
    listed = r.json()
    match = next(x for x in listed if x["id"] == artifact_id)
    assert match["metadata"] == {"blurb": "one-liner", "tokens": 12}

    r = client.patch(
        f"/entities/{entity_id}/artifacts/{artifact_id}",
        json={"metadata": None},
    )
    assert r.status_code == 200
    assert r.json()["metadata"] is None


def test_patch_metadata_rejects_non_object(client: TestClient):
    r = client.post("/entities", json={"name": "Reject Co"})
    entity_id = r.json()["id"]
    files = [("files", ("f.txt", io.BytesIO(b"x"), "text/plain"))]
    r = client.post("/ingest/resources", files=files, data={"entity_id": entity_id})
    resource_id = r.json()["resources"][0]["id"]
    r = client.patch(
        f"/entities/{entity_id}/resources/{resource_id}",
        json={"metadata": "not-an-object"},
    )
    assert r.status_code == 400
