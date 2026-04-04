"""Academic Tracking v2 API router — scholar-centric endpoints."""

import json
import logging
import shutil
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academic_database import get_academic_db
from app.academic_models import (
    AcademicChatJob,
    AcademicChatMessage,
    AcademicChatSession,
    Channel,
    Scholar,
    ScholarEvent,
)
from app.academic_schemas import (
    AcademicChatJobAccepted,
    AcademicChatJobStatus,
    AcademicChatMessageCreate,
    AcademicChatMessageResponse,
    AcademicChatSessionCreate,
    AcademicChatSessionDetailResponse,
    AcademicChatSessionResponse,
    ChannelResponse,
    CreateScholarRequest,
    CreateWeightPresetRequest,
    CustomDimensionRequest,
    CustomDimensionResponse,
    DigestResponse,
    EvaluationListResponse,
    EventResponse,
    MarkFeedReadRequest,
    PapersResponse,
    RankingScholarResponse,
    ReportListResponse,
    ReportResponse,
    ScholarListResponse,
    ScholarResponse,
    SignalFeedEventResponse,
    UpdateChannelRequest,
    UpdateEventRequest,
    UpdateScholarRequest,
    WeightPresetResponse,
)
from app.config import settings
from app.datetime_support import utc_now
from app.services.academic.file_utils import dossier_path, read_json, write_json
from app.services.academic.evaluation_service import (
    auto_create_channels,
    compute_and_attach_delta,
    get_latest_eval_scores,
    normalize_evaluation,
    run_comparative,
    run_evaluation,
    run_refresh,
    running_agents,
)
from app.services.academic.chat_service import run_chat_job
from app.services.academic.digest_service import DIGESTS_DIR, run_digest_generation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/academic", tags=["academic"])


# ── Helpers ────────────────────────────────────────────────────


def _enrich_scholar_response(scholar: Scholar) -> dict:
    """Merge SQL row with profile.json for the response."""
    data = {col.key: getattr(scholar, col.key) for col in scholar.__table__.columns}
    data["tags"] = json.loads(data["tags"]) if isinstance(data.get("tags"), str) else (data.get("tags") or [])

    profile = read_json(dossier_path(scholar.id) / "profile.json")
    if profile:
        data["affiliation"] = profile.get("affiliation", {}).get("current")
        data["h_index"] = profile.get("metrics", {}).get("h_index")
        data["i10_index"] = profile.get("metrics", {}).get("i10_index")
        data["total_citations"] = profile.get("metrics", {}).get("total_citations")
        data["research_areas"] = profile.get("research_areas", [])
        data["identity"] = profile.get("identity")

    return data


async def _get_scholar_or_404(db: AsyncSession, scholar_id: str) -> Scholar:
    scholar = await db.get(Scholar, scholar_id)
    if not scholar:
        raise HTTPException(404, "Scholar not found")
    return scholar


