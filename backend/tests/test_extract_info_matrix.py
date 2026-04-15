#!/usr/bin/env python3
"""Matrix test for extract_info preset across multiple entities + scenarios.

Scenarios covered (each run records pass/fail + quality):
  1. Fresh entity, no metadata, no Company Profile.json → first-time extraction
  2. Entity WITH existing metadata → incremental extraction (regression fix)
  3. Same entity, existing chat session (reused) → session persistence
  4. Tiny workspace (< 5 files) → edge case
  5. Large workspace (40+ files) → scale test

Usage:
    cd backend && python tests/test_extract_info_matrix.py

Requires a running backend on http://127.0.0.1:8000.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = httpx.Timeout(30.0, read=60.0)
POLL_INTERVAL = 3.0
JOB_TIMEOUT_S = 600

REPORT_PATH = Path(__file__).parent / "e2e_extract_info_matrix_report.md"

# Expected FACT schema (post Facts-vs-Opinions split; no signal keys here).
EXPECTED_FACT_KEYS = {
    "company_name", "legal_name", "one_liner", "description",
    "industry_tags", "business_model", "hq_location", "website",
    "founded_date", "incorporation_jurisdiction", "incorporation_entity_type",
    "founders", "team_size", "key_team",
    "investment_stage", "raise_amount", "raise_currency", "raise_instrument",
    "valuation_cap", "pre_money_valuation", "prior_rounds", "current_round_name",
    "existing_investors", "referral_source",
    "_positions", "_fact_discrepancies",
    "_extracted_at", "_extraction_version", "_files_examined",
}

FORBIDDEN_KEYS = {
    "priority_indicators", "red_flags", "competitors", "legal_reviews",
}

# Lifecycle / meta keys excluded from fill_rate denominator (always scaffolded).
_FILL_RATE_EXCLUDES = {
    "_extracted_at", "_extraction_version", "_files_examined",
    "_positions", "_fact_discrepancies",
}


@dataclass
class ScenarioResult:
    scenario: str
    entity_id: str
    entity_name: str
    had_existing_metadata: bool
    reused_session: bool
    status: str = "SKIP"            # PASS | FAIL | ERROR | SKIP
    elapsed_s: float = 0.0
    job_final_status: str = ""
    profile_written: bool = False
    signals_written: bool = False
    metadata_synced: bool = False
    fill_rate: float = 0.0
    fields_populated: int = 0
    files_examined: int = 0
    name_before: str = ""
    name_after: str = ""
    website_before: str = ""
    website_after: str = ""
    name_auto_updated: bool = False
    website_auto_updated: bool = False
    forbidden_leaked: list[str] = field(default_factory=list)
    signal_counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def get_entity(client: httpx.AsyncClient, entity_id: str) -> dict:
    r = await client.get(f"/entities/{entity_id}")
    r.raise_for_status()
    return r.json()


async def get_tree(client: httpx.AsyncClient, entity_id: str) -> list[dict]:
    r = await client.get(f"/entities/{entity_id}/workspace/tree")
    r.raise_for_status()
    return r.json()


def find_in_tree(nodes: list[dict], name: str) -> dict | None:
    for n in nodes:
        if n.get("name") == name:
            return n
        hit = find_in_tree(n.get("children") or [], name)
        if hit:
            return hit
    return None


def count_files(nodes: list[dict]) -> int:
    c = 0
    for n in nodes:
        if n.get("node_type") == "file":
            c += 1
        c += count_files(n.get("children") or [])
    return c


async def create_session(
    client: httpx.AsyncClient, entity_id: str, title: str
) -> str:
    r = await client.post(
        f"/entities/{entity_id}/chat/sessions",
        json={"title": title},
    )
    r.raise_for_status()
    return r.json()["id"]


async def send_user_message(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    text: str,
) -> None:
    """Send a regular chat message to seed session history (no agent)."""
    r = await client.post(
        f"/entities/{entity_id}/chat/sessions/{session_id}/messages",
        json={"text": text, "node_ids": [], "agent_mode": "one_shot"},
    )
    # one-shot returns 200 with the reply — we don't care about content
    r.raise_for_status()


async def trigger_preset(
    client: httpx.AsyncClient, entity_id: str, session_id: str
) -> dict:
    r = await client.post(
        f"/entities/{entity_id}/chat/presets/extract_info/run",
        json={
            "node_ids": [],
            "session_id": session_id,
            "agent_mode": "react",
            "model_profile_id": "gemini_google",
        },
    )
    if r.status_code != 202:
        raise RuntimeError(
            f"Preset request failed: {r.status_code} {r.text[:400]}"
        )
    return r.json()


async def poll_job(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    job_id: str,
    prefix: str,
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
        step = (last.get("step_detail") or "").strip()
        if step != last_step:
            t = int(JOB_TIMEOUT_S - (deadline - time.monotonic()))
            print(f"    {prefix} [{t:>4}s] {status:<10} {step[:80]}")
            last_step = step
        if status in ("succeeded", "failed"):
            return last
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"Job {job_id[:8]} timed out after {JOB_TIMEOUT_S}s"
    )


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


async def run_scenario(
    client: httpx.AsyncClient,
    scenario_name: str,
    entity_id: str,
    existing_session_id: str | None = None,
    seed_message: str | None = None,
) -> ScenarioResult:
    entity_before = await get_entity(client, entity_id)
    tree_before = await get_tree(client, entity_id)
    had_existing = bool(entity_before.get("metadata"))

    result = ScenarioResult(
        scenario=scenario_name,
        entity_id=entity_id,
        entity_name=entity_before["name"],
        had_existing_metadata=had_existing,
        reused_session=existing_session_id is not None,
        name_before=entity_before["name"],
        website_before=entity_before.get("website") or "",
    )

    prefix = f"[{scenario_name:<24}]"
    print(
        f"\n{prefix} entity={entity_before['name']} "
        f"files={count_files(tree_before)} had_meta={had_existing} "
        f"reuse_session={existing_session_id is not None}"
    )

    try:
        if existing_session_id:
            session_id = existing_session_id
        else:
            session_id = await create_session(
                client, entity_id, f"matrix-{scenario_name}"
            )

        if seed_message:
            print(f"    {prefix} seeding session with: {seed_message!r}")
            await send_user_message(client, entity_id, session_id, seed_message)

        t0 = time.monotonic()
        accepted = await trigger_preset(client, entity_id, session_id)
        job_id = accepted["job_id"]
        final = await poll_job(client, entity_id, session_id, job_id, prefix)
        result.elapsed_s = round(time.monotonic() - t0, 1)
        result.job_final_status = final.get("status", "")

        if final.get("status") != "succeeded":
            result.status = "FAIL"
            result.error = (
                final.get("error_message")
                or f"job ended with status={final.get('status')}"
            )
            return result

        # Verify profile written
        tree_after = await get_tree(client, entity_id)
        profile_node = find_in_tree(tree_after, "Company Profile.json")
        result.profile_written = profile_node is not None
        signals_node = find_in_tree(tree_after, "extract_info_signals.json")
        result.signals_written = signals_node is not None

        # Fetch signals counts when sidecar is present (for visibility).
        if signals_node:
            try:
                r = await client.get(
                    f"/entities/{entity_id}/workspace/file/{signals_node['id']}"
                )
                if r.status_code == 200:
                    s = json.loads(r.content)
                    result.signal_counts = {
                        k: (len(s.get(k) or []) if isinstance(s.get(k), list) else 0)
                        for k in ("priority_indicators", "red_flags", "competitors")
                    }
            except Exception:
                pass

        # Verify entity metadata synced
        entity_after = await get_entity(client, entity_id)
        meta = entity_after.get("metadata")
        result.metadata_synced = bool(meta)
        result.name_after = entity_after["name"]
        result.website_after = entity_after.get("website") or ""
        result.name_auto_updated = result.name_before != result.name_after
        result.website_auto_updated = (
            result.website_before != result.website_after
        )

        # Evaluate quality
        if meta:
            result.files_examined = len(meta.get("_files_examined") or [])
            result.forbidden_leaked = sorted(FORBIDDEN_KEYS & set(meta))
            counted_keys = EXPECTED_FACT_KEYS - _FILL_RATE_EXCLUDES
            populated = 0
            for k in counted_keys:
                v = meta.get(k)
                if v not in (None, [], ""):
                    populated += 1
            result.fields_populated = populated
            result.fill_rate = round(populated / len(counted_keys), 2)

        if (
            result.profile_written
            and result.metadata_synced
            and not result.forbidden_leaked
        ):
            result.status = "PASS"
        else:
            result.status = "FAIL"
            if not result.profile_written:
                result.warnings.append("Company Profile.json not written")
            if not result.metadata_synced:
                result.warnings.append("Entity.metadata_json not populated")
            if result.forbidden_leaked:
                result.warnings.append(
                    f"Forbidden (opinion/legacy) keys in metadata: "
                    f"{result.forbidden_leaked}"
                )

    except Exception as e:
        result.status = "ERROR"
        result.error = f"{type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()

    print(
        f"    {prefix} => {result.status} in {result.elapsed_s:.0f}s  "
        f"fill={result.fill_rate*100:.0f}%  files={result.files_examined}"
    )
    return result


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------


# Scenarios are executed in order; some depend on prior runs' state
# (incremental extraction requires metadata from a previous run).
# Each tuple: (scenario_label, entity_id, seed_message_or_None)
SCENARIOS = [
    # Tiny workspace (edge case)
    ("tiny-fresh", "f21fe5ec-cd74-46e5-a2f2-256f97055a95", None),  # wayfarer, 4 files
    # Small workspace
    ("small-fresh", "ec04890b-74cd-4019-857e-b27a31e36784", None),  # scenic, 7 files
    # Medium + incremental (Abinitia already has metadata from earlier tests)
    ("incremental-new-session", "b20347f8-3866-4ca7-a01f-291b480a4fb9", None),
    # Same entity, same chat session used again → tests session persistence.
    # We inject a prior user message to simulate "old chat" scenario.
    ("incremental-seeded-chat", "b20347f8-3866-4ca7-a01f-291b480a4fb9",
     "What's the pitch deck say about the team?"),
    # Large workspace (scale test)
    ("large-fresh", "89d52e41-e297-4235-b672-2de4b22379c8", None),  # Elastro, 43 files
    # Cybernexus — already has metadata, incremental path (the one that
    # previously 500'd before the render_extract_info fix)
    ("incremental-cybernexus", "01467c92-5f99-4363-b614-ac580a74533b", None),
]


async def main() -> int:
    results: list[ScenarioResult] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as client:
        # Health check
        try:
            r = await client.get("/health")
            r.raise_for_status()
        except Exception as e:
            print(f"Backend not ready: {e}")
            return 1

        for label, entity_id, seed in SCENARIOS:
            result = await run_scenario(
                client, label, entity_id, seed_message=seed,
            )
            results.append(result)

    # Print summary
    print("\n" + "=" * 90)
    print(f"  MATRIX SUMMARY  ({len(results)} scenarios)")
    print("=" * 90)
    header = (
        f"  {'scenario':<28} {'entity':<18} {'status':<6} "
        f"{'time':>6}  {'fill':>5}  {'files':>5}  notes"
    )
    print(header)
    print("  " + "-" * 86)
    passed = failed = errored = 0
    for r in results:
        status_col = r.status
        notes = []
        if r.name_auto_updated:
            notes.append(f"name→{r.name_after[:20]}")
        if r.website_auto_updated:
            notes.append(f"web→{r.website_after[:20]}")
        if r.error:
            notes.append(f"ERR:{r.error[:40]}")
        notes_str = ", ".join(notes) or "-"
        print(
            f"  {r.scenario:<28} {r.entity_name[:18]:<18} {status_col:<6} "
            f"{r.elapsed_s:>5.0f}s  {r.fill_rate*100:>4.0f}%  {r.files_examined:>5}  {notes_str}"
        )
        if r.status == "PASS":
            passed += 1
        elif r.status == "FAIL":
            failed += 1
        else:
            errored += 1

    print("  " + "-" * 86)
    print(f"  PASS: {passed} | FAIL: {failed} | ERROR: {errored}")

    # Write detailed markdown report
    lines = [
        "# Extract Info Matrix — E2E Report",
        "",
        f"**Run:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Scenarios:** {len(results)}  |  **PASS:** {passed}  |  **FAIL:** {failed}  |  **ERROR:** {errored}",
        "",
        "## Summary",
        "",
        "| Scenario | Entity | Status | Time | Fill | Files | Signals | Leaks | Name updated | Web updated |",
        "|----------|--------|--------|------|------|-------|---------|-------|--------------|-------------|",
    ]
    for r in results:
        leak_cell = ",".join(r.forbidden_leaked) if r.forbidden_leaked else "—"
        signals_cell = (
            "Y" if r.signals_written else
            ("n/a" if all(v == 0 for v in (r.signal_counts or {}).values()) else "N")
        )
        lines.append(
            f"| {r.scenario} | {r.entity_name} | {r.status} "
            f"| {r.elapsed_s:.0f}s | {r.fill_rate*100:.0f}% | {r.files_examined} "
            f"| {signals_cell} | {leak_cell} "
            f"| {'✓' if r.name_auto_updated else '—'} "
            f"| {'✓' if r.website_auto_updated else '—'} |"
        )
    lines.append("")
    lines.append("## Detail")
    lines.append("")
    for r in results:
        lines.append(f"### {r.scenario} — {r.entity_name}")
        lines.append("```json")
        lines.append(json.dumps(asdict(r), indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Detailed report: {REPORT_PATH}")

    return 0 if failed == 0 and errored == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
