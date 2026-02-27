from typing import List, Optional, Dict, Any, Literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import Entity, IngestItem


ResolutionStatus = Literal["resolved", "resolution_required", "failed"]


class ResolutionResult:
    """Result of entity resolution."""
    
    def __init__(
        self,
        status: ResolutionStatus,
        entity_id: Optional[str] = None,
        candidates: Optional[List[Entity]] = None,
        error: Optional[str] = None
    ):
        self.status = status
        self.entity_id = entity_id
        self.candidates = candidates or []
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"status": self.status}
        if self.entity_id:
            result["entity_id"] = self.entity_id
        if self.candidates:
            result["candidates"] = self.candidates
        if self.error:
            result["error"] = self.error
        return result


class EntityResolver:
    """Resolves ingest items to entities."""
    
    async def resolve(
        self,
        db: AsyncSession,
        ingest_item: IngestItem,
        provided_entity_id: Optional[str] = None
    ) -> ResolutionResult:
        """
        Resolve an ingest item to an entity.
        
        Resolution logic:
        1. If entity_id is provided, validate it exists -> resolved
        2. If entity_hint_name is provided, try exact match (case-insensitive)
           - Single match -> auto-resolved
           - Multiple/no match -> resolution_required with candidates
        3. If no hints -> resolution_required
        
        Args:
            db: Database session
            ingest_item: The ingest item to resolve
            provided_entity_id: Optional explicitly provided entity ID
        
        Returns:
            ResolutionResult with status and details
        """
        # Case 1: Explicit entity_id provided
        if provided_entity_id:
            entity = await self._get_entity_by_id(db, provided_entity_id)
            if entity:
                return ResolutionResult(
                    status="resolved",
                    entity_id=str(entity.id)
                )
            else:
                return ResolutionResult(
                    status="resolution_required",
                    candidates=await self._get_all_active_entities(db),
                    error=f"Provided entity_id {provided_entity_id} not found"
                )
        
        # Case 2: Try to match by name hint
        if ingest_item.entity_hint_name:
            matches = await self._find_entities_by_name(
                db, 
                ingest_item.entity_hint_name
            )
            
            if len(matches) == 1:
                # Single confident match
                return ResolutionResult(
                    status="resolved",
                    entity_id=str(matches[0].id)
                )
            elif len(matches) > 1:
                # Multiple matches - need user choice
                return ResolutionResult(
                    status="resolution_required",
                    candidates=matches
                )
            # No matches - fall through to resolution_required
        
        # Case 3: No resolution possible - require user input
        candidates = await self._get_all_active_entities(db)
        return ResolutionResult(
            status="resolution_required",
            candidates=candidates
        )
    
    async def _get_entity_by_id(
        self, 
        db: AsyncSession, 
        entity_id: str
    ) -> Optional[Entity]:
        """Get entity by ID."""
        result = await db.execute(
            select(Entity).where(
                Entity.id == entity_id,
                Entity.status == "active"
            )
        )
        return result.scalar_one_or_none()
    
    async def _find_entities_by_name(
        self, 
        db: AsyncSession, 
        name: str
    ) -> List[Entity]:
        """
        Find entities by name (case-insensitive exact match).
        """
        result = await db.execute(
            select(Entity).where(
                func.lower(Entity.name) == name.lower(),
                Entity.status == "active"
            )
        )
        return result.scalars().all()
    
    async def _get_all_active_entities(self, db: AsyncSession) -> List[Entity]:
        """Get all active entities as candidates."""
        result = await db.execute(
            select(Entity)
            .where(Entity.status == "active")
            .order_by(Entity.name)
        )
        return result.scalars().all()
