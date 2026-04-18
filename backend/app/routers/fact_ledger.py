"""Fact ledger — provenance read endpoint.

Surfaces the append-only ``Entity.metadata_json._ledger[]`` grouped by
fact_path, so the frontend can render provenance popovers + timeline drawers
next to each canonical fact.

Write paths go through ``services/fact_manager.py`` (not this router).

- ``GET /entities/{id}/facts/provenance`` — full ledger grouped by fact_path

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Entity
from app.services.fact_manager import get_provenance

router = APIRouter(prefix="/entities", tags=["fact-ledger"])


class FactSourceOut(BaseModel):
    # Schema mirrors services.fact_ledger_schema.FactSource. Extra fields are
    # dropped in responses to keep the API contract tight (the source model
    # upstream uses extra="allow" to survive forward-compat writes).
    model_config = ConfigDict(extra="ignore")

    type: str
    ref: Optional[str] = None
    quote: Optional[str] = None
    preset: Optional[str] = None
    run_id: Optional[str] = None


class FactEntryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry_id: str
    fact_path: str
    value: Any = None
    source: FactSourceOut
    confidence: float
    as_of: Optional[str] = None
    recorded_at: str
    supersedes: Optional[str] = None
    status: str
    notes: Optional[str] = None
    # Present when this entry mirrors a row in `_fact_discrepancies[]`.
    # The frontend's FactDiscrepancyPanel uses it to join the ledger's
    # source-tier pill to the discrepancy row.
    linked_discrepancy_id: Optional[str] = None


class FactProvenanceGroup(BaseModel):
    """All ledger entries at one fact_path, with the current active entry
    lifted for quick read."""

    current: Optional[FactEntryOut] = None
    history: List[FactEntryOut]


class FactProvenanceOut(BaseModel):
    """Response: ``{fact_path: {current, history}, ...}``.

    Only fact_paths with at least one ledger entry appear. Entities that have
    never had a hard-fact write return an empty object.
    """

    groups: Dict[str, FactProvenanceGroup]


@router.get(
    "/{entity_id}/facts/provenance",
    response_model=FactProvenanceOut,
)
async def get_fact_provenance(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return all ledger entries for an entity, grouped by fact_path.

    The frontend Facts tab opens a popover per-field using this endpoint:
    current value + source + confidence + as_of, plus a collapsible history
    drawer listing superseded entries.
    """
    # Verify entity exists before looking at the ledger so "entity not found"
    # and "empty ledger" produce different responses.
    ent = (
        await db.execute(select(Entity.id).where(Entity.id == entity_id))
    ).scalar_one_or_none()
    if ent is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    raw = await get_provenance(db, entity_id)
    groups: Dict[str, FactProvenanceGroup] = {}
    for path, bucket in raw.items():
        groups[path] = FactProvenanceGroup(
            current=bucket.get("current"),
            history=bucket.get("history") or [],
        )
    return FactProvenanceOut(groups=groups)
