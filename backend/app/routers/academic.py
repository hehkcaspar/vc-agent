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
    EventResponse,
    IdentitySourceDelete,
    IdentitySourceUpsert,
    MarkFeedReadRequest,
    PapersResponse,
    PatchContinuousTaskRequest,
    RankingScholarResponse,
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
    cancel_scholar_task,
    claim_evaluating,
    get_all_latest_evals,
    get_latest_eval_scores,
    launch_background_run,
    release_evaluating,
    run_evaluation,
    run_refresh,
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


# ── Identity source CRUD ───────────────────────────────────────


def _load_profile_or_404(scholar_id: str):
    path = dossier_path(scholar_id) / "profile.json"
    profile = read_json(path)
    if not profile:
        raise HTTPException(404, "Scholar dossier not found")
    return profile, path


def _source_url_and_id(body: IdentitySourceUpsert) -> tuple[str, Optional[str]]:
    """Derive the url + platform id from an upsert payload.

    If the caller didn't supply `id`, try to parse it from the URL via
    `classify_urls`. That keeps the API permissive (paste a URL, let
    the server figure out the id) without reimplementing parsing.
    """
    if body.id:
        return body.url, body.id
    # Lazy import to avoid a circular at module-load time.
    from app.services.academic.tool_utils import classify_urls
    parsed = classify_urls([body.url])
    id_keys = {
        "google_scholar": "google_scholar_id",
        "semantic_scholar": "semantic_scholar_id",
        "orcid": "orcid_id",
        "dblp": "dblp_id",
        "openreview": "openreview_id",
        "arxiv": "arxiv_author",
        "linkedin": "linkedin_handle",
        "github": "github_user",
        "twitter": "twitter_handle",
    }
    key = id_keys.get(body.source_id)
    return body.url, (parsed.get(key) if key else None)


@router.put("/scholars/{scholar_id}/identity", response_model=ScholarResponse)
async def upsert_scholar_identity(
    scholar_id: str,
    body: IdentitySourceUpsert,
    db: AsyncSession = Depends(get_academic_db),
):
    """Upsert one identity source. Always authoritative (user edit).

    Sets `confidence: verified`, `verified_by: user_edit`, and removes
    the id from `rejected_identity` if it was previously blacklisted —
    the user is overriding whatever automation said earlier.
    """
    scholar = await _get_scholar_or_404(db, scholar_id)
    profile, profile_path = _load_profile_or_404(scholar_id)
    identity = dict(profile.get("identity") or {})
    url, parsed_id = _source_url_and_id(body)

    entry: dict = {
        "url": url,
        "confidence": "verified",
        "verified_by": "user_edit",
    }
    if parsed_id:
        entry["id"] = parsed_id
    # Preserve platform-specific handles so the UI still renders them.
    if body.source_id == "linkedin" and parsed_id:
        entry["handle"] = parsed_id
    if body.source_id == "github" and parsed_id:
        entry["user"] = parsed_id
    if body.source_id == "twitter" and parsed_id:
        entry["handle"] = parsed_id
    if body.source_id == "arxiv" and parsed_id:
        entry["author"] = parsed_id

    identity[body.source_id] = entry
    profile["identity"] = identity

    # Drop any matching entry from rejected_identity — user is
    # overriding a previous rejection.
    rejected = dict(profile.get("rejected_identity") or {})
    if parsed_id and body.source_id in rejected:
        rejected[body.source_id] = [
            r for r in rejected[body.source_id] if str(r.get("id") or "") != str(parsed_id)
        ]
        if not rejected[body.source_id]:
            rejected.pop(body.source_id)
    profile["rejected_identity"] = rejected

    write_json(profile_path, profile)

    await db.refresh(scholar)
    return _enrich_scholar_response(scholar)