async def _get_chat_session_or_404(
    db: AsyncSession, scholar_id: str, session_id: str,
) -> AcademicChatSession:
    result = await db.execute(
        select(AcademicChatSession).where(
            AcademicChatSession.id == session_id,
            AcademicChatSession.scholar_id == scholar_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Chat session not found")
    return session


# ═══════════════════════════════════════════════════════════════
#  SCHOLAR CRUD
# ═══════════════════════════════════════════════════════════════


@router.post("/scholars", response_model=ScholarResponse)
async def create_scholar(
    body: CreateScholarRequest,
    db: AsyncSession = Depends(get_academic_db),
):
    """Create a new scholar and initialize their dossier directory."""
    scholar = Scholar(
        name=body.name,
        tracking_priority=body.tracking_priority,
        tags=json.dumps(body.tags) if body.tags else None,
        entity_id=body.entity_id,
        dossier_path="",
    )
    db.add(scholar)
    await db.flush()

    scholar.dossier_path = f"data/scholars/{scholar.id}/"

    dossier = dossier_path(scholar.id)
    for subdir in ("evaluations", "reports", "uploads", "agent_runs"):
        (dossier / subdir).mkdir(parents=True, exist_ok=True)

    from app.services.academic.tool_utils import classify_urls
    pre_known = classify_urls(body.urls)

    identity: dict = {}
    if pre_known.get("google_scholar_id"):
        identity["google_scholar"] = {
            "id": pre_known["google_scholar_id"],
            "url": pre_known.get("google_scholar_url", ""),
            "confidence": "verified",
            "verified_by": "deterministic_parse",
        }
    if pre_known.get("semantic_scholar_id"):
        identity["semantic_scholar"] = {
            "id": pre_known["semantic_scholar_id"],
            "url": pre_known.get("semantic_scholar_url", ""),
            "confidence": "verified",
            "verified_by": "deterministic_parse",
        }
    if pre_known.get("linkedin_url"):
        identity["linkedin"] = {"url": pre_known["linkedin_url"], "confidence": "verified"}
    if pre_known.get("dblp_url"):
        identity["dblp"] = {"url": pre_known["dblp_url"], "confidence": "verified"}

    profile = {
        "id": scholar.id,
        "name": body.name,
        "aliases": [],
        "identity": identity,
        "affiliation": {},
        "metrics": {},
        "research_areas": [],
        "user_notes": body.user_notes or "",
        "tags": body.tags,
        "input_urls": body.urls,
        "created_at": scholar.created_at.isoformat() if scholar.created_at else None,
    }
    write_json(dossier / "profile.json", profile)

    await db.commit()
    await db.refresh(scholar)
    return _enrich_scholar_response(scholar)


@router.get("/scholars", response_model=ScholarListResponse)
async def list_scholars(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, pattern=r"^(active|evaluating|paused|archived)$"),
    priority: Optional[str] = Query(None, pattern=r"^(high|medium|low)$"),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_academic_db),
):
    base = select(Scholar)
    if status:
        base = base.where(Scholar.status == status)
    if priority:
        base = base.where(Scholar.tracking_priority == priority)
    if search:
        safe = search.replace("%", "").replace("_", "")
        base = base.where(Scholar.name.ilike(f"%{safe}%"))

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    result = await db.execute(
        base.order_by(Scholar.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    scholars = result.scalars().all()

    return ScholarListResponse(
        scholars=[_enrich_scholar_response(s) for s in scholars],
        total=total,
    )


@router.get("/scholars/{scholar_id}", response_model=ScholarResponse)
async def get_scholar(scholar_id: str, db: AsyncSession = Depends(get_academic_db)):
    scholar = await _get_scholar_or_404(db, scholar_id)
    return _enrich_scholar_response(scholar)


@router.put("/scholars/{scholar_id}", response_model=ScholarResponse)
async def update_scholar(
    scholar_id: str,
    body: UpdateScholarRequest,
    db: AsyncSession = Depends(get_academic_db),
):
    scholar = await _get_scholar_or_404(db, scholar_id)

    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        if k == "tags" and v is not None:
            setattr(scholar, k, json.dumps(v))
        elif k == "user_notes":
            profile_path = dossier_path(scholar_id) / "profile.json"
            profile = read_json(profile_path)
            profile["user_notes"] = v
            write_json(profile_path, profile)
        else:
            setattr(scholar, k, v)

    await db.commit()
    await db.refresh(scholar)
    return _enrich_scholar_response(scholar)


@router.delete("/scholars/{scholar_id}")
async def delete_scholar(scholar_id: str, db: AsyncSession = Depends(get_academic_db)):
    scholar = await _get_scholar_or_404(db, scholar_id)

    if scholar.status == "evaluating":
        running_agents.pop(scholar_id, None)

    dossier = dossier_path(scholar_id)
    if dossier.exists():
        shutil.rmtree(dossier)

    await db.execute(
        delete(AcademicChatJob).where(AcademicChatJob.scholar_id == scholar_id)
    )
    await db.delete(scholar)
    await db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  AGENT — EVALUATE / STOP / REFRESH
# ═══════════════════════════════════════════════════════════════


@router.post("/scholars/{scholar_id}/evaluate")
async def evaluate_scholar(
    scholar_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_academic_db),
):
    scholar = await _get_scholar_or_404(db, scholar_id)
    if scholar.status == "evaluating":
        raise HTTPException(409, "Scholar is already being evaluated")

    scholar.status = "evaluating"
    await db.commit()

    background_tasks.add_task(run_evaluation, scholar_id)
    return {"ok": True, "status": "evaluating"}


@router.post("/scholars/{scholar_id}/stop")
async def stop_scholar(scholar_id: str, db: AsyncSession = Depends(get_academic_db)):
    scholar = await _get_scholar_or_404(db, scholar_id)
    running_agents.pop(scholar_id, None)
    scholar.status = "active"
    await db.commit()
    return {"ok": True}


@router.post("/scholars/{scholar_id}/refresh")
async def refresh_scholar(
    scholar_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_academic_db),
):
    """Refresh a scholar's dossier — fetch new papers, update metrics, rescore."""
    scholar = await _get_scholar_or_404(db, scholar_id)
    if scholar.status == "evaluating":
        raise HTTPException(409, "Scholar is already being evaluated")

    scholar.status = "evaluating"
    await db.commit()

    background_tasks.add_task(run_refresh, scholar_id)
    return {"ok": True, "status": "evaluating"}


