"""Metadata pre-process jobs (in-memory) + Gemini merge into metadata_json."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
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

_SAMPLE_LOOKUP_JSON = json.dumps(
    {
        "one_liner": "Q4 operations memo for Bot Auto.",
        "summary": "Three pages on runway and hiring; tables not reproduced here.",
        "languages": ["en"],
        "document_kind": "memo",
        "primary_topics": ["operations", "runway"],
        "key_entities_or_parties": ["Bot Auto"],
        "approx_length_signal": "short",
        "full_text_recommended": {
            "value": True,
            "reason": "Numbers and tables need full read for diligence.",
        },
        "skim_metadata_reliability": "medium",
        "caveats": [],
        "image_content": {
            "treatment": "not_image",
            "ocr_text": None,
            "objective_visual_description": None,
        },
    }
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_metadata_preprocess_merges_into_node(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _SAMPLE_LOOKUP_JSON,
    )

    r = client.post("/entities", json={"name": "Preprocess Co"})
    entity_id = r.json()["id"]

    # Upload file via workspace
    files = {"file": ("note.txt", io.BytesIO(b"hello world"), "text/plain")}
    r = client.post(
        f"/entities/{entity_id}/workspace/file?path=Inbox/note.txt",
        files=files,
    )
    assert r.status_code == 200
    node_id = r.json()["id"]

    # Set manual metadata first
    r = client.patch(
        f"/entities/{entity_id}/workspace/node/{node_id}",
        json={"metadata": {"manual": True}},
    )
    assert r.status_code == 200

    # Trigger metadata preprocess
    r = client.post(
        f"/entities/{entity_id}/workspace/node/{node_id}/metadata-preprocess",
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # Poll until done
    for _ in range(200):
        jr = client.get(
            f"/entities/{entity_id}/workspace/metadata-preprocess-jobs/{job_id}",
        )
        assert jr.status_code == 200
        st = jr.json()
        if st["status"] in ("succeeded", "failed"):
            assert st["status"] == "succeeded", st
            break
    else:
        pytest.fail("job did not complete")

    # Verify metadata was merged
    r = client.get(f"/entities/{entity_id}/workspace/node/{node_id}")
    assert r.status_code == 200
    meta = r.json()["metadata"]
    assert meta.get("manual") is True
    assert "gemini_preprocessed" in meta
    gp = meta["gemini_preprocessed"]
    assert gp.get("kind") == "file_lookup"
    assert gp["extraction"]["one_liner"] == "Q4 operations memo for Bot Auto."
    assert gp["extraction"]["languages"] == ["en"]
    assert gp["extraction"]["full_text_recommended"]["value"] is True
    assert gp["extraction"]["image_content"]["treatment"] == "not_image"

    native = meta["native_file_metadata"]
    assert native["size_bytes"] == len(b"hello world")
    assert native["sha256"] == hashlib.sha256(b"hello world").hexdigest()
    assert native["mime_type"] == "text/plain"
    assert "text_stats" in native


def test_create_or_reuse_job_returns_same_id_while_pending():
    from app.services import metadata_preprocess_jobs as jm

    async def run():
        jm._jobs.clear()
        jm._inflight.clear()
        jid1, sched1 = await jm.create_or_reuse_job("ent-a", "res-1")
        assert sched1 is True
        jid2, sched2 = await jm.create_or_reuse_job("ent-a", "res-1")
        assert sched2 is False
        assert jid1 == jid2

    asyncio.run(run())


def test_normalize_file_lookup_image_ocr_and_visual():
    from app.services.file_lookup_normalize import normalize_file_lookup_result

    base = {
        "one_liner": "x",
        "summary": "y",
        "languages": [],
        "document_kind": "image_or_scan",
        "primary_topics": [],
        "key_entities_or_parties": [],
        "approx_length_signal": "short",
        "full_text_recommended": {"value": True, "reason": "r"},
        "skim_metadata_reliability": "low",
        "caveats": [],
    }

    ocr_out = normalize_file_lookup_result(
        {
            **base,
            "image_content": {
                "treatment": "ocr",
                "ocr_text": " Line one ",
                "objective_visual_description": "should drop",
            },
        }
    )
    assert ocr_out["image_content"]["treatment"] == "ocr"
    assert ocr_out["image_content"]["ocr_text"] == "Line one"
    assert ocr_out["image_content"]["objective_visual_description"] is None

    vis_out = normalize_file_lookup_result(
        {
            **base,
            "image_content": {
                "treatment": "visual_description",
                "ocr_text": "nope",
                "objective_visual_description": "Blue sky, single tree.",
            },
        }
    )
    assert vis_out["image_content"]["treatment"] == "visual_description"
    assert vis_out["image_content"]["ocr_text"] is None
    assert vis_out["image_content"]["objective_visual_description"] == "Blue sky, single tree."
