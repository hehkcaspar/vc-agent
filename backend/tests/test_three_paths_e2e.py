#!/usr/bin/env python3
"""
Systematic E2E tests for all three chat execution paths.

Run with the backend server already running:
    cd backend && python tests/test_three_paths_e2e.py

Generates a detailed markdown report at tests/e2e_report.md.
"""

import asyncio
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
REPORT_PATH = Path(__file__).parent / "e2e_report.md"
TIMEOUT = httpx.Timeout(30.0, read=60.0)
POLL_INTERVAL = 2.0
JOB_TIMEOUT = 300  # 5 minutes max per agent job


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    test_id: str
    name: str
    phase: str
    status: str = "SKIP"  # PASS, FAIL, ERROR, SKIP
    timing_s: float = 0.0
    response_preview: str = ""
    response_len: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    tools_used: list[str] = field(default_factory=list)
    step_trace: list[str] = field(default_factory=list)
    steps: int = 0
    notes: str = ""
    inline_fix: str = ""  # If a bug was fixed inline


@dataclass
class EntityInfo:
    id: str
    name: str
    node_map: dict[str, str] = field(default_factory=dict)  # name -> node_id


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

async def create_session(client: httpx.AsyncClient, entity_id: str) -> str:
    r = await client.post(f"/entities/{entity_id}/chat/sessions", json={})
    r.raise_for_status()
    return r.json()["id"]


async def get_tree(client: httpx.AsyncClient, entity_id: str) -> list[dict]:
    r = await client.get(f"/entities/{entity_id}/workspace/tree")
    r.raise_for_status()
    return r.json()


def flatten_tree(nodes: list[dict], result: dict | None = None) -> dict[str, str]:
    """Flatten tree into {name: node_id} map."""
    if result is None:
        result = {}
    for n in nodes:
        result[n["name"]] = n["id"]
        if n.get("path"):
            result[n["path"]] = n["id"]
        flatten_tree(n.get("children", []), result)
    return result


async def one_shot_msg(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    text: str,
    node_ids: list[str] | None = None,
) -> dict:
    """Send a one-shot message. Returns the response dict."""
    body = {
        "text": text,
        "node_ids": node_ids or [],
        "agent_mode": "one_shot",
    }
    r = await client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json=body,
        timeout=httpx.Timeout(120.0),
    )
    r.raise_for_status()
    return r.json()


async def agent_msg(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    text: str,
    node_ids: list[str] | None = None,
    mode: str = "react",
) -> dict:
    """Send an agent message, poll until done. Returns final job status."""
    body = {
        "text": text,
        "node_ids": node_ids or [],
        "agent_mode": mode,
    }
    r = await client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json=body,
    )
    r.raise_for_status()
    data = r.json()

    # One-shot responses return directly (200)
    if "assistant_message" in data:
        return data

    # Agent responses return 202 with job_id
    job_id = data.get("job_id")
    if not job_id:
        return data

    return await poll_job(client, entity_id, session_id, job_id)


async def run_preset(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    preset_id: str,
    mode: str = "react",
    **extra,
) -> dict:
    """Run a preset, poll until done."""
    body = {
        "node_ids": [],
        "session_id": session_id,
        "agent_mode": mode,
        **extra,
    }
    r = await client.post(
        f"/entities/{entity_id}/chat/presets/{preset_id}/run",
        json=body,
    )
    r.raise_for_status()
    data = r.json()

    job_id = data.get("job_id")
    if not job_id:
        return data

    return await poll_job(client, entity_id, session_id, job_id)


async def poll_job(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    job_id: str,
) -> dict:
    """Poll a job until terminal state."""
    step_trace = []
    start = time.time()
    last_step = ""
    while time.time() - start < JOB_TIMEOUT:
        r = await client.get(
            f"/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}"
        )
        r.raise_for_status()
        status = r.json()
        step = status.get("step_detail", "")
        if step and step != last_step:
            step_trace.append(step)
            last_step = step
            print(f"    [{len(step_trace)}] {step[:80]}", flush=True)
        if status["status"] in ("succeeded", "failed"):
            status["_step_trace"] = step_trace
            status["_elapsed"] = time.time() - start
            return status
        await asyncio.sleep(POLL_INTERVAL)
    return {"status": "timeout", "_step_trace": step_trace, "_elapsed": time.time() - start}


