#!/usr/bin/env python3
"""E2E test for the `legal_review` preset (force-react agent mode).

Runs against a live backend (http://127.0.0.1:8000) using existing DB entities
that already have legal documents in their workspace. Mimics the frontend flow:

  1. Pre-check: GET /entities/{id} + /workspace/tree to find legal docs
  2. POST /entities/{id}/chat/sessions                  (create session)
  3. POST /entities/{id}/chat/presets/legal_review/run  (selected node_ids + session_id)
  4. GET  .../jobs/{job_id}                             (poll until terminal)
  5. GET  /entities/{id}/workspace/file/{legal_review_node_id}   (verify output)
  6. GET  /entities/{id}                                (verify metadata.legal_reviews)

Covers two entities that represent distinct scenarios:
  - CyberNexus (follow_on)   — existing investor, Series Pre-A+ closing binder
                                (SPA + SHA + MAA + MRL + cap table + disclosure)
  - Elastro (new_investment) — Certificate of Incorporation only (minimal docset)

Usage:
    cd backend && python tests/test_legal_review_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = httpx.Timeout(30.0, read=60.0)
POLL_INTERVAL = 4.0
JOB_TIMEOUT_S = 900  # 15 minutes — agent may read several large docs + templates

REPORT_PATH = Path(__file__).parent / "e2e_legal_review_report.md"


# ---------------------------------------------------------------------------
# Scenario specs
# ---------------------------------------------------------------------------

# CyberNexus — existing investor (metadata._positions populated), fresh
# Series Pre-A+ closing binder. We select only the top-level Pre-A+ docs (not
# the redlines nor the prior Series Pre-A binder) to mimic a focused review.
CYBERNEXUS_ID = "01467c92-5f99-4363-b614-ac580a74533b"

CYBERNEXUS_SELECT_FILENAMES = {
    # Top-level Pre-A+ transaction docs (all under
    # "Data Room/Legal/CyberNexus Series Pre-A Closing Binder/")
    "1. CyberNexus_Series Pre-A+_SPA (MLB 2026.04.04).docx",
    "2. CyberNexus_Series Pre-A+_SHA (MLB 2026.04.04).docx",
    "3. CyberNexus_Series Pre-A+ MAA (MLB 2026.04.04).docx",
    "4. CyberNexus_Series Pre-A+_MRL (Sky9) (MLB 2026.04.04).docx",
    "1-1 CyberNexus_Series Pre-A+_Disclosure Schedule (MLB 2026.04.04).docx",
    "[Confidential] CyberNexus - Pro Forma Cap Table (Pre-A+) - 20260404.xlsx",
}

# Elastro — new-investment scenario, minimal docset (just the COI). Agent
# should produce instrument_type="priced_round", scenario="new_investment",
# flag limited information (few terms extractable from COI alone).
ELASTRO_ID = "89d52e41-e297-4235-b672-2de4b22379c8"

ELASTRO_SELECT_FILENAME_HINTS = [
    # Path contains this — the exact filename is long and includes parens.
    "DE Certificate of Incorporation",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _banner(title: str, char: str = "=") -> None:
    print(f"\n{char * 72}")
    print(f"  {title}")
    print(f"{char * 72}")


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


async def _create_session(client: httpx.AsyncClient, entity_id: str, title: str) -> str:
    r = await client.post(
        f"/entities/{entity_id}/chat/sessions",
        json={"title": title},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _trigger_preset(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    node_ids: list[str],
) -> dict:
    """Mimics the frontend handleRunPreset for legal_review."""
    r = await client.post(
        f"/entities/{entity_id}/chat/presets/legal_review/run",
        json={
            "node_ids": node_ids,
            "session_id": session_id,
            "agent_mode": "react",
            "model_profile_id": "gemini_google",
        },
    )
    if r.status_code != 202:
        raise RuntimeError(
            f"Expected 202 Accepted, got {r.status_code}: {r.text[:400]}"
        )
    return r.json()


async def _poll_job(
    client: httpx.AsyncClient,
    entity_id: str,
    session_id: str,
    job_id: str,
) -> dict:
    deadline = time.monotonic() + JOB_TIMEOUT_S
    t0 = time.monotonic()
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
            elapsed = int(time.monotonic() - t0)
            print(f"  [{elapsed:>4}s] {status:<12} {step[:110]}")
            last_step = step
        if status in ("succeeded", "failed"):
            return last
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"Job {job_id} did not terminate in {JOB_TIMEOUT_S}s; last={last}"
    )


async def _read_workspace_file(
    client: httpx.AsyncClient, entity_id: str, node_id: str
) -> bytes:
    r = await client.get(f"/entities/{entity_id}/workspace/file/{node_id}")
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _walk_files(nodes: list[dict]):
    for n in nodes:
        if n.get("node_type") == "file":
            yield n
        for c in n.get("children") or []:
            if c.get("node_type") == "file":
                yield c
            else:
                yield from _walk_files(c.get("children") or [])


def _find_file(nodes: list[dict], name: str) -> dict | None:
    for n in _walk_files(nodes):
        if n.get("name") == name:
            return n
    return None


def _select_by_filename(nodes: list[dict], names: set[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for n in _walk_files(nodes):
        if n.get("name") in names and n["id"] not in seen:
            out.append(n)
            seen.add(n["id"])
    return out


def _select_by_path_substring(nodes: list[dict], substrings: list[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for n in _walk_files(nodes):
        path = n.get("path") or ""
        if any(s in path for s in substrings) and n["id"] not in seen:
            out.append(n)
            seen.add(n["id"])
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


REVIEW_TOP_KEYS = {
    "round_name", "review_date", "scenario", "instrument_type",
    "documents_reviewed", "reference_templates_consulted",
    "checklist_version",
    "company_terms", "safe_terms", "priced_round_terms",
    "governance", "investor_rights", "transfer_restrictions", "regulatory",
    "our_position", "unusual_terms", "red_flags",
    "priority_indicators", "killer_questions", "narrative_summary",
}

VALID_SCENARIOS = {"new_investment", "follow_on", "retrospective"}
VALID_INSTRUMENTS = {"safe", "convertible_note", "priced_round"}


def evaluate_review(
    review: dict,
    expected_scenario_in: set[str] | None,
    expected_instrument_in: set[str] | None,
) -> dict:
    """Score a single legal_reviews[i] entry."""
    out: dict[str, Any] = {
        "has_all_top_keys": REVIEW_TOP_KEYS.issubset(set(review)),
        "missing_top_keys": sorted(REVIEW_TOP_KEYS - set(review)),
        "round_name": review.get("round_name"),
        "scenario": review.get("scenario"),
        "instrument_type": review.get("instrument_type"),
        "review_date_populated": bool(review.get("review_date")),
        "checklist_version": review.get("checklist_version"),
        "documents_reviewed_count": len(review.get("documents_reviewed") or []),
        "documents_reviewed_with_node_id": sum(
            1 for d in (review.get("documents_reviewed") or [])
            if isinstance(d, dict) and d.get("node_id")
        ),
        "reference_templates_consulted": list(
            review.get("reference_templates_consulted") or []
        ),
        "unusual_terms_count": len(review.get("unusual_terms") or []),
        "red_flags_count": len(review.get("red_flags") or []),
        "killer_questions_count": len(review.get("killer_questions") or []),
        "narrative_summary_length": len(review.get("narrative_summary") or ""),
        "our_position_present": bool(review.get("our_position")),
    }
    out["scenario_valid"] = review.get("scenario") in (VALID_SCENARIOS | {None})
    out["instrument_valid"] = review.get("instrument_type") in (VALID_INSTRUMENTS | {None})
    out["scenario_as_expected"] = (
        expected_scenario_in is None
        or review.get("scenario") in expected_scenario_in
    )
    out["instrument_as_expected"] = (
        expected_instrument_in is None
        or review.get("instrument_type") in expected_instrument_in
    )
    # Unusual terms have the expected shape?
    bad_unusual = [
        u for u in (review.get("unusual_terms") or [])
        if not (isinstance(u, dict) and u.get("term") and "value" in u)
    ]
    out["unusual_terms_shape_ok"] = len(bad_unusual) == 0
    return out


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


async def run_scenario(
    client: httpx.AsyncClient,
    *,
    label: str,
    entity_id: str,
    session_title: str,
    select_files: Callable[[list[dict]], list[dict]],
    expected_scenario_in: set[str] | None,
    expected_instrument_in: set[str] | None,
) -> dict:
    report: dict[str, Any] = {"label": label, "entity_id": entity_id}

    _banner(f"{label}: entity {entity_id[:8]}...")

    entity_before = await _get_entity(client, entity_id)
    tree_before = await _list_workspace(client, entity_id)
    print(f"  entity: {entity_before['name']}")
    has_positions = bool(
        (entity_before.get("metadata") or {}).get("_positions")
    )
    prior_reviews = (
        (entity_before.get("metadata") or {}).get("legal_reviews") or []
    )
    print(f"  _positions present: {has_positions}")
    print(f"  prior legal_reviews: {len(prior_reviews)}")

    selected = select_files(tree_before)
    if not selected:
        report["status"] = "skipped"
        report["reason"] = "no matching legal docs found in workspace"
        print(f"  SKIP — no matching files")
        return report
    print(f"  selecting {len(selected)} file(s):")
    for s in selected:
        print(f"    - {s['path']} ({s.get('size_bytes') or 0} B)")
    node_ids = [s["id"] for s in selected]

    session_id = await _create_session(client, entity_id, session_title)
    print(f"  session_id: {session_id[:8]}...")

    t0 = time.monotonic()
    accepted = await _trigger_preset(client, entity_id, session_id, node_ids)
    job_id = accepted["job_id"]
    print(f"  job_id: {job_id[:8]}...  (202 Accepted)")

    # Poll
    final = await _poll_job(client, entity_id, session_id, job_id)
    elapsed = time.monotonic() - t0

    report["job"] = {
        "job_id": job_id,
        "session_id": session_id,
        "final_status": final.get("status"),
        "elapsed_s": round(elapsed, 1),
        "error_message": final.get("error_message"),
        "step_detail": final.get("step_detail"),
        "warnings": final.get("warnings") or [],
    }
    report["selection"] = {
        "node_ids": node_ids,
        "paths": [s["path"] for s in selected],
        "had_positions_before": has_positions,
        "prior_review_count": len(prior_reviews),
    }
    print(f"  final: {final.get('status')}  elapsed: {elapsed:.1f}s")

    if final.get("status") != "succeeded":
        report["status"] = "job_failed"
        return report

    # Verify workspace file
    tree_after = await _list_workspace(client, entity_id)
    review_node = _find_file(tree_after, "Legal Review.json")
    if not review_node:
        report["status"] = "no_file"
        print("  FAIL — Legal Review.json not found at workspace root")
        return report
    print(
        f"  Legal Review.json: path={review_node['path']} "
        f"v{review_node.get('version')} size={review_node.get('size_bytes')}"
    )

    # Read it
    raw = await _read_workspace_file(client, entity_id, review_node["id"])
    try:
        file_payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        report["status"] = "file_not_json"
        report["error"] = str(e)
        return report

    reviews_from_file = (
        file_payload.get("legal_reviews") if isinstance(file_payload, dict) else None
    )
    print(
        f"  Reviews in file: "
        f"{len(reviews_from_file) if isinstance(reviews_from_file, list) else 'N/A'}"
    )

    # Verify entity.metadata.legal_reviews
    entity_after = await _get_entity(client, entity_id)
    meta = entity_after.get("metadata") or {}
    reviews_in_meta = meta.get("legal_reviews") or []
    print(f"  entity.metadata.legal_reviews: {len(reviews_in_meta)}")

    # Evaluate ALL reviews produced by THIS run (use meta as source of truth)
    assessments = [
        evaluate_review(r, expected_scenario_in, expected_instrument_in)
        for r in reviews_in_meta
    ]

    report["workspace"] = {
        "review_node_id": review_node["id"],
        "review_path": review_node["path"],
        "review_version": review_node.get("version"),
        "review_size": review_node.get("size_bytes"),
        "reviews_in_file": (
            len(reviews_from_file) if isinstance(reviews_from_file, list) else None
        ),
    }
    report["entity_after"] = {
        "legal_reviews_count": len(reviews_in_meta),
        "has_positions": bool(meta.get("_positions")),
    }
    report["assessments"] = assessments
    report["reviews"] = reviews_in_meta
    report["file_reviews"] = (
        reviews_from_file if isinstance(reviews_from_file, list) else None
    )

    # Consistency: file reviews should match meta reviews exactly (server
    # re-persists the file after merge). Compare shallowly by round_name set.
    if isinstance(reviews_from_file, list):
        file_rounds = sorted([r.get("round_name") for r in reviews_from_file])
        meta_rounds = sorted([r.get("round_name") for r in reviews_in_meta])
        report["file_meta_round_sync"] = file_rounds == meta_rounds

    # Summary line per assessment
    for i, a in enumerate(assessments):
        flags = []
        if not a["has_all_top_keys"]:
            flags.append(f"missing={a['missing_top_keys']}")
        if not a["scenario_valid"]:
            flags.append(f"bad_scenario={a['scenario']}")
        if not a["instrument_valid"]:
            flags.append(f"bad_instrument={a['instrument_type']}")
        if not a["scenario_as_expected"]:
            flags.append(
                f"unexpected_scenario={a['scenario']} (want: {expected_scenario_in})"
            )
        if not a["instrument_as_expected"]:
            flags.append(
                f"unexpected_instrument={a['instrument_type']} (want: {expected_instrument_in})"
            )
        flag_str = "" if not flags else f"  WARN: {flags}"
        print(
            f"  [{i}] round={a['round_name']!r}  scenario={a['scenario']}  "
            f"instrument={a['instrument_type']}  docs_reviewed="
            f"{a['documents_reviewed_count']}  unusual={a['unusual_terms_count']}  "
            f"red_flags={a['red_flags_count']}  q={a['killer_questions_count']}{flag_str}"
        )

    # Pass/fail
    ok = bool(assessments) and all(
        a["has_all_top_keys"]
        and a["scenario_valid"]
        and a["instrument_valid"]
        and a["scenario_as_expected"]
        and a["instrument_as_expected"]
        for a in assessments
    )
    report["status"] = "pass" if ok else "fail"
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_all() -> list[dict]:
    reports: list[dict] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as client:
        _banner("Phase 0: Backend health")
        if not await _wait_for_backend(client):
            raise RuntimeError(f"Backend at {BASE} not responding")
        print("  backend is up")

        # ── Scenario 1: CyberNexus (existing investor → follow_on priced round) ──
        rep1 = await run_scenario(
            client,
            label="Scenario A: CyberNexus (follow_on, priced round)",
            entity_id=CYBERNEXUS_ID,
            session_title="legal_review e2e — CyberNexus Pre-A+",
            select_files=lambda tree: _select_by_filename(
                tree, CYBERNEXUS_SELECT_FILENAMES
            ),
            # Pre-A+ is a priced round, and we already hold Pre-A → follow_on
            expected_scenario_in={"follow_on", "retrospective"},
            expected_instrument_in={"priced_round"},
        )
        reports.append(rep1)

        # ── Scenario 2: Elastro (minimal, COI only → new_investment priced) ──
        rep2 = await run_scenario(
            client,
            label="Scenario B: Elastro (new_investment, COI only)",
            entity_id=ELASTRO_ID,
            session_title="legal_review e2e — Elastro COI",
            select_files=lambda tree: _select_by_path_substring(
                tree, ELASTRO_SELECT_FILENAME_HINTS
            ),
            # No prior position → new_investment; COI → priced_round
            expected_scenario_in={"new_investment"},
            expected_instrument_in={"priced_round"},
        )
        reports.append(rep2)

    return reports


def write_report(reports: list[dict]) -> None:
    lines: list[str] = []
    lines.append("# Legal Review Preset — E2E Test Report")
    lines.append("")
    lines.append(f"**Run:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## Summary")
    for rep in reports:
        lines.append(
            f"- **{rep.get('label')}** — status: `{rep.get('status')}`"
        )
    lines.append("")
    for rep in reports:
        lines.append(f"## {rep.get('label')}")
        lines.append("")
        lines.append("```json")
        # Strip big narrative/full review content for compactness; keep snapshots
        trimmed = dict(rep)
        if "reviews" in trimmed:
            trimmed["reviews"] = [
                {**r, "narrative_summary": f"({len(r.get('narrative_summary') or '')} chars)"}
                for r in trimmed["reviews"]
            ]
        if "file_reviews" in trimmed:
            trimmed["file_reviews"] = (
                f"<{len(trimmed['file_reviews'])} entries>"
                if isinstance(trimmed["file_reviews"], list) else None
            )
        lines.append(json.dumps(trimmed, indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        lines.append("")
        # Dump full reviews separately
        reviews = rep.get("reviews") or []
        if reviews:
            lines.append("### Full reviews (from entity.metadata)")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(reviews, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to: {REPORT_PATH}")


def main() -> int:
    try:
        reports = asyncio.run(run_all())
    except Exception as e:
        print(f"\nTEST RUN FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    write_report(reports)

    _banner("SUMMARY", char="#")
    exit_code = 0
    for rep in reports:
        label = rep.get("label", "(unknown)")
        status = rep.get("status")
        job_status = (rep.get("job") or {}).get("final_status")
        n_reviews = (rep.get("entity_after") or {}).get("legal_reviews_count", 0)
        sync = rep.get("file_meta_round_sync")
        print(f"  {label}")
        print(
            f"    status={status}  job={job_status}  reviews={n_reviews}  "
            f"file_meta_sync={sync}"
        )
        if status != "pass":
            exit_code = 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
