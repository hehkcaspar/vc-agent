from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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
    version: int
    status: str
    relative_path: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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
