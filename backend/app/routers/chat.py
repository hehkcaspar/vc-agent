"""Portfolio entity chat (Gemini + Kimi direct) and preset shortcuts."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, List, Optional, Sequence, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.datetime_support import utc_now
from app.models import (
    ChatCompletionJob,
    ConversationMessage,
    ConversationSession,
    Entity,
    WorkspaceNode,
)
from app.schemas import (
    ChatMessageCreate,
    ChatMessageJobAccepted,
    PresetRunJobAccepted,
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
from app.services.gemini_context import (
    build_deep_agent_multimodal_parts,
    build_context_parts,
    build_harness_user_attachment_text,
)
from app.services.direct_llm import (
    generate_json_one_shot,
    generate_one_shot,
    generate_with_interaction,
    generate_with_kimi,
)
from app.services.json_loose import parse_json_loose
from app.services.metadata_extraction import normalize_extraction_result
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
from app.services.storage import storage
from app.services.workspace import WorkspaceService, Actor

router = APIRouter(tags=["entity-chat"])
workspace_service = WorkspaceService(storage)


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


async def _load_nodes(
    db: AsyncSession, entity_id: str, ids: Sequence[str]
) -> List[WorkspaceNode]:
    if not ids:
        return []
    result = await db.execute(
        select(WorkspaceNode).where(
            WorkspaceNode.entity_id == entity_id,
            WorkspaceNode.id.in_(list(ids)),
            WorkspaceNode.deleted_at.is_(None),
        )
    )
    found = {n.id: n for n in result.scalars().all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown node ids for this entity: {missing}",
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
        job.updated_at = utc_now()
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
                job.updated_at = utc_now()
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
                job.updated_at = utc_now()
                await db.commit()
                return
            prior = all_msgs[:idx]
            history = _history_from_messages(
                prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
            )

            node_ids = json.loads(job.node_ids_json or "[]")
            nodes = await _load_nodes(db, job.entity_id, node_ids)
            profile_id = normalize_profile_id(job.model_profile_id)
            multimodal_parts, used_ids, _ = build_deep_agent_multimodal_parts(
                nodes, profile_id
            )
            attach_preamble, _ = build_harness_user_attachment_text(
                nodes, skip_node_ids=used_ids
            )

            # Build workspace context (three-layer: tree + descriptions + notes)
            workspace_context = await workspace_service.build_annotated_tree_text(
                db, job.entity_id
            )

            user_turn = user_row.content.strip()
            preamble_parts = []
            if workspace_context:
                preamble_parts.append(workspace_context)
            if attach_preamble:
                preamble_parts.append(attach_preamble)
            if preamble_parts:
                user_turn = (
                    "\n\n".join(preamble_parts) + f"\n\n--- User message ---\n{user_turn}"
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

            job.agent_run_id = agent_run_id_snap
            job.status = "running"
            job.step_detail = "Starting agent..."
            job.updated_at = utc_now()
            await db.commit()

        def _run_deep_agent() -> Tuple[str, Any]:
            agent = create_portfolio_agent(
                entity=brief,
                system_prompt_extras=harness_extras_snap,
                session_id=session_id_snap,
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

    except (ValueError, Exception) as e:
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
                job.updated_at = utc_now()
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
            model_profile_id=job.model_profile_id,
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
        job.updated_at = utc_now()
        sess.updated_at = utc_now()
        await db.commit()


async def run_preset_agent_job(job_id: str) -> None:
    """Background worker for deep-agent preset shortcuts (Red Team etc.).

    Mirrors run_chat_agent_job: streams step_detail via on_status, and on
    success writes the deliverable to the workspace and appends a deliverable
    card message to the session.
    """
    loop = asyncio.get_event_loop()
    status_trace: List[str] = []

    def on_status(msg: str) -> None:
        status_trace.append(msg)
        asyncio.run_coroutine_threadsafe(_job_step_update(job_id, msg), loop)

    deliverable_body = ""
    raw: Any = None

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
            )
            job = res.scalar_one_or_none()
            if not job or job.status != "pending":
                return
            if not job.preset_payload_json:
                job.status = "failed"
                job.error_message = "preset_payload_missing"
                job.updated_at = utc_now()
                await db.commit()
                return

            payload = json.loads(job.preset_payload_json)
            preset_id = payload.get("preset_id")
            preset = get_preset(preset_id) if preset_id else None
            if not preset:
                job.status = "failed"
                job.error_message = f"unknown_preset:{preset_id}"
                job.updated_at = utc_now()
                await db.commit()
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
                job.updated_at = utc_now()
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
            prior = all_msgs[:idx] if idx is not None else all_msgs
            history = _history_from_messages(
                prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
            )

            node_ids = json.loads(job.node_ids_json or "[]")
            nodes = await _load_nodes(db, job.entity_id, node_ids)
            profile_id = normalize_profile_id(job.model_profile_id)
            multimodal_parts, used_ids, _ = build_deep_agent_multimodal_parts(
                nodes, profile_id
            )
            attach_preamble, _ = build_harness_user_attachment_text(
                nodes, skip_node_ids=used_ids
            )

            workspace_context = await workspace_service.build_annotated_tree_text(
                db, job.entity_id
            )

            if preset_id == "red_team":
                task_body = render_red_team(
                    startup_name=entity.name,
                    industry=payload.get("industry"),
                    stage=payload.get("stage"),
                )
            else:
                job.status = "failed"
                job.error_message = f"preset_not_implemented:{preset_id}"
                job.updated_at = utc_now()
                await db.commit()
                return

            if preset.output_kind == "json":
                user_turn_core = (
                    "Using only the attached materials, output one JSON object "
                    "exactly as requested."
                )
            else:
                user_turn_core = (
                    "Execute the instructions above and output the full markdown report now."
                )
            preamble_parts = []
            if workspace_context:
                preamble_parts.append(workspace_context)
            if attach_preamble:
                preamble_parts.append(attach_preamble)
            if preamble_parts:
                user_turn = (
                    "\n\n".join(preamble_parts)
                    + f"\n\n--- User instruction ---\n{user_turn_core}"
                )
            else:
                user_turn = user_turn_core

            brief = EntityBrief(
                entity_id=entity.id,
                name=entity.name,
                website=entity.website,
            )
            agent_run_id_snap = job.agent_run_id or str(uuid.uuid4())
            harness_extras_snap = job.harness_extras
            session_id_snap = job.session_id
            model_profile_id_snap = job.model_profile_id

            job.agent_run_id = agent_run_id_snap
            job.status = "running"
            job.step_detail = "Starting agent..."
            job.updated_at = utc_now()
            await db.commit()

            output_kind_snap = preset.output_kind
            preset_label_snap = preset.label
            default_artifact_type_snap = preset.default_artifact_type
            default_artifact_status_snap = preset.default_artifact_status
            artifact_title_snap = preset.artifact_title

        def _run_deep_agent() -> Tuple[str, Any]:
            agent = create_portfolio_agent(
                entity=brief,
                system_prompt_extras=harness_extras_snap,
                session_id=session_id_snap,
                model_profile_id=model_profile_id_snap,
                run_id=agent_run_id_snap,
                initial_user_text=task_body,
                on_status=on_status,
            )
            lc_messages = history_to_lc_messages(history, user_turn)
            return invoke_portfolio_agent(
                agent,
                lc_messages,
                on_status=on_status,
                user_multimodal_parts=multimodal_parts,
            )

        deliverable_body, raw = await asyncio.to_thread(_run_deep_agent)

        # Post-process JSON presets
        if output_kind_snap == "json":
            try:
                parsed = parse_json_loose(deliverable_body)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Model returned invalid JSON: {e}") from e
            normalized = normalize_extraction_result(parsed)
            deliverable_body = json.dumps(normalized, indent=2, ensure_ascii=False)

        # Write deliverable to workspace + append card message
        file_suffix = ".json" if output_kind_snap == "json" else ".md"
        dtype = payload.get("deliverable_type") or default_artifact_type_snap
        dstatus = payload.get("deliverable_status") or default_artifact_status_snap
        title = artifact_title_snap or dtype
        folder = {
            "memo": "Deliverables/Memos",
            "factsheet": "Deliverables/Factsheets",
            "report": "Deliverables/Reports",
        }.get(dtype, "Deliverables")
        path = f"{folder}/{title}{file_suffix}"
        actor = Actor(type="system", ref=f"preset:{preset_id}")

        async with AsyncSessionLocal() as db:
            node = await workspace_service.write_file(
                db,
                job.entity_id,
                path,
                deliverable_body.encode("utf-8"),
                "application/json" if file_suffix == ".json" else "text/markdown",
                actor,
                metadata={"deliverable_type": dtype, "status": dstatus},
            )
            node.origin_type = "agent"
            await db.commit()
            await db.refresh(node)

            sess = await _get_session(db, job.entity_id, job.session_id)
            summary = (
                f"Created deliverable `{node.name}` v{node.version} at {node.path}."
            )
            deliverable_card = {
                "_vc_chat": "artifact_card",
                "node_id": node.id,
                "entity_id": job.entity_id,
                "preset_label": preset_label_snap,
                "deliverable_type": dtype,
                "artifact_title": title,
                "version": node.version,
                "status": dstatus,
                "summary": summary,
                "path": node.path,
            }
            assistant_msg = ConversationMessage(
                id=str(uuid.uuid4()),
                session_id=job.session_id,
                role="assistant",
                content=json.dumps(deliverable_card),
                model_profile_id=normalize_profile_id(model_profile_id_snap),
            )
            db.add(assistant_msg)
            res2 = await db.execute(
                select(ChatCompletionJob).where(ChatCompletionJob.id == job_id)
            )
            job2 = res2.scalar_one_or_none()
            if job2 and job2.status not in ("succeeded", "failed"):
                job2.assistant_message_id = assistant_msg.id
                job2.status = "succeeded"
                job2.step_detail = "Done"
                tool_trace: dict = {
                    "status_trace": status_trace[-40:],
                    "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
                }
                if isinstance(raw, dict):
                    tool_trace["keys"] = list(raw.keys())
                    tool_trace["message_count"] = len(raw.get("messages") or [])
                job2.tool_trace_json = json.dumps(tool_trace)
                job2.updated_at = utc_now()
            sess.updated_at = utc_now()
            await db.commit()

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
                job.updated_at = utc_now()
                await db.commit()


def _history_content_for_model(content: str) -> str:
    text = content.strip()
    if not text.startswith("{"):
        return content
    try:
        data = json.loads(text)
        if data.get("_vc_chat") == "artifact_card":
            label = data.get("artifact_title") or data.get("deliverable_type") or "deliverable"
            ver = data.get("version", "?")
            nid = data.get("node_id", "")
            preset = data.get("preset_label", "")
            tail = f" ({preset})" if preset else ""
            return (
                f"[Created deliverable `{label}` v{ver}, id={nid}{tail}. "
                "Full document is in workspace.]"
            )
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return content


def _history_from_messages(
    rows: List[ConversationMessage], max_pairs: int
) -> List[Tuple[str, str]]:
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
    limit = max_pairs * 2
    if len(out) > limit:
        out = out[-limit:]
    return out


logger = logging.getLogger(__name__)


def _interaction_still_valid(session: ConversationSession) -> bool:
    """Check if the session's Gemini Interactions API chain is still valid (within TTL)."""
    if not session.last_gemini_interaction_id:
        return False
    if not session.last_gemini_interaction_at:
        return False
    age = (utc_now() - session.last_gemini_interaction_at).days
    return age < settings.GEMINI_INTERACTION_TTL_DAYS


