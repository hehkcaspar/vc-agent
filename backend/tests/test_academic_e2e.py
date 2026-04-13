"""
End-to-end test for Academic Tracking v2 module.

Tests scholar CRUD operations, dossier creation, and agent evaluation
via the v2 scholar-centric API.

Run from backend/:
    ../venv/bin/python tests/test_academic_e2e.py
"""

import os
import sys
import time
import shutil
import threading
import tempfile
import logging

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ── Setup isolated test environment ──────────────────────────

TEST_DIR = os.path.join(tempfile.gettempdir(), "academic_v2_e2e_test")
TEST_DB = os.path.join(TEST_DIR, "test.db")
TEST_ACADEMIC_DB = os.path.join(TEST_DIR, "academic.db")
TEST_SCHOLARS_DIR = os.path.join(TEST_DIR, "scholars")
TEST_CONFIG_DIR = os.path.join(TEST_DIR, "config")

# Override env before importing app modules
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["ACADEMIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_ACADEMIC_DB}"
os.environ["ACADEMIC_SCHOLARS_DIR"] = TEST_SCHOLARS_DIR
os.environ["ACADEMIC_CONFIG_DIR"] = TEST_CONFIG_DIR


# ── Minimal server setup ────────────────────────────────────

def start_server():
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from contextlib import asynccontextmanager

    from app.academic_database import init_academic_db
    from app.database import init_db
    from app.routers.academic import router as academic_router

    @asynccontextmanager
    async def lifespan(app):
        await init_db()
        await init_academic_db()
        yield

    app = FastAPI(title="Academic v2 E2E Test", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(academic_router)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    uvicorn.run(app, host="127.0.0.1", port=8877, log_level="warning")


# ── Test runner ─────────────────────────────────────────────

import httpx

BASE = "http://127.0.0.1:8877"
TIMEOUT = httpx.Timeout(30.0)


def wait_for_server(max_wait=15):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = httpx.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_tests():
    client = httpx.Client(base_url=BASE, timeout=TIMEOUT)
    passed = 0
    failed = 0

    def test(name, fn):
        nonlocal passed, failed
        print(f"\n{'='*60}")
        print(f"TEST: {name}")
        print(f"{'='*60}")
        try:
            fn(client)
            print(f"  PASSED")
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    # ── Tests ──────────────────────────────────────────

    created_ids = []

    def test_health(c):
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_create_scholar(c):
        """Create scholars via v2 API."""
        test_cases = [
            {
                "name": "Yann LeCun",
                "urls": ["https://scholar.google.com/citations?user=WLN3QrAAAAAJ"],
                "tags": ["ai", "deep-learning"],
                "tracking_priority": "high",
            },
            {
                "name": "Geoffrey Hinton",
                "urls": ["https://www.cs.toronto.edu/~hinton/"],
                "tracking_priority": "medium",
            },
        ]

        for case in test_cases:
            r = c.post("/academic/scholars", json=case)
            assert r.status_code == 200, f"Create failed: {r.text}"
            data = r.json()
            assert data["name"] == case["name"]
            assert data["status"] == "active"
            assert data["tracking_priority"] == case.get("tracking_priority", "medium")
            assert data["dossier_path"].startswith("data/scholars/")
            created_ids.append(data["id"])
            print(f"  Created: {case['name']} -> {data['id']}")

            # Verify dossier directory exists
            dossier = os.path.join(TEST_SCHOLARS_DIR, data["id"])
            assert os.path.isdir(dossier), f"Dossier not created: {dossier}"
            assert os.path.isfile(os.path.join(dossier, "profile.json")), "profile.json not created"
            print(f"  Dossier OK: {dossier}")

        # List
        r = c.get("/academic/scholars")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        print(f"  Total scholars: {data['total']}")

    def test_list_filter(c):
        """Test list with filters."""
        r = c.get("/academic/scholars?priority=high")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["scholars"][0]["name"] == "Yann LeCun"
        print(f"  Priority filter: {data['total']} scholar(s)")

        r = c.get("/academic/scholars?search=Hinton")
        data = r.json()
        assert data["total"] == 1
        print(f"  Search filter: {data['total']} scholar(s)")

    def test_get_scholar(c):
        """Get single scholar."""
        r = c.get(f"/academic/scholars/{created_ids[0]}")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Yann LeCun"
        assert "high" == data["tracking_priority"]
        print(f"  Scholar: {data['name']} (priority={data['tracking_priority']})")

    def test_update_scholar(c):
        """Update scholar fields."""
        r = c.put(f"/academic/scholars/{created_ids[0]}", json={
            "tags": ["ai", "deep-learning", "meta"],
            "tracking_priority": "medium",
        })
        assert r.status_code == 200
        data = r.json()
        assert "meta" in data["tags"]
        assert data["tracking_priority"] == "medium"
        print(f"  Updated tags: {data['tags']}")

        # Revert
        c.put(f"/academic/scholars/{created_ids[0]}", json={
            "tracking_priority": "high",
        })

    def test_crud_delete(c):
        """Create, then delete a scholar — verify cleanup."""
        r = c.post("/academic/scholars", json={
            "name": "Delete Test", "urls": ["https://example.com"],
        })
        sid = r.json()["id"]
        dossier = os.path.join(TEST_SCHOLARS_DIR, sid)
        assert os.path.isdir(dossier)

        r = c.delete(f"/academic/scholars/{sid}")
        assert r.status_code == 200
        assert not os.path.exists(dossier), "Dossier not cleaned up"

        r = c.get(f"/academic/scholars/{sid}")
        assert r.status_code == 404
        print("  Create + delete + cleanup all OK")

    def test_duplicate_evaluate(c):
        """Evaluate, then try again — should get 409."""
        r = c.post("/academic/scholars", json={
            "name": "Dup Test", "urls": ["https://example.com"],
        })
        sid = r.json()["id"]

        r = c.post(f"/academic/scholars/{sid}/evaluate")
        assert r.status_code == 200

        time.sleep(0.3)
        r = c.post(f"/academic/scholars/{sid}/evaluate")
        assert r.status_code == 409, f"Expected 409, got {r.status_code}"
        print("  Duplicate evaluate correctly rejected")

        c.post(f"/academic/scholars/{sid}/stop")
        c.delete(f"/academic/scholars/{sid}")

    def test_empty_data_endpoints(c):
        """Data endpoints return empty when no evaluation has run."""
        sid = created_ids[0]

        r = c.get(f"/academic/scholars/{sid}/papers")
        assert r.status_code == 200
        assert r.json()["total"] == 0
        print(f"  Papers: {r.json()['total']} (expected 0)")

        r = c.get(f"/academic/scholars/{sid}/evaluations")
        assert r.status_code == 200
        data = r.json()
        # v2 shape: {dimensions: {...}, narrative: ..., peer_group: ..., red_flags: [...]}
        assert "dimensions" in data
        print(f"  Evaluations: v2 shape ok")

        r = c.get(f"/academic/scholars/{sid}/narrative-history")
        assert r.status_code == 200
        assert len(r.json()["narratives"]) == 0
        print(f"  Narratives: 0 (expected 0)")

        r = c.get(f"/academic/scholars/{sid}/events")
        assert r.status_code == 200
        assert len(r.json()) == 0
        print(f"  Events: 0 (expected 0)")

        r = c.get(f"/academic/scholars/{sid}/channels")
        assert r.status_code == 200
        assert len(r.json()) == 0
        print(f"  Channels: 0 (expected 0)")

    def test_signal_feed(c):
        """Signal feed returns empty initially."""
        r = c.get("/academic/signal-feed")
        assert r.status_code == 200
        assert len(r.json()) == 0
        print("  Signal feed: empty (expected)")

    def test_mark_feed_read(c):
        """Bulk mark-read endpoint works."""
        r = c.post("/academic/signal-feed/mark-read", json={"event_ids": []})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        print("  Mark all read: ok")

    def test_chat_crud(c):
        """Chat session CRUD + message post (202)."""
        # Create a scholar for chat
        r = c.post("/academic/scholars", json={
            "name": "Chat Test",
            "urls": ["https://scholar.google.com/citations?user=chattest"],
        })
        assert r.status_code == 200
        sid = r.json()["id"]
        print(f"  Scholar: {sid}")

        # List sessions (empty)
        r = c.get(f"/academic/scholars/{sid}/chat/sessions")
        assert r.status_code == 200
        assert len(r.json()) == 0
        print("  Sessions: 0 (ok)")

        # Create session
        r = c.post(f"/academic/scholars/{sid}/chat/sessions", json={"title": "Test Chat"})
        assert r.status_code == 200
        sess = r.json()
        assert sess["title"] == "Test Chat"
        assert sess["scholar_id"] == sid
        sess_id = sess["id"]
        print(f"  Created session: {sess_id}")

        # Get session detail
        r = c.get(f"/academic/scholars/{sid}/chat/sessions/{sess_id}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["session"]["title"] == "Test Chat"
        assert len(detail["messages"]) == 0
        print("  Session detail: ok (0 messages)")

        # Post message (returns 202 with job)
        r = c.post(f"/academic/scholars/{sid}/chat/sessions/{sess_id}/messages",
                    json={"text": "Hello agent"})
        assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
        job = r.json()
        assert "job_id" in job
        assert job["user_message"]["role"] == "user"
        assert "Hello agent" in job["user_message"]["content"]
        print(f"  Posted message, job_id: {job['job_id']}")

        # Poll job
        r = c.get(f"/academic/scholars/{sid}/chat/sessions/{sess_id}/jobs/{job['job_id']}")
        assert r.status_code == 200
        j = r.json()
        assert j["status"] in ("pending", "running", "succeeded", "failed")
        print(f"  Job status: {j['status']}")

        # Verify message was saved
        r = c.get(f"/academic/scholars/{sid}/chat/sessions/{sess_id}")
        assert r.status_code == 200
        assert len(r.json()["messages"]) >= 1  # at least user message
        print(f"  Messages in session: {len(r.json()['messages'])}")

        # Delete session
        r = c.delete(f"/academic/scholars/{sid}/chat/sessions/{sess_id}")
        assert r.status_code == 204
        print("  Deleted session: ok")

        # Verify session gone
        r = c.get(f"/academic/scholars/{sid}/chat/sessions")
        assert r.status_code == 200
        assert len(r.json()) == 0
        print("  Sessions after delete: 0 (ok)")

        # Cleanup
        c.delete(f"/academic/scholars/{sid}")
        print("  Cleanup: ok")

    def test_ranking(c):
        """Ranking endpoint returns scholars (empty scores ok)."""
        r = c.get("/academic/ranking")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # Should contain the scholars created earlier
        print(f"  Ranking: {len(data)} scholars")
        for s in data:
            assert "dimensions" in s
            assert "name" in s
        print("  All scholars have dimension fields")

    def test_weight_presets(c):
        """Weight presets CRUD."""
        # List presets (should have seeds)
        r = c.get("/academic/ranking/presets")
        assert r.status_code == 200
        presets = r.json()
        assert len(presets) >= 3, f"Expected >=3 seed presets, got {len(presets)}"
        print(f"  Seed presets: {len(presets)}")
        names = [p["name"] for p in presets]
        assert "Balanced" in names
        print(f"  Presets: {', '.join(names)}")

        # Create custom preset
        r = c.post("/academic/ranking/presets", json={
            "name": "Test Custom",
            "weights": {"academic_excellence": 0.5, "tech_transfer_experience": 0.5},
        })
        assert r.status_code == 200
        assert r.json()["name"] == "Test Custom"
        print("  Created custom preset: ok")

        # Verify it's listed
        r = c.get("/academic/ranking/presets")
        names = [p["name"] for p in r.json()]
        assert "Test Custom" in names
        print("  Custom preset listed: ok")

        # Delete it
        r = c.delete("/academic/ranking/presets/test_custom")
        assert r.status_code == 200
        print("  Deleted custom preset: ok")

    def test_digests(c):
        """Digest list endpoint works."""
        r = c.get("/academic/digests")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        print(f"  Digests: {len(r.json())} (expected 0)")

    def test_uploads(c):
        """Upload endpoint accepts files."""
        # Create a scholar
        r = c.post("/academic/scholars", json={
            "name": "Upload Test",
            "urls": ["https://example.com"],
        })
        sid = r.json()["id"]

        # Upload a file
        r = c.post(
            f"/academic/scholars/{sid}/uploads",
            files=[("files", ("test.txt", b"Hello world", "text/plain"))],
        )
        assert r.status_code == 200
        assert "test.txt" in r.json()["files"]
        print("  Upload file: ok")

        # List uploads
        r = c.get(f"/academic/scholars/{sid}/uploads")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["filename"] == "test.txt"
        print("  List uploads: 1 file")

        # Cleanup
        c.delete(f"/academic/scholars/{sid}")
        print("  Cleanup: ok")

    def test_custom_dimensions(c):
        """Custom dimensions CRUD."""
        # List (empty)
        r = c.get("/academic/custom-dimensions")
        assert r.status_code == 200
        assert len(r.json()) == 0
        print("  Custom dims: 0 (ok)")

        # Create
        r = c.post("/academic/custom-dimensions", json={
            "name": "Teaching Impact",
            "key": "teaching_impact",
            "prompt": "Assess teaching contributions and mentorship",
        })
        assert r.status_code == 200
        assert r.json()["key"] == "teaching_impact"
        print("  Created dim: ok")

        # List (1)
        r = c.get("/academic/custom-dimensions")
        assert len(r.json()) == 1
        print("  Custom dims: 1")

        # Duplicate rejected
        r = c.post("/academic/custom-dimensions", json={
            "name": "Teaching Impact",
            "key": "teaching_impact",
            "prompt": "duplicate",
        })
        assert r.status_code == 409
        print("  Duplicate rejected: ok")

        # Delete
        r = c.delete("/academic/custom-dimensions/teaching_impact")
        assert r.status_code == 200
        print("  Deleted dim: ok")

        # Verify gone
        r = c.get("/academic/custom-dimensions")
        assert len(r.json()) == 0
        print("  Custom dims after delete: 0")

    # Run all tests
    test("Health Check", test_health)
    test("Create Scholars", test_create_scholar)
    test("List with Filters", test_list_filter)
    test("Get Scholar", test_get_scholar)
    test("Update Scholar", test_update_scholar)
    test("CRUD Delete + Cleanup", test_crud_delete)
    test("Duplicate Evaluate Rejected", test_duplicate_evaluate)
    test("Empty Data Endpoints", test_empty_data_endpoints)
    test("Signal Feed", test_signal_feed)
    test("Mark Feed Read", test_mark_feed_read)
    test("Chat CRUD + Message", test_chat_crud)
    test("Ranking", test_ranking)
    test("Weight Presets", test_weight_presets)
    test("Digests", test_digests)
    test("Uploads", test_uploads)
    test("Custom Dimensions", test_custom_dimensions)

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    client.close()
    return failed == 0


if __name__ == "__main__":
    # Clean up old test dir
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR, exist_ok=True)

    print(f"Test dir: {TEST_DIR}")
    print("Starting test server on port 8877...")
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    if not wait_for_server():
        print("ERROR: Server failed to start")
        sys.exit(1)
    print("Server ready.\n")

    success = run_tests()

    # Cleanup
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)

    sys.exit(0 if success else 1)
