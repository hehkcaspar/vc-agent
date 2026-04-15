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


# Post Facts-vs-Opinions refactor:
# - Legal Review.json (workspace) holds OPINIONS per round — run metadata +
#   unusual_terms / red_flags / priority_indicators / killer_questions /
#   narrative_summary. No term blocks; those lift to prior_rounds[] in metadata.
# - entity.metadata_json has no `legal_reviews` key; round facts live in
#   `prior_rounds[round_name=X]` with full term blocks + our_position.

OPINION_TOP_KEYS = {
    "round_name", "review_date", "documents_reviewed",
    "reference_templates_consulted", "checklist_version",
    "unusual_terms", "red_flags",
    "priority_indicators", "killer_questions", "narrative_summary",
}

# Fact fields that should lift into prior_rounds[round_name=X] fact bag.
PRIOR_ROUND_FACT_NESTED = {
    "company_terms", "safe_terms", "priced_round_terms",
    "governance", "investor_rights", "transfer_restrictions",
    "regulatory",
}

VALID_SCENARIOS = {"new_investment", "follow_on", "retrospective"}
VALID_INSTRUMENTS = {"safe", "convertible_note", "priced_round"}


def evaluate_opinion_block(
    opinion: dict,
    prior_round_entry: dict | None,
    expected_scenario_in: set[str] | None,
    expected_instrument_in: set[str] | None,
) -> dict:
    """Score a single opinion entry (from Legal Review.json) + its corresponding
    prior_rounds[round_name=] fact-bag entry (from Entity.metadata_json).

    The split contract: opinions live in the workspace file; facts must lift to
    prior_rounds[]. We check both sides.
    """
    pr = prior_round_entry or {}
    out: dict[str, Any] = {
        "round_name": opinion.get("round_name"),
        # Opinion-side checks
        "has_all_opinion_keys": OPINION_TOP_KEYS.issubset(set(opinion)),
        "missing_opinion_keys": sorted(OPINION_TOP_KEYS - set(opinion)),
        "opinion_has_fact_leakage": sorted(
            PRIOR_ROUND_FACT_NESTED & set(opinion)
        ),
        "review_date_populated": bool(opinion.get("review_date")),
        "checklist_version": opinion.get("checklist_version"),
        "documents_reviewed_count": len(opinion.get("documents_reviewed") or []),
        "documents_reviewed_with_node_id": sum(
            1 for d in (opinion.get("documents_reviewed") or [])
            if isinstance(d, dict) and d.get("node_id")
        ),
        "reference_templates_consulted": list(
            opinion.get("reference_templates_consulted") or []
        ),
        "unusual_terms_count": len(opinion.get("unusual_terms") or []),
        "red_flags_count": len(opinion.get("red_flags") or []),
        "killer_questions_count": len(opinion.get("killer_questions") or []),
        "narrative_summary_length": len(opinion.get("narrative_summary") or ""),
        # Fact-side checks (lift to prior_rounds[round_name=X])
        "fact_bag_present": bool(prior_round_entry),
        "fact_bag_scenario": pr.get("scenario"),
        "fact_bag_instrument_type": pr.get("instrument_type"),
        "fact_bag_our_position_present": bool(pr.get("our_position")),
        "fact_bag_nested_blocks_present": sorted(
            k for k in PRIOR_ROUND_FACT_NESTED
            if isinstance(pr.get(k), dict) and pr.get(k)
        ),
    }
    out["scenario_valid"] = pr.get("scenario") in (VALID_SCENARIOS | {None})
    out["instrument_valid"] = pr.get("instrument_type") in (
        VALID_INSTRUMENTS | {None}
    )
    out["scenario_as_expected"] = (
        expected_scenario_in is None
        or pr.get("scenario") in expected_scenario_in
    )
    out["instrument_as_expected"] = (
        expected_instrument_in is None
        or pr.get("instrument_type") in expected_instrument_in
    )
    # Unusual terms shape?
    bad_unusual = [
        u for u in (opinion.get("unusual_terms") or [])
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
    meta_before = entity_before.get("metadata") or {}
    has_positions = bool(meta_before.get("_positions"))
    prior_rounds_before = meta_before.get("prior_rounds") or []
    print(f"  _positions present: {has_positions}")
    print(f"  prior_rounds (fact bags): {len(prior_rounds_before)}")
    if "legal_reviews" in meta_before:
        print(
            f"  WARN: legacy metadata.legal_reviews key still present "
            f"(len={len(meta_before['legal_reviews'])}); will be dropped on this run"
        )

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

    opinions_from_file = (
        file_payload.get("legal_reviews") if isinstance(file_payload, dict) else None
    )
    print(
        f"  Opinion entries in Legal Review.json: "
        f"{len(opinions_from_file) if isinstance(opinions_from_file, list) else 'N/A'}"
    )

    # After refactor: metadata.legal_reviews must be absent; round facts live
    # in metadata.prior_rounds[round_name=X] with full term blocks.
    entity_after = await _get_entity(client, entity_id)
    meta = entity_after.get("metadata") or {}
    legacy_legal_reviews = meta.get("legal_reviews")
    prior_rounds_after = meta.get("prior_rounds") or []
    prior_round_by_name: dict[str, dict] = {
        pr.get("round_name"): pr
        for pr in prior_rounds_after
        if isinstance(pr, dict) and pr.get("round_name")
    }
    discrepancies_after = meta.get("_fact_discrepancies") or []
    print(f"  entity.metadata.prior_rounds: {len(prior_rounds_after)}")
    print(f"  entity.metadata._fact_discrepancies: {len(discrepancies_after)}")
    if legacy_legal_reviews is not None:
        print(
            f"  FAIL: legacy metadata.legal_reviews STILL present "
            f"({len(legacy_legal_reviews)} entries) — refactor incomplete"
        )

    # Pair each opinion block in the workspace file with its prior_rounds[]
    # fact bag (keyed by round_name) for the full opinion + fact assessment.
    opinions_list = opinions_from_file if isinstance(opinions_from_file, list) else []
    assessments = [
        evaluate_opinion_block(
            op,
            prior_round_by_name.get(op.get("round_name")),
            expected_scenario_in,
            expected_instrument_in,
        )
        for op in opinions_list
    ]

    report["workspace"] = {
        "review_node_id": review_node["id"],
        "review_path": review_node["path"],
        "review_version": review_node.get("version"),
        "review_size": review_node.get("size_bytes"),
        "opinions_in_file": len(opinions_list),
    }
    report["entity_after"] = {
        "prior_rounds_count": len(prior_rounds_after),
        "fact_discrepancies_count": len(discrepancies_after),
        "has_positions": bool(meta.get("_positions")),
        "legacy_legal_reviews_leaked": legacy_legal_reviews is not None,
    }
    report["assessments"] = assessments
    report["opinions"] = opinions_list
    report["prior_rounds_after"] = prior_rounds_after
    report["discrepancies_after"] = discrepancies_after

    # Consistency: every opinion entry should have a matching prior_rounds[]
    # fact-bag entry (post-processing lifts proposed_facts into prior_rounds).
    opinion_rounds = {op.get("round_name") for op in opinions_list}
    prior_round_names = set(prior_round_by_name)
    report["opinion_prior_round_match"] = opinion_rounds.issubset(prior_round_names)

    # Summary line per assessment
    for i, a in enumerate(assessments):
        flags = []
        if not a["has_all_opinion_keys"]:
            flags.append(f"missing_opinion={a['missing_opinion_keys']}")
        if a["opinion_has_fact_leakage"]:
            flags.append(f"fact_leak_in_opinion={a['opinion_has_fact_leakage']}")
        if not a["scenario_valid"]:
            flags.append(f"bad_scenario={a['fact_bag_scenario']}")
        if not a["instrument_valid"]:
            flags.append(f"bad_instrument={a['fact_bag_instrument_type']}")
        if not a["scenario_as_expected"]:
            flags.append(
                f"unexpected_scenario={a['fact_bag_scenario']} (want: {expected_scenario_in})"
            )
        if not a["instrument_as_expected"]:
            flags.append(
                f"unexpected_instrument={a['fact_bag_instrument_type']} (want: {expected_instrument_in})"
            )
        if not a["fact_bag_present"]:
            flags.append("missing_prior_rounds_fact_bag")
        flag_str = "" if not flags else f"  WARN: {flags}"
        print(
            f"  [{i}] round={a['round_name']!r}  "
            f"scenario={a['fact_bag_scenario']}  "
            f"instrument={a['fact_bag_instrument_type']}  docs_reviewed="
            f"{a['documents_reviewed_count']}  unusual={a['unusual_terms_count']}  "
            f"red_flags={a['red_flags_count']}  q={a['killer_questions_count']}  "
            f"fact_blocks={len(a['fact_bag_nested_blocks_present'])}{flag_str}"
        )

    # Pass/fail
    ok = (
        bool(assessments)
        and legacy_legal_reviews is None
        and report["opinion_prior_round_match"]
        and all(
            a["has_all_opinion_keys"]
            and not a["opinion_has_fact_leakage"]
            and a["scenario_valid"]
            and a["instrument_valid"]
            and a["scenario_as_expected"]
            and a["instrument_as_expected"]
            and a["fact_bag_present"]
            for a in assessments
        )
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
        # Strip big narrative/full opinion content for compactness; keep snapshots
        trimmed = dict(rep)
        if "opinions" in trimmed:
            trimmed["opinions"] = [
                {**r, "narrative_summary": f"({len(r.get('narrative_summary') or '')} chars)"}
                for r in trimmed["opinions"]
            ]
        if "prior_rounds_after" in trimmed:
            trimmed["prior_rounds_after"] = (
                f"<{len(trimmed['prior_rounds_after'])} round(s)>"
                if isinstance(trimmed["prior_rounds_after"], list) else None
            )
        lines.append(json.dumps(trimmed, indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        lines.append("")
        # Dump full opinions + prior_rounds separately
        opinions = rep.get("opinions") or []
        if opinions:
            lines.append("### Opinions (from Legal Review.json workspace file)")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(opinions, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")
        prior_rounds = rep.get("prior_rounds_after") or []
        if prior_rounds:
            lines.append("### prior_rounds[] (from entity.metadata_json — fact bags)")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(prior_rounds, indent=2, ensure_ascii=False))
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
        ea = rep.get("entity_after") or {}
        n_pr = ea.get("prior_rounds_count", 0)
        n_disc = ea.get("fact_discrepancies_count", 0)
        legacy_leak = ea.get("legacy_legal_reviews_leaked")
        pr_match = rep.get("opinion_prior_round_match")
        print(f"  {label}")
        print(
            f"    status={status}  job={job_status}  prior_rounds={n_pr}  "
            f"discrepancies={n_disc}  opinion↔fact_match={pr_match}  "
            f"legacy_leaked={legacy_leak}"
        )
        if status != "pass":
            exit_code = 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