def extract_reply(data: dict) -> str:
    """Extract text from various response formats."""
    # One-shot response
    if "assistant_message" in data:
        return data["assistant_message"].get("content", "")
    # Agent job response
    msg = data.get("assistant_message")
    if msg:
        return msg.get("content", "")
    return data.get("error_message", "") or str(data.get("status", ""))


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

async def run_test(
    test_id: str,
    name: str,
    phase: str,
    coro,
) -> TestResult:
    """Run a single test coroutine, catch errors."""
    result = TestResult(test_id=test_id, name=name, phase=phase)
    print(f"\n{'='*60}")
    print(f"[{test_id}] {name}")
    print(f"{'='*60}", flush=True)
    start = time.time()
    try:
        await coro(result)
        result.timing_s = time.time() - start
        if result.status == "SKIP":
            result.status = "PASS"
    except Exception as e:
        result.timing_s = time.time() - start
        result.status = "ERROR"
        result.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"  ERROR: {e}", flush=True)
    print(f"  → {result.status} ({result.timing_s:.1f}s)", flush=True)
    return result


# ---------------------------------------------------------------------------
# Phase 1: One-Shot Tests
# ---------------------------------------------------------------------------

async def test_1_1(r: TestResult):
    """wayfarer — simple query, no files."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "f21fe5ec-cd74-46e5-a2f2-256f97055a95"
        sid = await create_session(c, eid)
        data = await one_shot_msg(c, eid, sid, "What does this company do?")
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.warnings = data.get("warnings", [])
        if len(reply) < 20:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = "Got meaningful response from workspace context alone."


async def test_1_2(r: TestResult):
    """wayfarer — query with PDF selected."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "f21fe5ec-cd74-46e5-a2f2-256f97055a95"
        sid = await create_session(c, eid)
        tree = flatten_tree(await get_tree(c, eid))
        pdf_id = tree.get("wayfarer lab.pdf")
        if not pdf_id:
            r.status = "SKIP"
            r.notes = "PDF not found in tree"
            return
        data = await one_shot_msg(c, eid, sid, "Summarize this pitch deck.", [pdf_id])
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.warnings = data.get("warnings", [])
        if len(reply) < 50:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = "PDF sent native via build_context_parts."


async def test_1_3(r: TestResult):
    """Abinitia Labs — query with 3 files selected (exec summary + 2 founder DDs)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "b20347f8-3866-4ca7-a01f-291b480a4fb9"
        sid = await create_session(c, eid)
        tree = flatten_tree(await get_tree(c, eid))
        ids = [
            tree.get("Abinitia Labs Executive Summary.docx"),
            tree.get("Abinitia Labs Diligence Packet_Wenhao Gao.pdf"),
            tree.get("Abinitia Labs Diligence Packet_Zhuoran Qiao.pdf"),
        ]
        ids = [i for i in ids if i]
        if len(ids) < 3:
            r.status = "SKIP"
            r.notes = f"Only found {len(ids)}/3 files"
            return
        data = await one_shot_msg(c, eid, sid,
            "Based on the attached materials, summarize the founding team's backgrounds and the company's value proposition.",
            ids)
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.warnings = data.get("warnings", [])
        if len(reply) < 100:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = f"3 files (2 PDFs + 1 docx) sent. {len(r.warnings)} warnings."


async def test_1_4(r: TestResult):
    """Cybernexus — SPA docx extraction."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "01467c92-5f99-4363-b614-ac580a74533b"
        sid = await create_session(c, eid)
        tree = flatten_tree(await get_tree(c, eid))
        spa_id = tree.get("1. CyberNexus_Series Pre-A+_SPA (MLB 2026.04.04).docx")
        if not spa_id:
            r.status = "SKIP"
            r.notes = "SPA docx not found"
            return
        data = await one_shot_msg(c, eid, sid,
            "Summarize the key terms of this Share Purchase Agreement — valuation, share price, investor rights, conditions precedent.",
            [spa_id])
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.warnings = data.get("warnings", [])
        if len(reply) < 100:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = "Large docx (174KB) extraction + analysis."


