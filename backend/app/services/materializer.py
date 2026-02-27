import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Entity, Resource, IngestItem
from app.services.storage import StorageAdapter
from app.services.parking import ParkingLotManager


class ResourceMaterializer:
    """
    Converts IngestItems into canonical Resources under a real Entity.
    
    Materialization safety rule: Copy -> Verify -> Write DB -> Delete parking
    """
    
    def __init__(self, storage: StorageAdapter):
        self.storage = storage
        self.parking_manager = ParkingLotManager(storage)
    
    async def materialize_to_existing_entity(
        self,
        db: AsyncSession,
        ingest_id: str,
        entity_id: str
    ) -> Optional[Entity]:
        """
        Materialize ingest item to an existing entity.
        
        Args:
            db: Database session
            ingest_id: ID of the ingest item
            entity_id: ID of the target entity
        
        Returns:
            The entity if successful, None otherwise
        """
        # Get ingest item
        ingest_item = await self.parking_manager.get_ingest_item(db, ingest_id)
        if not ingest_item:
            raise ValueError(f"Ingest item {ingest_id} not found")
        
        # Get entity
        from sqlalchemy import select
        result = await db.execute(
            select(Entity).where(Entity.id == entity_id)
        )
        entity = result.scalar_one_or_none()
        if not entity:
            raise ValueError(f"Entity {entity_id} not found")
        
        # Perform materialization
        await self._materialize(db, ingest_item, entity)
        
        return entity
    
    async def materialize_to_new_entity(
        self,
        db: AsyncSession,
        ingest_id: str,
        entity_name: str,
        website: Optional[str] = None
    ) -> Entity:
        """
        Create a new entity and materialize ingest item to it.
        
        Args:
            db: Database session
            ingest_id: ID of the ingest item
            entity_name: Name for the new entity
            website: Optional website for the new entity
        
        Returns:
            The created entity
        """
        # Get ingest item
        ingest_item = await self.parking_manager.get_ingest_item(db, ingest_id)
        if not ingest_item:
            raise ValueError(f"Ingest item {ingest_id} not found")
        
        # Create new entity
        entity = Entity(
            id=str(uuid.uuid4()),
            name=entity_name,
            website=website,
            type="company",
            status="active"
        )
        db.add(entity)
        await db.flush()  # Get the entity ID assigned
        
        # Perform materialization
        await self._materialize(db, ingest_item, entity)
        
        return entity
    
    async def _materialize(
        self,
        db: AsyncSession,
        ingest_item: IngestItem,
        entity: Entity
    ) -> List[Resource]:
        """
        Core materialization logic.
        
        Steps:
        1. Copy files from parking lot to entity folder
        2. Create Resource records
        3. Mark ingest item as materialized
        4. (Optional) Delete parking lot files
        """
        resources = []
        parking_path = ingest_item.parkinglot_path
        
        # Ensure entity resources directory exists
        await self.storage.ensure_dir(f"{entity.id}/resources")
        
        # Get files from parking lot
        files_info = await self.parking_manager.get_files_in_parkinglot(
            ingest_item.ingest_id
        )
        
        # Process each file
        for file_info in files_info:
            resource_id = str(uuid.uuid4())
            filename = file_info['filename']
            mime_type = file_info.get('mime_type')
            
            # Source and destination paths
            src_path = f"{parking_path}/files/{filename}"
            dest_relative = f"{entity.id}/resources/{resource_id}/{filename}"
            
            # 1. Copy file
            await self.storage.copy_file(src_path, dest_relative)
            
            # 2. Verify file exists
            if not await self.storage.exists(dest_relative):
                raise RuntimeError(f"Failed to copy file: {filename}")
            
            # 3. Create Resource record
            resource = Resource(
                id=resource_id,
                entity_id=entity.id,
                resource_type="file",
                title=filename,
                mime_type=mime_type,
                original_filename=filename,
                relative_path=dest_relative,
                origin_ingest_id=ingest_item.ingest_id
            )
            db.add(resource)
            resources.append(resource)
        
        # Process text content if exists
        text_path = f"{parking_path}/payload/text.md"
        if await self.storage.exists(text_path):
            resource_id = str(uuid.uuid4())
            dest_relative = f"{entity.id}/resources/{resource_id}/note.md"
            
            # Copy text file
            await self.storage.copy_file(text_path, dest_relative)
            
            # Create resource record
            resource = Resource(
                id=resource_id,
                entity_id=entity.id,
                resource_type="text",
                title="Note",
                mime_type="text/markdown",
                original_filename=None,
                relative_path=dest_relative,
                origin_ingest_id=ingest_item.ingest_id
            )
            db.add(resource)
            resources.append(resource)
        
        # Process URLs if exist
        urls_path = f"{parking_path}/payload/urls.json"
        if await self.storage.exists(urls_path):
            urls_content = await self.storage.read_file(urls_path)
            urls = json.loads(urls_content.decode('utf-8'))
            
            for url in urls:
                resource_id = str(uuid.uuid4())
                resource = Resource(
                    id=resource_id,
                    entity_id=entity.id,
                    resource_type="url",
                    title=url,
                    url=url,
                    relative_path="",  # URLs don't have file paths
                    origin_ingest_id=ingest_item.ingest_id
                )
                db.add(resource)
                resources.append(resource)
        
        # Mark ingest item as materialized
        ingest_item.status = "materialized"
        ingest_item.updated_at = datetime.utcnow()
        
        # Commit all changes
        await db.commit()
        
        # Step 4: Delete parking lot files (after successful commit)
        try:
            await self.storage.delete_recursive(parking_path)
        except Exception:
            # Log error but don't fail - files can be cleaned up later
            pass
        
        # Refresh all resources
        for resource in resources:
            await db.refresh(resource)
        
        return resources
