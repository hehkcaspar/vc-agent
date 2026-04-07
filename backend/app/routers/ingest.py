"""Ingestion endpoint — parking lot → workspace materialization."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import WorkspaceNodeResponse
from app.services.storage import storage
from app.services.parking import ParkingLotManager
from app.services.resolver import EntityResolver
from app.services.materializer import WorkspaceMaterializer
from app.services.workspace import WorkspaceService

router = APIRouter(prefix="/ingest", tags=["ingest"])

parking_manager = ParkingLotManager(storage)
resolver = EntityResolver()
workspace_service = WorkspaceService(storage)
materializer = WorkspaceMaterializer(storage, workspace_service)


@router.post("/resources")
async def ingest_resources(
    files: List[UploadFile] = File(default=[]),
    text: Optional[str] = Form(default=None),
    urls: Optional[str] = Form(default=None),
    entity_id: Optional[str] = Form(default=None),
    entity_hint_name: Optional[str] = Form(default=None),
    entity_hint_domain: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        parsed_urls = json.loads(urls) if urls else None

        file_data = []
        for file in files:
            content = await file.read()
            file_data.append({
                "filename": file.filename,
                "content": content,
                "mime_type": file.content_type,
            })

        ingest_item = await parking_manager.create_ingest_item(
            db=db,
            files=file_data,
            text=text,
            urls=parsed_urls,
            source="frontend",
            entity_hint_name=entity_hint_name,
            entity_hint_domain=entity_hint_domain,
        )

        resolution = await resolver.resolve(
            db=db,
            ingest_item=ingest_item,
            provided_entity_id=entity_id,
        )

        if resolution.status == "resolved":
            try:
                entity = await materializer.materialize_to_existing_entity(
                    db, ingest_item.ingest_id, resolution.entity_id,
                )

                from sqlalchemy import select
                from app.models import WorkspaceNode
                result = await db.execute(
                    select(WorkspaceNode).where(
                        WorkspaceNode.origin_ref == ingest_item.ingest_id,
                        WorkspaceNode.entity_id == resolution.entity_id,
                    )
                )
                nodes = result.scalars().all()

                return {
                    "status": "resolved",
                    "entity_id": resolution.entity_id,
                    "nodes": [WorkspaceNodeResponse.model_validate(n) for n in nodes],
                }
            except Exception as e:
                await parking_manager.update_status(
                    db, ingest_item.ingest_id, "failed", str(e),
                )
                return {
                    "status": "failed",
                    "ingest_id": ingest_item.ingest_id,
                    "error": str(e),
                }

        elif resolution.status == "resolution_required":
            await parking_manager.update_status(
                db, ingest_item.ingest_id, "resolution_required",
            )
            from app.schemas import EntityResponse
            return {
                "status": "resolution_required",
                "ingest_id": ingest_item.ingest_id,
                "candidates": [
                    EntityResponse.model_validate(e)
                    for e in resolution.candidates
                ],
            }

        else:
            await parking_manager.update_status(
                db, ingest_item.ingest_id, "failed", resolution.error,
            )
            return {
                "status": "failed",
                "ingest_id": ingest_item.ingest_id,
                "error": resolution.error,
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
