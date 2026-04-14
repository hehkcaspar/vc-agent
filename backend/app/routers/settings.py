"""Portfolio-side settings endpoints.

Covers the user-configurable portfolio configs:
- Fund registry (`data/config/funds.json`)
- Legal template catalog (`data/config/legal_templates.json`) — read-only catalog,
  with a per-template text endpoint so the UI can preview extracted text
- Legal review checklist (`data/config/legal_review_checklist.json`) — full GET/PUT
  since the rubric is user-tunable post-launch
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from app.services.funds_config import (
    Fund,
    FundsConfig,
    delete_fund,
    load_funds,
    upsert_fund,
)
from app.services.legal_review_checklist_config import (
    LegalReviewChecklist,
    load_legal_review_checklist,
    write_legal_review_checklist,
)
from app.services.legal_templates_config import (
    LegalTemplatesConfig,
    load_legal_templates_config,
    read_template_text,
)

router = APIRouter(prefix="/settings", tags=["settings"])


# ── Funds ──────────────────────────────────────────────────────────────────


@router.get("/funds", response_model=FundsConfig)
async def get_funds() -> FundsConfig:
    try:
        return load_funds()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/funds", response_model=FundsConfig)
async def add_or_update_fund(fund: Fund) -> FundsConfig:
    try:
        return upsert_fund(fund)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/funds/{fund_id}", response_model=FundsConfig)
async def remove_fund(fund_id: str) -> FundsConfig:
    try:
        return delete_fund(fund_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── Legal Review: template catalog (Tier R1) ──────────────────────────────


@router.get("/legal-templates", response_model=LegalTemplatesConfig)
async def get_legal_templates() -> LegalTemplatesConfig:
    try:
        return load_legal_templates_config()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


class TemplateText(BaseModel):
    id: str
    label: str
    text: str


@router.get("/legal-templates/{template_id}/text", response_model=TemplateText)
async def get_legal_template_text(template_id: str) -> TemplateText:
    try:
        cfg = load_legal_templates_config()
        tpl = next((t for t in cfg.templates if t.id == template_id), None)
        if tpl is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown template id: {template_id!r}"
            )
        text = read_template_text(template_id)
        return TemplateText(id=tpl.id, label=tpl.label, text=text)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── Legal Review: distilled checklist (Tier R2) ───────────────────────────


@router.get("/legal-review-checklist", response_model=LegalReviewChecklist)
async def get_legal_review_checklist() -> LegalReviewChecklist:
    try:
        return load_legal_review_checklist()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/legal-review-checklist", response_model=LegalReviewChecklist)
async def put_legal_review_checklist(body: dict[str, Any]) -> LegalReviewChecklist:
    try:
        return write_legal_review_checklist(body)
    except ValidationError as e:
        # Pydantic errors → 400 with structured details so the UI can surface
        # exactly which field failed (write_legal_review_checklist does not
        # wrap these itself).
        raise HTTPException(status_code=400, detail=e.errors()) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
