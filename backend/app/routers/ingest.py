import json
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas import (
    IngestSuccessResponse,
    IngestResolutionRequiredResponse,
    IngestFailedResponse
)
from app.services.storage import storage
from app.services.parking import ParkingLotManager
from app.services.resolver import EntityResolver
from app.services.materializer import ResourceMaterializer

router = APIRouter(prefix="/ingest", tags=["ingest"])

parking_manager = ParkingLotManager(storage)
resolver = EntityResolver()
materializer = ResourceMaterializer(storage)


@router.post("/resources")
async def ingest_resources(
    files: List[UploadFile] = File(default=[]),
    text: Optional[str] = Form(default=None),
    urls: Optional[str] = Form(default=None),  # JSON string
    entity_id: Optional[str] = Form(default=None),
    entity_hint_name: Optional[str] = Form(default=None),
    entity_hint_domain: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db)
):
    """
    Main ingestion endpoint for resources.
    
    - Files are uploaded and stored in parking lot
    - Text and URLs are stored as metadata
    - Entity resolution is attempted
    - Returns resolution status
    """
    try:
        # Parse URLs from JSON string
        parsed_urls = None
        if urls:
            parsed_urls = json.loads(urls)
        
        # Read file contents
        file_data = []
        for file in files:
            content = await file.read()
            file_data.append({
                "filename": file.filename,
                "content": content,
                "mime_type": file.content_type
            })
        
        # Create parking lot item
        ingest_item = await parking_manager.create_ingest_item(
            db=db,
            files=file_data,
            text=text,
            urls=parsed_urls,
            source="frontend",
            entity_hint_name=entity_hint_name,
            entity_hint_domain=entity_hint_domain
        )
        
        # Attempt resolution
        resolution = await resolver.resolve(
            db=db,
            ingest_item=ingest_item,
            provided_entity_id=entity_id
        )
        
        # Handle resolution result
        if resolution.status == "resolved":
            # Auto-materialize
            try:
                entity = await materializer.materialize_to_existing_entity(
                    db, ingest_item.ingest_id, resolution.entity_id
                )
                
                # Get created resources
                from sqlalchemy import select
                from app.models import Resource
                result = await db.execute(
                    select(Resource)
                    .where(Resource.origin_ingest_id == ingest_item.ingest_id)
                )
                resources = result.scalars().all()
                
                from app.schemas import ResourceResponse
                return {
                    "status": "resolved",
                    "entity_id": resolution.entity_id,
                    "resources": [ResourceResponse.model_validate(r) for r in resources]
                }
            except Exception as e:
                # Mark as failed
                await parking_manager.update_status(
                    db, ingest_item.ingest_id, "failed", str(e)
                )
                return {
                    "status": "failed",
                    "ingest_id": ingest_item.ingest_id,
                    "error": str(e)
                }
        
        elif resolution.status == "resolution_required":
            # Update status and return candidates
            await parking_manager.update_status(
                db, ingest_item.ingest_id, "resolution_required"
            )
            
            from app.schemas import EntityResponse
            return {
                "status": "resolution_required",
                "ingest_id": ingest_item.ingest_id,
                "candidates": [
                    EntityResponse.model_validate(e) 
                    for e in resolution.candidates
                ]
            }
        
        else:  # failed
            await parking_manager.update_status(
                db, ingest_item.ingest_id, "failed", resolution.error
            )
            return {
                "status": "failed",
                "ingest_id": ingest_item.ingest_id,
                "error": resolution.error
            }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
