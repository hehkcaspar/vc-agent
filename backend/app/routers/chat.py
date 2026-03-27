"""Portfolio entity chat (Gemini) and preset shortcuts."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models import (
    Artifact,
    ChatCompletionJob,
    ConversationMessage,
    ConversationSession,
    Entity,
    Resource,
)
from app.schemas import (
    ChatMessageCreate,
    ChatMessageJobAccepted,
    ChatMessageJobStatus,
    ChatMessageResponse,
    ChatMessageResult,
    ChatSessionCreate,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    PresetInfoResponse,
    PresetRunRequest,
    PresetRunResponse,
)
from app.config import settings
from app.services.artifact_service import create_artifact_for_entity
from app.services.gemini_context import (
    build_deep_agent_multimodal_parts,
    build_context_parts,
    build_harness_user_attachment_text,
)
from app.services.gemini_runner import generate_json_with_context, generate_with_context
from app.services.metadata_extraction import normalize_extraction_result
from app.services.model_profiles import normalize_profile_id
from app.services.model_profiles import normalize_profile_id
from app.services.preset_registry import (
    get_preset,
    list_presets,
    render_extract_info,
    render_red_team,
)
from app.services.portfolio_deep_agent import (
    create_portfolio_agent,
    history_to_lc_messages,
    invoke_portfolio_agent,
)
from app.services.prompt_assembly import EntityBrief, build_portfolio_system_prompt

router = APIRouter(tags=["entity-chat"])


def _parse_json_loose(text: str) -> dict:
    """Parse JSON from raw, fenced, or prose-wrapped model output."""
    s = (text or "").strip()
    if not s:
        raise json.JSONDecodeError("empty response", s, 0)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", s, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj

    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        candidate = s[start : end + 1]
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    raise json.JSONDecodeError("no JSON object found", s, 0)


async def _get_entity(db: AsyncSession, entity_id: str) -> Entity:
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


async def _get_session(
    db: AsyncSession, entity_id: str, session_id: str
) -> ConversationSession:
    result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.entity_id == entity_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


async def _load_resources(
    db: AsyncSession, entity_id: str, ids: Sequence[str]
) -> List[Resource]:
    if not ids:
        return []
    result = await db.execute(
        select(Resource).where(
            Resource.entity_id == entity_id,
            Resource.id.in_(list(ids)),
        )
    )
    found = {r.id: r for r in result.scalars().all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown resource ids for this entity: {missing}",
        )
    return [found[i] for i in ids]


async def _load_artifacts(
    db: AsyncSession, entity_id: str, ids: Sequence[str]
) -> List[Artifact]:
    if not ids:
        return []
    result = await db.execute(
        select(Artifact).where(
            Artifact.entity_id == entity_id,
            Artifact.id.in_(list(ids)),
        )
    )
    found = {a.id: a for a in result.scalars().all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown artifact ids for this entity: {missing}",
        )
    return [found[i] for i in ids]


async def _job_step_update(job_id: str, step_detail: str) -> None:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
        )
        job = res.scalar_one_or_none()
        if not job or job.status in ("succeeded", "failed"):
            return
        job.step_detail = (step_detail or "")[:4000]
        job.updated_at = datetime.utcnow()
        await db.commit()


async def run_chat_agent_job(job_id: str) -> None:
    loop = asyncio.get_event_loop()
    status_trace: List[str] = []

    def on_status(msg: str) -> None:
        status_trace.append(msg)
        asyncio.run_coroutine_threadsafe(_job_step_update(job_id, msg), loop)

    tool_trace: Optional[dict] = None
    reply_text = ""
    raw: Any = None

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
            )
            job = res.scalar_one_or_none()
            if not job or job.status != "pending":
                return

            entity = await _get_entity(db, job.entity_id)
            await _get_session(db, job.entity_id, job.session_id)

            res_msg = await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.id == job.user_message_id
                )
            )
            user_row = res_msg.scalar_one_or_none()
            if not user_row:
                job.status = "failed"
                job.error_message = "user_message_missing"
                job.updated_at = datetime.utcnow()
                await db.commit()
                return

            res_all = await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == job.session_id)
                .order_by(ConversationMessage.created_at.asc())
            )
            all_msgs = res_all.scalars().all()
            idx = next(
                (i for i, m in enumerate(all_msgs) if m.id == job.user_message_id),
                None,
            )
            if idx is None:
                job.status = "failed"
                job.error_message = "user_message_not_in_session"
                job.updated_at = datetime.utcnow()
                await db.commit()
                return
            prior = all_msgs[:idx]
            history = _history_from_messages(
                prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
            )

            resource_ids = json.loads(job.resource_ids_json or "[]")
            artifact_ids = json.loads(job.artifact_ids_json or "[]")
            resources = await _load_resources(db, job.entity_id, resource_ids)
            artifacts = await _load_artifacts(db, job.entity_id, artifact_ids)
            profile_id = normalize_profile_id(job.model_profile_id)
            multimodal_parts, used_ids, _ = build_deep_agent_multimodal_parts(
                resources, profile_id
            )
            attach_preamble, _ = build_harness_user_attachment_text(
                resources, artifacts, skip_resource_ids=used_ids
            )

            user_turn = user_row.content.strip()
            if attach_preamble:
                user_turn = (
                    f"{attach_preamble}\n\n--- User message ---\n{user_turn}"
                )

            brief = EntityBrief(
                entity_id=entity.id,
                name=entity.name,
                website=entity.website,
            )
            agent_run_id_snap = job.agent_run_id or str(uuid.uuid4())
            harness_extras_snap = job.harness_extras
            session_id_snap = job.session_id
            model_profile_id_snap = job.model_profile_id
            artifact_ids_snap = list(artifact_ids)

            job.agent_run_id = agent_run_id_snap
            job.status = "running"
            job.step_detail = "Starting agent…"
            job.updated_at = datetime.utcnow()
            await db.commit()

        def _run_deep_agent() -> Tuple[str, Any]:
            agent = create_portfolio_agent(
                entity=brief,
                system_prompt_extras=harness_extras_snap,
                session_id=session_id_snap,
                session_artifact_ids=artifact_ids_snap,
                model_profile_id=model_profile_id_snap,
                run_id=agent_run_id_snap,
                initial_user_text=user_row.content,
                on_status=on_status,
            )
            lc_messages = history_to_lc_messages(history, user_turn)
            return invoke_portfolio_agent(
                agent,
                lc_messages,
                on_status=on_status,
                user_multimodal_parts=multimodal_parts,
            )

        reply_text, raw = await asyncio.to_thread(_run_deep_agent)
        if isinstance(raw, dict):
            message_count = len(raw.get("messages") or [])
            tool_trace = {
                "keys": list(raw.keys()),
                "message_count": message_count,
                "status_trace": status_trace[-40:],
                "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
            }

    except ValueError as e:
        fail_trace = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "status_trace": status_trace[-40:],
            "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
        }
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
            )
            job = res.scalar_one_or_none()
            if job and job.status not in ("succeeded", "failed"):
                job.status = "failed"
                job.error_message = str(e)
                job.tool_trace_json = json.dumps(fail_trace)
                job.updated_at = datetime.utcnow()
                await db.commit()
        return
    except Exception as e:
        fail_trace = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "status_trace": status_trace[-40:],
            "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
        }
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
            )
            job = res.scalar_one_or_none()
            if job and job.status not in ("succeeded", "failed"):
                job.status = "failed"
                job.error_message = str(e)
                job.tool_trace_json = json.dumps(fail_trace)
                job.updated_at = datetime.utcnow()
                await db.commit()
        return

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
        )
        job = res.scalar_one_or_none()
        if not job or job.status in ("failed", "succeeded"):
            return
        sess = await _get_session(db, job.entity_id, job.session_id)
        assistant_msg = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=job.session_id,
            role="assistant",
            content=reply_text,
        )
        db.add(assistant_msg)
        job.assistant_message_id = assistant_msg.id
        job.status = "succeeded"
        job.step_detail = "Done"
        if tool_trace:
            job.tool_trace_json = json.dumps(tool_trace)
        elif status_trace:
            job.tool_trace_json = json.dumps(
                {
                    "status_trace": status_trace[-40:],
                    "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
                }
            )
        else:
            job.tool_trace_json = None
        job.updated_at = datetime.utcnow()
        sess.updated_at = datetime.utcnow()
        await db.commit()


def _history_content_for_model(content: str) -> str:
    """Avoid feeding large or opaque JSON artifact cards into Gemini as verbatim history."""
    text = content.strip()
    if not text.startswith("{"):
        return content
    try:
        data = json.loads(text)
        if data.get("_vc_chat") == "artifact_card":
            label = data.get("artifact_title") or data.get("artifact_type") or "artifact"
            ver = data.get("version", "?")
            aid = data.get("artifact_id", "")
            preset = data.get("preset_label", "")
            tail = f" ({preset})" if preset else ""
            return (
                f"[Created saved artifact `{label}` v{ver}, id={aid}{tail}. "
                "Full document is in workspace artifacts.]"
            )
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return content


def _history_from_messages(
    rows: List[ConversationMessage], max_pairs: int
) -> List[Tuple[str, str]]:
    """Return (role, text) for user/model; role names user|assistant."""
    out: List[Tuple[str, str]] = []
    for m in rows:
        if m.role not in ("user", "assistant"):
            continue
        r = "user" if m.role == "user" else "assistant"
        body = (
            _history_content_for_model(m.content)
            if m.role == "assistant"
            else m.content
        )
        out.append((r, body))
    # keep last N messages (count user+assistant)
    limit = max_pairs * 2
    if len(out) > limit:
        out = out[-limit:]
    return out


@router.get("/entities/{entity_id}/chat/presets", response_model=List[PresetInfoResponse])
async def list_chat_presets(entity_id: str, db: AsyncSession = Depends(get_db)):
    await _get_entity(db, entity_id)
    return [
        PresetInfoResponse(id=p.id, label=p.label, description=p.description)
        for p in list_presets()
    ]


@router.get(
    "/entities/{entity_id}/chat/sessions",
    response_model=List[ChatSessionResponse],
)
async def list_chat_sessions(entity_id: str, db: AsyncSession = Depends(get_db)):
    await _get_entity(db, entity_id)
    result = await db.execute(
        select(ConversationSession)
        .where(ConversationSession.entity_id == entity_id)
        .order_by(ConversationSession.updated_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/entities/{entity_id}/chat/sessions",
    response_model=ChatSessionResponse,
)
async def create_chat_session(
    entity_id: str,
    body: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
):
    await _get_entity(db, entity_id)
    session = ConversationSession(
        id=str(uuid.uuid4()),
        entity_id=entity_id,
        title=body.title,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get(
    "/entities/{entity_id}/chat/sessions/{session_id}",
    response_model=ChatSessionDetailResponse,
)
async def get_chat_session(
    entity_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, entity_id, session_id)
    result = await db.execute(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return ChatSessionDetailResponse(
        session=session,
        messages=messages,
    )


@router.delete("/entities/{entity_id}/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    entity_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _get_entity(db, entity_id)
    session = await _get_session(db, entity_id, session_id)
    await db.execute(
        delete(ChatCompletionJob).where(ChatCompletionJob.session_id == session_id)
    )
    await db.delete(session)
    await db.commit()
    return Response(status_code=204)


@router.get(
    "/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}",
    response_model=ChatMessageJobStatus,
)
async def get_chat_message_job(
    entity_id: str,
    session_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _get_entity(db, entity_id)
    await _get_session(db, entity_id, session_id)
    res = await db.execute(
        select(ChatCompletionJob).where(
            ChatCompletionJob.id == job_id,
            ChatCompletionJob.session_id == session_id,
            ChatCompletionJob.entity_id == entity_id,
        )
    )
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    warnings: List[str] = (
        json.loads(job.warnings_json) if job.warnings_json else []
    )
    assistant: Optional[ChatMessageResponse] = None
    if job.assistant_message_id:
        ra = await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.id == job.assistant_message_id
            )
        )
        row = ra.scalar_one_or_none()
        if row:
            assistant = ChatMessageResponse.model_validate(row)
    tool_trace = (
        json.loads(job.tool_trace_json) if job.tool_trace_json else None
    )
    return ChatMessageJobStatus(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        step_detail=job.step_detail,
        user_message_id=job.user_message_id,
        assistant_message=assistant,
        warnings=warnings,
        error_message=job.error_message,
        run_id=job.agent_run_id,
        tool_trace=tool_trace,
    )


@router.post(
    "/entities/{entity_id}/chat/sessions/{session_id}/messages",
    response_model=None,
    responses={
        200: {"model": ChatMessageResult},
        202: {"model": ChatMessageJobAccepted},
    },
)
async def post_chat_message(
    entity_id: str,
    session_id: str,
    body: ChatMessageCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    entity = await _get_entity(db, entity_id)
    session = await _get_session(db, entity_id, session_id)

    result = await db.execute(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    prior = result.scalars().all()
    history = _history_from_messages(
        prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
    )

    resources = await _load_resources(db, entity_id, body.resource_ids)
    artifacts = await _load_artifacts(db, entity_id, body.artifact_ids)
    use_deep_agent = (
        body.use_deep_agent
        if body.use_deep_agent is not None
        else settings.CHAT_USE_DEEP_AGENT
    )
    if use_deep_agent:
        profile_id = normalize_profile_id(body.model_profile_id)
        _, used_ids, mm_warnings = build_deep_agent_multimodal_parts(
            resources, profile_id
        )
        attach_preamble, warnings = build_harness_user_attachment_text(
            resources, artifacts, skip_resource_ids=used_ids
        )
        warnings.extend(mm_warnings)
        context_parts = None
    else:
        attach_preamble = ""
        context_parts, warnings = await build_context_parts(resources, artifacts)

    diff_bits = []
    if body.resource_ids:
        diff_bits.append(f"{len(body.resource_ids)} resource(s) attached for this turn.")
    if body.artifact_ids:
        diff_bits.append(f"{len(body.artifact_ids)} artifact excerpt(s) attached.")
    diff_summary = "\n".join(diff_bits) if diff_bits else None

    brief = EntityBrief(
        entity_id=entity.id,
        name=entity.name,
        website=entity.website,
    )
    system_prompt = build_portfolio_system_prompt(
        brief,
        diff_summary=diff_summary,
        task_block="Answer the user's latest message using the attached context and search when needed.",
    )

    run_id: Optional[str] = None
    tool_trace: Optional[dict] = None
    reply_text = ""

    if use_deep_agent:
        run_id = str(uuid.uuid4())
        ctx_note = []
        if body.resource_ids or body.artifact_ids or attach_preamble:
            ctx_note.append(
                "The user attached resources and/or artifacts for this turn; "
                "use tools to list or read full saved documents when needed."
            )
        task_lines = [
            "Answer the user's latest message using search when needed.",
            "Use portfolio_* tools to list or read artifacts and resources; use apply only after validation.",
        ]
        if ctx_note:
            task_lines.insert(0, ctx_note[0])
        extras = ""
        if diff_summary:
            extras += f"## Resource / corpus changes\n{diff_summary}\n\n"
        extras += "## Task\n" + "\n".join(task_lines)

        user_msg = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=body.text.strip(),
        )
        db.add(user_msg)
        await db.flush()
        job = ChatCompletionJob(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            session_id=session_id,
            user_message_id=user_msg.id,
            status="pending",
            step_detail="Queued…",
            agent_run_id=run_id,
            resource_ids_json=json.dumps(body.resource_ids),
            artifact_ids_json=json.dumps(body.artifact_ids),
            model_profile_id=body.model_profile_id,
            harness_extras=extras,
            warnings_json=json.dumps(warnings),
        )
        db.add(job)
        session.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user_msg)
        background_tasks.add_task(run_chat_agent_job, job.id)
        return JSONResponse(
            status_code=202,
            content=ChatMessageJobAccepted(
                job_id=job.id,
                user_message=ChatMessageResponse.model_validate(user_msg),
                warnings=warnings,
            ).model_dump(mode="json"),
        )

    try:
        reply_text = generate_with_context(
            system_instruction=system_prompt,
            history=history,
            user_message_text=body.text.strip(),
            context_parts=context_parts,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    user_msg = ConversationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=body.text.strip(),
    )
    assistant_msg = ConversationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role="assistant",
        content=reply_text,
    )
    db.add(user_msg)
    db.add(assistant_msg)
    session.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(assistant_msg)

    return ChatMessageResult(
        assistant_message=assistant_msg,
        warnings=warnings,
        run_id=run_id,
        tool_trace=tool_trace,
    )


@router.post(
    "/entities/{entity_id}/chat/presets/{preset_id}/run",
    response_model=PresetRunResponse,
)
async def run_chat_preset(
    entity_id: str,
    preset_id: str,
    body: PresetRunRequest,
    db: AsyncSession = Depends(get_db),
):
    entity = await _get_entity(db, entity_id)
    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Unknown preset")

    resources = await _load_resources(db, entity_id, body.resource_ids)
    artifacts = await _load_artifacts(db, entity_id, body.artifact_ids)
    use_deep_agent = (
        body.use_deep_agent
        if body.use_deep_agent is not None
        else settings.CHAT_USE_DEEP_AGENT
    )
    context_parts = None
    if use_deep_agent:
        profile_id = normalize_profile_id(body.model_profile_id)
        multimodal_parts, used_ids, mm_warnings = build_deep_agent_multimodal_parts(
            resources, profile_id
        )
        attach_preamble, warnings = build_harness_user_attachment_text(
            resources, artifacts, skip_resource_ids=used_ids
        )
        warnings.extend(mm_warnings)
    else:
        multimodal_parts = []
        attach_preamble = ""
        context_parts, warnings = await build_context_parts(resources, artifacts)

    history: List[Tuple[str, str]] = []
    if body.session_id:
        await _get_session(db, entity_id, body.session_id)
        # Keep preset extraction deterministic: use attachments as source of truth.
        if preset_id != "extract_info":
            result = await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == body.session_id)
                .order_by(ConversationMessage.created_at.asc())
            )
            history = _history_from_messages(
                result.scalars().all(),
                settings.CHAT_MAX_HISTORY_MESSAGES // 2,
            )

    if preset_id == "red_team":
        task_body = render_red_team(
            startup_name=entity.name,
            industry=body.industry,
            stage=body.stage,
        )
    elif preset_id == "extract_info":
        task_body = render_extract_info(entity.name, entity.website)
    else:
        raise HTTPException(status_code=400, detail="Preset not implemented")

    diff_bits = [
        f"Running preset: {preset.label} ({preset.id}).",
    ]
    if body.resource_ids:
        diff_bits.append(f"{len(body.resource_ids)} resource(s) attached.")
    if body.artifact_ids:
        diff_bits.append(f"{len(body.artifact_ids)} artifact excerpt(s) attached.")
    diff_summary = "\n".join(diff_bits)

    brief = EntityBrief(
        entity_id=entity.id,
        name=entity.name,
        website=entity.website,
    )
    system_prompt = build_portfolio_system_prompt(
        brief,
        diff_summary=diff_summary,
        task_block=task_body,
    )

    file_suffix = ".json" if preset.output_kind == "json" else ".md"

    try:
        if preset.output_kind == "json":
            if use_deep_agent:
                run_id = str(uuid.uuid4())
                extras = f"## Preset\n{preset.label} ({preset.id})\n\n## Task\n{task_body}"
                agent = create_portfolio_agent(
                    entity=brief,
                    system_prompt_extras=extras,
                    session_id=body.session_id or f"preset-{preset.id}",
                    session_artifact_ids=body.artifact_ids,
                    model_profile_id=body.model_profile_id,
                    run_id=run_id,
                    initial_user_text=task_body,
                )
                user_turn = (
                    "Using only the attached materials, output one JSON object exactly as requested."
                )
                if attach_preamble:
                    user_turn = (
                        f"{attach_preamble}\n\n--- User instruction ---\n{user_turn}"
                    )
                lc_messages = history_to_lc_messages(history, user_turn)
                raw_json, _ = invoke_portfolio_agent(
                    agent, lc_messages, user_multimodal_parts=multimodal_parts
                )
            else:
                raw_json = generate_json_with_context(
                    system_instruction=system_prompt,
                    history=history,
                    user_message_text=(
                        "Using only the attached materials (and Google Search when enabled), "
                        "output a single JSON object exactly as specified in the system instructions."
                    ),
                    context_parts=context_parts,
                )
            try:
                parsed = _parse_json_loose(raw_json)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Model returned invalid JSON: {e}",
                ) from e
            normalized = normalize_extraction_result(parsed)
            artifact_body = json.dumps(normalized, indent=2, ensure_ascii=False)
        else:
            if use_deep_agent:
                run_id = str(uuid.uuid4())
                extras = f"## Preset\n{preset.label} ({preset.id})\n\n## Task\n{task_body}"
                agent = create_portfolio_agent(
                    entity=brief,
                    system_prompt_extras=extras,
                    session_id=body.session_id or f"preset-{preset.id}",
                    session_artifact_ids=body.artifact_ids,
                    model_profile_id=body.model_profile_id,
                    run_id=run_id,
                    initial_user_text=task_body,
                )
                user_turn = (
                    "Execute the instructions above and output the full markdown report now."
                )
                if attach_preamble:
                    user_turn = (
                        f"{attach_preamble}\n\n--- User instruction ---\n{user_turn}"
                    )
                lc_messages = history_to_lc_messages(history, user_turn)
                artifact_body, _ = invoke_portfolio_agent(
                    agent, lc_messages, user_multimodal_parts=multimodal_parts
                )
            else:
                artifact_body = generate_with_context(
                    system_instruction=system_prompt,
                    history=history,
                    user_message_text=(
                        "Execute the instructions above and output the full markdown report now."
                    ),
                    context_parts=context_parts,
                )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    atype = body.artifact_type or preset.default_artifact_type
    astatus = body.artifact_status or preset.default_artifact_status

    try:
        artifact = await create_artifact_for_entity(
            db,
            entity_id,
            atype,
            artifact_body,
            astatus,
            title=preset.artifact_title,
            file_suffix=file_suffix,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Entity not found") from None

    summary = f"Created artifact `{artifact.id}` ({artifact.artifact_type} v{artifact.version})."

    if body.session_id:
        sess = await _get_session(db, entity_id, body.session_id)
        artifact_card = {
            "_vc_chat": "artifact_card",
            "artifact_id": artifact.id,
            "entity_id": entity_id,
            "preset_label": preset.label,
            "artifact_type": artifact.artifact_type,
            "artifact_title": artifact.title,
            "version": artifact.version,
            "status": artifact.status,
            "summary": summary,
        }
        note = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=body.session_id,
            role="assistant",
            content=json.dumps(artifact_card),
        )
        db.add(note)
        sess.updated_at = datetime.utcnow()
        await db.commit()

    return PresetRunResponse(
        artifact_id=artifact.id,
        assistant_summary=summary,
        warnings=warnings,
    )
