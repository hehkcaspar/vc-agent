"""Pydantic schemas for Academic Tracking v2 — scholar-centric API."""

import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Helpers ────────────────────────────────────────────────────


def _json_text_to_list(raw: Any) -> list[str]:
    """Parse a JSON-encoded TEXT column into a Python list of strings."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return val
        except json.JSONDecodeError:
            pass
    return []


def _json_text_to_dict(raw: Any) -> dict:
    """Parse a JSON-encoded TEXT column into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass
    return {}


# ── Scholar CRUD ──────────────────────────────────────────────


class CreateScholarRequest(BaseModel):
    name: str = Field(..., min_length=1)
    urls: list[str] = Field(..., min_length=1, description="Homepage, GS profile, or other URLs")
    tags: list[str] = Field(default_factory=list)
    tracking_priority: str = Field(default="medium", pattern=r"^(high|medium|low)$")
    entity_id: Optional[str] = None
    user_notes: Optional[str] = None


class UpdateScholarRequest(BaseModel):
    name: Optional[str] = None
    tags: Optional[list[str]] = None
    tracking_priority: Optional[str] = Field(default=None, pattern=r"^(high|medium|low)$")
    status: Optional[str] = Field(default=None, pattern=r"^(active|paused|archived)$")
    entity_id: Optional[str] = None
    user_notes: Optional[str] = None


class ScholarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str = "active"
    tracking_priority: str = "medium"
    tags: list[str] = []
    entity_id: Optional[str] = None
    dossier_path: str
    created_at: datetime
    updated_at: datetime

    # Enriched from profile.json (set by router, not ORM)
    affiliation: Optional[str] = None
    h_index: Optional[int] = None
    i10_index: Optional[int] = None
    total_citations: Optional[int] = None
    research_areas: list[str] = []
    identity: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_tags(cls, data: Any) -> Any:
        if hasattr(data, "__table__"):
            d = {col.key: getattr(data, col.key) for col in data.__table__.columns}
            d["tags"] = _json_text_to_list(d.get("tags"))
            return d
        if isinstance(data, dict):
            data["tags"] = _json_text_to_list(data.get("tags"))
        return data


class ScholarListResponse(BaseModel):
    scholars: list[ScholarResponse]
    total: int


# ── Events ────────────────────────────────────────────────────


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scholar_id: str
    event_type: str
    significance: str = "medium"
    title: Optional[str] = None
    is_read: bool = False
    event_date: Optional[datetime] = None
    created_at: datetime
    # Full payload from JSONL (set by router when expanded)
    payload: Optional[dict] = None


class UpdateEventRequest(BaseModel):
    is_read: Optional[bool] = None
    significance: Optional[str] = Field(default=None, pattern=r"^(high|medium|low)$")


# ── Channels ──────────────────────────────────────────────────


class ChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scholar_id: str
    channel_type: str
    url: Optional[str] = None
    is_active: bool = True
    polling_interval_hours: int = 168
    last_polled_at: Optional[datetime] = None
    last_changed_at: Optional[datetime] = None
    poll_error_count: int = 0
    created_at: datetime


class UpdateChannelRequest(BaseModel):
    is_active: Optional[bool] = None
    polling_interval_hours: Optional[int] = None


# ── Evaluations (read from JSON files, not SQL) ─────────────


class EvaluationDimension(BaseModel):
    score: int
    explanation: str = ""
    evidence: list[str] = []
    archetype_used: Optional[str] = None


class EvaluationDelta(BaseModel):
    vs_evaluation: Optional[str] = None
    dimension_changes: dict[str, dict[str, Any]] = {}
    new_papers_since: int = 0
    notable_events: list[str] = []


class EvaluationResponse(BaseModel):
    """Evaluation snapshot read from data/scholars/{id}/evaluations/{file}.json

    Agent output may not perfectly match the schema — fields have lenient defaults.
    """

    id: Optional[str] = None
    type: str = "full"
    trigger: str = "manual"
    model: str = ""
    created_at: Optional[str] = None

    dimensions: dict[str, EvaluationDimension] = {}
    computed_metrics: dict[str, Any] = {}
    field_context: dict[str, Any] = {}
    commercialization_signals: Any = {}  # Agent may output list or dict
    delta: Optional[EvaluationDelta] = None
    agent_trace_ref: Optional[str] = None


