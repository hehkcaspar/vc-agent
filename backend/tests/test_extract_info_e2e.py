#!/usr/bin/env python3
"""E2E test for the upgraded extract_info preset (agent mode).

Runs against a live backend (http://127.0.0.1:8000) using existing DB entities.
Mimics the frontend flow:
  1. POST /entities/{id}/chat/sessions     (create session)
  2. POST /entities/{id}/chat/presets/extract_info/run  (trigger preset, expect 202)
  3. GET  .../jobs/{job_id}                (poll until terminal)
  4. GET  /entities/{id}/workspace/tree    (verify Company Profile.json)
  5. GET  /entities/{id}                   (verify metadata synced)

Usage:
    cd backend && python tests/test_extract_info_e2e.py [entity_id]

If no entity_id is provided, defaults to Abinitia Labs (rich workspace for testing).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = httpx.Timeout(30.0, read=60.0)
POLL_INTERVAL = 3.0
JOB_TIMEOUT_S = 600  # 10 minutes — agent browses many files

# Default: Abinitia Labs (rich workspace with pitch decks, founder PDFs, etc.)
DEFAULT_ENTITY_ID = "b20347f8-3866-4ca7-a01f-291b480a4fb9"

REPORT_PATH = Path(__file__).parent / "e2e_extract_info_report.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _banner(title: str, char: str = "=") -> None:
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


async def _wait_for_backend(client: httpx.AsyncClient, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = await client.get("/health")
            if r.status_code < 500:
                return True
        except httpx.TransportError:
            pass
        await asyncio.sleep(1.0)
    return False


async def _get_entity(client: httpx.AsyncClient, entity_id: str) -> dict:
    r = await client.get(f"/entities/{entity_id}")
    r.raise_for_status()
    return r.json()


async def _list_workspace(client: httpx.AsyncClient, entity_id: str) -> list[dict]:
    r = await client.get(f"/entities/{entity_id}/workspace/tree")
    r.raise_for_status()
    return r.json()


def _find_in_tree(nodes: list[dict], name: str) -> dict | None:
    for n in nodes:
        if n.get("name") == name:
            return n
        children = n.get("children") or []
        if children:
            hit = _find_in_tree(children, name)
            if hit:
                return hit
    return None


def _flatten_file_count(nodes: list[dict]) -> int:
    count = 0
    for n in nodes:
        if n.get("node_type") == "file":
            count += 1
        count += _flatten_file_count(n.get("children") or [])
    return count


async def _create_session(client: httpx.AsyncClient, entity_id: str) -> str:
    r = await client.post(
        f"/entities/{entity_id}/chat/sessions",
        json={"title": "extract_info e2e test"},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _trigger_preset(
    client: httpx.AsyncClient, entity_id: str, session_id: str
) -> dict:
    """Mimics the frontend handleRunPreset call."""
    r = await client.post(
        f"/entities/{entity_id}/chat/presets/extract_info/run",
        json={
            "node_ids": [],              # Frontend passes empty — agent picks
            "session_id": session_id,
            "agent_mode": "react",       # Frontend toggle (but backend forces react anyway)
            "model_profile_id": "gemini_google",
        },
    )
    if r.status_code != 202:
        raise RuntimeError(
            f"Expected 202, got {r.status_code}: {r.text[:400]}"
        )
    return r.json()


async def _poll_job(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    job_id: str,
) -> dict:
    deadline = time.monotonic() + JOB_TIMEOUT_S
    last: dict = {}
    last_step = ""
    while time.monotonic() < deadline:
        r = await client.get(
            f"/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}"
        )
        r.raise_for_status()
        last = r.json()
        status = last.get("status", "?")
        step = last.get("step_detail", "")
        if step != last_step:
            elapsed = int(JOB_TIMEOUT_S - (deadline - time.monotonic()))
            print(f"  [{elapsed:>4}s] {status:<12} {step[:90]}")
            last_step = step
        if status in ("succeeded", "failed"):
            return last
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Job {job_id} did not terminate in {JOB_TIMEOUT_S}s; last={last}")


async def _read_workspace_file_json(
    client: httpx.AsyncClient, entity_id: str, node_id: str
) -> dict:
    r = await client.get(f"/entities/{entity_id}/workspace/file/{node_id}")
    r.raise_for_status()
    return json.loads(r.content)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

# Tier 1-3 expected keys
EXPECTED_KEYS = {
    # Tier 1
    "company_name", "legal_name", "one_liner", "description",
    "industry_tags", "business_model", "hq_location", "website",
    "founded_date", "incorporation_jurisdiction", "incorporation_entity_type",
    # Tier 2
    "founders", "team_size", "key_team",
    # Tier 3
    "investment_stage", "raise_amount", "raise_currency", "raise_instrument",
    "valuation_cap", "pre_money_valuation", "prior_rounds",
    "existing_investors", "referral_source",
    # Signals
    "priority_indicators", "red_flags", "competitors",
    # Meta
    "_extracted_at", "_extraction_version", "_files_examined",
}


def evaluate_metadata(meta: dict) -> dict[str, Any]:
    """Score the extracted metadata."""
    assessment = {
        "has_all_keys": EXPECTED_KEYS.issubset(set(meta)),
        "missing_keys": sorted(EXPECTED_KEYS - set(meta)),
        "extra_keys": sorted(set(meta) - EXPECTED_KEYS),
        "populated_fields": [],
        "empty_fields": [],
        "files_examined_count": len(meta.get("_files_examined") or []),
        "has_extracted_at": bool(meta.get("_extracted_at")),
        "has_version": bool(meta.get("_extraction_version")),
    }
    for k in EXPECTED_KEYS:
        if k.startswith("_"):
            continue
        v = meta.get(k)
        if v is None or v == [] or v == "":
            assessment["empty_fields"].append(k)
        else:
            assessment["populated_fields"].append(k)
    assessment["fill_rate"] = (
        len(assessment["populated_fields"])
        / max(1, len(EXPECTED_KEYS) - 3)  # minus the 3 meta keys
    )
    return assessment


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_test(entity_id: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "entity_id": entity_id,
        "timestamps": {},
        "phases": {},
    }

    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as client:
        # ---- Phase 0: wait for backend ----
        _banner("Phase 0: Backend health check")
        if not await _wait_for_backend(client):
            raise RuntimeError(f"Backend at {BASE} is not responding")
        print("  backend is up")

        # ---- Phase 1: entity + workspace pre-check ----
        _banner("Phase 1: Entity + workspace pre-check")
        entity_before = await _get_entity(client, entity_id)
        print(f"  Entity: {entity_before['name']}  (id={entity_id[:8]}...)")
        print(f"  Website: {entity_before.get('website') or '(none)'}")
        print(f"  Existing metadata: {bool(entity_before.get('metadata'))}")

        tree_before = await _list_workspace(client, entity_id)
        file_count_before = _flatten_file_count(tree_before)
        print(f"  Workspace files: {file_count_before}")

        had_prev_profile = _find_in_tree(tree_before, "Company Profile.json") is not None
        print(f"  Pre-existing Company Profile.json: {had_prev_profile}")

        report["phases"]["precheck"] = {
            "entity_name": entity_before["name"],
            "entity_website": entity_before.get("website"),
            "had_existing_metadata": bool(entity_before.get("metadata")),
            "workspace_file_count": file_count_before,
            "had_prev_profile_json": had_prev_profile,
        }

        # ---- Phase 2: create session + trigger preset ----
        _banner("Phase 2: Trigger extract_info preset")
        session_id = await _create_session(client, entity_id)
        print(f"  session_id: {session_id[:8]}...")

        t_start = time.monotonic()
        report["timestamps"]["preset_start"] = time.time()
        accepted = await _trigger_preset(client, entity_id, session_id)
        job_id = accepted["job_id"]
        print(f"  job_id: {job_id[:8]}...")
        print(f"  Status: 202 Accepted (background agent job)")

        # ---- Phase 3: poll job ----
        _banner("Phase 3: Poll job progress")
        final = await _poll_job(client, entity_id, session_id, job_id)
        elapsed = time.monotonic() - t_start
        report["phases"]["job"] = {
            "job_id": job_id,
            "session_id": session_id,
            "final_status": final["status"],
            "elapsed_s": round(elapsed, 1),
            "error_message": final.get("error_message"),
            "step_detail": final.get("step_detail"),
            "warnings": final.get("warnings") or [],
            "tool_trace_keys": (final.get("tool_trace") or {}).get("keys", []),
            "tool_trace_message_count": (final.get("tool_trace") or {}).get(
                "message_count", 0
            ),
        }
        print(f"  Final status: {final['status']}")
        print(f"  Elapsed: {elapsed:.1f}s")
        if final.get("error_message"):
            print(f"  Error: {final['error_message']}")

        if final["status"] != "succeeded":
            report["phases"]["evaluation"] = {
                "skipped": True,
                "reason": "job did not succeed",
            }
            return report

        # ---- Phase 4: verify Company Profile.json in workspace ----
        _banner("Phase 4: Verify Company Profile.json in workspace")
        tree_after = await _list_workspace(client, entity_id)
        profile_node = _find_in_tree(tree_after, "Company Profile.json")

        if not profile_node:
            print("  FAIL: Company Profile.json not found in workspace")
            report["phases"]["workspace"] = {
                "profile_found": False,
            }
            return report

        print(f"  Path: {profile_node['path']}")
        print(f"  Node id: {profile_node['id']}")
        print(f"  Version: {profile_node.get('version')}")
        print(f"  Size: {profile_node.get('size_bytes')} bytes")

        report["phases"]["workspace"] = {
            "profile_found": True,
            "profile_node_id": profile_node["id"],
            "profile_path": profile_node["path"],
            "profile_version": profile_node.get("version"),
            "profile_size_bytes": profile_node.get("size_bytes"),
            "file_count_after": _flatten_file_count(tree_after),
        }

        # ---- Phase 5: read the file content ----
        _banner("Phase 5: Read Company Profile.json content")
        profile_data = await _read_workspace_file_json(
            client, entity_id, profile_node["id"]
        )
        print(f"  Top-level keys: {len(profile_data)}")

        # ---- Phase 6: verify Entity.metadata_json synced ----
        _banner("Phase 6: Verify Entity.metadata_json synced")
        entity_after = await _get_entity(client, entity_id)
        meta = entity_after.get("metadata")
        print(f"  Entity.metadata populated: {bool(meta)}")
        if meta:
            print(f"  Keys: {len(meta)}")
        print(f"  Name after: {entity_after['name']}  (was: {entity_before['name']})")
        print(
            f"  Website after: {entity_after.get('website') or '(none)'}  "
            f"(was: {entity_before.get('website') or '(none)'})"
        )

        report["phases"]["entity_sync"] = {
            "metadata_synced": bool(meta),
            "name_before": entity_before["name"],
            "name_after": entity_after["name"],
            "name_changed": entity_before["name"] != entity_after["name"],
            "website_before": entity_before.get("website"),
            "website_after": entity_after.get("website"),
            "website_changed": (
                (entity_before.get("website") or None)
                != (entity_after.get("website") or None)
            ),
        }

        # ---- Phase 7: evaluate extraction quality ----
        _banner("Phase 7: Evaluate extraction quality")
        # Use Entity.metadata_json (the merged source of truth) for evaluation
        eval_meta = meta or profile_data
        assessment = evaluate_metadata(eval_meta)
        print(f"  Schema coverage: {'OK' if assessment['has_all_keys'] else 'MISSING KEYS'}")
        if assessment["missing_keys"]:
            print(f"    missing: {assessment['missing_keys']}")
        if assessment["extra_keys"]:
            print(f"    extra (warn): {assessment['extra_keys']}")
        print(
            f"  Fill rate: {assessment['fill_rate']*100:.0f}%  "
            f"({len(assessment['populated_fields'])}/{len(EXPECTED_KEYS)-3} fields)"
        )
        print(f"  Files examined: {assessment['files_examined_count']}")
        print(f"  Populated:  {assessment['populated_fields']}")
        print(f"  Empty:      {assessment['empty_fields']}")

        report["phases"]["evaluation"] = assessment
        report["extracted_metadata"] = eval_meta
        report["profile_json_from_workspace"] = profile_data

        # ---- Phase 8: dump full extracted metadata ----
        _banner("Phase 8: Full extracted metadata (JSON)")
        print(json.dumps(eval_meta, indent=2, ensure_ascii=False))

    return report


def write_report(report: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Extract Info Preset — E2E Test Report")
    lines.append("")
    lines.append(f"**Entity:** `{report['entity_id']}`")
    lines.append(f"**Run:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report['timestamps'].get('preset_start', time.time())))}")
    lines.append("")

    for phase, data in report.get("phases", {}).items():
        lines.append(f"## Phase: {phase}")
        lines.append("```json")
        lines.append(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        lines.append("")

    if "extracted_metadata" in report:
        lines.append("## Extracted metadata")
        lines.append("```json")
        lines.append(json.dumps(report["extracted_metadata"], indent=2, ensure_ascii=False))
        lines.append("```")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to: {REPORT_PATH}")


def main() -> int:
    entity_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENTITY_ID
    try:
        report = asyncio.run(run_test(entity_id))
    except Exception as e:
        print(f"\nTEST FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1

    write_report(report)

    # Determine overall pass/fail
    eval_phase = report.get("phases", {}).get("evaluation", {})
    job_phase = report.get("phases", {}).get("job", {})
    ws_phase = report.get("phases", {}).get("workspace", {})
    sync_phase = report.get("phases", {}).get("entity_sync", {})

    ok = (
        job_phase.get("final_status") == "succeeded"
        and ws_phase.get("profile_found") is True
        and sync_phase.get("metadata_synced") is True
        and eval_phase.get("has_all_keys") is True
    )

    _banner("SUMMARY", char="#")
    print(f"  Job:                    {job_phase.get('final_status')}")
    print(f"  Profile.json written:   {ws_phase.get('profile_found')}")
    print(f"  Entity.metadata synced: {sync_phase.get('metadata_synced')}")
    print(f"  Schema coverage:        {eval_phase.get('has_all_keys')}")
    print(f"  Fill rate:              {eval_phase.get('fill_rate', 0)*100:.0f}%")
    print(f"  Overall: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