async def test_1_5(r: TestResult):
    """Elastro — query with image selected."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "89d52e41-e297-4235-b672-2de4b22379c8"
        sid = await create_session(c, eid)
        tree = flatten_tree(await get_tree(c, eid))
        img_id = tree.get("Elastro - 300k.png")
        if not img_id:
            r.status = "SKIP"
            r.notes = "Image not found in tree"
            return
        data = await one_shot_msg(c, eid, sid, "Describe what this image shows.", [img_id])
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.warnings = data.get("warnings", [])
        if len(reply) < 20:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = "Image sent as native binary via build_context_parts."


async def test_1_6(r: TestResult):
    """scenic — query with 34MB PDF (compression test)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "ec04890b-74cd-4019-857e-b27a31e36784"
        sid = await create_session(c, eid)
        tree = flatten_tree(await get_tree(c, eid))
        pdf_id = tree.get("Scenix-v2 Deck.pdf")
        if not pdf_id:
            r.status = "SKIP"
            r.notes = "Large PDF not found"
            return
        data = await one_shot_msg(c, eid, sid, "What is this pitch deck about?", [pdf_id])
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.warnings = data.get("warnings", [])
        if len(reply) < 20:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = "34MB PDF compressed + sent native."


async def test_1_7(r: TestResult):
    """wayfarer — Extract Info preset (one-shot forced)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "f21fe5ec-cd74-46e5-a2f2-256f97055a95"
        sid = await create_session(c, eid)
        tree = flatten_tree(await get_tree(c, eid))
        pdf_id = tree.get("wayfarer lab.pdf")
        data = await run_preset(
            c, eid, sid, "extract_info",
            mode="one_shot",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        if "error" in str(data.get("status", "")):
            r.status = "FAIL"
            r.error = data.get("error_message", "Unknown error")
        else:
            r.notes = "Extract Info preset runs as one-shot."


# ---------------------------------------------------------------------------
# Phase 2: ReAct Agent Tests
# ---------------------------------------------------------------------------

async def test_2_1(r: TestResult):
    """wayfarer — workspace browse."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "f21fe5ec-cd74-46e5-a2f2-256f97055a95"
        sid = await create_session(c, eid)
        data = await agent_msg(c, eid, sid, "What files are in this workspace?", mode="react")
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        r.timing_s = data.get("_elapsed", 0)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 30:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = f"Completed in {r.steps} steps."


async def test_2_2(r: TestResult):
    """scenic — read docx (office extraction)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "ec04890b-74cd-4019-857e-b27a31e36784"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Read the SceniX Executive Summary and tell me what SceniX does.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 50:
            r.status = "FAIL"
            r.error = f"Response too short"


async def test_2_3(r: TestResult):
    """scenic — read 34MB PDF via agent (compression + native binary)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "ec04890b-74cd-4019-857e-b27a31e36784"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Read the Scenix-v2 Deck.pdf and summarize the key points.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 50:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = "34MB PDF compressed + base64 via agent tool."


async def test_2_4(r: TestResult):
    """scenic — read image via agent (native binary)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "ec04890b-74cd-4019-857e-b27a31e36784"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Read the file 'Scenix - 200k.png' and describe what the image shows.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 20:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = "Image sent native via base64 in agent tool response."


async def test_2_5(r: TestResult):
    """Abinitia Labs — compare two founder diligence packets (ReAct)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "b20347f8-3866-4ca7-a01f-291b480a4fb9"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Compare the two founder diligence packets (Wenhao Gao and Zhuoran Qiao). "
            "What are each founder's strengths and what gaps or risks do you see?",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 100:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"Cross-referenced 2 PDFs in {r.steps} steps."


