import json
from datetime import datetime
from typing import Any, List, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Entity, Resource, Artifact
from app.schemas import (
    EntityCreate, 
    EntityUpdate, 
    EntityResponse,
    ResourceResponse,
    ArtifactResponse,
    ArtifactCreate
)
from app.services.storage import storage
from app.services.artifact_service import create_artifact_for_entity
import aiofiles
import mimetypes
import os

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=List[EntityResponse])
async def list_entities(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """List all entities."""
    result = await db.execute(
        select(Entity)
        .order_by(Entity.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    entities = result.scalars().all()
    return entities


@router.post("", response_model=EntityResponse)
async def create_entity(
    entity: EntityCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new entity."""
    db_entity = Entity(
        name=entity.name,
        website=entity.website,
        type="company",
        status="active"
    )
    db.add(db_entity)
    await db.commit()
    await db.refresh(db_entity)
    return db_entity


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific entity by ID."""
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update_entity(
    entity_id: str,
    entity_update: EntityUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update an entity."""
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    update_data = entity_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(entity, field, value)
    
    await db.commit()
    await db.refresh(entity)
    return entity


@router.delete("/{entity_id}")
async def delete_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete an entity."""
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    await db.delete(entity)
    await db.commit()
    return {"message": "Entity deleted successfully"}


@router.get("/{entity_id}/resources", response_model=List[ResourceResponse])
async def get_entity_resources(
    entity_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get all resources for an entity."""
    # Verify entity exists
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get resources
    result = await db.execute(
        select(Resource)
        .where(Resource.entity_id == entity_id)
        .order_by(Resource.created_at.desc())
    )
    resources = result.scalars().all()
    return resources


@router.get("/{entity_id}/artifacts", response_model=List[ArtifactResponse])
async def get_entity_artifacts(
    entity_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get all artifacts for an entity."""
    # Verify entity exists
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get artifacts
    result = await db.execute(
        select(Artifact)
        .where(Artifact.entity_id == entity_id)
        .order_by(Artifact.created_at.desc())
    )
    artifacts = result.scalars().all()
    return artifacts


@router.post("/{entity_id}/artifacts", response_model=ArtifactResponse)
async def create_artifact(
    entity_id: str,
    artifact_type: Literal["memo", "factsheet", "report", "other"] = Form(...),
    content: str = Form(...),
    status: Literal["draft", "final"] = Form(default="draft"),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new artifact for an entity.
    
    - **artifact_type**: Type of artifact (memo, factsheet, report, other)
    - **content**: Markdown content of the artifact
    - **status**: draft or final
    """
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Entity not found")

    try:
        artifact = await create_artifact_for_entity(
            db, entity_id, artifact_type, content, status
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Entity not found")
    return artifact


@router.get("/{entity_id}/resources/{resource_id}/view")
async def view_resource(
    entity_id: str,
    resource_id: str,
    db: AsyncSession = Depends(get_db)
):
    """View/download a resource file."""
    # Verify entity exists
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get resource
    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.entity_id == entity_id
        )
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    
    # For URL resources, return the URL
    if resource.resource_type == "url":
        return {"url": resource.url, "type": "url"}
    
    # For file/text resources, serve the file
    if not resource.relative_path:
        raise HTTPException(status_code=404, detail="File path not found")
    
    full_path = storage.get_full_path(resource.relative_path)
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    # Guess content type
    content_type = resource.mime_type or mimetypes.guess_type(str(full_path))[0] or 'application/octet-stream'
    
    return FileResponse(
        path=str(full_path),
        media_type=content_type,
        filename=resource.original_filename or resource.title
    )


@router.get("/{entity_id}/artifacts/{artifact_id}/view")
async def view_artifact(
    entity_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_db)
):
    """View an artifact's markdown content."""
    # Verify entity exists
    result = await db.execute(
        select(Entity).where(Entity.id == entity_id)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get artifact
    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.entity_id == entity_id
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    
    # Read and return content
    try:
        content = await storage.read_file(artifact.relative_path)
        return {
            "id": artifact.id,
            "type": artifact.artifact_type,
            "version": artifact.version,
            "status": artifact.status,
            "content": content.decode('utf-8'),
            "created_at": artifact.created_at
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read artifact: {str(e)}")


@router.put("/{entity_id}/artifacts/{artifact_id}/content", response_model=ArtifactResponse)
async def update_artifact_content(
    entity_id: str,
    artifact_id: str,
    payload: Any = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Replace artifact file contents with JSON-serializable data (pretty-printed JSON on disk)."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Entity not found")

    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.entity_id == entity_id,
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    if payload is not None and not isinstance(
        payload, (dict, list, str, int, float, bool)
    ):
        raise HTTPException(
            status_code=400,
            detail="Body must be JSON: object, array, string, number, boolean, or null",
        )

    try:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400, detail=f"Value is not JSON-serializable: {e}"
        ) from e

    try:
        await storage.write_file(artifact.relative_path, text.encode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to write artifact: {e}"
        ) from e

    artifact.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(artifact)
    return artifact
