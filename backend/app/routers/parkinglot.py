from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import IngestItem
from app.schemas import (
    IngestItemResponse, 
    ResolveRequest,
    EntityResponse
)
from app.services.storage import storage
from app.services.parking import ParkingLotManager
from app.services.materializer import ResourceMaterializer
from app.services.resolver import EntityResolver

router = APIRouter(prefix="/parkinglot", tags=["parkinglot"])

parking_manager = ParkingLotManager(storage)
materializer = ResourceMaterializer(storage)
resolver = EntityResolver()


@router.get("", response_model=List[IngestItemResponse])
async def list_parkinglot_items(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """List parking lot items, optionally filtered by status."""
    items = await parking_manager.list_ingest_items(db, status=status)
    return items


@router.get("/{ingest_id}", response_model=IngestItemResponse)
async def get_parkinglot_item(
    ingest_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific parking lot item."""
    item = await parking_manager.get_ingest_item(db, ingest_id)
    if not item:
        raise HTTPException(status_code=404, detail="Ingest item not found")
    return item


@router.post("/{ingest_id}/resolve", response_model=EntityResponse)
async def resolve_parkinglot_item(
    ingest_id: str,
    resolve_request: ResolveRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Resolve a parking lot item to an entity.
    
    Provide either:
    - entity_id: to attach to existing entity
    - create_entity: {"name": "..."} to create new entity
    """
    # Get ingest item
    ingest_item = await parking_manager.get_ingest_item(db, ingest_id)
    if not ingest_item:
        raise HTTPException(status_code=404, detail="Ingest item not found")
    
    if ingest_item.status == "materialized":
        raise HTTPException(
            status_code=400, 
            detail="Ingest item already materialized"
        )
    
    # Resolve to existing entity
    if resolve_request.entity_id:
        try:
            entity = await materializer.materialize_to_existing_entity(
                db, ingest_id, resolve_request.entity_id
            )
            return entity
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # Create new entity and resolve
    elif resolve_request.create_entity:
        entity_name = resolve_request.create_entity.get("name")
        if not entity_name:
            raise HTTPException(
                status_code=400, 
                detail="Entity name is required"
            )
        
        try:
            entity = await materializer.materialize_to_new_entity(
                db, ingest_id, entity_name
            )
            return entity
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either entity_id or create_entity"
        )


@router.post("/{ingest_id}/retry")
async def retry_resolution(
    ingest_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Retry resolution for a failed item."""
    ingest_item = await parking_manager.get_ingest_item(db, ingest_id)
    if not ingest_item:
        raise HTTPException(status_code=404, detail="Ingest item not found")
    
    if ingest_item.status not in ["failed", "resolution_required"]:
        raise HTTPException(
            status_code=400,
            detail="Can only retry failed or resolution_required items"
        )
    
    # Reset status to parked for re-processing
    await parking_manager.update_status(db, ingest_id, "parked")
    
    return {"message": "Item reset for retry", "ingest_id": ingest_id}