@router.delete("/scholars/{scholar_id}/identity/{source_id}", response_model=ScholarResponse)
async def delete_scholar_identity(
    scholar_id: str,
    source_id: str,
    body: Optional[IdentitySourceDelete] = None,
    db: AsyncSession = Depends(get_academic_db),
):
    """Remove one identity source. Optionally blacklist the id.

    With `{blacklist: true}`, the previously-committed id is appended
    to `rejected_identity[source_id]` so the next `/refresh` run will
    not re-pick it. Without blacklisting, the entry is simply removed
    and the resolver is free to find a fresh candidate.
    """
    scholar = await _get_scholar_or_404(db, scholar_id)
    profile, profile_path = _load_profile_or_404(scholar_id)
    identity = dict(profile.get("identity") or {})
    removed = identity.pop(source_id, None)
    profile["identity"] = identity

    if body and body.blacklist and removed and removed.get("id"):
        rejected = dict(profile.get("rejected_identity") or {})
        lst = list(rejected.get(source_id) or [])
        lst.append(
            {
                "id": removed.get("id"),
                "url": removed.get("url"),
                "rejected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "reason": "user-deleted with blacklist=true",
                "rejected_by": "user_delete",
            }
        )
        rejected[source_id] = lst
        profile["rejected_identity"] = rejected

    write_json(profile_path, profile)

    await db.refresh(scholar)
    return _enrich_scholar_response(scholar)


@router.delete("/scholars/{scholar_id}")
async def delete_scholar(scholar_id: str, db: AsyncSession = Depends(get_academic_db)):
    scholar = await _get_scholar_or_404(db, scholar_id)

    # If a run is in flight, cancel and wait for it to unwind before we
    # delete the row — otherwise the background task would keep writing
    # to a dossier directory we're about to rm -rf.
    await cancel_scholar_task(scholar_id)

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
    db: AsyncSession = Depends(get_academic_db),
):
    """Kick off a full bootstrap run in the background.

    Uses `claim_evaluating` for an atomic active→evaluating transition
    so duplicate clicks and heartbeat ticks cannot race. The background
    task is launched via `launch_background_run`, which stores the
    `asyncio.Task` in a registry so `/stop` can actually cancel it.
    """
    await _get_scholar_or_404(db, scholar_id)
    if not await claim_evaluating(scholar_id):
        raise HTTPException(409, "Scholar is already being evaluated")
    launch_background_run(scholar_id, lambda: run_evaluation(scholar_id))
    return {"ok": True, "status": "evaluating"}


@router.post("/scholars/{scholar_id}/stop")
async def stop_scholar(scholar_id: str, db: AsyncSession = Depends(get_academic_db)):
    """Cancel an in-flight bootstrap and wait for it to unwind.

    `cancel_scholar_task` awaits the task's `finally` block, which
    releases the SQL `evaluating` lock. If nothing was running (e.g.
    the task crashed without releasing, or state drifted), we force
    the lock back to `active` as a safety net.
    """
    await _get_scholar_or_404(db, scholar_id)
    cancelled = await cancel_scholar_task(scholar_id)
    if not cancelled:
        await release_evaluating(scholar_id)
    return {"ok": True, "cancelled": cancelled}


@router.post("/scholars/{scholar_id}/refresh")
async def refresh_scholar(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    """Incremental refresh — same pipeline as evaluate, different mode."""
    await _get_scholar_or_404(db, scholar_id)
    if not await claim_evaluating(scholar_id):
        raise HTTPException(409, "Scholar is already being evaluated")
    launch_background_run(scholar_id, lambda: run_refresh(scholar_id))
    return {"ok": True, "status": "evaluating"}


# ═══════════════════════════════════════════════════════════════
#  PAPERS
# ═══════════════════════════════════════════════════════════════


def _normalize_paper(raw: dict, ss_author_id: str | None) -> dict:
    """Map raw SS API paper dict to PaperResponse-compatible shape."""
    # Author position
    position = None
    if ss_author_id:
        authors = raw.get("authors") or []
        n = len(authors)
        for i, a in enumerate(authors):
            if str(a.get("authorId") or "") == ss_author_id:
                if n == 1:
                    position = "sole"
                elif i == 0:
                    position = "first"
                elif i == n - 1 and n >= 3:
                    position = "last"
                else:
                    position = "middle"
                break

    # fields_of_study: SS returns [{category, source}] or [str]; also check "fields"
    fos_raw = raw.get("fields_of_study") or raw.get("fields") or raw.get("s2_fields") or []
    fos = []
    for item in fos_raw:
        if isinstance(item, str):
            fos.append(item)
        elif isinstance(item, dict) and "category" in item:
            fos.append(item["category"])

    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled",
        "authors": raw.get("authors") or [],
        "year": raw.get("year"),
        "venue": raw.get("venue") or raw.get("journal") or None,
        "publication_type": (raw.get("publication_types") or [None])[0]
            if isinstance(raw.get("publication_types"), list)
            else raw.get("publication_type"),
        "citations": raw.get("citations") or 0,
        "influential_citations": raw.get("influential_citations") or 0,
        "fields_of_study": fos,
        "ss_paper_id": raw.get("id"),
        "author_position": position,
    }


