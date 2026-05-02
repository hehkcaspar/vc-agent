"""Initial Screening composer-only rerun.

Re-runs ONLY the composer phase of the IS preset, reading the existing
section JSONs from the workspace and overwriting the memo. Skips the
survey + section agents — there's no web-search or recursion-heavy ReAct
loop, so it's cheap (~10 s, one Gemini call) and safe to invoke from a
button click without backgrounding.

Use case: the user has edited canonical facts (referral_source, founders,
etc.) via EntityEditModal and wants the memo regenerated with the
corrections without paying for a full IS run.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Entity
from app.services.initial_screening_job import (
    INITIAL_SCREENING_ANALYSIS_DIR,
    INITIAL_SCREENING_MEMO_PATH,
    run_compose_stage,
)
from app.services.initial_screening_v2_job import (
    V2_ANALYSIS_DIR,
    V2_MEMO_PATH,
)
from app.services.storage import storage
from app.services.workspace import WorkspaceService

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/entities/{entity_id}/initial_screening",
    tags=["initial-screening"],
)


class RecomposeRequest(BaseModel):
    version: Literal["v1", "v2"] = "v2"


class RecomposeResponse(BaseModel):
    ok: bool
    memo_path: str
    memo_node_id: Optional[str]
    warnings: list[str]


@router.post("/recompose", response_model=RecomposeResponse)
async def recompose_memo(
    entity_id: str,
    body: RecomposeRequest,
    db: AsyncSession = Depends(get_db),
) -> RecomposeResponse:
    """Recompose the IS memo from existing section JSONs.

    Selects the v1 (``Deliverables/Analysis/initial_screening/``) or v2
    (``initial_screening_v2/``) directory based on ``body.version``. The
    memo is overwritten in place with a fresh compose-stage run. Any
    review_notes file is left alone — it's based on the prior draft.

    Returns 404 if the entity doesn't exist; 422 if no section JSONs
    are present (composer would have nothing to read). Network/Gemini
    failures surface as 502.
    """
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalars().first()
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity {entity_id} not found",
        )

    ws = WorkspaceService(storage)
    if body.version == "v2":
        analysis_dir = V2_ANALYSIS_DIR
        memo_path = V2_MEMO_PATH
    else:
        analysis_dir = INITIAL_SCREENING_ANALYSIS_DIR
        memo_path = INITIAL_SCREENING_MEMO_PATH

    try:
        memo, warnings = await run_compose_stage(
            db, ws,
            entity_id=entity_id,
            entity_name=entity.name,
            entity_website=entity.website,
            agent_run_id=f"recompose:{body.version}",
            analysis_dir=analysis_dir,
            memo_path=memo_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "recompose %s failed for entity=%s: %s",
            body.version, entity_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Recompose failed: {exc}",
        ) from exc

    if memo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No section JSONs found — run the full Initial Screening "
                f"({body.version}) preset at least once before recomposing."
            ),
        )

    node = await ws.get_node_by_path(db, entity_id, memo_path)
    return RecomposeResponse(
        ok=True,
        memo_path=memo_path,
        memo_node_id=(node.id if node else None),
        warnings=warnings,
    )
