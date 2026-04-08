"""Workspace zip upload: happy path, zip-slip rejection, size cap."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _create_entity(client: TestClient, name: str) -> str:
    r = client.post("/entities", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def test_zip_upload_happy_path(client):
    entity_id = _create_entity(client, "Zip Co")
    zbytes = _make_zip({
        "Series A Closing/Transaction Docs/SPA.txt": b"spa",
        "Series A Closing/Board Consents/UWC.txt": b"uwc",
    })
    r = client.post(
        f"/entities/{entity_id}/workspace/upload-zip",
        files={"file": ("Series A Closing.zip", io.BytesIO(zbytes), "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uploaded"] == 2
    assert body["base_path"] == "Inbox/Series A Closing"

    # Verify tree
    r = client.get(
        f"/entities/{entity_id}/workspace/ls?path=Inbox/Series A Closing"
    )
    assert r.status_code == 200
    names = sorted(c["name"] for c in r.json())
    assert names == ["Board Consents", "Transaction Docs"]


def test_zip_upload_rejects_zip_slip(client):
    entity_id = _create_entity(client, "Slip Co")
    # Build a zip with an evil entry by hand (zipfile.writestr will normalize, so use ZipInfo)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("../../escape.txt")
        zf.writestr(zi, b"evil")
    r = client.post(
        f"/entities/{entity_id}/workspace/upload-zip",
        files={"file": ("evil.zip", io.BytesIO(buf.getvalue()), "application/zip")},
    )
    assert r.status_code == 400
    assert "unsafe" in r.text.lower()


def test_zip_upload_rejects_oversize_zip(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.WORKSPACE_MAX_ZIP_BYTES", 100)
    entity_id = _create_entity(client, "Big Co")
    zbytes = _make_zip({"a.txt": b"x" * 500})
    r = client.post(
        f"/entities/{entity_id}/workspace/upload-zip",
        files={"file": ("big.zip", io.BytesIO(zbytes), "application/zip")},
    )
    assert r.status_code == 413


def test_zip_upload_rejects_oversize_entry(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.WORKSPACE_MAX_FILE_BYTES", 10)
    entity_id = _create_entity(client, "Entry Co")
    zbytes = _make_zip({"big.txt": b"x" * 100})
    r = client.post(
        f"/entities/{entity_id}/workspace/upload-zip",
        files={"file": ("ent.zip", io.BytesIO(zbytes), "application/zip")},
    )
    assert r.status_code == 413