# ═══════════════════════════════════════════════════════════════
#  PAPERS
# ═══════════════════════════════════════════════════════════════


@router.get("/scholars/{scholar_id}/papers", response_model=PapersResponse)
async def get_scholar_papers(
    scholar_id: str,
    limit: int = Query(50, ge=1, le=500),
    sort_by: str = Query("citations", pattern="^(citations|year)$"),
    author_position: Optional[str] = Query(None, pattern="^(first|last|middle|sole)$"),
    db: AsyncSession = Depends(get_academic_db),
):
    await _get_scholar_or_404(db, scholar_id)

    papers_data = read_json(dossier_path(scholar_id) / "papers.json")
    papers = papers_data.get("papers", [])

    # Coerce fields_of_study entries that are dicts (SS API format) to strings
    for p in papers:
        fos = p.get("fields_of_study")
        if isinstance(fos, list):
            p["fields_of_study"] = [
                item["category"] if isinstance(item, dict) and "category" in item else str(item)
                for item in fos
                if item
            ]

    if author_position:
        papers = [p for p in papers if p.get("author_position") == author_position]

    if sort_by == "year":
        papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    else:
        papers.sort(key=lambda p: p.get("citations") or 0, reverse=True)

    return PapersResponse(
        updated_at=papers_data.get("updated_at"),
        summary=papers_data.get("summary", {}),
        total=len(papers),
        papers=papers[:limit],
    )


# ═══════════════════════════════════════════════════════════════
#  EVALUATIONS
# ═══════════════════════════════════════════════════════════════


