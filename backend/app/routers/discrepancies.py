"""Fact discrepancies — user adjudication surface.

When an opinion run (legal_review / extract_info) surfaces a fact that
contradicts canonical state, it appends an entry to
``Entity.metadata_json._fact_discrepancies[]`` via ``propose_fact_update``.
These endpoints let the UI list pending rows and accept/reject each.

- ``GET /entities/{id}/fact-discrepancies?status=pending|accepted|rejected|all``
- ``POST /entities/{id}/fact-discrepancies/{discrepancy_id}/accept``
- ``POST /entities/{id}/fact-discrepancies/{discrepancy_id}/reject``

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.datetime_support import utc_now
from app.models import Entity
from app.schemas import EntityResponse, coerce_orm_metadata_before_model
from app.services.fact_discrepancies import (
    accept_discrepancy,
    list_discrepancies,
    reject_discrepancy,
)

router = APIRouter(prefix="/entities", tags=["fact-discrepancies"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FactDiscrepancyOut(BaseModel):
    id: str
    detected_at: str
    detected_by: str
    field_path: str
    round_name: Optional[str] = None
    current_value: Any = None
    proposed_value: Any = None
    source_doc_node_id: str
    source_doc_quote: Optional[str] = None
    confidence: str
    rationale: str
    status: str
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    dismiss_reason: Optional[str] = None
    source_run: dict = {}


class RejectRequest(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_entity(db: AsyncSession, entity_id: str) -> Entity:
    res = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = res.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


def _load_metadata(entity: Entity) -> dict:
    if not entity.metadata_json:
        return {}
    try:
        meta = json.loads(entity.metadata_json)
        return meta if isinstance(meta, dict) else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{entity_id}/fact-discrepancies",
    response_model=List[FactDiscrepancyOut],
)
async def list_fact_discrepancies(
    entity_id: str,
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
):
    if status not in ("pending", "accepted", "rejected", "all"):
        raise HTTPException(
            status_code=400,
            detail="status must be pending | accepted | rejected | all",
        )
    entity = await _get_entity(db, entity_id)
    metadata = _load_metadata(entity)
    return list_discrepancies(metadata, status=status)


@router.post(
    "/{entity_id}/fact-discrepancies/{discrepancy_id}/accept",
    response_model=EntityResponse,
)
async def accept_fact_discrepancy(
    entity_id: str,
    discrepancy_id: str,
    db: AsyncSession = Depends(get_db),
):
    entity = await _get_entity(db, entity_id)
    metadata = _load_metadata(entity)
    try:
        updated_meta, _entry = accept_discrepancy(metadata, discrepancy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="discrepancy not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entity.metadata_json = json.dumps(updated_meta, ensure_ascii=False)
    entity.updated_at = utc_now()
    await db.commit()
    await db.refresh(entity)
    return coerce_orm_metadata_before_model(entity)


@router.post(
    "/{entity_id}/fact-discrepancies/{discrepancy_id}/reject",
    response_model=EntityResponse,
)
async def reject_fact_discrepancy(
    entity_id: str,
    discrepancy_id: str,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
):
    entity = await _get_entity(db, entity_id)
    metadata = _load_metadata(entity)
    try:
        updated_meta, _entry = reject_discrepancy(
            metadata, discrepancy_id, body.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="discrepancy not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entity.metadata_json = json.dumps(updated_meta, ensure_ascii=False)
    entity.updated_at = utc_now()
    await db.commit()
    await db.refresh(entity)
    return coerce_orm_metadata_before_model(entity)