async def test_2_6(r: TestResult):
    """Abinitia Labs — Red Team preset (ReAct)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "b20347f8-3866-4ca7-a01f-291b480a4fb9"
        sid = await create_session(c, eid)
        data = await run_preset(c, eid, sid, "red_team", mode="react")
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 200:
            r.status = "FAIL"
            r.error = f"Report too short ({len(reply)} chars)"
        else:
            r.notes = f"Red team produced {len(reply)} char report in {r.steps} steps."


async def test_2_7(r: TestResult):
    """Cybernexus — legal review (SPA + SHA) via agent."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "01467c92-5f99-4363-b614-ac580a74533b"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Review the SPA and SHA documents for investor-unfriendly provisions. "
            "Focus on liquidation preferences, anti-dilution, board composition, and drag-along rights.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 100:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"Legal review in {r.steps} steps."


async def test_2_8(r: TestResult):
    """Cybernexus — compare Pre-A executed SPA with Pre-A+ draft SPA."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "01467c92-5f99-4363-b614-ac580a74533b"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Compare the original Pre-A executed SPA (the PDF in the Closing Binder) with the new Pre-A+ draft SPA (the .docx). "
            "What are the key changes between the two versions?",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 100:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"Cross-version comparison (PDF vs docx) in {r.steps} steps."


async def test_2_9(r: TestResult):
    """Elastro — data room gap analysis (43 files, tree-only triage)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "89d52e41-e297-4235-b672-2de4b22379c8"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "What's missing from this data room for a pre-seed deal?",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 100:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"Browsed 43-file workspace, {r.steps} steps."


async def test_2_10(r: TestResult):
    """Elastro — read xlsx cap table via agent."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "89d52e41-e297-4235-b672-2de4b22379c8"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Read the cap table spreadsheet (the .xlsx file) and summarize the ownership structure — "
            "who holds what percentage, how many shares outstanding, option pool size.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 50:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"xlsx extraction + analysis in {r.steps} steps."


async def test_2_11(r: TestResult):
    """Elastro — read legacy .doc file (LibreOffice conversion)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "89d52e41-e297-4235-b672-2de4b22379c8"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Read the stock incentive plan document (the .doc file under Equity Plans) and summarize the key terms.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 50:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = "Legacy .doc extracted via LibreOffice conversion."


async def test_2_12(r: TestResult):
    """Elastro — multi-turn conversation (ReAct)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "89d52e41-e297-4235-b672-2de4b22379c8"
        sid = await create_session(c, eid)

        # Turn 1
        print("  Turn 1: What IP does Elastro have?", flush=True)
        data1 = await agent_msg(c, eid, sid, "What IP does Elastro have?", mode="react")
        reply1 = extract_reply(data1)
        if data1.get("status") == "failed":
            r.status = "FAIL"
            r.error = f"Turn 1 failed: {data1.get('error_message', '')}"
            return

        # Turn 2 (same session — history should carry over)
        print("  Turn 2: Are there any gaps in IP protection?", flush=True)
        data2 = await agent_msg(c, eid, sid, "Are there any gaps in IP protection?", mode="react")
        reply2 = extract_reply(data2)
        if data2.get("status") == "failed":
            r.status = "FAIL"
            r.error = f"Turn 2 failed: {data2.get('error_message', '')}"
            return

        r.response_preview = f"Turn 1 ({len(reply1)} chars): {reply1[:150]}...\n\nTurn 2 ({len(reply2)} chars): {reply2[:150]}..."
        r.response_len = len(reply1) + len(reply2)
        r.steps = len(data1.get("_step_trace", [])) + len(data2.get("_step_trace", []))
        r.notes = f"Multi-turn: {len(data1.get('_step_trace',[]))} + {len(data2.get('_step_trace',[]))} steps."


async def test_2_13(r: TestResult):
    """Abinitia Labs — search files for revenue/ARR mentions."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "b20347f8-3866-4ca7-a01f-291b480a4fb9"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Search across all files for any mentions of revenue, ARR, MRR, or financial projections. "
            "Report what you find.",
            mode="react",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")
        elif len(reply) < 30:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"Search-based query in {r.steps} steps."


# ---------------------------------------------------------------------------
# Phase 3: Deep Agent Tests
# ---------------------------------------------------------------------------