@router.get("/scholars/{scholar_id}/evaluations", response_model=EvaluationListResponse)
async def list_evaluations(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    await _get_scholar_or_404(db, scholar_id)

    evals_dir = dossier_path(scholar_id) / "evaluations"
    evaluations = []
    if evals_dir.exists():
        for f in sorted(evals_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data = normalize_evaluation(data, f)
                evaluations.append(data)
            except (json.JSONDecodeError, KeyError, Exception) as e:
                logger.warning("Skipping malformed evaluation file %s: %s", f, e)

    return EvaluationListResponse(evaluations=evaluations)


# ═══════════════════════════════════════════════════════════════
#  REPORTS
# ═══════════════════════════════════════════════════════════════


def _parse_report_filename(stem: str) -> tuple[str, str]:
    """Extract (date_str, report_type) from a report filename stem."""
    parts = stem.split("_", 1)
    date_str = parts[0] if parts else stem
    report_type = parts[1] if len(parts) > 1 else "full"
    return date_str, report_type


@router.get("/scholars/{scholar_id}/reports", response_model=ReportListResponse)
async def list_reports(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    await _get_scholar_or_404(db, scholar_id)

    reports_dir = dossier_path(scholar_id) / "reports"
    reports = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.md"), reverse=True):
            date_str, report_type = _parse_report_filename(f.stem)
            reports.append(ReportResponse(
                id=f.stem,
                filename=f.name,
                report_type=report_type,
                created_at=date_str,
                content=None,
            ))

    return ReportListResponse(reports=reports)


@router.get("/scholars/{scholar_id}/reports/{report_id}", response_model=ReportResponse)
async def get_report(
    scholar_id: str,
    report_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    report_path = dossier_path(scholar_id) / "reports" / f"{report_id}.md"
    if not report_path.exists():
        raise HTTPException(404, "Report not found")

    date_str, report_type = _parse_report_filename(report_path.stem)
    return ReportResponse(
        id=report_path.stem,
        filename=report_path.name,
        report_type=report_type,
        created_at=date_str,
        content=report_path.read_text(encoding="utf-8"),
    )


@router.delete("/scholars/{scholar_id}/reports/{report_id}")
async def delete_report(scholar_id: str, report_id: str):
    report_path = dossier_path(scholar_id) / "reports" / f"{report_id}.md"
    if not report_path.exists():
        raise HTTPException(404, "Report not found")
    report_path.unlink()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════


@router.get("/scholars/{scholar_id}/events", response_model=list[EventResponse])
async def list_events(
    scholar_id: str,
    limit: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    significance: Optional[str] = Query(None, pattern="^(high|medium|low)$"),
    db: AsyncSession = Depends(get_academic_db),
):
    stmt = select(ScholarEvent).where(ScholarEvent.scholar_id == scholar_id)
    if event_type:
        stmt = stmt.where(ScholarEvent.event_type == event_type)
    if significance:
        stmt = stmt.where(ScholarEvent.significance == significance)

    stmt = stmt.order_by(ScholarEvent.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/scholars/{scholar_id}/events/{event_id}", response_model=EventResponse)
async def update_event(
    scholar_id: str,
    event_id: str,
    body: UpdateEventRequest,
    db: AsyncSession = Depends(get_academic_db),
):
    event = await db.get(ScholarEvent, event_id)
    if not event or event.scholar_id != scholar_id:
        raise HTTPException(404, "Event not found")

    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(event, k, v)
    await db.commit()
    await db.refresh(event)
    return event


# ═══════════════════════════════════════════════════════════════
#  CHANNELS
# ═══════════════════════════════════════════════════════════════


@router.get("/scholars/{scholar_id}/channels", response_model=list[ChannelResponse])
async def list_channels(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    result = await db.execute(
        select(Channel).where(Channel.scholar_id == scholar_id)
    )
    return result.scalars().all()


@router.put("/scholars/{scholar_id}/channels/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    scholar_id: str,
    channel_id: str,
    body: UpdateChannelRequest,
    db: AsyncSession = Depends(get_academic_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.scholar_id != scholar_id:
        raise HTTPException(404, "Channel not found")

    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(channel, k, v)
    await db.commit()
    await db.refresh(channel)
    return channel


# ═══════════════════════════════════════════════════════════════
#  SIGNAL FEED
# ═══════════════════════════════════════════════════════════════


@router.get("/signal-feed", response_model=list[SignalFeedEventResponse])
async def signal_feed(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_academic_db),
):
    """Cross-scholar unread events enriched with scholar names."""
    result = await db.execute(
        select(ScholarEvent, Scholar.name.label("scholar_name"))
        .join(Scholar, ScholarEvent.scholar_id == Scholar.id)
        .where(ScholarEvent.is_read == False)  # noqa: E712
        .where(ScholarEvent.significance.in_(["high", "medium"]))
        .order_by(ScholarEvent.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        SignalFeedEventResponse(
            id=evt.id,
            scholar_id=evt.scholar_id,
            scholar_name=scholar_name,
            event_type=evt.event_type,
            significance=evt.significance,
            title=evt.title,
            is_read=evt.is_read,
            event_date=evt.event_date,
            created_at=evt.created_at,
        )
        for evt, scholar_name in rows
    ]


@router.post("/signal-feed/mark-read")
async def mark_signal_feed_read(
    body: MarkFeedReadRequest,
    db: AsyncSession = Depends(get_academic_db),
):
    """Bulk mark signal feed events as read. Empty event_ids = mark all."""
    if body.event_ids:
        result = await db.execute(
            select(ScholarEvent).where(ScholarEvent.id.in_(body.event_ids))
        )
        for evt in result.scalars().all():
            evt.is_read = True
    else:
        result = await db.execute(
            select(ScholarEvent)
            .where(ScholarEvent.is_read == False)  # noqa: E712
            .where(ScholarEvent.significance.in_(["high", "medium"]))
        )
        for evt in result.scalars().all():
            evt.is_read = True
    await db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  CHAT
# ═══════════════════════════════════════════════════════════════


@router.get(
    "/scholars/{scholar_id}/chat/sessions",
    response_model=list[AcademicChatSessionResponse],
)
async def list_chat_sessions(
    scholar_id: str, db: AsyncSession = Depends(get_academic_db),
):
    await _get_scholar_or_404(db, scholar_id)
    result = await db.execute(
        select(AcademicChatSession)
        .where(AcademicChatSession.scholar_id == scholar_id)
        .order_by(AcademicChatSession.updated_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/scholars/{scholar_id}/chat/sessions",
    response_model=AcademicChatSessionResponse,
)
async def create_chat_session(
    scholar_id: str,
    body: AcademicChatSessionCreate,
    db: AsyncSession = Depends(get_academic_db),
):
    await _get_scholar_or_404(db, scholar_id)
    session = AcademicChatSession(
        id=str(_uuid.uuid4()),
        scholar_id=scholar_id,
        title=body.title,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get(
    "/scholars/{scholar_id}/chat/sessions/{session_id}",
    response_model=AcademicChatSessionDetailResponse,
)
async def get_chat_session(
    scholar_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    session = await _get_chat_session_or_404(db, scholar_id, session_id)
    result = await db.execute(
        select(AcademicChatMessage)
        .where(AcademicChatMessage.session_id == session_id)
        .order_by(AcademicChatMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return AcademicChatSessionDetailResponse(session=session, messages=messages)


@router.delete(
    "/scholars/{scholar_id}/chat/sessions/{session_id}",
    status_code=204,
)
async def delete_chat_session(
    scholar_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    session = await _get_chat_session_or_404(db, scholar_id, session_id)
    await db.execute(
        delete(AcademicChatJob).where(AcademicChatJob.session_id == session_id)
    )
    await db.delete(session)
    await db.commit()


@router.post(
    "/scholars/{scholar_id}/chat/sessions/{session_id}/messages",
    response_model=None,
    responses={202: {"model": AcademicChatJobAccepted}},
)
async def post_chat_message(
    scholar_id: str,
    session_id: str,
    body: AcademicChatMessageCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_academic_db),
):
    """Send a message to the scholar chat agent. Always async (202)."""
    await _get_scholar_or_404(db, scholar_id)
    session = await _get_chat_session_or_404(db, scholar_id, session_id)

    user_msg = AcademicChatMessage(
        id=str(_uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=body.text,
    )
    db.add(user_msg)

    job = AcademicChatJob(
        id=str(_uuid.uuid4()),
        scholar_id=scholar_id,
        session_id=session_id,
        user_message_id=user_msg.id,
        status="pending",
    )
    db.add(job)

    session.updated_at = utc_now()
    await db.commit()
    await db.refresh(user_msg)

    background_tasks.add_task(run_chat_job, job.id)
    return JSONResponse(
        status_code=202,
        content=AcademicChatJobAccepted(
            job_id=job.id,
            user_message=AcademicChatMessageResponse.model_validate(user_msg),
            status="pending",
        ).model_dump(mode="json"),
    )


@router.get(
    "/scholars/{scholar_id}/chat/sessions/{session_id}/jobs/{job_id}",
    response_model=AcademicChatJobStatus,
)
async def get_chat_job(
    scholar_id: str,
    session_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    result = await db.execute(
        select(AcademicChatJob).where(
            AcademicChatJob.id == job_id,
            AcademicChatJob.session_id == session_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")

    assistant: Optional[AcademicChatMessageResponse] = None
    if job.assistant_message_id:
        msg_r = await db.execute(
            select(AcademicChatMessage).where(
                AcademicChatMessage.id == job.assistant_message_id
            )
        )
        row = msg_r.scalar_one_or_none()
        if row:
            assistant = AcademicChatMessageResponse.model_validate(row)

    return AcademicChatJobStatus(
        job_id=job.id,
        status=job.status,
        step_detail=job.step_detail,
        user_message_id=job.user_message_id,
        assistant_message=assistant,
        error_message=job.error_message,
    )


# ═══════════════════════════════════════════════════════════════
#  RANKING
# ═══════════════════════════════════════════════════════════════


@router.get("/ranking", response_model=list[RankingScholarResponse])
async def get_ranking(
    status: Optional[str] = Query(None, pattern=r"^(active|evaluating|paused|archived)$"),
    priority: Optional[str] = Query(None, pattern=r"^(high|medium|low)$"),
    db: AsyncSession = Depends(get_academic_db),
):
    """All scholars with latest evaluation dimension scores for ranking."""
    base = select(Scholar)
    if status:
        base = base.where(Scholar.status == status)
    if priority:
        base = base.where(Scholar.tracking_priority == priority)

    result = await db.execute(base.order_by(Scholar.name.asc()))
    scholars = result.scalars().all()

    ranking: list[dict] = []
    for s in scholars:
        enriched = _enrich_scholar_response(s)
        dims, eval_date = get_latest_eval_scores(s.id)
        ranking.append(RankingScholarResponse(
            id=s.id,
            name=s.name,
            affiliation=enriched.get("affiliation"),
            h_index=enriched.get("h_index"),
            tracking_priority=s.tracking_priority,
            status=s.status,
            dimensions=dims,
            eval_date=eval_date,
        ))
    return ranking


# ── Weight presets ────────────────────────────────────────────

PRESETS_DIR = settings.ACADEMIC_CONFIG_DIR / "ranking_presets"

_SEED_PRESETS = {
    "balanced": {
        "name": "Balanced",
        "weights": {
            "research_impact": 1.0, "commercialization": 1.0,
            "career_trajectory": 1.0, "collaboration_strength": 1.0,
            "field_position": 1.0, "founder_potential": 1.0,
            "public_profile": 1.0,
        },
    },
    "impact_focused": {
        "name": "Impact Focused",
        "weights": {
            "research_impact": 0.3, "commercialization": 0.1,
            "career_trajectory": 0.15, "collaboration_strength": 0.1,
            "field_position": 0.3, "founder_potential": 0.05,
            "public_profile": 0.0,
        },
    },
    "vc_commercialization": {
        "name": "VC Commercialization",
        "weights": {
            "research_impact": 0.1, "commercialization": 0.35,
            "career_trajectory": 0.05, "collaboration_strength": 0.05,
            "field_position": 0.05, "founder_potential": 0.35,
            "public_profile": 0.05,
        },
    },
}


def _ensure_presets_dir() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    for slug, preset in _SEED_PRESETS.items():
        path = PRESETS_DIR / f"{slug}.json"
        if not path.exists():
            path.write_text(json.dumps(preset, indent=2), encoding="utf-8")


@router.get("/ranking/presets", response_model=list[WeightPresetResponse])
async def list_weight_presets():
    _ensure_presets_dir()
    presets: list[WeightPresetResponse] = []
    for f in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            presets.append(WeightPresetResponse(
                name=data.get("name", f.stem),
                weights=data.get("weights", {}),
            ))
        except Exception:
            continue
    return presets


@router.post("/ranking/presets", response_model=WeightPresetResponse)
async def create_weight_preset(body: CreateWeightPresetRequest):
    _ensure_presets_dir()
    slug = body.name.lower().replace(" ", "_").replace("-", "_")
    slug = "".join(c for c in slug if c.isalnum() or c == "_")
    path = PRESETS_DIR / f"{slug}.json"
    data = {"name": body.name, "weights": body.weights}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return WeightPresetResponse(name=body.name, weights=body.weights)


@router.delete("/ranking/presets/{name}")
async def delete_weight_preset(name: str):
    _ensure_presets_dir()
    path = PRESETS_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(404, "Preset not found")
    path.unlink()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  COMPARATIVE EVALUATION
# ═══════════════════════════════════════════════════════════════


@router.post("/scholars/{scholar_id}/compare/{other_id}")
async def compare_scholars(
    scholar_id: str,
    other_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_academic_db),
):
    """Compare two scholars — runs agent with both scholars' data as context."""
    scholar_a = await _get_scholar_or_404(db, scholar_id)
    scholar_b = await db.get(Scholar, other_id)
    if not scholar_b:
        raise HTTPException(404, "Scholar B not found")
    if scholar_a.status == "evaluating":
        raise HTTPException(409, "Scholar A is already being evaluated")

    scholar_a.status = "evaluating"
    await db.commit()

    background_tasks.add_task(run_comparative, scholar_id, other_id)
    return {"ok": True, "status": "evaluating"}


# ═══════════════════════════════════════════════════════════════
#  DIGEST
# ═══════════════════════════════════════════════════════════════


@router.post("/digest/generate")
async def generate_digest(background_tasks: BackgroundTasks):
    """Generate a portfolio digest via direct Gemini call."""
    background_tasks.add_task(run_digest_generation)
    return {"ok": True, "status": "generating"}


@router.get("/digests", response_model=list[DigestResponse])
async def list_digests():
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    digests = []
    for f in sorted(DIGESTS_DIR.glob("*.md"), reverse=True):
        stem = f.stem
        parts = stem.split("_", 1)
        digests.append(DigestResponse(
            id=stem,
            filename=f.name,
            created_at=parts[0] if parts else stem,
        ))
    return digests


@router.get("/digests/{digest_id}", response_model=DigestResponse)
async def get_digest(digest_id: str):
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGESTS_DIR / f"{digest_id}.md"
    if not path.exists():
        raise HTTPException(404, "Digest not found")
    stem = path.stem
    parts = stem.split("_", 1)
    return DigestResponse(
        id=stem,
        filename=path.name,
        created_at=parts[0] if parts else stem,
        content=path.read_text(encoding="utf-8"),
    )


# ═══════════════════════════════════════════════════════════════
#  UPLOADS
# ═══════════════════════════════════════════════════════════════


@router.post("/scholars/{scholar_id}/uploads")
async def upload_scholar_files(
    scholar_id: str,
    files: list[UploadFile] = File(...),
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_academic_db),
):
    """Upload files to a scholar's dossier and optionally process with agent."""
    await _get_scholar_or_404(db, scholar_id)

    uploads_dir = dossier_path(scholar_id) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        safe_name = f.filename.replace("/", "_").replace("\\", "_") if f.filename else f"upload_{_uuid.uuid4().hex[:8]}"
        dest = uploads_dir / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved.append(safe_name)

    if background_tasks and saved:
        background_tasks.add_task(_run_upload_processing, scholar_id, saved)

    return {"ok": True, "files": saved}


async def _run_upload_processing(scholar_id: str, filenames: list[str]) -> None:
    """Background task: invoke scholar agent to process uploaded files."""
    from app.services.academic.scholar_agent import invoke_scholar_agent
    from app.services.academic.scholar_prompts import GOAL_UPLOAD_PROCESSING

    try:
        goal = GOAL_UPLOAD_PROCESSING.format(uploaded_files=", ".join(filenames))
        await invoke_scholar_agent(scholar_id, goal)
    except Exception as e:
        logger.exception("Upload processing failed for %s: %s", scholar_id, e)


@router.get("/scholars/{scholar_id}/uploads")
async def list_scholar_uploads(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    """List uploaded files for a scholar."""
    await _get_scholar_or_404(db, scholar_id)

    uploads_dir = dossier_path(scholar_id) / "uploads"
    if not uploads_dir.exists():
        return []

    return [
        {"filename": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
        for f in sorted(uploads_dir.iterdir())
        if f.is_file()
    ]


# ═══════════════════════════════════════════════════════════════
#  CUSTOM DIMENSIONS
# ═══════════════════════════════════════════════════════════════

CUSTOM_DIMS_PATH = settings.ACADEMIC_CONFIG_DIR / "custom_dimensions.json"


def _read_custom_dims() -> list[dict]:
    if CUSTOM_DIMS_PATH.exists():
        try:
            data = json.loads(CUSTOM_DIMS_PATH.read_text(encoding="utf-8"))
            return data.get("dimensions", [])
        except Exception:
            pass
    return []


def _write_custom_dims(dims: list[dict]) -> None:
    CUSTOM_DIMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_DIMS_PATH.write_text(
        json.dumps({"dimensions": dims}, indent=2), encoding="utf-8"
    )


@router.get("/custom-dimensions", response_model=list[CustomDimensionResponse])
async def list_custom_dimensions():
    return _read_custom_dims()


@router.post("/custom-dimensions", response_model=CustomDimensionResponse)
async def create_custom_dimension(body: CustomDimensionRequest):
    dims = _read_custom_dims()
    if any(d["key"] == body.key for d in dims):
        raise HTTPException(409, f"Dimension key '{body.key}' already exists")
    new_dim = {"name": body.name, "key": body.key, "prompt": body.prompt}
    dims.append(new_dim)
    _write_custom_dims(dims)
    return new_dim


@router.delete("/custom-dimensions/{key}")
async def delete_custom_dimension(key: str):
    dims = _read_custom_dims()
    new_dims = [d for d in dims if d["key"] != key]
    if len(new_dims) == len(dims):
        raise HTTPException(404, "Dimension not found")
    _write_custom_dims(new_dims)
    return {"ok": True}
