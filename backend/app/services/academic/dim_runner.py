"""Generic Layer 3 dimension evaluation task.

Pipeline per Concept 6:
  1. Load dim config + prompt
  2. Build context bundle from fact store + peer group + last eval + red flags
  3. Cheap triage call → material | not_material
  4. If material, full scoring call with DimEvalResult schema
  5. Apply red-flag caps
  6. Append one line to evaluations/{dim_id}.jsonl

The same runner is invoked for all 4 dims; per-dim behavior lives in
`dimensions.json` (prompt) + `continuous_tasks.json` (models,
required_sources, cadence).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .continuous_config import ContinuousTasksConfig, load_continuous_tasks
from .dimensions import _SCOREABLE_GUIDANCE, read_dimensions
from .fact_store import (
    FactStoreSnapshot,
    apply_red_flag_caps,
    current_state,
    last_snapshot_for_source,
)
from .file_utils import append_record, latest_record, read_records
from .llm_client import generate_structured
from .schemas import DimEvalResult, TriageResult

logger = logging.getLogger(__name__)


def _dim_prompt(dim_id: str) -> str | None:
    for d in read_dimensions():
        if d.get("key") == dim_id or d.get("id") == dim_id:
            base = d.get("prompt") or d.get("system_prompt")
            return (base + _SCOREABLE_GUIDANCE) if base else None
    return None


def _compose_fact_context(snap: FactStoreSnapshot) -> str:
    """Render the fact-store snapshot into a prompt-friendly block."""
    profile_min = {
        "name": snap.profile.get("name"),
        "affiliation": snap.profile.get("affiliation"),
        "metrics": snap.profile.get("metrics"),
        "research_areas": snap.profile.get("research_areas"),
        "identity": snap.profile.get("identity"),
    }
    return json.dumps(
        {
            "profile": profile_min,
            "attributed_metrics": snap.attributed_metrics,
            "paper_count": len(snap.papers),
            "top_papers_by_citations": sorted(
                snap.papers,
                key=lambda p: int(p.get("citations") or 0),
                reverse=True,
            )[:20],
            "patents_count": len(snap.patents),
            "startups_count": len(snap.startups),
            "patents": snap.patents[:20],
            "startups": snap.startups[:20],
        },
        ensure_ascii=False,
        default=str,
        indent=2,
    )


_PEER_GROUP_INTERNAL_KEYS = {"discovery_excerpt", "prev_id", "id"}


def _compose_peer_group_context(snap: FactStoreSnapshot) -> str:
    if not snap.peer_group:
        return "PEER GROUP: not yet classified. Use your best subfield guess, mark uncertainty: high."
    # Strip internal metadata before sending to the scorer. The
    # phase_classifier stamps `discovery_excerpt` (a 2000-char dossier
    # snippet) onto each peer_group record for auditability — Gemini
    # was picking that field name as the literal `source` value in
    # every EvidenceItem because it was the only "source-looking" label
    # in its context.
    cleaned = {k: v for k, v in snap.peer_group.items() if k not in _PEER_GROUP_INTERNAL_KEYS}
    return "PEER GROUP:\n" + json.dumps(cleaned, ensure_ascii=False, default=str, indent=2)


def _compose_last_eval_context(scholar_id: str, dim_id: str) -> str:
    last = latest_record(scholar_id, f"evaluations/{dim_id}")
    if not last or last.get("triage_decision") == "not_material":
        last_scored = _last_scored_eval(scholar_id, dim_id)
        if not last_scored:
            return "LAST EVAL: none (first run)"
        return "LAST EVAL:\n" + json.dumps(last_scored, ensure_ascii=False, default=str, indent=2)
    return "LAST EVAL:\n" + json.dumps(last, ensure_ascii=False, default=str, indent=2)


def _last_scored_eval(scholar_id: str, dim_id: str) -> dict[str, Any] | None:
    recs = read_records(scholar_id, f"evaluations/{dim_id}")
    for r in reversed(recs):
        if isinstance(r.get("score"), (int, float)) and r.get("scoreable", True):
            return r
    return None


def _compose_red_flags_context(snap: FactStoreSnapshot, dim_id: str) -> str:
    flags = [
        f
        for f in snap.red_flags_active
        if dim_id in (f.get("affected_dimensions") or [])
    ]
    if not flags:
        return "ACTIVE RED FLAGS: none"
    return "ACTIVE RED FLAGS:\n" + json.dumps(flags, ensure_ascii=False, default=str, indent=2)


_TRIAGE_PROMPT = (
    "You are the triage gate for a Layer 3 dimension evaluation. Given "
    "the fact-store context and the last scored evaluation for this "
    "dimension, decide whether there is **material new evidence** that "
    "would likely change the score or its confidence. Be decisive. "
    "Return `decision: material` if any of: new high-signal papers, "
    "new commercial events, new recognition, new red flags, big metric "
    "shifts. Return `decision: not_material` if nothing meaningful has "
    "changed since the last scored run. Provide a 1-sentence reason."
)


async def run_dim_eval(
    scholar_id: str,
    dim_id: str,
    *,
    cfg: ContinuousTasksConfig | None = None,
    force_score: bool = False,
) -> dict[str, Any]:
    """Run one dim evaluation cycle. Appends one line to the eval log."""
    cfg = cfg or load_continuous_tasks()
    dim_cfg = cfg.dimensions.get(dim_id)
    if dim_cfg is None or not dim_cfg.enabled:
        logger.info("dim_runner: %s disabled or unknown, skipping", dim_id)
        return {"skipped": True, "reason": "disabled_or_unknown"}

    prompt = _dim_prompt(dim_id)
    if not prompt:
        return {"skipped": True, "reason": "no_prompt"}

    snap = current_state(scholar_id)
    fact_ctx = _compose_fact_context(snap)
    peer_ctx = _compose_peer_group_context(snap)
    last_ctx = _compose_last_eval_context(scholar_id, dim_id)
    red_ctx = _compose_red_flags_context(snap, dim_id)

    # Audit snapshot id = most recent snapshot across *all* required
    # sources this dim reads, so the id genuinely represents the
    # consistency point of the fact-store state fed to the LLM.
    snapshot_id = ""
    for src in dim_cfg.required_sources:
        s = last_snapshot_for_source(scholar_id, src)
        if s and s.get("id", "") > snapshot_id:
            snapshot_id = s["id"]

    # ── Triage ────────────────────────────────────────────────────
    # Cold-start optimization: when there's no prior scored eval
    # there's nothing to diff against, so skip triage and go
    # straight to scoring. This saves one LLM call per dim on the
    # first bootstrap.
    has_prior_scored = _last_scored_eval(scholar_id, dim_id) is not None
    if not force_score and has_prior_scored:
        try:
            triage = await generate_structured(
                model=dim_cfg.triage_model,
                prompt_parts=[_TRIAGE_PROMPT, fact_ctx, last_ctx, red_ctx],
                response_schema=TriageResult,
            )
        except Exception as e:
            logger.exception("dim_runner: triage failed for %s/%s", scholar_id, dim_id)
            triage = TriageResult(decision="material", reason=f"triage_error:{e}")

        if triage.decision == "not_material":
            record_id = await append_record(
                scholar_id,
                f"evaluations/{dim_id}",
                {
                    "dimension_id": dim_id,
                    "snapshot_id": snapshot_id,
                    "peer_group_ref": (snap.peer_group or {}).get("id"),
                    "triage_decision": "not_material",
                    "triage_reason": triage.reason,
                },
            )
            return {
                "dimension_id": dim_id,
                "record_id": record_id,
                "triage_decision": "not_material",
            }

    # ── Scoring ───────────────────────────────────────────────────
    try:
        result = await generate_structured(
            model=dim_cfg.scoring_model,
            prompt_parts=[prompt, fact_ctx, peer_ctx, last_ctx, red_ctx],
            response_schema=DimEvalResult,
        )
    except Exception as e:
        logger.exception("dim_runner: scoring failed for %s/%s", scholar_id, dim_id)
        record_id = await append_record(
            scholar_id,
            f"evaluations/{dim_id}",
            {
                "dimension_id": dim_id,
                "snapshot_id": snapshot_id,
                "peer_group_ref": (snap.peer_group or {}).get("id"),
                "triage_decision": "material",
                "error": str(e),
            },
        )
        return {"dimension_id": dim_id, "record_id": record_id, "error": str(e)}

    # Score 0 is the LLM sentinel for "insufficient evidence to evaluate".
    # Convert to scoreable=False / score=None — skip red-flag caps.
    if result.score == 0:
        payload = {
            "dimension_id": dim_id,
            "scholar_id": scholar_id,
            "snapshot_id": snapshot_id,
            "peer_group_ref": (snap.peer_group or {}).get("id"),
            "triage_decision": "material",
            "scoreable": False,
            "score": None,
            "evidence": [e.model_dump() for e in result.evidence],
            "uncertainty": result.uncertainty,
            "missing_data": list(result.missing_data),
            "mini_report": result.mini_report,
            "questions_for_investor": result.questions_for_investor,
            "diff_from_last": (
                result.diff_from_last.model_dump() if result.diff_from_last else None
            ),
        }
        record_id = await append_record(
            scholar_id, f"evaluations/{dim_id}", payload
        )
        payload["id"] = record_id
        return payload

    # Apply red-flag caps before persisting.
    capped_score, flag_notes, forced_unc = apply_red_flag_caps(
        result.score, dim_id, scholar_id
    )
    final_uncertainty = forced_unc or result.uncertainty
    missing = list(result.missing_data)
    if flag_notes:
        missing.extend(flag_notes)

    payload = {
        "dimension_id": dim_id,
        "scholar_id": scholar_id,
        "snapshot_id": snapshot_id,
        "peer_group_ref": (snap.peer_group or {}).get("id"),
        "triage_decision": "material",
        "scoreable": True,
        "score": capped_score,
        "score_before_caps": result.score,
        "evidence": [e.model_dump() for e in result.evidence],
        "uncertainty": final_uncertainty,
        "missing_data": missing,
        "mini_report": result.mini_report,
        "questions_for_investor": result.questions_for_investor,
        "diff_from_last": (
            result.diff_from_last.model_dump() if result.diff_from_last else None
        ),
    }

    record_id = await append_record(
        scholar_id, f"evaluations/{dim_id}", payload
    )
    payload["id"] = record_id
    return payload