async def test_3_1(r: TestResult):
    """wayfarer — workspace browse (Deep Agent)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "f21fe5ec-cd74-46e5-a2f2-256f97055a95"
        sid = await create_session(c, eid)
        data = await agent_msg(c, eid, sid, "What files are in this workspace?", mode="deep_agent")
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")[:300]
        elif len(reply) < 30:
            r.status = "FAIL"
            r.error = f"Response too short ({len(reply)} chars)"
        else:
            r.notes = f"Deep Agent completed in {r.steps} steps."


async def test_3_2(r: TestResult):
    """scenic — Red Team preset (Deep Agent, legacy)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "ec04890b-74cd-4019-857e-b27a31e36784"
        sid = await create_session(c, eid)
        data = await run_preset(c, eid, sid, "red_team", mode="deep_agent")
        reply = extract_reply(data)
        r.response_preview = reply[:500]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")[:300]
            r.notes = "Known issue: Deep Agent may loop or produce empty reports."
        elif len(reply) < 200:
            r.status = "FAIL"
            r.error = f"Report too short ({len(reply)} chars)"
        else:
            r.notes = f"Deep Agent red team: {len(reply)} chars in {r.steps} steps."


async def test_3_3(r: TestResult):
    """Abinitia Labs — read executive summary (Deep Agent)."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "b20347f8-3866-4ca7-a01f-291b480a4fb9"
        sid = await create_session(c, eid)
        data = await agent_msg(
            c, eid, sid,
            "Read the Abinitia Labs Executive Summary and summarize what the company does.",
            mode="deep_agent",
        )
        reply = extract_reply(data)
        r.response_preview = reply[:300]
        r.response_len = len(reply)
        r.step_trace = data.get("_step_trace", [])
        r.steps = len(r.step_trace)
        if data.get("status") == "failed":
            r.status = "FAIL"
            r.error = data.get("error_message", "")[:300]
        elif len(reply) < 50:
            r.status = "FAIL"
            r.error = "Response too short"
        else:
            r.notes = f"Deep Agent docx read in {r.steps} steps."


# ---------------------------------------------------------------------------
# Phase 4: Process Inbox Verification
# ---------------------------------------------------------------------------

async def test_4_desc_coverage(r: TestResult):
    """All entities — description coverage check."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        resp = await c.get("/entities")
        entities = resp.json()
        lines = []
        total_files = 0
        total_with_desc = 0
        for ent in entities:
            tree = await get_tree(c, ent["id"])
            flat = flatten_tree(tree)
            # Get full tree details
            all_nodes = []
            def collect(nodes):
                for n in nodes:
                    if n["node_type"] == "file":
                        all_nodes.append(n)
                    collect(n.get("children", []))
            collect(tree)
            with_desc = sum(1 for n in all_nodes if n.get("description"))
            total_files += len(all_nodes)
            total_with_desc += with_desc
            lines.append(
                f"  {ent['name']}: {with_desc}/{len(all_nodes)} files have descriptions"
            )
        r.response_preview = "\n".join(lines)
        r.notes = f"Overall: {total_with_desc}/{total_files} files have descriptions."
        if total_with_desc < total_files * 0.5:
            r.status = "FAIL"
            r.error = f"Only {total_with_desc}/{total_files} have descriptions."