def _build_summary(papers: list[dict]) -> dict:
    """Build papers summary from normalized papers."""
    by_position: dict[str, int] = {}
    by_decade: dict[str, int] = {}
    for p in papers:
        pos = p.get("author_position")
        if pos:
            by_position[pos] = by_position.get(pos, 0) + 1
        yr = p.get("year")
        if yr:
            decade = f"{(yr // 10) * 10}s"
            by_decade[decade] = by_decade.get(decade, 0) + 1

    sorted_by_cites = sorted(papers, key=lambda p: p.get("citations") or 0, reverse=True)
    sorted_by_year = sorted(papers, key=lambda p: p.get("year") or 0, reverse=True)

    return {
        "total": len(papers),
        "by_position": by_position,
        "by_decade": by_decade,
        "top_cited": [
            {"title": p["title"], "year": p.get("year"), "citations": p.get("citations", 0), "venue": p.get("venue")}
            for p in sorted_by_cites[:5]
        ],
        "recent_5": [
            {"title": p["title"], "year": p["year"], "citations": p.get("citations", 0),
             "venue": p.get("venue"), "position": p.get("author_position")}
            for p in sorted_by_year[:5] if p.get("year")
        ],
    }


@router.get("/scholars/{scholar_id}/papers", response_model=PapersResponse)
async def get_scholar_papers(
    scholar_id: str,
    limit: int = Query(50, ge=1, le=500),
    sort_by: str = Query("citations", pattern="^(citations|year)$"),
    author_position: Optional[str] = Query(None, pattern="^(first|last|middle|sole)$"),
    db: AsyncSession = Depends(get_academic_db),
):
    await _get_scholar_or_404(db, scholar_id)

    papers_data = read_json(dossier_path(scholar_id) / "papers.json") or {}
    raw_items = papers_data.get("items") or papers_data.get("papers") or []

    # Get SS author id for position computation
    profile = read_json(dossier_path(scholar_id) / "profile.json") or {}
    ss_id = ((profile.get("identity") or {}).get("semantic_scholar") or {}).get("id")

    papers = [_normalize_paper(p, ss_id) for p in raw_items]
    summary = _build_summary(papers)

    if author_position:
        papers = [p for p in papers if p.get("author_position") == author_position]

    if sort_by == "year":
        papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    else:
        papers.sort(key=lambda p: p.get("citations") or 0, reverse=True)

    return PapersResponse(
        updated_at=papers_data.get("updated_at"),
        summary=summary,
        total=len(papers),
        papers=papers[:limit],
    )


# ═══════════════════════════════════════════════════════════════
#  EVALUATIONS
# ═══════════════════════════════════════════════════════════════


