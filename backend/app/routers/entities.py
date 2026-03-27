import json
from typing import Any, List, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from app.database import get_db
from app.datetime_support import utc_now
from app.models import ChatCompletionJob, Entity, Resource, Artifact
from app.schemas import (
    ArtifactCreate,
    ArtifactResponse,
    EntityCreate,
    EntityResponse,
    EntityUpdate,
    MetadataPreprocessAccepted,
    MetadataPreprocessJobStatus,
    MetadataPreprocessStart,
    ResourceResponse,
    metadata_json_to_dict,
)
from app.services.metadata_preprocess_jobs import (
    create_or_reuse_job,
    get_job_status,
    run_metadata_preprocess_job,
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
    
    # Remove background chat jobs first; they do not cascade from Entity.
    await db.execute(
        delete(ChatCompletionJob).where(ChatCompletionJob.entity_id == entity_id)
    )
    await db.delete(entity)
    await db.commit()

    # Best-effort filesystem cleanup for this entity's resources/artifacts.
    await storage.delete_recursive(f"entities/{entity_id}")
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


@router.post(
    "/{entity_id}/metadata-preprocess",
    response_model=MetadataPreprocessAccepted,
)
async def start_metadata_preprocess(
    entity_id: str,
    body: MetadataPreprocessStart,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Enqueue Gemini metadata extraction into resource/artifact metadata_json (async)."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Entity not found")

    if body.target == "resource":
        res = await db.execute(
            select(Resource).where(
                Resource.id == body.id,
                Resource.entity_id == entity_id,
            )
        )
        if not res.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Resource not found")
    else:
        art = await db.execute(
            select(Artifact).where(
                Artifact.id == body.id,
                Artifact.entity_id == entity_id,
            )
        )
        if not art.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Artifact not found")

    job_id, schedule = await create_or_reuse_job(entity_id, body.target, body.id)
    if schedule:
        background_tasks.add_task(run_metadata_preprocess_job, job_id)
    return MetadataPreprocessAccepted(job_id=job_id)


@router.get(
    "/{entity_id}/metadata-preprocess-jobs/{job_id}",
    response_model=MetadataPreprocessJobStatus,
)
async def get_metadata_preprocess_job(
    entity_id: str,
    job_id: str,
):
    row = await get_job_status(entity_id, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return MetadataPreprocessJobStatus(
        job_id=row["job_id"],
        status=row["status"],
        error_message=row.get("error_message"),
    )


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


@router.patch("/{entity_id}/resources/{resource_id}", response_model=ResourceResponse)
async def update_resource(
    entity_id: str,
    resource_id: str,
    payload: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Update mutable resource fields (title, metadata)."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Entity not found")

    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.entity_id == entity_id,
        )
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    title = payload.get("title")
    if title is not None:
        t = str(title).strip()
        if not t:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        resource.title = t

    if "metadata" in payload:
        meta = payload["metadata"]
        if meta is None:
            resource.metadata_json = None
        elif not isinstance(meta, dict):
            raise HTTPException(
                status_code=400,
                detail="metadata must be a JSON object or null",
            )
        else:
            resource.metadata_json = json.dumps(meta, ensure_ascii=False)

    resource.updated_at = utc_now()
    await db.commit()
    await db.refresh(resource)
    return resource


@router.delete("/{entity_id}/resources/{resource_id}")
async def delete_resource(
    entity_id: str,
    resource_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a resource and best-effort remove its backing file."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Entity not found")

    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.entity_id == entity_id,
        )
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    rel_path = resource.relative_path
    await db.delete(resource)
    await db.commit()

    if rel_path:
        await storage.delete_file(rel_path)
    return {"message": "Resource deleted successfully"}


@router.patch("/{entity_id}/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def update_artifact(
    entity_id: str,
    artifact_id: str,
    payload: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Update mutable artifact fields (title, metadata)."""
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

    title = payload.get("title")
    if title is not None:
        t = str(title).strip()
        artifact.title = t or None

    if "metadata" in payload:
        meta = payload["metadata"]
        if meta is None:
            artifact.metadata_json = None
        elif not isinstance(meta, dict):
            raise HTTPException(
                status_code=400,
                detail="metadata must be a JSON object or null",
            )
        else:
            artifact.metadata_json = json.dumps(meta, ensure_ascii=False)

    artifact.updated_at = utc_now()
    await db.commit()
    await db.refresh(artifact)
    return artifact


@router.delete("/{entity_id}/artifacts/{artifact_id}")
async def delete_artifact(
    entity_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an artifact and best-effort remove its file."""
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

    rel_path = artifact.relative_path
    await db.delete(artifact)
    await db.commit()

    if rel_path:
        await storage.delete_file(rel_path)
    return {"message": "Artifact deleted successfully"}


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
        return {
            "url": resource.url,
            "type": "url",
            "metadata": metadata_json_to_dict(
                getattr(resource, "metadata_json", None)
            ),
        }
    
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
            "created_at": artifact.created_at,
            "metadata": metadata_json_to_dict(
                getattr(artifact, "metadata_json", None)
            ),
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

    artifact.updated_at = utc_now()
    await db.commit()
    await db.refresh(artifact)
    return artifact
