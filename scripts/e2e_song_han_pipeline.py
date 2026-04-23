"""E2E check: exercise the new sync-write + background-refinement flow.

Runs a bounded simulation against Song Han (scholar id
``1e563bf0-1892-4690-8222-164a3c230d08``):

Stage 1 — SYNC path
  Call ``grounded_search_json`` directly for 4 source-like prompts
  (news, patents, startups, red_flags). Items come back with first-
  pass URLs (may include Vertex redirects or Google search fallbacks).

Stage 2 — REFINE path
  Drive ``refine_pending_items``-style logic in isolation against
  the items from stage 1 — verify (flash-lite + grounded), triage,
  apply 3-tier URL fallback. Mutates items in place.

Stage 3 — REPORT
  Count per-tier and per-decision distributions; compare to the
  pre-refactor content-fit audit.

Uses a tmp working directory for tombstones so we don't dirty the real
dossier.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "audits"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.services.academic.destinations import accept_into  # noqa: E402
from app.services.academic.item_triage import triage  # noqa: E402
from app.services.academic.item_verification import verify_item  # noqa: E402
from app.services.academic.llm_client import grounded_search_json  # noqa: E402
from app.services.academic.url_fallback import apply_url_fallback  # noqa: E402

NAME = "Song Han"
AFFIL = "Massachusetts Institute of Technology"
CTX = (
    f"{NAME} at {AFFIL}; efficient deep learning, model compression, "
    f"TinyML, quantization. Co-founder of DeePhi Tech and OmniML."
)

PROMPTS = {
    "news": (
        f"Find news, press releases, or blog posts about academic researcher "
        f"**{NAME}** at {AFFIL} published since 2025-01-01. Include any "
        f"story where they are a named protagonist, OR the story is "
        f"primarily about a commercial venture they have founded. "
        f"Return ONLY a JSON array. Each item: title, url, published_date, "
        f"source, summary, category."
    ),
    "patents": (
        f"Find U.S. or international patents where {NAME} (affiliated with "
        f"{AFFIL}) is a named inventor. A patent is a filing with USPTO / "
        f"EPO / WIPO / JPO / CNIPA — NOT a research paper. Return JSON "
        f"array with: title, url, patent_number, inventors, assignee, "
        f"filing_date, grant_date, abstract, jurisdiction."
    ),
    "startups": (
        f"Find commercial ventures that {NAME} at {AFFIL} has founded or "
        f"co-founded. JSON array with: name, url (company homepage), "
        f"founded_year, one_liner, current_status, funding_total_usd, "
        f"last_funding_type, acquirer, acquisition_date, notes."
    ),
    "red_flags": (
        f"Screen for business-reputation red flags on academic researcher "
        f"**{NAME}** at {AFFIL}: paper retractions, misconduct, lawsuits, "
        f"ethics concerns, grant clawbacks, sanctions. JSON array with: "
        f"category, severity, claim, source_url, source_summary, "
        f"affected_dimensions."
    ),
}


async def _run_source(name: str, prompt: str) -> tuple[str, list, float]:
    t0 = time.monotonic()
    items = await grounded_search_json(prompt)
    return name, list(items), time.monotonic() - t0


async def _refine_item(
    item: dict[str, Any], category: str, scholar_id: str,
    client: httpx.AsyncClient,
) -> None:
    vr = await verify_item(item, context=CTX, source_category=category)
    decision = triage(item, vr, source_category=category)
    item["_verify_verdict"] = vr.verdict
    item["_category_correct"] = vr.category_correct
    item["_suggested_category"] = vr.suggested_category
    item["_triage_action"] = decision.action

    if decision.action in ("drop", "route"):
        item["_rejected"] = True
        item["_rejection_reason"] = decision.reason
        item["_refinement_status"] = "rejected"
        if decision.action == "route" and decision.destination:
            result = await accept_into(
                decision.destination, scholar_id, item,
                source_category=category,
            )
            item["_routed_to"] = decision.destination
            item["_routing_result"] = result
        return

    if vr.authoritative_url:
        for field in ("url", "source_url"):
            if field in item:
                item[field] = vr.authoritative_url
                item["_url_source"] = "grounding"
                break
    await apply_url_fallback(item, client=client)
    item["_refinement_status"] = "finalized"


async def main() -> int:
    print("Stage 1: sync path (grounded_search_json × 4 sources)",
          file=sys.stderr)
    t0 = time.monotonic()
    results = await asyncio.gather(
        *(_run_source(n, p) for n, p in PROMPTS.items())
    )
    stage1_time = time.monotonic() - t0

    all_items: list[tuple[str, dict[str, Any]]] = []
    for name, items, t in results:
        print(f"  {name}: {len(items)} items in {t:.1f}s", file=sys.stderr)
        for it in items:
            if isinstance(it, dict):
                all_items.append((name, it))

    print(f"  total {len(all_items)} items, stage-1 wall "
          f"time {stage1_time:.1f}s", file=sys.stderr)

    # Snapshot first-pass URL tiers before refinement
    pre_tier = Counter(it.get("_url_source", "n/a") for _, it in all_items)

    print(f"Stage 2: refining {len(all_items)} items (verify + triage + "
          f"url_fallback)", file=sys.stderr)
    t1 = time.monotonic()
    sem = asyncio.Semaphore(5)

    # Use a sandbox scholar id + tmp papers.json so the e2e doesn't
    # write stubs into the real dossier. We monkey-patch dossier_path
    # the same way unit tests do.
    import tempfile
    from app.services.academic import destinations as _dest_mod

    tmp_root = Path(tempfile.mkdtemp(prefix="e2e_songhan_"))
    sandbox_scholar = "e2e_song_han"
    (tmp_root / sandbox_scholar).mkdir()

    original_dossier_path = _dest_mod.dossier_path
    _dest_mod.dossier_path = lambda s: tmp_root / s  # type: ignore[assignment]

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0), follow_redirects=True,
    ) as client:

        async def _one(category: str, item: dict[str, Any]) -> None:
            async with sem:
                await _refine_item(item, category, sandbox_scholar, client)

        try:
            await asyncio.gather(
                *(_one(cat, it) for cat, it in all_items)
            )
        finally:
            _dest_mod.dossier_path = original_dossier_path

    stage2_time = time.monotonic() - t1

    # Dump the sandbox papers.json so we can inspect what routed.
    papers_sandbox = tmp_root / sandbox_scholar / "papers.json"
    if papers_sandbox.exists():
        (OUT_DIR / "song_han_e2e_papers_sandbox.json").write_text(
            papers_sandbox.read_text(), encoding="utf-8",
        )

    # Post-refine distributions
    post_tier = Counter(it.get("_url_source", "n/a") for _, it in all_items)
    status_ctr = Counter(it.get("_refinement_status") for _, it in all_items)
    decision_ctr = Counter(
        "drop" if it.get("_rejected") else "keep" for _, it in all_items
    )

    print()
    print(f"Stage 1 (sync) wall time    : {stage1_time:.1f}s")
    print(f"Stage 2 (refine) wall time  : {stage2_time:.1f}s")
    print()
    print("BEFORE refinement — URL tier distribution:")
    for k, v in pre_tier.most_common():
        print(f"  {k:<24} {v}")
    print()
    print("AFTER refinement — URL tier distribution:")
    for k, v in post_tier.most_common():
        print(f"  {k:<24} {v}")
    print()
    # Use triage action instead of keep/drop binary
    triage_ctr = Counter(it.get("_triage_action", "?") for _, it in all_items)
    print("Triage actions:")
    for k, v in triage_ctr.most_common():
        print(f"  {k:<24} {v}")
    print()
    print("Refinement status:")
    for k, v in status_ctr.most_common():
        print(f"  {k or '(unset)':<24} {v}")

    print()
    print("=== Non-KEEP items (drop + route) ===")
    for cat, it in all_items:
        if it.get("_rejected"):
            title = it.get('title') or it.get('name') or it.get('claim')
            print(f"\n[{cat}] {title}")
            print(f"  action   : {it.get('_triage_action')}")
            print(f"  reason   : {it.get('_rejection_reason')}")
            if it.get("_routed_to"):
                r = it.get("_routing_result") or {}
                print(f"  routed   : → {it['_routed_to']}")
                print(f"  accepted : {r.get('accepted')}  "
                      f"({r.get('action')})")
                print(f"  stored_id: {r.get('stored_id')}")
                print(f"  note     : {r.get('reason')}")

    # Persist raw detail
    payload = [{"category": c, **it} for c, it in all_items]
    (OUT_DIR / "song_han_e2e.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
