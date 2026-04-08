"""Process Inbox: Path A grouping/routing + Path B folder routing.

The Gemini calls are monkeypatched. The single-file metadata preprocess Gemini
call (used by Pass 1 + sampling) and the synoptic Gemini calls (Pass 2, B1)
both go through `generate_json_one_shot`, but live in different modules; we
patch each at its import site.
"""

from __future__ import annotations

import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


# Canned per-file extraction (returned by metadata_preprocess Gemini call)
def _make_extraction(one_liner: str, kind: str, topics=None):
    return json.dumps({
        "one_liner": one_liner,
        "summary": one_liner + " (summary)",
        "languages": ["en"],
        "document_kind": kind,
        "primary_topics": topics or [],
        "key_entities_or_parties": [],
        "approx_length_signal": "short",
        "full_text_recommended": {"value": False, "reason": "summary suffices"},
        "skim_metadata_reliability": "high",
        "caveats": [],
        "image_content": {
            "treatment": "not_image",
            "ocr_text": None,
            "objective_visual_description": None,
        },
    })


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _create_entity(client: TestClient, name: str) -> str:
    r = client.post("/entities", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _upload_loose(client: TestClient, entity_id: str, name: str, content: bytes):
    r = client.post(
        f"/entities/{entity_id}/workspace/file?path=Inbox/{name}",
        files={"file": (name, io.BytesIO(content), "text/plain")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _wait_done(client: TestClient, entity_id: str, job_id: str, *, timeout: int = 30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/entities/{entity_id}/workspace/inbox/process/{job_id}")
        assert r.status_code == 200
        st = r.json()
        if st["status"] in ("succeeded", "failed"):
            return st
        time.sleep(0.05)
    pytest.fail(f"job did not finish in {timeout}s")


def _read_node(client: TestClient, entity_id: str, node_id: str) -> dict:
    r = client.get(f"/entities/{entity_id}/workspace/node/{node_id}")
    assert r.status_code == 200
    return r.json()


def _ls(client: TestClient, entity_id: str, path: str) -> list[dict]:
    r = client.get(
        f"/entities/{entity_id}/workspace/ls?path={path}"
    )
    assert r.status_code == 200
    return r.json()


# ──────────────────────────────────────────────────────────────────────
# Scaffold sanity check
# ──────────────────────────────────────────────────────────────────────

def test_new_entity_has_minimal_scaffold(client):
    entity_id = _create_entity(client, "Lazy Co")
    children = _ls(client, entity_id, "")
    names = sorted(c["name"] for c in children)
    # Only Inbox/ + WORKSPACE_NOTES.md should exist on day 1.
    assert names == ["Inbox", "WORKSPACE_NOTES.md"]


# ──────────────────────────────────────────────────────────────────────
# Path A: loose files → grouping
# ──────────────────────────────────────────────────────────────────────

def test_path_a_routes_loose_files_into_groups(client, monkeypatch):
    # Pass 1 returns canned extractions per file (always the same for this test).
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("Q4 financials", "spreadsheet_data"),
    )

    entity_id = _create_entity(client, "Path A Co")
    f1 = _upload_loose(client, entity_id, "q4.csv", b"a,b\n1,2")
    f2 = _upload_loose(client, entity_id, "memo.txt", b"q4 memo")
    f3 = _upload_loose(client, entity_id, "weird.bin", b"\x00\x00\x00")

    # Pass 2 grouping decision: f1+f2 → Q4 batch, f3 → triage
    pass2_payload = {
        "groups": [
            {
                "name": "Q4 2025 Financials",
                "parent": "Data Room/Financials",
                "existing_folder": None,
                "file_ids": [f1["id"], f2["id"]],
                "reason": "both reference Q4",
                "confidence": "high",
            },
        ],
        "needs_triage": [
            {"file_id": f3["id"], "reason": "binary blob"},
        ],
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(pass2_payload),
    )

    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    st = _wait_done(client, entity_id, job_id)
    assert st["status"] == "succeeded", st

    # Verify f1 + f2 were moved into the new batch folder
    moved_paths = sorted(m["to"] for m in st["moved"])
    assert moved_paths == [
        "Data Room/Financials/Q4 2025 Financials/memo.txt",
        "Data Room/Financials/Q4 2025 Financials/q4.csv",
    ]
    for m in st["moved"]:
        assert m["batch_name"] == "Q4 2025 Financials"
        assert m["joined_existing"] is False

    # f3 stayed in Inbox with needs_triage
    assert any(t["path"] == "Inbox/weird.bin" for t in st["needs_triage"])
    f3_meta = _read_node(client, entity_id, f3["id"])["metadata"]
    assert f3_meta["intake_routing"]["status"] == "needs_triage"
    assert f3_meta["intake_routing"]["reason"] == "binary blob"

    # f1 metadata: native, gemini_preprocessed, intake_routing all present
    f1_meta = _read_node(client, entity_id, f1["id"])["metadata"]
    assert "gemini_preprocessed" in f1_meta
    assert "native_file_metadata" in f1_meta
    assert f1_meta["intake_routing"]["status"] == "routed"
    assert f1_meta["intake_routing"]["batch_name"] == "Q4 2025 Financials"
    assert f1_meta["intake_routing"]["destination"] == "Data Room/Financials/Q4 2025 Financials"
    assert f1_meta["intake_routing"]["run_id"] == job_id


def test_path_a_disambiguates_filename_collisions_within_group(client, monkeypatch):
    """Two files with the same basename in one group must both land cleanly."""
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("memo doc", "memo"),
    )

    entity_id = _create_entity(client, "Collide Co")
    f1 = _upload_loose(client, entity_id, "memo.txt", b"first")
    # Create a folder so we can shove a second file with the same basename in
    # the inbox using a sibling path. Easier: just upload a second file at a
    # different inbox path that has the same basename via folder upload.
    files = [
        ("files", ("Inbox/dup/memo.txt", io.BytesIO(b"second"), "text/plain")),
    ]
    r = client.post(
        f"/entities/{entity_id}/workspace/upload?base_path=",
        files=files,
    )
    assert r.status_code == 200
    f2_id = r.json()["results"][0]["id"]

    # Both files go into the same group; same basename → second must be renamed
    pass2 = {
        "groups": [
            {
                "name": "Q1 Memos",
                "parent": "Deliverables/Memos",
                "existing_folder": None,
                "file_ids": [f1["id"], f2_id],
                "reason": "both Q1 memos",
                "confidence": "high",
            }
        ],
        "needs_triage": [],
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(pass2),
    )

    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    job_id = r.json()["job_id"]
    st = _wait_done(client, entity_id, job_id)
    assert st["status"] == "succeeded", st
    assert st["errors"] == [], st["errors"]

    # Both files moved into the destination, second one renamed
    moved_to = sorted(m["to"] for m in st["moved"])
    assert "Deliverables/Memos/Q1 Memos/memo.txt" in moved_to
    assert any("memo (1).txt" in p for p in moved_to)


def test_path_a_surfaces_one_liner_as_description(client, monkeypatch):
    """The annotated tree builder shows metadata.description; verify Pass 1
    extraction's one_liner gets merged into that field."""
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("Q4 board deck v1", "presentation"),
    )

    entity_id = _create_entity(client, "Desc Co")
    f1 = _upload_loose(client, entity_id, "deck.txt", b"slides")

    pass2 = {
        "groups": [
            {"name": None, "parent": "Data Room", "existing_folder": None,
             "file_ids": [f1["id"]], "reason": "single deck", "confidence": "high"}
        ],
        "needs_triage": [],
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(pass2),
    )
    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    _wait_done(client, entity_id, r.json()["job_id"])

    meta = _read_node(client, entity_id, f1["id"])["metadata"]
    assert meta.get("description") == "Q4 board deck v1"


def test_path_a_invalid_existing_folder_marks_triage(client, monkeypatch):
    """LLM proposing existing_folder outside the taxonomy must trigger triage."""
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("note", "memo"),
    )
    entity_id = _create_entity(client, "Bad Target Co")
    f1 = _upload_loose(client, entity_id, "note.txt", b"hi")

    pass2 = {
        "groups": [
            {
                "name": None,
                "parent": None,
                "existing_folder": "Inbox",  # outside taxonomy
                "file_ids": [f1["id"]],
                "reason": "model went rogue",
                "confidence": "high",
            }
        ],
        "needs_triage": [],
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(pass2),
    )
    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    st = _wait_done(client, entity_id, r.json()["job_id"])
    assert st["status"] == "succeeded"
    assert any("invalid_existing_folder" in t["reason"] for t in st["needs_triage"])

    meta = _read_node(client, entity_id, f1["id"])["metadata"]
    assert meta["intake_routing"]["status"] == "needs_triage"
    assert meta["intake_routing"]["reason"].startswith("invalid_existing_folder")