def _is_interaction_not_found(e: Exception) -> bool:
    """Check if a Gemini exception indicates the previous_interaction_id was invalid/expired."""
    err_str = str(e).lower()
    return "not found" in err_str or "interaction" in err_str


async def _build_history_with_summary(
    all_messages: List[ConversationMessage],
    max_pairs: int,
) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """
    If conversation fits in window: returns all messages, no summary.
    If exceeds: summarizes older portion via cheap flash model,
    returns recent window + summary preamble.
    """
    history = _history_from_messages(all_messages, max_pairs)

    total_eligible = sum(1 for m in all_messages if m.role in ("user", "assistant"))
    if total_eligible <= max_pairs * 2:
        return history, None

    # Older messages that got truncated
    all_pairs = _history_from_messages(all_messages, len(all_messages))
    truncated = all_pairs[: -(max_pairs * 2)]

    try:
        summary = await asyncio.to_thread(
            generate_one_shot,
            system_instruction="You are a conversation summarizer.",
            history=[],
            user_message_text=(
                "Summarize this conversation concisely. "
                "Preserve key facts, decisions, and context.\n\n"
                + "\n".join(f"{r.title()}: {t}" for r, t in truncated)
            ),
            enable_google_search=False,
            model=settings.GEMINI_METADATA_EXTRACTION_MODEL,
        )
    except Exception:
        logger.warning("History summarization failed; proceeding without summary")
        return history, None

    preamble = f"[Summary of earlier conversation ({len(truncated)} messages):\n{summary}\n]"
    return history, preamble


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
        status=job.status,
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

    nodes = await _load_nodes(db, entity_id, body.node_ids)
    profile_id = normalize_profile_id(body.model_profile_id)
    use_deep_agent = (
        body.use_deep_agent
        if body.use_deep_agent is not None
        else settings.CHAT_USE_DEEP_AGENT
    )
    if use_deep_agent:
        _, used_ids, mm_warnings = build_deep_agent_multimodal_parts(
            nodes, profile_id
        )
        attach_preamble, warnings = build_harness_user_attachment_text(
            nodes, skip_node_ids=used_ids
        )
        warnings.extend(mm_warnings)
        context_parts = None
    elif profile_id == "kimi_moonshot":
        attach_preamble, warnings = build_harness_user_attachment_text(nodes)
        context_parts = None
    else:
        attach_preamble = ""
        context_parts, warnings = await build_context_parts(nodes)

    diff_bits = []
    if body.node_ids:
        diff_bits.append(f"{len(body.node_ids)} file(s) attached for this turn.")
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
        if body.node_ids or attach_preamble:
            ctx_note.append(
                "The user attached files for this turn; "
                "use workspace tools to browse or read full documents when needed."
            )
        task_lines = [
            "Answer the user's latest message using search when needed.",
            "Use workspace_* tools to list, read, and manage files.",
        ]
        if ctx_note:
            task_lines.insert(0, ctx_note[0])
        extras = ""
        if diff_summary:
            extras += f"## File context\n{diff_summary}\n\n"
        extras += "## Task\n" + "\n".join(task_lines)

        user_msg = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=body.text.strip(),
            model_profile_id=profile_id,
            node_ids_json=json.dumps(body.node_ids) if body.node_ids else None,
        )
        db.add(user_msg)
        await db.flush()
        job = ChatCompletionJob(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            session_id=session_id,
            user_message_id=user_msg.id,
            status="pending",
            step_detail="Queued...",
            agent_run_id=run_id,
            node_ids_json=json.dumps(body.node_ids),
            model_profile_id=body.model_profile_id,
            harness_extras=extras,
            warnings_json=json.dumps(warnings),
        )
        db.add(job)
        session.updated_at = utc_now()
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
        if profile_id == "kimi_moonshot":
            # Kimi: stateless, text-only attachments
            history_pairs, summary_preamble = await _build_history_with_summary(
                prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
            )
            user_text = body.text.strip()
            if attach_preamble:
                user_text = attach_preamble + "\n\n--- User message ---\n" + user_text
            if summary_preamble:
                user_text = summary_preamble + "\n\n" + user_text

            reply_text = await asyncio.to_thread(
                generate_with_kimi,
                system_instruction=system_prompt,
                history=history_pairs,
                user_message_text=user_text,
            )

            # Invalidate Gemini chain
            session.last_gemini_interaction_id = None
            session.last_gemini_interaction_at = None
        else:
            # Gemini with Interactions API
            prev_id = (
                session.last_gemini_interaction_id
                if _interaction_still_valid(session)
                else None
            )

            if prev_id:
                history_for_fresh = None
                summary_preamble = None
            else:
                history_pairs, summary_preamble = await _build_history_with_summary(
                    prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
                )
                history_for_fresh = history_pairs

            user_text = body.text.strip()
            if summary_preamble:
                user_text = summary_preamble + "\n\n" + user_text

            try:
                reply_text, new_id = await asyncio.to_thread(
                    generate_with_interaction,
                    system_instruction=system_prompt,
                    user_message_text=user_text,
                    context_parts=context_parts,
                    previous_interaction_id=prev_id,
                    history_for_fresh_chain=history_for_fresh,
                )
            except Exception as e:
                if prev_id and _is_interaction_not_found(e):
                    # Chain broke — fall back to fresh
                    logger.warning("Gemini interaction chain broke (prev_id=%s): %s", prev_id, e)
                    if not history_for_fresh:
                        history_pairs, summary_preamble = await _build_history_with_summary(
                            prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
                        )
                        history_for_fresh = history_pairs
                        if summary_preamble:
                            user_text = summary_preamble + "\n\n" + body.text.strip()
                    reply_text, new_id = await asyncio.to_thread(
                        generate_with_interaction,
                        system_instruction=system_prompt,
                        user_message_text=user_text,
                        context_parts=context_parts,
                        previous_interaction_id=None,
                        history_for_fresh_chain=history_for_fresh,
                    )
                else:
                    raise

            session.last_gemini_interaction_id = new_id
            session.last_gemini_interaction_at = utc_now()

    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    user_msg = ConversationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=body.text.strip(),
        model_profile_id=profile_id,
        node_ids_json=json.dumps(body.node_ids) if body.node_ids else None,
    )
    assistant_msg = ConversationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role="assistant",
        content=reply_text,
        model_profile_id=profile_id,
    )
    db.add(user_msg)
    db.add(assistant_msg)
    session.updated_at = utc_now()
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
    responses={202: {"model": PresetRunJobAccepted}},
)
async def run_chat_preset(
    entity_id: str,
    preset_id: str,
    body: PresetRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    entity = await _get_entity(db, entity_id)
    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Unknown preset")

    nodes = await _load_nodes(db, entity_id, body.node_ids)
    profile_id = normalize_profile_id(body.model_profile_id)
    use_deep_agent = (
        body.use_deep_agent
        if body.use_deep_agent is not None
        else settings.CHAT_USE_DEEP_AGENT
    )
    # extract_info is a one-shot JSON extraction — never route through the agent
    if preset_id == "extract_info":
        use_deep_agent = False

    # ----- Deep-agent path: dispatch as background job, return 202 -----
    if use_deep_agent:
        if not body.session_id:
            raise HTTPException(
                status_code=400,
                detail="session_id is required when running a preset with the deep agent",
            )
        session = await _get_session(db, entity_id, body.session_id)

        if preset_id == "red_team":
            task_body = render_red_team(
                startup_name=entity.name,
                industry=body.industry,
                stage=body.stage,
            )
        else:
            raise HTTPException(status_code=400, detail="Preset not implemented")

        _, _, mm_warnings = build_deep_agent_multimodal_parts(nodes, profile_id)
        warnings = list(mm_warnings)

        extras = (
            f"## Preset\n{preset.label} ({preset.id})\n\n## Task\n{task_body}"
        )

        user_msg = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=body.session_id,
            role="user",
            content=f"▶ Run preset: {preset.label}",
            model_profile_id=profile_id,
            node_ids_json=json.dumps(body.node_ids) if body.node_ids else None,
        )
        db.add(user_msg)
        await db.flush()

        preset_payload = {
            "preset_id": preset_id,
            "deliverable_type": body.deliverable_type,
            "deliverable_status": body.deliverable_status,
            "industry": body.industry,
            "stage": body.stage,
        }

        job = ChatCompletionJob(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            session_id=body.session_id,
            user_message_id=user_msg.id,
            status="pending",
            step_detail="Queued...",
            agent_run_id=str(uuid.uuid4()),
            node_ids_json=json.dumps(body.node_ids),
            model_profile_id=body.model_profile_id,
            harness_extras=extras,
            warnings_json=json.dumps(warnings),
            preset_payload_json=json.dumps(preset_payload),
        )
        db.add(job)
        session.updated_at = utc_now()
        await db.commit()
        await db.refresh(user_msg)

        background_tasks.add_task(run_preset_agent_job, job.id)
        return JSONResponse(
            status_code=202,
            content=PresetRunJobAccepted(
                job_id=job.id,
                session_id=body.session_id,
                user_message=ChatMessageResponse.model_validate(user_msg),
                warnings=warnings,
            ).model_dump(mode="json"),
        )

    # ----- Synchronous (one-shot) path -----
    context_parts = None
    if profile_id == "kimi_moonshot":
        multimodal_parts = []
        attach_preamble, warnings = build_harness_user_attachment_text(nodes)
        context_parts = None
    else:
        multimodal_parts = []
        attach_preamble = ""
        context_parts, warnings = await build_context_parts(nodes)

    history: List[Tuple[str, str]] = []
    if body.session_id:
        await _get_session(db, entity_id, body.session_id)
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

    diff_bits = [f"Running preset: {preset.label} ({preset.id})."]
    if body.node_ids:
        diff_bits.append(f"{len(body.node_ids)} file(s) attached.")
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
            preset_user_text = (
                "Using only the attached materials (and Google Search when enabled), "
                "output a single JSON object exactly as specified in the system instructions."
            )
            if profile_id == "kimi_moonshot":
                if attach_preamble:
                    preset_user_text = attach_preamble + "\n\n--- User instruction ---\n" + preset_user_text
                raw_json = await asyncio.to_thread(
                    generate_with_kimi,
                    system_instruction=system_prompt,
                    history=history,
                    user_message_text=preset_user_text,
                )
            else:
                raw_json = await asyncio.to_thread(
                    generate_json_one_shot,
                    system_instruction=system_prompt,
                    history=history,
                    user_message_text=preset_user_text,
                    context_parts=context_parts,
                )
            try:
                parsed = parse_json_loose(raw_json)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Model returned invalid JSON: {e}",
                ) from e
            normalized = normalize_extraction_result(parsed)
            deliverable_body = json.dumps(normalized, indent=2, ensure_ascii=False)
        else:
            preset_user_text = "Execute the instructions above and output the full markdown report now."
            if profile_id == "kimi_moonshot":
                if attach_preamble:
                    preset_user_text = attach_preamble + "\n\n--- User instruction ---\n" + preset_user_text
                deliverable_body = await asyncio.to_thread(
                    generate_with_kimi,
                    system_instruction=system_prompt,
                    history=history,
                    user_message_text=preset_user_text,
                )
            else:
                deliverable_body = await asyncio.to_thread(
                    generate_one_shot,
                    system_instruction=system_prompt,
                    history=history,
                    user_message_text=preset_user_text,
                    context_parts=context_parts,
                )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # Write deliverable to workspace
    dtype = body.deliverable_type or preset.default_artifact_type
    dstatus = body.deliverable_status or preset.default_artifact_status
    title = preset.artifact_title or dtype
    folder = {
        "memo": "Deliverables/Memos",
        "factsheet": "Deliverables/Factsheets",
        "report": "Deliverables/Reports",
    }.get(dtype, "Deliverables")
    path = f"{folder}/{title}{file_suffix}"

    actor = Actor(type="system", ref=f"preset:{preset_id}")
    node = await workspace_service.write_file(
        db, entity_id, path,
        deliverable_body.encode("utf-8"),
        "application/json" if file_suffix == ".json" else "text/markdown",
        actor,
        metadata={"deliverable_type": dtype, "status": dstatus},
    )
    node.origin_type = "agent"
    await db.commit()
    await db.refresh(node)

    summary = f"Created deliverable `{node.name}` v{node.version} at {node.path}."

    if body.session_id:
        sess = await _get_session(db, entity_id, body.session_id)
        deliverable_card = {
            "_vc_chat": "artifact_card",
            "node_id": node.id,
            "entity_id": entity_id,
            "preset_label": preset.label,
            "deliverable_type": dtype,
            "artifact_title": title,
            "version": node.version,
            "status": dstatus,
            "summary": summary,
            "path": node.path,
        }
        note = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=body.session_id,
            role="assistant",
            content=json.dumps(deliverable_card),
            model_profile_id=profile_id,
        )
        db.add(note)
        sess.updated_at = utc_now()
        await db.commit()

    return PresetRunResponse(
        node_id=node.id,
        assistant_summary=summary,
        warnings=warnings,
    )
