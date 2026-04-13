"""Layer 3 non-scoring task — classify scholar into an R1-R4 phase.

Two-pass pipeline (Concept 2):
  1. **Discovery pass** — grounded Google Search to gather raw
     evidence about grants, awards, appointments, years since PhD,
     field cohort. Output is free-form text; we do NOT try to
     structure it here because Gemini's grounded-search mode cannot
     combine `response_schema` with the google_search tool.
  2. **Synthesis pass** — pure structured output (no tools) over the
     discovery transcript, returning a typed
     `PhaseClassificationResult`.

This sidesteps the fragile regex-extract-json-from-grounded-prose
pattern and keeps the typed contract.

Writes one line per classification to `peer_group.jsonl`.
"""

from __future__ import annotations

import json
import logging

from .continuous_config import load_continuous_tasks
from .fact_store import current_state
from .file_utils import append_record, latest_record
from .llm_client import generate_structured, grounded_generate_text
from .schemas import PhaseClassificationResult

logger = logging.getLogger(__name__)


_DISCOVERY_PROMPT = """\
You are researching an academic scholar to classify their career phase.
Use Google Search to find concrete evidence for each of these items.
Do NOT return JSON — write a structured evidence dossier in prose.

Required evidence:
1. **PhD year** (or best estimate with source) and any documented
   career interruptions (parental leave, clinical training, national
   service, illness).
2. **First independent position** (tenure-track, group leader,
   permanent researcher) — year and institution. Distinguish from
   postdoc / staff scientist / long-term fellow.
3. **Major PI grants**: ERC Starting / Consolidator / Advanced, NIH
   R01, NSF CAREER, DOE Early Career, DARPA YFA, Sloan, Packard, NIH
   Director's New Innovator, DFG Emmy Noether, UKRI Future Leaders,
   VIDI/VICI, ANR JCJC, NSFC Excellent Young / Distinguished Young,
   JSPS Kakenhi A, or the local equivalent for the scholar's
   country/region. For non-US/EU scholars, explicitly note what
   counts as a major first-PI grant in that region.
4. **Tenure** (or equivalent permanence) — year.
5. **Signature result(s)** — the specific research contribution(s)
   the subfield associates with the scholar.
6. **Field-leadership markers**: field-level awards, editor-in-chief
   / major editorial boards, keynote density, named chairs, society
   fellowships, trainees running their own labs.
7. **Narrowest defensible subfield** and **3–5 named worldwide peers**
   in that subfield. If you can't name 3+ peers, step one level
   broader and say so.
8. **Institution tier** (elite / strong / regional / emerging) and
   **geographic region**.

Scholar fact store:
"""


_SYNTHESIS_PROMPT = """\
You are synthesizing a career-phase classification from an evidence
dossier. Apply the R1-R4 rules strictly.

**Gates (cumulative):**
  G1 — first independent position (tenure-track, group leader,
        permanent researcher). Staff scientist does NOT count.
  G2 — first major independent PI grant (see list in dossier).
  G3 — tenure or equivalent + second major grant + signature result.
  G4 — field-leadership markers.

**Phases:**
  R2 Recognised             | G1 not yet passed
  R3a Emerging Independent  | G1 passed, G2 not yet
  R3b Established           | G1+G2, G3 not yet
  R3c Consolidated          | G1+G2+G3
  R4 Leading                | all of G1-G4

**Conflict resolution:**
  - Gates dominate age. A 15-yr post-PhD scholar without G2 is R3a.
  - Age caps upward mobility. A 3-yr post-PhD scholar with an early
    G2 is still R3a (not R3b).
  - No skipping gates. Failing G1 means R2 regardless of age.
  - If PhD year unknown, estimate from first-publication year minus
    4 and set `academic_age_adjustments` to mention the estimate.

**Cohort:** name 3–5 worldwide peers in the narrowest defensible
subfield. If the dossier couldn't name 3, set `cohort_size_estimate`
conservatively and note the issue in `change_reason`.

**Context modifiers** do not affect the percentile; they are framing
only. Set `data_availability: low` if the dossier had thin coverage.

Return a single `PhaseClassificationResult` JSON object per the schema.

=== EVIDENCE DOSSIER ===
"""


async def run_phase_classifier(scholar_id: str) -> dict:
    """Two-pass discovery → synthesis classifier. Appends to peer_group.jsonl."""
    cfg = load_continuous_tasks()
    task = cfg.phase_classifier
    if not task.enabled:
        return {"skipped": True, "reason": "disabled"}

    snap = current_state(scholar_id)
    fact_block = json.dumps(
        {
            "name": (snap.profile or {}).get("name"),
            "affiliation": (snap.profile or {}).get("affiliation"),
            "identity": (snap.profile or {}).get("identity"),
            "metrics": (snap.profile or {}).get("metrics"),
            "research_areas": (snap.profile or {}).get("research_areas"),
            "attributed_metrics": snap.attributed_metrics,
            "paper_count": len(snap.papers),
            "top_papers": sorted(
                snap.papers,
                key=lambda p: int(p.get("citations") or 0),
                reverse=True,
            )[:15],
        },
        ensure_ascii=False,
        default=str,
        indent=2,
    )

    # ── Pass 1 — grounded discovery (text only) ──────────────────
    try:
        dossier_text = await grounded_generate_text(
            [_DISCOVERY_PROMPT, fact_block],
            model=task.classifier_model,
        )
    except Exception as e:
        logger.exception("phase_classifier: discovery pass failed for %s", scholar_id)
        return {"error": f"discovery_failed: {e}"}

    if not dossier_text.strip():
        logger.warning("phase_classifier: empty dossier for %s", scholar_id)
        return {"error": "empty_discovery_response"}

    # ── Pass 2 — typed synthesis (no tools) ──────────────────────
    try:
        result = await generate_structured(
            model=task.classifier_model,
            prompt_parts=[_SYNTHESIS_PROMPT, dossier_text],
            response_schema=PhaseClassificationResult,
        )
    except Exception as e:
        logger.exception("phase_classifier: synthesis pass failed for %s", scholar_id)
        return {"error": f"synthesis_failed: {e}"}

    prev = latest_record(scholar_id, "peer_group") or {}
    record = {
        **result.model_dump(),
        "prev_id": prev.get("id") if prev else None,
        "discovery_excerpt": dossier_text[:2000],
    }
    record_id = await append_record(scholar_id, "peer_group", record)
    return {**record, "id": record_id}