@router.get("/scholars/{scholar_id}/evaluations")
async def list_evaluations(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    """V2 shape — single read that bundles per-dim latest, narrative,
    peer group, and active red flags. No more monolithic _full.json.
    """
    await _get_scholar_or_404(db, scholar_id)
    return get_all_latest_evals(scholar_id)


# ═══════════════════════════════════════════════════════════════
#  NARRATIVE HISTORY (replaces legacy reports/*.md)
# ═══════════════════════════════════════════════════════════════
#
# V2 does not write reports to the filesystem. Historical analyses
# live as append-only records in `narrative.jsonl` (per Concept 6
# of the framework doc). The endpoint here replaces the old
# `/reports` endpoints; the current (latest) narrative is also
# exposed inline via `/evaluations`. This endpoint returns the
# full history for the UI's narrative-history sidebar.


@router.get("/scholars/{scholar_id}/narrative-history")
async def list_narrative_history(
    scholar_id: str,
    db: AsyncSession = Depends(get_academic_db),
):
    """Return the full history of narrative syntheses for a scholar."""
    await _get_scholar_or_404(db, scholar_id)
    from app.services.academic.file_utils import read_records
    records = read_records(scholar_id, "narrative")
    # Most recent first.
    return {"narratives": list(reversed(records))}


# ═══════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════


@router.get("/scholars/{scholar_id}/events", response_model=list[EventResponse])
async def list_events(
    scholar_id: str,
    limit: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    significance: Optional[str] = Query(None, pattern="^(high|medium|low)$"),
    sort_by: str = Query("discovered", pattern="^(discovered|event_date)$"),
    db: AsyncSession = Depends(get_academic_db),
):
    stmt = select(ScholarEvent).where(ScholarEvent.scholar_id == scholar_id)
    if event_type:
        stmt = stmt.where(ScholarEvent.event_type == event_type)
    if significance:
        stmt = stmt.where(ScholarEvent.significance == significance)

    if sort_by == "event_date":
        # NULLs (unknown event date) sort last
        stmt = stmt.order_by(ScholarEvent.event_date.desc().nullslast()).limit(limit)
    else:
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
            source_url=evt.source_url,
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
            "academic_excellence": 1.0,
            "tech_transfer_experience": 1.0,
            "founder_potential": 1.0,
            "growth_trajectory": 1.0,
        },
    },
    "impact_focused": {
        "name": "Impact Focused",
        "weights": {
            "academic_excellence": 0.6,
            "tech_transfer_experience": 0.1,
            "founder_potential": 0.1,
            "growth_trajectory": 0.2,
        },
    },
    "vc_commercialization": {
        "name": "VC Commercialization",
        "weights": {
            "academic_excellence": 0.15,
            "tech_transfer_experience": 0.35,
            "founder_potential": 0.35,
            "growth_trajectory": 0.15,
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
async def compare_scholars(scholar_id: str, other_id: str):
    """Removed in v2. Comparative eval is now a client-side diff over
    the per-dim JSONL history — no dedicated agent run."""
    raise HTTPException(
        410,
        "Comparative evaluation was removed in the v2 framework rewrite. "
        "Diff the per-dim evaluations client-side from /evaluations.",
    )


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
    """Background task: process uploads via the v2 structured-output pipeline."""
    from app.services.academic.upload_processor import process_uploads

    try:
        uploads_dir = dossier_path(scholar_id) / "uploads"
        paths = [uploads_dir / fn for fn in filenames if (uploads_dir / fn).exists()]
        if not paths:
            return
        await process_uploads(scholar_id, paths)
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
#  DIMENSIONS — unified (built-in + custom), file-backed, all editable
# ═══════════════════════════════════════════════════════════════

from ..services.academic.dimensions import read_dimensions, write_dimensions


@router.get("/custom-dimensions", response_model=list[CustomDimensionResponse])
async def list_dimensions():
    return read_dimensions()


@router.post("/custom-dimensions", response_model=CustomDimensionResponse)
async def create_dimension(body: CustomDimensionRequest):
    dims = read_dimensions()
    if any(d["key"] == body.key for d in dims):
        raise HTTPException(409, f"Dimension key '{body.key}' already exists")
    new_dim = {"name": body.name, "key": body.key, "prompt": body.prompt}
    dims.append(new_dim)
    write_dimensions(dims)
    return new_dim


@router.put("/custom-dimensions/{key}", response_model=CustomDimensionResponse)
async def update_dimension(key: str, body: CustomDimensionRequest):
    dims = read_dimensions()
    idx = next((i for i, d in enumerate(dims) if d["key"] == key), -1)
    if idx == -1:
        raise HTTPException(404, "Dimension not found")
    if body.key != key and any(d["key"] == body.key for d in dims):
        raise HTTPException(409, f"Dimension key '{body.key}' already exists")
    updated = {"name": body.name, "key": body.key, "prompt": body.prompt}
    dims[idx] = updated
    write_dimensions(dims)
    return updated


@router.delete("/custom-dimensions/{key}")
async def delete_dimension(key: str):
    dims = read_dimensions()
    new_dims = [d for d in dims if d["key"] != key]
    if len(new_dims) == len(dims):
        raise HTTPException(404, "Dimension not found")
    write_dimensions(new_dims)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  EVALUATION LOG
# ═══════════════════════════════════════════════════════════════

from ..services.academic.eval_log import read_tail_jsonl


@router.get("/eval-log")
async def get_eval_log(
    limit: int = Query(200, ge=1, le=1000),
    scholar_id: Optional[str] = Query(None),
):
    """Return recent evaluation log entries (newest first)."""
    return read_tail_jsonl(limit=limit, scholar_id=scholar_id)


# ═══════════════════════════════════════════════════════════════
#  CONTINUOUS TASKS — catalog, health, mutation, run-now
# ═══════════════════════════════════════════════════════════════

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from ..services.academic.continuous_config import (
    load_continuous_tasks,
    load_raw_continuous_tasks,
    write_continuous_tasks,
)
from ..services.academic.eval_log import log_eval, log_step
from ..services.academic.evaluation_service import (
    launch_background_run,
)
from ..services.academic.heartbeat import get_heartbeat_status


_CONFIG_AUDIT_SENTINEL = "__config__"
_VALID_KINDS = {"source", "dimension", "phase_classifier", "narrative_synthesizer"}


def _step_for_task(kind: str, task_id: str) -> str:
    """Map (kind, task_id) to the step key used in eval_log entries."""
    if kind == "source":
        return f"source/{task_id}"
    if kind == "dimension":
        return f"dim/{task_id}"
    if kind == "phase_classifier":
        return "phase_classifier"
    if kind == "narrative_synthesizer":
        return "narrative"
    raise ValueError(f"unknown kind: {kind}")


def _health_from_log(
    step_key: str,
    entries: list[dict],
    now: datetime,
) -> dict:
    """Aggregate eval-log entries for one task step.

    Walks a fresh tail of entries (newest first) and summarises the
    last 7 days into `{runs_7d, success_rate_7d, avg_duration_s_7d,
    last_run_ts, last_status, last_error}`.

    Only terminal statuses (`done` / `error` / `cancelled`) count;
    `start` entries are skipped since they'd double-count every run.
    """
    seven_days_ago = now - timedelta(days=7)

    total_7d = 0
    success_7d = 0
    duration_sum = 0.0
    duration_count = 0
    last_run_ts: str | None = None
    last_status: str | None = None
    last_error: str | None = None

    for e in entries:
        if e.get("step") != step_key:
            continue
        status = e.get("status")
        if status not in ("done", "error", "cancelled"):
            continue
        ts = e.get("ts")
        if not ts:
            continue
        try:
            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)

        # Newest-first: first match wins as "last".
        if last_run_ts is None:
            last_run_ts = ts
            last_status = status
            if status != "done":
                detail = e.get("detail")
                if isinstance(detail, str):
                    last_error = detail[:200]
                elif detail:
                    last_error = str(detail)[:200]

        if ts_dt < seven_days_ago:
            continue
        total_7d += 1
        if status == "done":
            success_7d += 1
        dur = e.get("duration_s")
        if isinstance(dur, (int, float)):
            duration_sum += float(dur)
            duration_count += 1

    return {
        "runs_7d": total_7d,
        "success_rate_7d": round(success_7d / total_7d, 3) if total_7d else None,
        "avg_duration_s_7d": round(duration_sum / duration_count, 2)
        if duration_count
        else None,
        "last_run_ts": last_run_ts,
        "last_status": last_status,
        "last_error": last_error,
    }


def _build_tasks_response() -> dict:
    """Compose the GET /continuous-tasks payload.

    Re-reads the raw JSON file (not a Pydantic dump) so any optional
    fields the schema didn't model explicitly are still surfaced.
    """
    raw = load_raw_continuous_tasks()
    # Read the log tail once, re-use across every task's health calc.
    log_entries = read_tail_jsonl(limit=5000)
    now = datetime.now(timezone.utc)

    def _health(step_key: str) -> dict:
        return _health_from_log(step_key, log_entries, now)

    # Spread raw config first, then layer synthetic keys on top so
    # `kind` / `id` / `health` always win over anything the config
    # might happen to contain. The raw file already carries `layer`
    # so we don't re-set it.
    sources = [
        {
            **source_cfg,
            "id": sid,
            "kind": "source",
            "health": _health(f"source/{sid}"),
        }
        for sid, source_cfg in (raw.get("sources") or {}).items()
    ]
    dimensions = [
        {
            **dim_cfg,
            "id": did,
            "kind": "dimension",
            "health": _health(f"dim/{did}"),
        }
        for did, dim_cfg in (raw.get("dimensions") or {}).items()
    ]
    phase = {
        **(raw.get("phase_classifier") or {}),
        "id": "phase_classifier",
        "kind": "phase_classifier",
        "health": _health("phase_classifier"),
    }
    narrative = {
        **(raw.get("narrative_synthesizer") or {}),
        "id": "narrative_synthesizer",
        "kind": "narrative_synthesizer",
        "health": _health("narrative"),
    }

    return {
        "heartbeat": get_heartbeat_status(),
        "sources": sources,
        "dimensions": dimensions,
        "phase_classifier": phase,
        "narrative_synthesizer": narrative,
    }


@router.get("/continuous-tasks")
async def get_continuous_tasks():
    """Catalog of every heartbeat-dispatched task with health metrics.

    Single request returns sources, dimensions, phase_classifier,
    narrative_synthesizer, and live heartbeat liveness — enough to
    render the Tasks page on the frontend without any follow-up calls.
    """
    return _build_tasks_response()


@router.patch("/continuous-tasks/{kind}/{task_id}")
async def patch_continuous_task(
    kind: str,
    task_id: str,
    body: PatchContinuousTaskRequest,
):
    """Update one task's enabled / cadence / priority overrides.

    Validates the whole config against the Pydantic schema before
    writing, so the file is never left in a broken state. Emits an
    audit entry to the eval log with the diff.
    """
    if kind not in _VALID_KINDS:
        raise HTTPException(
            422, f"Unknown kind '{kind}'. Expected one of: {sorted(_VALID_KINDS)}"
        )

    raw = load_raw_continuous_tasks()

    # Locate the target slot. Sources and dimensions are keyed maps;
    # phase_classifier and narrative_synthesizer are fixed singletons
    # (the `task_id` path param must match the slot name for those).
    if kind == "source":
        bucket = raw.get("sources") or {}
        if task_id not in bucket:
            raise HTTPException(404, f"Source '{task_id}' not found")
        target = bucket[task_id]
    elif kind == "dimension":
        bucket = raw.get("dimensions") or {}
        if task_id not in bucket:
            raise HTTPException(404, f"Dimension '{task_id}' not found")
        target = bucket[task_id]
    elif kind == "phase_classifier":
        if task_id != "phase_classifier":
            raise HTTPException(
                422, "For kind=phase_classifier the task_id must be 'phase_classifier'"
            )
        target = raw.setdefault("phase_classifier", {})
    else:  # narrative_synthesizer
        if task_id != "narrative_synthesizer":
            raise HTTPException(
                422,
                "For kind=narrative_synthesizer the task_id must be 'narrative_synthesizer'",
            )
        target = raw.setdefault("narrative_synthesizer", {})

    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(422, "No fields to update")
    changes: dict = {}
    for k, v in patch.items():
        if target.get(k) != v:
            changes[k] = {"from": target.get(k), "to": v}
        target[k] = v

    # Validate + atomic write. `ValidationError` comes from the
    # Pydantic schema (e.g. `default_cadence_days=0`); `ValueError`
    # comes from `validate_cross_refs` (e.g. a dim referencing a
    # source the patch just disabled — doesn't apply here today but
    # future patches might). Both are caller-fixable → 422.
    try:
        write_continuous_tasks(raw)
    except ValidationError as e:
        raise HTTPException(422, f"Invalid config after patch: {e}")
    except ValueError as e:
        raise HTTPException(422, f"Invalid config after patch: {e}")

    # Audit entry — scholar_id sentinel keeps it out of per-scholar
    # filters but still visible in the global Activity Log.
    log_eval(
        _CONFIG_AUDIT_SENTINEL,
        "config/continuous_tasks",
        "ok",
        detail={"kind": kind, "id": task_id, "changes": changes},
        scholar_name="config",
    )

    return _build_tasks_response()


@router.post("/continuous-tasks/{kind}/{task_id}/run-now")
async def run_continuous_task_now(
    kind: str,
    task_id: str,
    scholar_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_academic_db),
):
    """Force-execute one task across active scholars (or one scholar).

    Reuses the existing runners (`trigger_refresh`, `run_dim_eval`,
    `run_phase_classifier`, `run_narrative_synthesizer`) and the
    `claim_evaluating` / `release_evaluating` lock pair so this can't
    race against heartbeat or a manual `/evaluate` on the same
    scholar. Returns `{ok, queued}` immediately and the user watches
    progress in the existing Activity Log.
    """
    if kind not in _VALID_KINDS:
        raise HTTPException(
            422, f"Unknown kind '{kind}'. Expected one of: {sorted(_VALID_KINDS)}"
        )

    cfg = load_continuous_tasks()
    # Validate task_id exists.
    if kind == "source":
        if task_id not in cfg.sources:
            raise HTTPException(404, f"Source '{task_id}' not found")
    elif kind == "dimension":
        if task_id not in cfg.dimensions:
            raise HTTPException(404, f"Dimension '{task_id}' not found")
    elif kind == "phase_classifier" and task_id != "phase_classifier":
        raise HTTPException(422, "For kind=phase_classifier use task_id='phase_classifier'")
    elif kind == "narrative_synthesizer" and task_id != "narrative_synthesizer":
        raise HTTPException(
            422, "For kind=narrative_synthesizer use task_id='narrative_synthesizer'"
        )

    # Target scholars: one or all active.
    if scholar_id:
        scholar = await _get_scholar_or_404(db, scholar_id)
        targets = [(scholar.id, scholar.name)]
    else:
        result = await db.execute(
            select(Scholar).where(Scholar.status == "active")
        )
        targets = [(s.id, s.name) for s in result.scalars().all()]

    if not targets:
        return {"ok": True, "queued": 0}

    launch_background_run(
        f"run-now::{kind}/{task_id}",
        lambda: _run_now_sweep(kind, task_id, targets),
    )
    return {"ok": True, "queued": len(targets)}


