"""Pydantic response schemas for the scholar evaluation framework.

These schemas are used as `response_schema` arguments to
`llm_client.generate_structured` and as the persisted shape in
`evaluations/{dim_id}.jsonl`, `peer_group.jsonl`, and
`narrative.jsonl`. Matches Concept 3 (evidence contract), Concept 6
(eval file shape), Concept 2 (peer group), and Concept 7 (red flags).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


Uncertainty = Literal["low", "medium", "high"]
EvidenceWeight = Literal["primary", "supporting"]
Severity = Literal["low", "medium", "high", "critical"]
TriageDecision = Literal["material", "not_material"]


class EvidenceItem(BaseModel):
    claim: str = Field(
        description="A concrete, factual claim supporting or qualifying the score."
    )
    source: str = Field(
        description=(
            "A citable URL from the structured facts (paper URL, startup "
            "URL, patent URL, news article URL, profile URL, etc.). If no "
            "URL is available for this claim, return an empty string — do "
            "NOT return a field name, label, tag, or placeholder like "
            "'discovery_excerpt' or 'paper'."
        )
    )
    weight: EvidenceWeight


class DiffBlock(BaseModel):
    prev_score: Optional[int] = None
    delta: Optional[int] = None
    drivers: list[str] = Field(default_factory=list)


class DimEvalResult(BaseModel):
    """Per-dimension evaluation output. Persisted per Concept 6."""

    score: int = Field(ge=0, le=100)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    uncertainty: Uncertainty = "medium"
    missing_data: list[str] = Field(default_factory=list)
    mini_report: str = ""
    questions_for_investor: list[str] = Field(default_factory=list, max_length=3)
    diff_from_last: Optional[DiffBlock] = None


class TriageResult(BaseModel):
    decision: TriageDecision
    reason: str = ""


class ContextModifiers(BaseModel):
    # NOTE: no `extra="allow"` — Gemini's response_schema validator
    # rejects `additionalProperties` in any form. Fields here must
    # be the closed set the classifier can emit.
    institution_name: Optional[str] = None
    institution_tier: Optional[
        Literal["elite", "strong", "regional", "emerging"]
    ] = None
    resource_level: Optional[Literal["high", "medium", "low"]] = None
    geographic_region: Optional[str] = None
    data_availability: Literal["high", "medium", "low"] = "medium"


class PhaseClassificationResult(BaseModel):
    """Shape written as one line to peer_group.jsonl (Concept 2)."""

    field: str
    field_parent: Optional[str] = None
    cohort_size_estimate: int = 0
    cohort_examples: list[str] = Field(default_factory=list)
    academic_age: Optional[int] = None
    academic_age_adjustments: list[str] = Field(default_factory=list)
    gates_passed: list[str] = Field(default_factory=list)
    phase: Literal["R1", "R2", "R3a", "R3b", "R3c", "R4"]
    phase_evidence: list[str] = Field(default_factory=list)
    context_modifiers: ContextModifiers = Field(default_factory=ContextModifiers)
    change_reason: Optional[str] = None


class RedFlagDetection(BaseModel):
    category: str
    severity: Severity
    claim: str
    source_url: str
    source_summary: str
    affected_dimensions: list[str] = Field(default_factory=list)


class DimHighlight(BaseModel):
    """One highlight line per dimension for the narrative report.

    Open dicts (`dict[str, str]`) are not supported by Gemini's
    response_schema validator, so we use a list of typed entries.
    """

    dimension_id: Literal[
        "academic_excellence",
        "tech_transfer_experience",
        "founder_potential",
        "growth_trajectory",
    ]
    highlight: str


class NarrativeReport(BaseModel):
    """Unified cross-dim narrative written to narrative.jsonl."""

    headline: str
    summary: str
    per_dim_highlights: list[DimHighlight] = Field(default_factory=list)
    red_flag_banner: Optional[str] = None
    open_questions: list[str] = Field(default_factory=list)


# ── News relevance filtering ────────────────────────────────────────────


class NewsRelevanceItem(BaseModel):
    """Per-item relevance judgment from the filtering pass."""

    index: int
    relevant: bool
    reason: str
    duplicate_of: Optional[int] = None  # index of earlier item this duplicates


class NewsFilterResult(BaseModel):
    """Batch relevance + dedup judgment for news candidates."""

    items: list[NewsRelevanceItem]
