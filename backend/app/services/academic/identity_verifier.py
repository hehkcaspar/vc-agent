"""LLM-based per-source identity verification.

Every high-signal identity source (Google Scholar, Semantic Scholar,
ORCID, homepage) must pass through `verify_source_candidate` before
the resolver commits it to `profile.json`. The old `verify_ss_metrics`
ratio gate in `tool_utils.py` is kept as a cheap pre-filter, but it
is no longer the final word — a zero-metric candidate that the old
gate accepted (see the Hannah Ritchie case) is rejected here because
the LLM actually reads the candidate's name, affiliation, and top
papers and decides whether it belongs to the scholar under question.

Three properties the caller relies on:

1. **Source-independent.** Verification never requires another source
   as an "anchor". Context comes from the scholar's own name /
   affiliation / research areas plus any previously-verified sources
   from the same resolve pass — so "GS only", "SS only", "both", and
   "neither" all work.
2. **Persistent rejection.** When the LLM rejects a candidate the
   resolver appends the id to `profile.rejected_identity[source_type]`
   so future resolve passes skip it before ever reaching the LLM.
3. **In-run caching.** Multiple tiers (parsed-URL, Tier 1, Tier 2)
   can surface the same candidate. The verifier caches verdicts by
   `(source_type, candidate_id)` for the lifetime of one `Verifier`
   instance so we don't pay twice for the same LLM call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from ...config import settings
from .llm_client import generate_structured

logger = logging.getLogger(__name__)


# ── Schemas ─────────────────────────────────────────────────────────


class IdentityVerdict(BaseModel):
    """Structured LLM verdict for one (scholar, candidate) pair."""

    match: bool = Field(
        description=(
            "True only if the LLM is confident this candidate and the "
            "scholar are the same person."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-reported confidence in [0, 1].",
    )
    reason: str = Field(
        description=(
            "Short free-text justification. One or two sentences. "
            "Cite the evidence that drove the decision (e.g. "
            "'affiliation matches Oxford, top papers are on climate "
            "change as expected')."
        )
    )


class ScholarContext(BaseModel):
    """Everything the verifier needs to know about the scholar.

    Built once per resolve pass and reused across source verifications.
    """

    name: str
    aliases: list[str] = Field(default_factory=list)
    affiliation_current: Optional[str] = None
    affiliation_department: Optional[str] = None
    affiliation_past: list[str] = Field(default_factory=list)
    research_areas: list[str] = Field(default_factory=list)
    # Already-verified sources from this resolve pass. Gives the LLM
    # cross-source context (e.g. verifying SS after GS has been
    # verified — it sees GS's affiliation + top papers).
    already_verified: dict[str, dict[str, Any]] = Field(default_factory=dict)
    user_notes: Optional[str] = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "ScholarContext":
        aff = profile.get("affiliation") or {}
        return cls(
            name=profile.get("name") or "",
            aliases=list(profile.get("aliases") or []),
            affiliation_current=aff.get("current"),
            affiliation_department=aff.get("department"),
            affiliation_past=list(aff.get("past") or []),
            research_areas=list(profile.get("research_areas") or []),
            already_verified={},
            user_notes=profile.get("user_notes"),
        )


# ── Public API ──────────────────────────────────────────────────────


# Sources that get routed through the LLM verifier. Other sources
# are committed with heuristic confidence and surfaced in the UI for
# manual review. Mirror of `tool_utils.HIGH_SIGNAL_IDENTITY_SOURCES`
# (kept in sync manually — the resolver imports from tool_utils).
HIGH_SIGNAL_SOURCES: frozenset[str] = frozenset(
    {"google_scholar", "semantic_scholar", "orcid", "homepage"}
)

# Commit threshold below which a match verdict is still written but
# marked `llm_low_confidence` so the UI can flag it for review.
CONFIDENCE_VERIFIED_THRESHOLD = 0.6


class IdentityVerifier:
    """Stateful verifier with a per-run cache."""

    def __init__(self, ctx: ScholarContext) -> None:
        self.ctx = ctx
        self._cache: dict[tuple[str, str], IdentityVerdict] = {}

    async def verify(
        self,
        source_type: str,
        candidate: dict[str, Any],
        enrichment: dict[str, Any],
    ) -> IdentityVerdict:
        """Return the LLM verdict for a single candidate.

        `candidate` is the minimal metadata we know up-front (id, url,
        name as reported by the platform). `enrichment` is whatever
        the caller fetched to help the LLM — top papers, affiliation
        strings, bio text, etc. Both are serialised into the prompt;
        there is no fixed schema so the caller is free to include any
        fields the model might find useful.
        """
        cache_key = (source_type, str(candidate.get("id") or candidate.get("url") or ""))
        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt = _build_prompt(self.ctx, source_type, candidate, enrichment)
        try:
            verdict = await generate_structured(
                model=settings.ACADEMIC_GEMINI_MODEL,
                prompt_parts=[prompt],
                response_schema=IdentityVerdict,
                system_instruction=_SYSTEM_INSTRUCTION,
            )
        except Exception as e:  # noqa: BLE001
            # Soft-fail: if the LLM is unreachable we do NOT commit
            # blindly. Return a rejection so the resolver tries the
            # next tier / candidate. An all-sources rejection means
            # the profile stays GS-only (or empty) for this run and
            # the user can correct via the Profiles tab.
            logger.warning(
                "identity_verifier: LLM call failed for %s candidate %s: %s",
                source_type,
                cache_key[1],
                e,
            )
            verdict = IdentityVerdict(
                match=False,
                confidence=0.0,
                reason=f"llm_unavailable: {e}",
            )

        self._cache[cache_key] = verdict
        logger.info(
            "identity_verifier: %s/%s → match=%s conf=%.2f (%s)",
            source_type,
            cache_key[1],
            verdict.match,
            verdict.confidence,
            verdict.reason[:120],
        )
        return verdict


def commit_label(verdict: IdentityVerdict, source_type: str) -> tuple[str, str]:
    """Return `(confidence_label, verified_by)` for a matched verdict.

    Callers should only invoke this when `verdict.match` is True.
    Low-confidence matches are still committed but tagged so the UI
    can render an amber "review me" badge.
    """
    if not verdict.match:
        raise ValueError("commit_label called on a non-matched verdict")
    if verdict.confidence >= CONFIDENCE_VERIFIED_THRESHOLD:
        return "verified", f"llm_verified:{source_type}"
    return "low", f"llm_low_confidence:{source_type}"


def build_rejection_entry(
    candidate: dict[str, Any], verdict: IdentityVerdict
) -> dict[str, Any]:
    """Shape a `rejected_identity[source_type]` list element."""
    return {
        "id": candidate.get("id"),
        "url": candidate.get("url"),
        "rejected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": verdict.reason,
        "rejected_by": "llm_verifier",
    }


def is_rejected(
    rejected_identity: dict[str, list[dict[str, Any]]],
    source_type: str,
    candidate_id: Optional[str],
) -> bool:
    """Check if a candidate id is on the persistent rejection list."""
    if not candidate_id:
        return False
    entries = rejected_identity.get(source_type) or []
    target = str(candidate_id)
    for entry in entries:
        if str(entry.get("id") or "") == target:
            return True
    return False


def append_rejection(
    rejected_identity: dict[str, list[dict[str, Any]]],
    source_type: str,
    entry: dict[str, Any],
) -> None:
    """Append a new rejection, deduping by id."""
    if not entry.get("id"):
        # Without a stable id we can't dedupe — skip to keep the
        # list useful. The verdict is still logged.
        return
    lst = rejected_identity.setdefault(source_type, [])
    target = str(entry["id"])
    for existing in lst:
        if str(existing.get("id") or "") == target:
            return
    lst.append(entry)


# ── Prompt construction ────────────────────────────────────────────


_SYSTEM_INSTRUCTION = (
    "You are an identity-verification assistant for an academic "
    "scholar-tracking system. Given a scholar profile and a candidate "
    "public profile from one specific platform (Google Scholar, "
    "Semantic Scholar, ORCID, or personal homepage), decide whether "
    "the candidate is the same person as the scholar. "
    "\n\n"
    "Use contradictions as the primary rejection signal: wrong field, "
    "wrong institution, metrics wildly inconsistent with a real senior "
    "scholar, top papers clearly from a different discipline, or a "
    "different canonical name. Reject on any of these. "
    "\n\n"
    "When the scholar context is thin (only a name, no affiliation / "
    "research areas yet — this is normal for fresh scholars being "
    "bootstrapped for the first time), do NOT over-reject. Lean "
    "toward match=true if the candidate has internally consistent "
    "data and nothing in it contradicts a plausible real person with "
    "the given name. In that case, return a moderate confidence "
    "(0.55–0.75) and cite what you verified and what you couldn't. "
    "\n\n"
    "For common names (e.g. 'John Smith', 'Michael Chen', 'Hannah "
    "Ritchie'), require at least one strong corroborating signal — "
    "affiliation match, research-area match, or a distinctive top "
    "paper — before accepting. Reply strictly in the structured JSON "
    "schema."
)


def _build_prompt(
    ctx: ScholarContext,
    source_type: str,
    candidate: dict[str, Any],
    enrichment: dict[str, Any],
) -> str:
    """Pack all evidence into a single prompt string.

    Kept compact: the model is fast (gemini-3-flash) and the payload
    is small. We don't try to trim anything below ~1 KB; longer
    enrichment (e.g. homepage HTML) is the caller's job to truncate.
    """
    scholar_block = {
        "name": ctx.name,
        "aliases": ctx.aliases,
        "affiliation_current": ctx.affiliation_current,
        "affiliation_department": ctx.affiliation_department,
        "affiliation_past": ctx.affiliation_past,
        "research_areas": ctx.research_areas,
        "user_notes": ctx.user_notes,
    }
    already_block = {
        k: {
            "id": v.get("id"),
            "url": v.get("url"),
            "verified_by": v.get("verified_by"),
        }
        for k, v in ctx.already_verified.items()
    }

    return (
        f"# Task\n"
        f"Decide whether this candidate {source_type} profile belongs "
        f"to the scholar described below.\n\n"
        f"# Scholar (what we already know)\n"
        f"{json.dumps(scholar_block, ensure_ascii=False, indent=2)}\n\n"
        f"# Already-verified sources (same resolve pass)\n"
        f"{json.dumps(already_block, ensure_ascii=False, indent=2)}\n\n"
        f"# Candidate ({source_type})\n"
        f"{json.dumps(candidate, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"# Enrichment (fetched evidence about the candidate)\n"
        f"{json.dumps(enrichment, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"# Decision rubric\n"
        f"- match=true when the candidate is plausibly the same person "
        f"and nothing contradicts it. Strong corroboration (affiliation / "
        f"research-area / top-paper alignment) gives high confidence "
        f"(0.85+). Consistent but sparse context gives moderate "
        f"confidence (0.55–0.75).\n"
        f"- match=false ONLY on direct contradiction: wrong field, wrong "
        f"institution, wrong research area, wildly inconsistent metrics, "
        f"or an empty profile that suggests a different person with the "
        f"same name.\n"
        f"- A well-curated platform profile (real affiliation, real "
        f"papers, real metrics) for a plausibly-named person is enough "
        f"to accept even when the scholar context is thin — a fresh "
        f"scholar may not have research areas populated yet.\n"
        f"- confidence reflects how strongly the evidence supports your "
        f"decision. Cap high confidence on thin evidence.\n"
        f"- reason: one or two sentences citing the decisive evidence.\n"
    )
