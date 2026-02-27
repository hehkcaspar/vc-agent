import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import IngestItem
from app.services.storage import StorageAdapter


class ParkingLotManager:
    """Manages parking lot ingestion items."""
    
    PSEUDO_ENTITY_ID = "00000"
    
    def __init__(self, storage: StorageAdapter):
        self.storage = storage
    
    async def create_ingest_item(
        self,
        db: AsyncSession,
        files: List[Dict[str, Any]],
        text: Optional[str],
        urls: Optional[List[str]],
        source: str = "frontend",
        entity_hint_name: Optional[str] = None,
        entity_hint_domain: Optional[str] = None
    ) -> IngestItem:
        """
        Create a new ingest item in the parking lot.
        
        Args:
            files: List of dicts with 'filename', 'content', 'mime_type'
            text: Optional text content
            urls: Optional list of URLs
            source: Source of ingestion
            entity_hint_name: Optional entity name hint
            entity_hint_domain: Optional domain hint
        
        Returns:
            Created IngestItem
        """
        ingest_id = str(uuid.uuid4())
        parkinglot_path = f"{self.PSEUDO_ENTITY_ID}/parkinglot/{ingest_id}"
        
        # Create directory structure
        await self.storage.ensure_dir(f"{parkinglot_path}/files")
        await self.storage.ensure_dir(f"{parkinglot_path}/payload")
        
        # Write files
        for file_info in files:
            file_path = f"{parkinglot_path}/files/{file_info['filename']}"
            await self.storage.write_file(file_path, file_info['content'])
        
        # Write text if provided
        if text:
            text_path = f"{parkinglot_path}/payload/text.md"
            await self.storage.write_file(text_path, text.encode('utf-8'))
        
        # Write URLs if provided
        if urls:
            urls_path = f"{parkinglot_path}/payload/urls.json"
            await self.storage.write_file(
                urls_path, 
                json.dumps(urls, indent=2).encode('utf-8')
            )
        
        # Write metadata
        meta = {
            "source": source,
            "hints": {
                "entity_hint_name": entity_hint_name,
                "entity_hint_domain": entity_hint_domain
            },
            "timestamps": {
                "created": datetime.utcnow().isoformat()
            },
            "files": [
                {
                    "filename": f['filename'],
                    "mime_type": f.get('mime_type'),
                    "size": len(f['content'])
                }
                for f in files
            ]
        }
        meta_path = f"{parkinglot_path}/payload/meta.json"
        await self.storage.write_file(
            meta_path,
            json.dumps(meta, indent=2).encode('utf-8')
        )
        
        # Create database record
        ingest_item = IngestItem(
            ingest_id=ingest_id,
            source=source,
            status="parked",
            parkinglot_path=parkinglot_path,
            entity_hint_name=entity_hint_name,
            entity_hint_domain=entity_hint_domain
        )
        db.add(ingest_item)
        await db.commit()
        await db.refresh(ingest_item)
        
        return ingest_item
    
    async def get_ingest_item(self, db: AsyncSession, ingest_id: str) -> Optional[IngestItem]:
        """Get ingest item by ID."""
        result = await db.execute(
            select(IngestItem).where(IngestItem.ingest_id == ingest_id)
        )
        return result.scalar_one_or_none()
    
    async def list_ingest_items(
        self, 
        db: AsyncSession, 
        status: Optional[str] = None
    ) -> List[IngestItem]:
        """List ingest items, optionally filtered by status."""
        query = select(IngestItem).order_by(IngestItem.created_at.desc())
        if status:
            query = query.where(IngestItem.status == status)
        result = await db.execute(query)
        return result.scalars().all()
    
    async def update_status(
        self, 
        db: AsyncSession, 
        ingest_id: str, 
        status: str,
        error: Optional[str] = None
    ) -> Optional[IngestItem]:
        """Update ingest item status."""
        ingest_item = await self.get_ingest_item(db, ingest_id)
        if ingest_item:
            ingest_item.status = status
            if error:
                ingest_item.error = error
            ingest_item.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(ingest_item)
        return ingest_item
    
    async def delete_ingest_item(self, db: AsyncSession, ingest_id: str) -> bool:
        """Delete ingest item and its files."""
        ingest_item = await self.get_ingest_item(db, ingest_id)
        if not ingest_item:
            return False
        
        # Delete files (optional - can be done lazily)
        # For now, just mark as deleted in DB
        await db.delete(ingest_item)
        await db.commit()
        return True
    
    async def get_files_in_parkinglot(self, ingest_id: str) -> List[Dict[str, Any]]:
        """Get list of files in a parking lot item."""
        files_path = f"{self.PSEUDO_ENTITY_ID}/parkinglot/{ingest_id}/files"
        meta_path = f"{self.PSEUDO_ENTITY_ID}/parkinglot/{ingest_id}/payload/meta.json"
        
        try:
            meta_content = await self.storage.read_file(meta_path)
            meta = json.loads(meta_content.decode('utf-8'))
            return meta.get('files', [])
        except Exception:
            return []
