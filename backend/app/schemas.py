import json
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import class_mapper


def metadata_json_to_dict(raw: Optional[str]) -> Optional[dict[str, Any]]:
    """Parse DB metadata_json TEXT into a single JSON object; invalid or non-object → None."""
    if raw is None or not str(raw).strip():
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def orm_row_with_metadata_dict(row: Any) -> dict[str, Any]:
    m = class_mapper(row.__class__)
    d = {col.key: getattr(row, col.key) for col in m.columns}
    mj = d.pop("metadata_json", None)
    d["metadata"] = metadata_json_to_dict(mj if isinstance(mj, str) else None)
    return d


def coerce_orm_metadata_before_model(data: Any) -> Any:
    if isinstance(data, dict):
        if "metadata_json" in data:
            out = {k: v for k, v in data.items() if k != "metadata_json"}
            mj = data.get("metadata_json")
            out["metadata"] = metadata_json_to_dict(
                mj if isinstance(mj, str) else None
            )
            return out
        return data
    if getattr(data, "__table__", None) is not None:
        return orm_row_with_metadata_dict(data)
    return data


# ============== Entity Schemas ==============

class EntityBase(BaseModel):
    name: str
    website: Optional[str] = None


class EntityCreate(EntityBase):
    pass


class EntityUpdate(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None


class EntityResponse(EntityBase):
    id: str
    type: str = "company"
    status: str = "active"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== Resource Schemas ==============

class ResourceBase(BaseModel):
    title: str
    resource_type: Literal["file", "text", "url"]


class ResourceCreate(ResourceBase):
    entity_id: str
    mime_type: Optional[str] = None
    original_filename: Optional[str] = None
    relative_path: str
    url: Optional[str] = None
    origin_ingest_id: Optional[str] = None


class ResourceResponse(ResourceBase):
    id: str
    entity_id: str
    mime_type: Optional[str]
    original_filename: Optional[str]
    relative_path: str
    url: Optional[str]
    origin_ingest_id: Optional[str]
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_metadata(cls, data: Any) -> Any:
        return coerce_orm_metadata_before_model(data)


# ============== Artifact Schemas ==============

class ArtifactBase(BaseModel):
    artifact_type: Literal["memo", "factsheet", "report", "other"]


class ArtifactCreate(ArtifactBase):
    entity_id: str
    relative_path: str
    version: int = 1
    status: Literal["draft", "final"] = "draft"


class ArtifactResponse(ArtifactBase):
    id: str
    entity_id: str
    title: Optional[str] = None
    version: int
    status: str
    relative_path: str
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_metadata(cls, data: Any) -> Any:
        return coerce_orm_metadata_before_model(data)


# ============== Metadata pre-process (in-memory jobs) ==============


class MetadataPreprocessStart(BaseModel):
    target: Literal["resource", "artifact"]
    id: str


class MetadataPreprocessAccepted(BaseModel):
    job_id: str


class MetadataPreprocessJobStatus(BaseModel):
    job_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    error_message: Optional[str] = None


# ============== IngestItem Schemas ==============

class IngestItemResponse(BaseModel):
    ingest_id: str
    source: str
    status: str
    parkinglot_path: str
    entity_hint_name: Optional[str]
    entity_hint_domain: Optional[str]
    error: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== Ingestion Request/Response ==============

class IngestResourcesRequest(BaseModel):
    text: Optional[str] = None
    urls: Optional[List[str]] = None
    entity_id: Optional[str] = None
    entity_hint_name: Optional[str] = None
    entity_hint_domain: Optional[str] = None


class IngestSuccessResponse(BaseModel):
    status: Literal["resolved"]
    entity_id: str
    resources: List[ResourceResponse]


class IngestResolutionRequiredResponse(BaseModel):
    status: Literal["resolution_required"]
    ingest_id: str
    candidates: List[EntityResponse]


class IngestFailedResponse(BaseModel):
    status: Literal["failed"]
    ingest_id: str
    error: str


# ============== Parking Lot Resolution ==============

class ResolveToExistingRequest(BaseModel):
    entity_id: str


class ResolveToNewRequest(BaseModel):
    create_entity: dict = Field(..., example={"name": "New Company"})


class ResolveRequest(BaseModel):
    entity_id: Optional[str] = None
    create_entity: Optional[dict] = None


# ============== Entity chat ==============

class ChatSessionCreate(BaseModel):
    title: Optional[str] = None


class ChatSessionResponse(BaseModel):
    id: str
    entity_id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class ChatSessionDetailResponse(BaseModel):
    session: ChatSessionResponse
    messages: List[ChatMessageResponse]


class ChatMessageCreate(BaseModel):
    text: str
    resource_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    model_profile_id: Optional[str] = None
    # When set, overrides server CHAT_USE_DEEP_AGENT for this message only.
    use_deep_agent: Optional[bool] = None


class ChatMessageResult(BaseModel):
    assistant_message: ChatMessageResponse
    warnings: List[str] = Field(default_factory=list)
    run_id: Optional[str] = None
    tool_trace: Optional[dict] = None


class ChatMessageJobAccepted(BaseModel):
    """Deep-agent message accepted; poll GET .../jobs/{job_id} until status is terminal."""

    job_id: str
    user_message: ChatMessageResponse
    warnings: List[str] = Field(default_factory=list)
    status: Literal["pending"] = "pending"


class ChatMessageJobStatus(BaseModel):
    job_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    step_detail: Optional[str] = None
    user_message_id: str
    assistant_message: Optional[ChatMessageResponse] = None
    warnings: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    run_id: Optional[str] = None
    tool_trace: Optional[dict] = None


class PresetInfoResponse(BaseModel):
    id: str
    label: str
    description: str


class PresetRunRequest(BaseModel):
    resource_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    use_deep_agent: Optional[bool] = None
    industry: Optional[str] = None
    stage: Optional[str] = None
    artifact_type: Optional[Literal["memo", "factsheet", "report", "other"]] = None
    artifact_status: Optional[Literal["draft", "final"]] = None


class PresetRunResponse(BaseModel):
    artifact_id: str
    assistant_summary: str
    warnings: List[str] = Field(default_factory=list)
