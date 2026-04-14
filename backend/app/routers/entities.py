"""Entity CRUD — workspace scaffolding on create, cleanup on delete."""

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, func, select

from app.database import get_db
from app.models import ChatCompletionJob, Entity, WorkspaceNode
from app.schemas import (
    EntityCreate,
    EntityResponse,
    EntityUpdate,
    coerce_orm_metadata_before_model,
)
from app.services.storage import storage
from app.services.workspace import WorkspaceService, Actor

router = APIRouter(prefix="/entities", tags=["entities"])
workspace_service = WorkspaceService(storage)


async def _latest_user_content_at(db: AsyncSession, entity_id: str):
    """MAX(created_at) of user-origin workspace nodes (upload/ingest/user).

    Excludes agent writes + deleted rows. Returns None if entity has no such
    content. Captures "last genuine new info for this entity."
    """
    res = await db.execute(
        select(func.max(WorkspaceNode.created_at)).where(
            WorkspaceNode.entity_id == entity_id,
            WorkspaceNode.deleted_at.is_(None),
            WorkspaceNode.origin_type.in_(("upload", "ingest", "user")),
        )
    )
    return res.scalar_one_or_none()


def _entity_with_last_content(entity: Entity, last_content_at) -> dict[str, Any]:
    """Return a dict ready for EntityResponse with last_content_at injected."""
    data = coerce_orm_metadata_before_model(entity)
    if isinstance(data, dict):
        data["last_content_at"] = last_content_at
    return data


@router.get("", response_model=List[EntityResponse])
async def list_entities(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Entity).order_by(Entity.updated_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.post("", response_model=EntityResponse)
async def create_entity(
    entity: EntityCreate,
    db: AsyncSession = Depends(get_db),
):
    db_entity = Entity(
        name=entity.name,
        website=entity.website,
        type="company",
        status="active",
        deal_stage=entity.deal_stage or "diligence",
    )
    db.add(db_entity)
    await db.flush()

    # Scaffold default workspace folders + WORKSPACE_NOTES.md
    await workspace_service.scaffold_workspace(db, db_entity.id)

    await db.commit()
    await db.refresh(db_entity)
    return db_entity


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    last = await _latest_user_content_at(db, entity_id)
    return EntityResponse.model_validate(_entity_with_last_content(entity, last))


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update_entity(
    entity_id: str,
    entity_update: EntityUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    update_data = entity_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(entity, field, value)

    await db.commit()
    await db.refresh(entity)
    last = await _latest_user_content_at(db, entity_id)
    return EntityResponse.model_validate(_entity_with_last_content(entity, last))


@router.delete("/{entity_id}")
async def delete_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    await db.execute(
        delete(ChatCompletionJob).where(ChatCompletionJob.entity_id == entity_id)
    )
    await db.delete(entity)
    await db.commit()

    # Best-effort filesystem cleanup
    await storage.delete_recursive(entity_id)
    return {"message": "Entity deleted successfully"}