class EvaluationListResponse(BaseModel):
    evaluations: list[EvaluationResponse]


# ── Papers (read from papers.json) ──────────────────────────


class PaperResponse(BaseModel):
    id: Optional[str] = None
    title: str
    authors: list[dict[str, Any]] = []
    year: Optional[int] = None
    venue: Optional[str] = None
    publication_type: Optional[str] = None
    citations: int = 0
    influential_citations: int = 0
    fields_of_study: list[str] = []
    ss_paper_id: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = None
    author_position: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields_of_study(cls, data: Any) -> Any:
        """SS API may return fields_of_study as [{"category": "...", "source": "..."}]."""
        if isinstance(data, dict):
            fos = data.get("fields_of_study")
            if isinstance(fos, list):
                coerced = []
                for item in fos:
                    if isinstance(item, str):
                        coerced.append(item)
                    elif isinstance(item, dict) and "category" in item:
                        coerced.append(item["category"])
                data["fields_of_study"] = coerced
        return data


class PapersResponse(BaseModel):
    """papers.json — summary header + optional full list."""

    updated_at: Optional[str] = None
    summary: dict[str, Any] = {}
    total: int = 0
    papers: list[PaperResponse] = []


# ── Reports (read from markdown files) ──────────────────────


class ReportResponse(BaseModel):
    id: str
    filename: str
    report_type: str = "full"
    created_at: str
    content: Optional[str] = None


class ReportListResponse(BaseModel):
    reports: list[ReportResponse]


# ── Chat ─────────────────────────────────────────────────────


class AcademicChatSessionCreate(BaseModel):
    title: Optional[str] = None


class AcademicChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scholar_id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AcademicChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime


class AcademicChatSessionDetailResponse(BaseModel):
    session: AcademicChatSessionResponse
    messages: list[AcademicChatMessageResponse]


class AcademicChatMessageCreate(BaseModel):
    text: str = Field(..., min_length=1)


class AcademicChatJobAccepted(BaseModel):
    job_id: str
    user_message: AcademicChatMessageResponse
    status: str = "pending"


class AcademicChatJobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | succeeded | failed
    step_detail: Optional[str] = None
    user_message_id: Optional[str] = None
    assistant_message: Optional[AcademicChatMessageResponse] = None
    error_message: Optional[str] = None


# ── Signal Feed (enriched) ───────────────────────────────────


class SignalFeedEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scholar_id: str
    scholar_name: str = ""
    event_type: str
    significance: str = "medium"
    title: Optional[str] = None
    is_read: bool = False
    event_date: Optional[datetime] = None
    created_at: datetime


# ── Signal Feed bulk mark-read ───────────────────────────────


class MarkFeedReadRequest(BaseModel):
    event_ids: list[str] = Field(default_factory=list, description="Empty = mark all unread")


# ── Ranking ──────────────────────────────────────────────────


class RankingScholarResponse(BaseModel):
    id: str
    name: str
    affiliation: Optional[str] = None
    h_index: Optional[int] = None
    tracking_priority: str = "medium"
    status: str = "active"
    dimensions: dict[str, int] = {}
    eval_date: Optional[str] = None


class WeightPresetResponse(BaseModel):
    name: str
    weights: dict[str, float]


class CreateWeightPresetRequest(BaseModel):
    name: str = Field(..., min_length=1)
    weights: dict[str, float]


# ── Digest ───────────────────────────────────────────────────


class DigestResponse(BaseModel):
    id: str
    filename: str
    created_at: str
    content: Optional[str] = None


# ── Custom Dimensions ────────────────────────────────────────


class CustomDimensionRequest(BaseModel):
    name: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    prompt: str = Field(..., min_length=1, description="Guiding prompt for the agent")


class CustomDimensionResponse(BaseModel):
    name: str
    key: str
    prompt: str