async def _run_now_sweep(
    kind: str,
    task_id: str,
    targets: list[tuple[str, str]],
) -> None:
    """Sequentially execute one task across *targets*, claiming the lock per scholar."""
    from ..services.academic.evaluation_service import (
        claim_evaluating,
        release_evaluating,
    )
    from ..services.academic.refresh_dispatcher import trigger_refresh
    from ..services.academic.dim_runner import run_dim_eval
    from ..services.academic.phase_classifier import run_phase_classifier
    from ..services.academic.narrative_synthesizer import run_narrative_synthesizer

    cfg = load_continuous_tasks()
    step_key = _step_for_task(kind, task_id)

    for sid, name in targets:
        if not await claim_evaluating(sid):
            logger.info("run-now: %s busy, skipping %s", sid, step_key)
            continue
        try:
            async with log_step(sid, step_key, scholar_name=name) as ctx:
                if kind == "source":
                    await trigger_refresh(
                        sid, task_id, mode="incremental", reason="run_now"
                    )
                elif kind == "dimension":
                    r = await run_dim_eval(sid, task_id, cfg=cfg, force_score=True)
                    if isinstance(r, dict) and "score" in r:
                        ctx.detail = {"score": r.get("score")}
                elif kind == "phase_classifier":
                    await run_phase_classifier(sid)
                else:  # narrative_synthesizer
                    await run_narrative_synthesizer(sid)
        except Exception:
            logger.exception("run-now: task %s failed for %s", step_key, sid)
        finally:
            await release_evaluating(sid)