def test_path_a_joins_existing_subfolder(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("Series A SPA amendment", "legal"),
    )

    entity_id = _create_entity(client, "Join Co")

    # Pre-create the existing subfolder
    r = client.post(
        f"/entities/{entity_id}/workspace/folder?path=Data Room/Legal/Series A Closing"
    )
    assert r.status_code == 200, r.text

    f1 = _upload_loose(client, entity_id, "amendment.txt", b"amendment text")

    pass2 = {
        "groups": [
            {
                "name": None,
                "parent": "Data Room/Legal",
                "existing_folder": "Data Room/Legal/Series A Closing",
                "file_ids": [f1["id"]],
                "reason": "extends existing Series A batch",
                "confidence": "high",
            }
        ],
        "needs_triage": [],
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(pass2),
    )

    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    job_id = r.json()["job_id"]
    st = _wait_done(client, entity_id, job_id)
    assert st["status"] == "succeeded"

    assert st["moved"][0]["to"] == "Data Room/Legal/Series A Closing/amendment.txt"
    assert st["moved"][0]["joined_existing"] is True

    f1_meta = _read_node(client, entity_id, f1["id"])["metadata"]
    assert f1_meta["intake_routing"]["joined_existing"] is True
    assert f1_meta["intake_routing"]["destination"] == "Data Room/Legal/Series A Closing"


