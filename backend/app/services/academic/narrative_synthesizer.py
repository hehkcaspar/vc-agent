"""Cross-dim narrative synthesizer.

Reads the latest per-dim eval for every enabled dimension, plus the
active red flags and peer group, and produces a unified narrative
report. NOT a dim — cannot trigger refreshes, only reads existing
state. Writes one line per run to `narrative.jsonl`.
"""

from __future__ import annotations

import json
import logging

from .continuous_config import load_continuous_tasks
from .fact_store import current_state
from .file_utils import append_record, read_records
from .llm_client import generate_structured
from .schemas import NarrativeReport

logger = logging.getLogger(__name__)


def _latest_eval(scholar_id: str, dim_id: str) -> dict | None:
    """Latest eval record — scored or not-scoreable (skips triage-only/error)."""
    for rec in reversed(read_records(scholar_id, f"evaluations/{dim_id}")):
        if isinstance(rec.get("score"), (int, float)) and rec.get("scoreable", True):
            return rec
        if rec.get("scoreable") is False:
            return rec
    return None


_PROMPT = """\
You are the narrative synthesizer for a scholar evaluation framework.
Read the latest scored evaluation for each dimension and produce a
concise VC-facing narrative report.

Guidelines:
- Start with a 1-sentence headline capturing the scholar's investment
  character (e.g. 'accelerating domain leader with strong commercial
  bridge, gated by one unverified venture').
- Summary: 4-8 sentences.
- For each dim, one highlight line in `per_dim_highlights` keyed by
  the dim id.
- If any active red flags exist, surface them prominently in
  `red_flag_banner`. Critical-severity flags MUST be first sentence.
- `open_questions`: collect the `questions_for_investor` from each dim
  (deduplicate, max 8).
- Some dimensions may have `"score": null` and `"scoreable": false` —
  this means insufficient evidence to evaluate. Acknowledge this in the
  narrative (e.g. "Tech-transfer could not be scored due to no commercial
  activity on record") rather than treating it as a zero.

Scholar state:
"""


async def run_narrative_synthesizer(scholar_id: str) -> dict:
    cfg = load_continuous_tasks()
    task = cfg.narrative_synthesizer
    if not task.enabled:
        return {"skipped": True, "reason": "disabled"}

    snap = current_state(scholar_id)
    dim_latest = {
        dim_id: _latest_eval(scholar_id, dim_id)
        for dim_id, dim in cfg.dimensions.items()
        if dim.enabled
    }

    state_block = json.dumps(
        {
            "name": (snap.profile or {}).get("name"),
            "peer_group": snap.peer_group,
            "active_red_flags": snap.red_flags_active,
            "dim_latest": dim_latest,
        },
        ensure_ascii=False,
        default=str,
        indent=2,
    )

    try:
        result = await generate_structured(
            model=task.model,
            prompt_parts=[_PROMPT, state_block],
            response_schema=NarrativeReport,
        )
    except Exception as e:
        logger.exception("narrative_synthesizer: failed for %s", scholar_id)
        return {"error": str(e)}

    # Post-process: dedupe open_questions while preserving order, cap at 8.
    # The prompt asks the model to do this but we enforce it server-side
    # so a misbehaving model can't bloat the report.
    seen_q: set[str] = set()
    deduped: list[str] = []
    for q in result.open_questions:
        key = q.strip().lower()
        if key and key not in seen_q:
            seen_q.add(key)
            deduped.append(q.strip())
        if len(deduped) >= 8:
            break
    result.open_questions = deduped

    record_id = await append_record(
        scholar_id, "narrative", result.model_dump()
    )
    return {**result.model_dump(), "id": record_id}
