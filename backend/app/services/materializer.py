"""Materialize parking-lot ingest items into workspace nodes."""

import json
import uuid
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.datetime_support import utc_now
from app.models import Entity, IngestItem, WorkspaceNode
from app.services.parking import ParkingLotManager
from app.services.storage import StorageAdapter
from app.services.workspace import Actor, WorkspaceService


class WorkspaceMaterializer:
    """
    Converts IngestItems into WorkspaceNodes under an entity's Inbox/.

    Materialization safety rule: Copy -> Verify -> Write DB -> Delete parking
    """

    def __init__(self, storage: StorageAdapter, workspace_service: WorkspaceService):
        self.storage = storage
        self.ws = workspace_service
        self.parking_manager = ParkingLotManager(storage)

    async def materialize_to_existing_entity(
        self, db: AsyncSession, ingest_id: str, entity_id: str,
    ) -> Optional[Entity]:
        ingest_item = await self.parking_manager.get_ingest_item(db, ingest_id)
        if not ingest_item:
            raise ValueError(f"Ingest item {ingest_id} not found")

        from sqlalchemy import select
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if not entity:
            raise ValueError(f"Entity {entity_id} not found")

        await self._materialize(db, ingest_item, entity)
        return entity

    async def materialize_to_new_entity(
        self, db: AsyncSession, ingest_id: str, entity_name: str,
        website: Optional[str] = None,
    ) -> Entity:
        ingest_item = await self.parking_manager.get_ingest_item(db, ingest_id)
        if not ingest_item:
            raise ValueError(f"Ingest item {ingest_id} not found")

        entity = Entity(
            id=str(uuid.uuid4()),
            name=entity_name,
            website=website,
            type="company",
            status="active",
        )
        db.add(entity)
        await db.flush()

        # Scaffold workspace template
        await self.ws.scaffold_workspace(db, entity.id)

        # Materialize files
        await self._materialize(db, ingest_item, entity)
        return entity

    async def _materialize(
        self, db: AsyncSession, ingest_item: IngestItem, entity: Entity,
    ) -> List[WorkspaceNode]:
        nodes: list[WorkspaceNode] = []
        parking_path = ingest_item.parkinglot_path
        actor = Actor(type="system", ref=f"ingest:{ingest_item.ingest_id}")

        # Get files from parking lot
        files_info = await self.parking_manager.get_files_in_parkinglot(
            ingest_item.ingest_id
        )

        for file_info in files_info:
            filename = file_info["filename"]
            mime_type = file_info.get("mime_type")
            src_path = f"{parking_path}/files/{filename}"

            content = await self.storage.read_file(src_path)
            node = await self.ws.write_file(
                db, entity.id, f"Inbox/{filename}",
                content, mime_type, actor,
            )
            node.origin_type = "ingest"
            node.origin_ref = ingest_item.ingest_id
            nodes.append(node)

        # Text content
        text_path = f"{parking_path}/payload/text.md"
        if await self.storage.exists(text_path):
            content = await self.storage.read_file(text_path)
            node = await self.ws.write_file(
                db, entity.id, "Inbox/note.md",
                content, "text/markdown", actor,
            )
            node.origin_type = "ingest"
            node.origin_ref = ingest_item.ingest_id
            nodes.append(node)

        # URLs
        urls_path = f"{parking_path}/payload/urls.json"
        if await self.storage.exists(urls_path):
            urls_content = await self.storage.read_file(urls_path)
            urls = json.loads(urls_content.decode("utf-8"))
            for url in urls:
                # Derive a short name from URL
                from urllib.parse import urlparse
                name = urlparse(url).netloc or url[:40]
                node = await self.ws.create_bookmark(
                    db, entity.id, f"Inbox/{name}",
                    url, actor,
                )
                node.origin_type = "ingest"
                node.origin_ref = ingest_item.ingest_id
                nodes.append(node)

        # Mark ingest item as materialized
        ingest_item.status = "materialized"
        ingest_item.updated_at = utc_now()

        await db.commit()

        # Clean up parking lot
        try:
            await self.storage.delete_recursive(parking_path)
        except Exception:
            pass

        for node in nodes:
            await db.refresh(node)
        return nodes