# ──────────────────────────────────────────────────────────────────────
# Path B: user-uploaded folder → fast routing
# ──────────────────────────────────────────────────────────────────────

def test_path_b_place_whole(client, monkeypatch):
    # Per-file extraction shouldn't be called for place_whole (background only).
    # We still patch it as a safety net so any background calls don't hit the network.
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("doc", "legal"),
    )

    entity_id = _create_entity(client, "Folder Co")

    # Upload a folder via the upload-folder endpoint with relative paths
    files = [
        ("files", ("Series A Closing Binder/Transaction Docs/SPA.txt",
                   io.BytesIO(b"spa"), "text/plain")),
        ("files", ("Series A Closing Binder/Board Consents/UWC.txt",
                   io.BytesIO(b"uwc"), "text/plain")),
    ]
    r = client.post(
        f"/entities/{entity_id}/workspace/upload?base_path=Inbox",
        files=files,
    )
    assert r.status_code == 200, r.text

    b1_decision = {
        "action": "place_whole",
        "destination": "Data Room/Legal",
        "join_existing": None,
        "rename_root_to": "Series A Closing",
        "confidence": "high",
        "reason": "structure indicates Series A closing binder",
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(b1_decision),
    )

    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    job_id = r.json()["job_id"]
    st = _wait_done(client, entity_id, job_id)
    assert st["status"] == "succeeded", st

    # The whole folder moved
    decisions = st["folder_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["action"] == "place_whole"
    assert decisions[0]["destination"] == "Data Room/Legal"
    assert decisions[0]["rename_root_to"] == "Series A Closing"

    moved_to = [m["to"] for m in st["moved"]]
    assert "Data Room/Legal/Series A Closing" in moved_to

    # Verify internal tree preserved by listing the destination
    after = _ls(client, entity_id, "Data Room/Legal/Series A Closing")
    names = sorted(c["name"] for c in after)
    assert names == ["Board Consents", "Transaction Docs"]


def test_path_b_unpack_flattens_to_inbox(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.metadata_preprocess_jobs.generate_json_one_shot",
        lambda *a, **k: _make_extraction("doc", "other"),
    )

    entity_id = _create_entity(client, "Unpack Co")

    files = [
        ("files", ("Mixed Stuff/memo.txt", io.BytesIO(b"m"), "text/plain")),
        ("files", ("Mixed Stuff/financials.txt", io.BytesIO(b"f"), "text/plain")),
    ]
    r = client.post(
        f"/entities/{entity_id}/workspace/upload?base_path=Inbox",
        files=files,
    )
    assert r.status_code == 200

    b1_decision = {
        "action": "unpack",
        "destination": None,
        "join_existing": None,
        "rename_root_to": None,
        "confidence": "low",
        "reason": "unrelated documents",
    }
    monkeypatch.setattr(
        "app.services.inbox_processing_jobs.generate_json_one_shot",
        lambda *a, **k: json.dumps(b1_decision),
    )

    r = client.post(f"/entities/{entity_id}/workspace/inbox/process")
    job_id = r.json()["job_id"]
    st = _wait_done(client, entity_id, job_id)
    assert st["status"] == "succeeded"

    inbox_after = _ls(client, entity_id, "Inbox")
    names = sorted(c["name"] for c in inbox_after if c["node_type"] == "file")
    assert "memo.txt" in names
    assert "financials.txt" in names

    # The Mixed Stuff folder should be gone (deleted after unpack)
    folders = [c["name"] for c in inbox_after if c["node_type"] == "folder"]
    assert "Mixed Stuff" not in folders


# ──────────────────────────────────────────────────────────────────────
# Idempotency
# ──────────────────────────────────────────────────────────────────────

def test_create_inbox_job_one_per_entity(client):
    from app.services import inbox_processing_jobs as jm

    import asyncio

    async def run():
        jm._jobs.clear()
        jm._inflight.clear()
        jid1, sched1 = await jm.create_inbox_job("ent-x")
        assert sched1 is True
        jid2, sched2 = await jm.create_inbox_job("ent-x")
        assert sched2 is False
        assert jid1 == jid2

    asyncio.run(run())
