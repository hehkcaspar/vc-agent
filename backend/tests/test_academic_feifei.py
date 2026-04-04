"""
Quick e2e test: create a scholar for Fei-Fei Li and run evaluation.

Requires real Gemini + Semantic Scholar API access.

Run from backend/:
    ../venv/bin/python tests/test_academic_feifei.py
"""

import os
import sys
import time
import shutil
import threading
import tempfile
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

TEST_DIR = os.path.join(tempfile.gettempdir(), "academic_v2_feifei_test")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(TEST_DIR, 'test.db')}"
os.environ["ACADEMIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(TEST_DIR, 'academic.db')}"
os.environ["ACADEMIC_SCHOLARS_DIR"] = os.path.join(TEST_DIR, "scholars")
os.environ["ACADEMIC_CONFIG_DIR"] = os.path.join(TEST_DIR, "config")


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

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(academic_router)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    uvicorn.run(app, host="127.0.0.1", port=8878, log_level="warning")


import httpx
BASE = "http://127.0.0.1:8878"


def wait_for_server():
    for _ in range(30):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR, exist_ok=True)

    print("Starting server...")
    threading.Thread(target=start_server, daemon=True).start()
    assert wait_for_server(), "Server failed to start"

    c = httpx.Client(base_url=BASE, timeout=httpx.Timeout(30.0))

    # Create scholar for Fei-Fei Li
    print("\nCreating scholar for Fei-Fei Li...")
    r = c.post("/academic/scholars", json={
        "name": "Fei-Fei Li",
        "urls": ["https://profiles.stanford.edu/fei-fei-li"],
        "tracking_priority": "high",
        "tags": ["ai", "computer-vision", "stanford"],
    })
    assert r.status_code == 200, f"Create failed: {r.text}"
    scholar_id = r.json()["id"]
    print(f"  Scholar ID: {scholar_id}")

    # Run evaluation
    print("Starting evaluation...")
    r = c.post(f"/academic/scholars/{scholar_id}/evaluate")
    assert r.status_code == 200

    # Poll for completion
    start = time.time()
    while time.time() - start < 300:
        r = c.get(f"/academic/scholars/{scholar_id}")
        status = r.json()["status"]
        if status != "evaluating":
            break
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] Evaluating...")
        time.sleep(5)

    elapsed = int(time.time() - start)
    print(f"  Evaluation finished in {elapsed}s -> status={status}")

    # Check results
    r = c.get(f"/academic/scholars/{scholar_id}")
    scholar = r.json()
    print(f"\n{'='*50}")
    print(f"Scholar: {scholar['name']}")
    print(f"Affiliation: {scholar.get('affiliation')}")
    print(f"H-Index: {scholar.get('h_index')}")
    print(f"i10-Index: {scholar.get('i10_index')}")
    print(f"Citations: {scholar.get('total_citations')}")
    print(f"Research areas: {scholar.get('research_areas')}")
    print(f"{'='*50}")

    # Check papers
    r = c.get(f"/academic/scholars/{scholar_id}/papers?limit=5")
    papers_data = r.json()
    print(f"\nPapers: {papers_data.get('total', 0)}")
    for p in papers_data.get("papers", [])[:3]:
        print(f"  - {p['title']} ({p.get('year')}) — {p.get('citations', 0)} cit.")

    # Check evaluations
    r = c.get(f"/academic/scholars/{scholar_id}/evaluations")
    evals = r.json().get("evaluations", [])
    if evals:
        e = evals[0]
        print(f"\nEvaluation dimensions:")
        for dim, data in e.get("dimensions", {}).items():
            print(f"  {dim}: {data.get('score', '?')}")

    # Check reports
    r = c.get(f"/academic/scholars/{scholar_id}/reports")
    reports = r.json().get("reports", [])
    if reports:
        r = c.get(f"/academic/scholars/{scholar_id}/reports/{reports[0]['id']}")
        content = r.json().get("content", "")
        print(f"\nReport: {len(content)} chars")
        print(f"  Preview: {content[:200]}...")

    # Validate basic expectations
    h = scholar.get("h_index")
    if h is not None:
        assert h > 50, f"h-index too low ({h}), expected >50 for Fei-Fei Li"
        print(f"\n  h-index {h} — OK")

    print(f"\nPipeline completed in {elapsed}s")
    print("TEST PASSED" if status == "active" else f"TEST NEEDS REVIEW (status={status})")

    c.close()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