async def test_4_2(r: TestResult):
    """Cybernexus — legal binder nested structure preserved."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "01467c92-5f99-4363-b614-ac580a74533b"
        tree = await get_tree(c, eid)
        flat = flatten_tree(tree)
        # Check key nested paths exist
        expected = [
            "CyberNexus Series Pre-A Closing Binder",
            "1. Transaction Documents",
            "2. Group Resolutions",
            "3. Ancillary Documents",
        ]
        found = []
        missing = []
        for name in expected:
            if name in flat:
                found.append(name)
            else:
                missing.append(name)
        r.response_preview = f"Found: {found}\nMissing: {missing}"
        if missing:
            r.status = "FAIL"
            r.error = f"Missing folders: {missing}"
        else:
            r.notes = f"All {len(found)} expected nested folders present. Total nodes: {len(flat)}."


async def test_4_3(r: TestResult):
    """Elastro — data room 12-section taxonomy."""
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "89d52e41-e297-4235-b672-2de4b22379c8"
        tree = await get_tree(c, eid)
        flat = flatten_tree(tree)
        # Check key data room sections
        expected_sections = [
            "1. Basic Corporate Documents",
            "2. Agreements Regarding Securities",
            "11. Business Plan and Financial Information",
            "12. Product",
        ]
        found = []
        missing = []
        for name in expected_sections:
            if name in flat:
                found.append(name)
            else:
                missing.append(name)
        r.response_preview = f"Found sections: {found}\nMissing: {missing}"
        if missing:
            r.status = "FAIL"
            r.error = f"Missing sections: {missing}"
        else:
            r.notes = f"All {len(found)} checked sections present. Total nodes: {len(flat)}."


# ---------------------------------------------------------------------------
# Phase 5: Cross-Mode Comparison
# ---------------------------------------------------------------------------

async def test_5_1(r: TestResult):
    """Abinitia Labs — same question across all 3 modes."""
    question = "What are the key risks for this investment?"
    results_by_mode = {}
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "b20347f8-3866-4ca7-a01f-291b480a4fb9"

        # One-shot
        print("  Mode: one_shot", flush=True)
        sid = await create_session(c, eid)
        t0 = time.time()
        data = await one_shot_msg(c, eid, sid, question)
        reply = extract_reply(data)
        results_by_mode["one_shot"] = {
            "time": time.time() - t0,
            "len": len(reply),
            "preview": reply[:150],
        }

        # ReAct
        print("  Mode: react", flush=True)
        sid = await create_session(c, eid)
        data = await agent_msg(c, eid, sid, question, mode="react")
        reply = extract_reply(data)
        results_by_mode["react"] = {
            "time": data.get("_elapsed", 0),
            "len": len(reply),
            "steps": len(data.get("_step_trace", [])),
            "preview": reply[:150],
        }

        # Deep Agent
        print("  Mode: deep_agent", flush=True)
        sid = await create_session(c, eid)
        data = await agent_msg(c, eid, sid, question, mode="deep_agent")
        reply = extract_reply(data)
        results_by_mode["deep_agent"] = {
            "time": data.get("_elapsed", 0),
            "len": len(reply),
            "steps": len(data.get("_step_trace", [])),
            "preview": reply[:150],
        }

    lines = []
    for mode, info in results_by_mode.items():
        status = "OK" if info["len"] > 50 else "SHORT"
        lines.append(f"{mode}: {info['len']} chars, {info['time']:.1f}s, {info.get('steps','N/A')} steps — {status}")
        lines.append(f"  Preview: {info['preview']}")
    r.response_preview = "\n".join(lines)
    r.response_len = sum(v["len"] for v in results_by_mode.values())
    # Pass if at least 2 of 3 modes produced meaningful responses
    good = sum(1 for v in results_by_mode.values() if v["len"] > 50)
    if good < 2:
        r.status = "FAIL"
        r.error = f"Only {good}/3 modes produced meaningful responses"
    else:
        r.notes = f"All 3 modes compared. One-shot: {results_by_mode['one_shot']['time']:.0f}s, ReAct: {results_by_mode['react']['time']:.0f}s, Deep: {results_by_mode['deep_agent']['time']:.0f}s"


async def test_5_2(r: TestResult):
    """scenic — Red Team ReAct vs Deep Agent comparison."""
    results_by_mode = {}
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
        eid = "ec04890b-74cd-4019-857e-b27a31e36784"

        # ReAct
        print("  Mode: react", flush=True)
        sid = await create_session(c, eid)
        data = await run_preset(c, eid, sid, "red_team", mode="react")
        reply = extract_reply(data)
        results_by_mode["react"] = {
            "status": data.get("status", "unknown"),
            "time": data.get("_elapsed", 0),
            "len": len(reply),
            "steps": len(data.get("_step_trace", [])),
            "trace": data.get("_step_trace", [])[:10],
        }

        # Deep Agent
        print("  Mode: deep_agent", flush=True)
        sid = await create_session(c, eid)
        data = await run_preset(c, eid, sid, "red_team", mode="deep_agent")
        reply = extract_reply(data)
        results_by_mode["deep_agent"] = {
            "status": data.get("status", "unknown"),
            "time": data.get("_elapsed", 0),
            "len": len(reply),
            "steps": len(data.get("_step_trace", [])),
            "trace": data.get("_step_trace", [])[:10],
        }

    lines = []
    for mode, info in results_by_mode.items():
        lines.append(f"{mode}: status={info['status']}, {info['len']} chars, {info['time']:.1f}s, {info['steps']} steps")
        for s in info["trace"]:
            lines.append(f"  - {s[:80]}")
    r.response_preview = "\n".join(lines)
    r.notes = "See response preview for detailed comparison."
    # Pass if at least ReAct succeeded
    if results_by_mode["react"]["status"] != "succeeded":
        r.status = "FAIL"
        r.error = f"ReAct failed: {results_by_mode['react']['status']}"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results: list[TestResult]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    errors = sum(1 for r in results if r.status == "ERROR")
    skipped = sum(1 for r in results if r.status == "SKIP")

    lines = [
        f"# E2E Test Report — Three Execution Paths",
        f"Generated: {now}",
        "",
        "## Summary",
        f"- **Total tests**: {len(results)}",
        f"- **Passed**: {passed}  **Failed**: {failed}  **Errors**: {errors}  **Skipped**: {skipped}",
        "",
    ]

    # Group by phase
    phases = {}
    for r in results:
        phases.setdefault(r.phase, []).append(r)

    for phase, tests in phases.items():
        lines.append(f"## {phase}")
        lines.append("")
        for r in tests:
            icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "💥", "SKIP": "⏭️"}.get(r.status, "?")
            lines.append(f"### {icon} {r.test_id}: {r.name}")
            lines.append(f"- **Status**: {r.status}")
            lines.append(f"- **Timing**: {r.timing_s:.1f}s")
            if r.steps:
                lines.append(f"- **Steps**: {r.steps}")
            if r.response_len:
                lines.append(f"- **Response length**: {r.response_len} chars")
            if r.warnings:
                lines.append(f"- **Warnings**: {r.warnings}")
            if r.error:
                lines.append(f"- **Error**: `{r.error[:500]}`")
            if r.step_trace:
                lines.append(f"- **Step trace**:")
                for i, s in enumerate(r.step_trace[:20]):
                    lines.append(f"  {i+1}. {s[:100]}")
                if len(r.step_trace) > 20:
                    lines.append(f"  ... ({len(r.step_trace) - 20} more steps)")
            if r.response_preview:
                preview = r.response_preview.replace("\n", " ")[:300]
                lines.append(f"- **Response preview**: {preview}")
            if r.inline_fix:
                lines.append(f"- **Inline fix applied**: {r.inline_fix}")
            if r.notes:
                lines.append(f"- **Notes**: {r.notes}")
            lines.append("")

    # Findings section
    bugs = [r for r in results if r.status in ("FAIL", "ERROR")]
    if bugs:
        lines.append("## Bugs Found")
        lines.append("")
        for i, r in enumerate(bugs, 1):
            lines.append(f"{i}. **[{r.test_id}] {r.name}**: {r.error[:200]}")
        lines.append("")

    fixes = [r for r in results if r.inline_fix]
    if fixes:
        lines.append("## Inline Fixes Applied")
        lines.append("")
        for i, r in enumerate(fixes, 1):
            lines.append(f"{i}. **[{r.test_id}]**: {r.inline_fix}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("E2E Test Suite — Three Execution Paths")
    print("=" * 60)

    # Verify server is running
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
            r = await c.get("/entities")
            r.raise_for_status()
            print(f"Server OK — {len(r.json())} entities")
    except Exception as e:
        print(f"Server not reachable at {BASE}: {e}")
        sys.exit(1)

    # Support --quick flag for post-refactor validation (16 tests, ~50%)
    quick = "--quick" in sys.argv

    results: list[TestResult] = []

    # Phase 1: One-Shot
    results.append(await run_test("1.1", "wayfarer — simple query (no files)", "Phase 1: One-Shot", test_1_1))
    results.append(await run_test("1.2", "wayfarer — query with PDF selected", "Phase 1: One-Shot", test_1_2))
    if not quick:
        results.append(await run_test("1.3", "Abinitia — 3 files selected (2 PDFs + docx)", "Phase 1: One-Shot", test_1_3))
        results.append(await run_test("1.4", "Cybernexus — SPA docx extraction", "Phase 1: One-Shot", test_1_4))
    results.append(await run_test("1.5", "Elastro — query with image selected", "Phase 1: One-Shot", test_1_5))
    results.append(await run_test("1.6", "scenic — 34MB PDF (compression)", "Phase 1: One-Shot", test_1_6))
    results.append(await run_test("1.7", "wayfarer — Extract Info preset", "Phase 1: One-Shot", test_1_7))

    # Phase 2: ReAct Agent
    results.append(await run_test("2.1", "wayfarer — workspace browse (ReAct)", "Phase 2: ReAct Agent", test_2_1))
    results.append(await run_test("2.2", "scenic — read docx (ReAct)", "Phase 2: ReAct Agent", test_2_2))
    results.append(await run_test("2.3", "scenic — 34MB PDF via agent (ReAct)", "Phase 2: ReAct Agent", test_2_3))
    results.append(await run_test("2.4", "scenic — image via agent (ReAct)", "Phase 2: ReAct Agent", test_2_4))
    if not quick:
        results.append(await run_test("2.5", "Abinitia — compare 2 founder DD packets (ReAct)", "Phase 2: ReAct Agent", test_2_5))
    results.append(await run_test("2.6", "Abinitia Labs — Red Team preset (ReAct)", "Phase 2: ReAct Agent", test_2_6))
    if not quick:
        results.append(await run_test("2.7", "Cybernexus — legal review SPA+SHA (ReAct)", "Phase 2: ReAct Agent", test_2_7))
        results.append(await run_test("2.8", "Cybernexus — compare Pre-A vs Pre-A+ SPA (ReAct)", "Phase 2: ReAct Agent", test_2_8))
    results.append(await run_test("2.9", "Elastro — data room gap analysis (ReAct)", "Phase 2: ReAct Agent", test_2_9))
    if not quick:
        results.append(await run_test("2.10", "Elastro — xlsx cap table (ReAct)", "Phase 2: ReAct Agent", test_2_10))
    results.append(await run_test("2.11", "Elastro — legacy .doc file (ReAct)", "Phase 2: ReAct Agent", test_2_11))
    if not quick:
        results.append(await run_test("2.12", "Elastro — multi-turn conversation (ReAct)", "Phase 2: ReAct Agent", test_2_12))
        results.append(await run_test("2.13", "Abinitia — search files for revenue/ARR (ReAct)", "Phase 2: ReAct Agent", test_2_13))

    # Phase 3: Deep Agent (legacy — fewer tests, mainly comparison)
    results.append(await run_test("3.1", "wayfarer — workspace browse (Deep Agent)", "Phase 3: Deep Agent", test_3_1))
    results.append(await run_test("3.2", "scenic — Red Team preset (Deep Agent)", "Phase 3: Deep Agent", test_3_2))
    if not quick:
        results.append(await run_test("3.3", "Abinitia — read executive summary (Deep Agent)", "Phase 3: Deep Agent", test_3_3))

    # Phase 4: Process Inbox
    results.append(await run_test("4.1", "All entities — description coverage", "Phase 4: Process Inbox", test_4_desc_coverage))
    results.append(await run_test("4.2", "Cybernexus — legal binder structure", "Phase 4: Process Inbox", test_4_2))
    results.append(await run_test("4.3", "Elastro — data room taxonomy", "Phase 4: Process Inbox", test_4_3))

    # Phase 5: Cross-Mode Comparison
    if not quick:
        results.append(await run_test("5.1", "Abinitia — key risks across 3 modes", "Phase 5: Cross-Mode", test_5_1))
        results.append(await run_test("5.2", "scenic — Red Team ReAct vs Deep Agent", "Phase 5: Cross-Mode", test_5_2))

    # Generate report
    report = generate_report(results)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"Report written to {REPORT_PATH}")
    print(f"{'='*60}")
    print(f"\nResults: {sum(1 for r in results if r.status=='PASS')} PASS, "
          f"{sum(1 for r in results if r.status=='FAIL')} FAIL, "
          f"{sum(1 for r in results if r.status=='ERROR')} ERROR")


if __name__ == "__main__":
    asyncio.run(main())
