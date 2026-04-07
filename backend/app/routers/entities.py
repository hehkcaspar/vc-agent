"""Entity CRUD — workspace scaffolding on create, cleanup on delete."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select

from app.database import get_db
from app.models import ChatCompletionJob, Entity
from app.schemas import EntityCreate, EntityResponse, EntityUpdate
from app.services.storage import storage
from app.services.workspace import WorkspaceService, Actor

router = APIRouter(prefix="/entities", tags=["entities"])
workspace_service = WorkspaceService(storage)


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
    return entity


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
    return entity


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
