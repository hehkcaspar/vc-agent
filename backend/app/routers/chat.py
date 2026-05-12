"""Portfolio entity chat (Gemini + Kimi direct) and preset shortcuts."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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
    WorkspaceOp,
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
    build_context_parts,
    build_harness_user_attachment_text,
    build_selected_files_pointer_list,
)
from app.services.direct_llm import (
    generate_json_one_shot,
    generate_one_shot,
    generate_with_interaction,
    generate_with_kimi,
)
from app.services.json_loose import parse_json_loose
from app.services.extract_info_signals import (
    SIGNALS_WORKSPACE_PATH,
    build_signals_document,
    has_any_signal,
    split_extract_info_payload,
)
from app.services.fact_discrepancies import append_discrepancy
from app.services.fact_ledger_schema import FactSource
from app.services.fact_manager import (
    extract_hard_facts_from_payload,
    record_fact_in_metadata,
)
from app.services.legal_review_facts import (
    merge_legal_review_opinions,
    merge_prior_round_facts,
    split_legal_review_entry,
    validate_legal_review_opinions,
    validate_legal_reviews,
)
from app.services.metadata_extraction import (
    _migrate_prior_round_entry,
    merge_entity_metadata,
    validate_entity_metadata,
)
from app.services.job_tasks import (
    TERMINAL_JOB_STATUSES,
    cancel_tracked_task,
    launch_tracked_task,
    mark_job_cancelled,
)
from app.services.model_profiles import normalize_profile_id
from app.services.preset_registry import (
    get_preset,
    list_presets,
    render_extract_info,
    render_initial_screening_research,
    render_legal_review,
    render_red_team,
)
from app.services.agent_harness import (
    create_react_portfolio_agent,
    history_to_lc_messages,
    invoke_react_portfolio_agent,
)

# Deep Agent compat — removable module. Delete deep_agent_compat.py
# and deepagents from requirements.txt to fully remove.
try:
    from app.services.deep_agent_compat import (
        create_portfolio_agent,
        invoke_portfolio_agent,
    )
    DEEP_AGENT_AVAILABLE = True
except ImportError:
    DEEP_AGENT_AVAILABLE = False
from app.services.prompt_assembly import EntityBrief, build_portfolio_system_prompt
from app.services.storage import storage
from app.services.workspace import WorkspaceService, Actor

router = APIRouter(tags=["entity-chat"])
workspace_service = WorkspaceService(storage)

AGENT_MODES = {"one_shot", "react", "deep_agent"}


def _resolve_agent_mode(
    agent_mode: str | None, use_deep_agent: bool | None,
) -> str:
    """Resolve the execution mode from request fields + server default."""
    if agent_mode and agent_mode in AGENT_MODES:
        return agent_mode
    if use_deep_agent is not None:
        return "react" if use_deep_agent else "one_shot"
    return settings.CHAT_DEFAULT_AGENT_MODE


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


async def _load_legal_review_opinions(
    db: AsyncSession, entity_id: str,
) -> list[dict]:
    """Load the opinions-only Legal Review.json from workspace root. Returns [] if missing."""
    node = await workspace_service.get_node_by_path(
        db, entity_id, "Legal Review.json",
    )
    if node is None or not node.storage_key:
        return []
    try:
        raw = storage.read_file_sync(node.storage_key)
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        payload = payload.get("legal_reviews") or []
    if not isinstance(payload, list):
        return []
    # Accept either new opinions-only shape or legacy combined shape — the
    # validator tolerates both by only extracting opinion fields.
    try:
        validated, _ = validate_legal_review_opinions(payload)
    except Exception:
        return []
    return validated


async def _apply_fact_claims_from_payload(
    db: AsyncSession,
    entity_id: str,
    raw_payload: Any,
    preset_id: str,
    run_id: str,
) -> list[str]:
    """Belt-and-suspenders recovery: convert `fact_claims[]` left in the agent's
    final JSON into ``_fact_discrepancies[]`` entries for any claims the agent
    didn't already surface via ``propose_fact_update``. Returns warning strings.
    """
    if not isinstance(raw_payload, dict):
        return []
    claims = raw_payload.get("fact_claims") or []
    if not isinstance(claims, list) or not claims:
        return []

    entity = await _get_entity(db, entity_id)
    metadata: dict = {}
    if entity.metadata_json:
        try:
            metadata = json.loads(entity.metadata_json)
            if not isinstance(metadata, dict):
                metadata = {}
        except json.JSONDecodeError:
            metadata = {}

    existing_keys: set = {
        (
            str(d.get("field_path")),
            json.dumps(d.get("proposed_value"), sort_keys=True, default=str),
        )
        for d in (metadata.get("_fact_discrepancies") or [])
        if isinstance(d, dict)
    }

    warnings: list[str] = []
    appended = 0
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            warnings.append(f"fact_claim[{i}] skipped: not a dict")
            continue
        # Explicitly validate required fields so an agent's partial payload
        # doesn't silently vanish — the user needs to know something was
        # surfaced-but-dropped, not just assume zero discrepancies.
        missing = [
            k for k in ("field_path", "source_doc_path", "rationale")
            if not claim.get(k)
        ]
        if missing:
            warnings.append(
                f"fact_claim[{i}] skipped (missing required: {missing!r}); "
                f"keys present: {sorted(claim)}"
            )
            continue

        field_path = claim.get("field_path")
        proposed_value = claim.get("proposed_value")
        dedup_key = (
            str(field_path),
            json.dumps(proposed_value, sort_keys=True, default=str),
        )
        if dedup_key in existing_keys:
            continue

        source_path = claim.get("source_doc_path")
        node = await workspace_service.get_node_by_path(
            db, entity_id, source_path,
        )
        node_id = node.id if node else None
        if not node_id:
            warnings.append(
                f"fact_claim[{i}] skipped (source_doc_path not resolvable): "
                f"{source_path!r}"
            )
            continue

        entry = {
            "detected_by": preset_id,
            "field_path": field_path,
            "current_value": claim.get("current_value"),
            "proposed_value": proposed_value,
            "source_doc_node_id": node_id,
            "source_doc_quote": claim.get("source_doc_quote"),
            "confidence": claim.get("confidence") or "medium",
            "rationale": claim.get("rationale") or "",
            "round_name": claim.get("round_name"),
            "source_run": {
                "agent_run_id": run_id,
                "preset_id": preset_id,
                "channel": "fact_claims_fallback",
            },
        }
        try:
            append_discrepancy(metadata, entry)
            existing_keys.add(dedup_key)
            appended += 1
        except ValueError as e:
            warnings.append(f"fact_claim skipped ({e}): {field_path}")

    if appended:
        entity.metadata_json = json.dumps(metadata, ensure_ascii=False)
        entity.updated_at = utc_now()
        warnings.append(
            f"Recovered {appended} fact_claim entries from agent output "
            "(agent skipped propose_fact_update)"
        )
    return warnings


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
        if not job or job.status in TERMINAL_JOB_STATUSES:
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

            pointer_list = build_selected_files_pointer_list(nodes)

            # Build workspace context (three-layer: tree + descriptions + notes)
            workspace_context = await workspace_service.build_annotated_tree_text(
                db, job.entity_id
            )

            user_turn = user_row.content.strip()
            preamble_parts = []
            if workspace_context:
                preamble_parts.append(workspace_context)
            if pointer_list:
                preamble_parts.append(pointer_list)
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

            agent_mode_snap = job.agent_mode or "deep_agent"

            job.agent_run_id = agent_run_id_snap
            job.status = "running"
            job.step_detail = "Starting agent..."
            job.updated_at = utc_now()
            await db.commit()

        def _run_agent() -> Tuple[str, Any]:
            kwargs = dict(
                entity=brief,
                system_prompt_extras=harness_extras_snap,
                session_id=session_id_snap,
                model_profile_id=model_profile_id_snap,
                run_id=agent_run_id_snap,
                on_status=on_status,
            )
            lc_messages = history_to_lc_messages(history, user_turn)
            if agent_mode_snap == "deep_agent" and DEEP_AGENT_AVAILABLE:
                agent = create_portfolio_agent(**kwargs)
                return invoke_portfolio_agent(
                    agent, lc_messages, on_status=on_status,
                )
            else:
                # ReAct (default) or fallback when deep_agent unavailable
                agent = create_react_portfolio_agent(**kwargs)
                return invoke_react_portfolio_agent(
                    agent, lc_messages, on_status=on_status,
                )

        reply_text, raw = await asyncio.to_thread(_run_agent)
        if isinstance(raw, dict):
            message_count = len(raw.get("messages") or [])
            tool_trace = {
                "keys": list(raw.keys()),
                "message_count": message_count,
                "status_trace": status_trace[-40:],
                "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
            }

    except asyncio.CancelledError:
        await mark_job_cancelled(AsyncSessionLocal, ChatCompletionJob, job_id)
        raise
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
            if job and job.status not in TERMINAL_JOB_STATUSES:
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
        if not job or job.status in TERMINAL_JOB_STATUSES:
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
        # Force the INSERT before we set the FK on the job row — Postgres
        # rejects an UPDATE that references an uncommitted row, and the
        # default unit-of-work order doesn't reliably interleave INSERT
        # before UPDATE in this session (works on SQLite which skips FK
        # enforcement; fails on prod Postgres).
        await db.flush()
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

            pointer_list = build_selected_files_pointer_list(nodes)

            workspace_context = await workspace_service.build_annotated_tree_text(
                db, job.entity_id
            )

            if preset_id == "red_team":
                task_body = render_red_team(
                    startup_name=entity.name,
                    industry=payload.get("industry"),
                    stage=payload.get("stage"),
                )
            elif preset_id == "extract_info":
                existing_meta = None
                if entity.metadata_json:
                    try:
                        existing_meta = json.loads(entity.metadata_json)
                    except json.JSONDecodeError:
                        pass
                task_body = render_extract_info(
                    entity.name, entity.website,
                    existing_metadata=existing_meta,
                )
            elif preset_id == "legal_review":
                existing_meta = None
                if entity.metadata_json:
                    try:
                        existing_meta = json.loads(entity.metadata_json)
                    except json.JSONDecodeError:
                        pass
                meta = existing_meta or {}
                existing_opinions = await _load_legal_review_opinions(
                    db, job.entity_id,
                )
                prior_rounds_migrated = [
                    _migrate_prior_round_entry(e)
                    for e in (meta.get("prior_rounds") or [])
                    if isinstance(e, dict)
                ]
                task_body = render_legal_review(
                    entity.name,
                    entity.website,
                    entity_positions=meta.get("_positions") or [],
                    existing_legal_reviews=existing_opinions,
                    existing_prior_rounds=prior_rounds_migrated,
                    existing_fact_discrepancies=(
                        meta.get("_fact_discrepancies") or []
                    ),
                )
            elif preset_id == "initial_screening":
                task_body = render_initial_screening_research(
                    entity_name=entity.name,
                    entity_website=entity.website,
                    entity_id=job.entity_id,
                    # agent_run_id_snap is assigned a bit later; the job id
                    # is a stable identifier the agent can echo into each
                    # section JSON's `generated_by_run_id` field.
                    run_id=str(job.id),
                )
            elif preset_id == "initial_screening_v2":
                # v2 runs its own orchestrator below — task_body stays empty
                # because we bypass the generic agent invocation.
                task_body = ""
            else:
                job.status = "failed"
                job.error_message = f"preset_not_implemented:{preset_id}"
                job.updated_at = utc_now()
                await db.commit()
                return

            if preset_id == "extract_info":
                user_turn_core = (
                    "Browse the workspace, read relevant files, and extract "
                    "company metadata per the schema in your instructions. "
                    "Write the result as Company Profile.json in the workspace root."
                )
            elif preset_id == "legal_review":
                user_turn_core = (
                    "Review the selected legal document(s) per the checklist in "
                    "your instructions. Use legal_template_read for precise "
                    "comparison whenever a term looks unusual, then call "
                    "workspace_write_file to save Legal Review.json with the "
                    "complete updated legal_reviews array at the workspace root."
                )
            elif preset.output_kind == "json":
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
            if pointer_list:
                preamble_parts.append(pointer_list)
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

            agent_mode_snap = job.agent_mode or "deep_agent"

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
            # v2 orchestrator needs access to the workspace tree + pointer
            # list independent of the `user_turn` preamble packing, since
            # each section agent composes its own turn.
            workspace_context_snap = workspace_context
            pointer_list_snap = pointer_list
            history_snap = list(history)

        # --- initial_screening_v2: bypass single-agent path ---------------
        # Run survey + 6 parallel section agents + compose + review here,
        # then emit the assistant message and return. The rest of this
        # function handles v1 presets untouched.
        if preset_id == "initial_screening_v2":
            from app.services.initial_screening_v2_job import (
                post_process_section_jsons,
                run_compose_review_v2,
                run_research_v2,
                V2_MEMO_PATH,
            )

            outcome = await run_research_v2(
                brief=brief,
                session_id=session_id_snap,
                run_id=agent_run_id_snap,
                history=history_snap,
                workspace_context=workspace_context_snap,
                pointer_list=pointer_list_snap,
                model_profile_id=model_profile_id_snap,
                on_status=on_status,
            )

            async with AsyncSessionLocal() as db:
                ws = WorkspaceService(storage)
                # Enforce the GS contract on team.json + pipe co-investor
                # details into entity metadata. Runs before compose so
                # the open_gaps[] additions show up in the memo.
                await post_process_section_jsons(
                    db, ws,
                    entity_id=job.entity_id,
                    agent_run_id=agent_run_id_snap,
                    on_status=on_status,
                )
                memo, warns = await run_compose_review_v2(
                    db, ws,
                    entity_id=job.entity_id,
                    entity_name=brief.name,
                    entity_website=brief.website,
                    agent_run_id=agent_run_id_snap,
                    on_status=on_status,
                )

                memo_node = await ws.get_node_by_path(
                    db, job.entity_id, V2_MEMO_PATH,
                )
                succeeded = sum(1 for r in outcome.sections if r.succeeded)
                summary_lines = [
                    f"Initial Screening v2 complete: "
                    f"{succeeded}/{len(outcome.sections)} sections, "
                    f"research {outcome.total_seconds:.0f}s."
                ]
                if outcome.survey and outcome.survey.notes:
                    summary_lines.append(
                        f"Survey: {outcome.survey.notes}",
                    )
                for sec in outcome.sections:
                    if not sec.succeeded:
                        summary_lines.append(
                            f"- [{sec.section_id}] failed "
                            f"({sec.wall_seconds:.0f}s): {sec.error}",
                        )
                if warns:
                    summary_lines.append("Compose/review warnings:")
                    for w in warns:
                        summary_lines.append(f"- {w}")
                summary_body = "\n\n".join(summary_lines)

                if memo_node is not None:
                    content = json.dumps({
                        "_vc_chat": "artifact_card",
                        "node_id": memo_node.id,
                        "entity_id": job.entity_id,
                        "preset_label": preset_label_snap,
                        "deliverable_type": default_artifact_type_snap,
                        "artifact_title": artifact_title_snap,
                        "version": memo_node.version,
                        "status": default_artifact_status_snap,
                        "summary": summary_body,
                        "path": memo_node.path,
                    })
                else:
                    content = (
                        f"**Initial Screening v2 failed** — no memo was "
                        f"produced.\n\n{summary_body}"
                    )

                assistant_msg = ConversationMessage(
                    id=str(uuid.uuid4()),
                    session_id=job.session_id,
                    role="assistant",
                    content=content,
                    model_profile_id=normalize_profile_id(model_profile_id_snap),
                )
                db.add(assistant_msg)

                res2 = await db.execute(
                    select(ChatCompletionJob).where(
                        ChatCompletionJob.id == job_id,
                    )
                )
                job2 = res2.scalar_one_or_none()
                if job2 and job2.status not in TERMINAL_JOB_STATUSES:
                    job2.assistant_message_id = assistant_msg.id
                    job2.status = "succeeded"
                    job2.step_detail = "Done"
                    tool_trace: dict = {
                        "status_trace": status_trace[-60:],
                        "v2_total_seconds": outcome.total_seconds,
                        "v2_sections": [
                            {
                                "id": r.section_id,
                                "ok": r.succeeded,
                                "seconds": round(r.wall_seconds, 1),
                                "error": r.error,
                            }
                            for r in outcome.sections
                        ],
                    }
                    job2.tool_trace_json = json.dumps(tool_trace)
                    job2.updated_at = utc_now()

                sess = await _get_session(db, job.entity_id, job.session_id)
                sess.updated_at = utc_now()
                await db.commit()

            try:
                from app.routers.entity_news import maybe_bootstrap_after_preset
                await maybe_bootstrap_after_preset(
                    job.entity_id, trigger_preset="initial_screening_v2",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "news_web auto-bootstrap (initial_screening_v2) failed",
                    exc_info=True,
                )

            return  # Skip the single-agent path below.

        def _run_agent() -> Tuple[str, Any]:
            kwargs = dict(
                entity=brief,
                system_prompt_extras=harness_extras_snap,
                session_id=session_id_snap,
                model_profile_id=model_profile_id_snap,
                run_id=agent_run_id_snap,
                on_status=on_status,
                preset_id=preset_id,
            )
            lc_messages = history_to_lc_messages(history, user_turn)
            if agent_mode_snap == "deep_agent" and DEEP_AGENT_AVAILABLE:
                agent = create_portfolio_agent(**kwargs)
                return invoke_portfolio_agent(
                    agent, lc_messages, on_status=on_status,
                )
            else:
                # Initial Screening's phase-1 research agent needs Google-
                # grounded web search. Opt-in per preset so other presets
                # don't get the tool by default.
                react_kwargs = dict(kwargs)
                recursion_override: Optional[int] = None
                if preset_id == "initial_screening":
                    react_kwargs["include_web_search"] = True
                    # Six section JSONs × (~2 file reads + 1 web search + 1
                    # write) + survey overhead = ~40-50 tool calls in a tight
                    # workspace, ~60-80 in a rich one. The .env default (50)
                    # is too tight; give this preset its own headroom without
                    # relaxing the global cap.
                    recursion_override = max(
                        120, settings.CHAT_AGENT_RECURSION_LIMIT,
                    )
                agent = create_react_portfolio_agent(**react_kwargs)
                return invoke_react_portfolio_agent(
                    agent, lc_messages,
                    on_status=on_status,
                    recursion_limit=recursion_override,
                )

        deliverable_body, raw = await asyncio.to_thread(_run_agent)

        # --- extract_info: sync Company Profile.json → Entity.metadata_json ---
        if preset_id == "extract_info":
            _log = logging.getLogger(__name__)
            async with AsyncSessionLocal() as db:
                # Find Company Profile.json written by this agent run
                profile_node = None
                ops_res = await db.execute(
                    select(WorkspaceOp.node_id)
                    .where(
                        WorkspaceOp.entity_id == job.entity_id,
                        WorkspaceOp.actor_type == "agent",
                        WorkspaceOp.actor_ref == agent_run_id_snap,
                        WorkspaceOp.op_type.in_(["create_file", "overwrite"]),
                    )
                )
                written_ids = [r[0] for r in ops_res.all() if r[0]]
                if written_ids:
                    nodes_res = await db.execute(
                        select(WorkspaceNode)
                        .where(
                            WorkspaceNode.id.in_(written_ids),
                            WorkspaceNode.name == "Company Profile.json",
                            WorkspaceNode.deleted_at.is_(None),
                        )
                    )
                    profile_node = nodes_res.scalars().first()

                # Read, validate, merge to entity
                sync_warnings: List[str] = []
                raw_payload_for_claims: Any = None
                signals_payload: dict | None = None
                signals_files_examined: list = []
                if profile_node and profile_node.storage_key:
                    try:
                        raw_bytes = storage.read_file_sync(profile_node.storage_key)
                        profile_data = json.loads(raw_bytes.decode("utf-8"))
                        raw_payload_for_claims = profile_data
                        facts_payload, signals_payload = (
                            split_extract_info_payload(profile_data)
                        )
                        validated, v_warnings = validate_entity_metadata(facts_payload)
                        sync_warnings.extend(v_warnings)

                        # Override agent-generated meta fields with trusted values.
                        # LLMs hallucinate timestamps; status_trace reliably
                        # captures which files the agent actually read.
                        read_paths: List[str] = []
                        _seen: set[str] = set()
                        for msg in status_trace:
                            m = re.match(r"^Reading (.+?)\.\.\.$", msg or "")
                            if m:
                                p = m.group(1)
                                if p not in _seen:
                                    _seen.add(p)
                                    read_paths.append(p)
                        validated["_extracted_at"] = utc_now().isoformat()
                        validated["_extraction_version"] = 1
                        validated["_files_examined"] = read_paths
                        signals_files_examined = list(read_paths)

                        # Persist corrected meta back to workspace file so the
                        # user-visible Company Profile.json matches Entity.metadata.
                        corrected_body = json.dumps(
                            validated, indent=2, ensure_ascii=False,
                        )
                        try:
                            await workspace_service.write_file(
                                db,
                                job.entity_id,
                                profile_node.path,
                                corrected_body.encode("utf-8"),
                                "application/json",
                                Actor(type="system", ref=f"preset:{preset_id}"),
                            )
                        except Exception:
                            _log.warning(
                                "Failed to rewrite Company Profile.json with "
                                "corrected meta", exc_info=True,
                            )

                        entity_row = await _get_entity(db, job.entity_id)
                        existing = None
                        if entity_row.metadata_json:
                            try:
                                existing = json.loads(entity_row.metadata_json)
                            except json.JSONDecodeError:
                                pass

                        merged = merge_entity_metadata(existing, validated)
                        # HEAD-check LinkedIn URLs (canonical pattern was
                        # already enforced in validate_entity_metadata) and
                        # null any 4xx so the Facts UI doesn't render a
                        # broken Link2 icon. Failure-isolated: timeouts
                        # leave the URL intact with _linkedin_status set.
                        try:
                            from app.services.metadata_extraction import (
                                head_validate_linkedin_urls,
                            )
                            await head_validate_linkedin_urls(merged)
                        except Exception:  # noqa: BLE001
                            _log.warning(
                                "extract_info: linkedin head-check failed (non-fatal)",
                                exc_info=True,
                            )
                        entity_row.metadata_json = json.dumps(
                            merged, ensure_ascii=False,
                        )

                        # Auto-update name/website if extraction found better values
                        extracted_name = merged.get("company_name")
                        if (
                            extracted_name
                            and isinstance(extracted_name, str)
                            and extracted_name.strip()
                            and extracted_name.strip() != entity_row.name
                        ):
                            entity_row.name = extracted_name.strip()
                            _log.info(
                                "extract_info updated entity name to %r",
                                entity_row.name,
                            )

                        extracted_website = merged.get("website")
                        if (
                            extracted_website
                            and isinstance(extracted_website, str)
                            and extracted_website.strip()
                            and extracted_website.strip() != (entity_row.website or "")
                        ):
                            entity_row.website = extracted_website.strip()

                        entity_row.updated_at = utc_now()
                        await db.commit()
                        _log.info(
                            "Synced extract_info metadata to entity %s",
                            job.entity_id,
                        )

                        # ── Fact-ledger retrofit ──────────────────────────
                        # Mirror each hard-fact field into the append-only
                        # `_ledger[]`. Source = the workspace docs examined
                        # in this run. Soft claims (one_liner, description,
                        # signals, etc.) are NOT routed — they stay on the
                        # flat fields as per the existing flow.
                        #
                        # Implementation note: the flat merge already wrote
                        # entity.metadata_json above, so re-load it once here
                        # and mutate in-memory. Per-fact DB roundtrips would
                        # be O(N) SELECTs for no gain — the pure-dict variant
                        # batches all writes into one UPDATE at the end.
                        try:
                            hard_facts = extract_hard_facts_from_payload(
                                validated,
                            )
                            if hard_facts:
                                existing_meta_for_ledger = {}
                                if entity_row.metadata_json:
                                    try:
                                        existing_meta_for_ledger = json.loads(
                                            entity_row.metadata_json,
                                        )
                                    except json.JSONDecodeError:
                                        existing_meta_for_ledger = {}
                                primary_ref = (
                                    f"workspace://{read_paths[0]}"
                                    if read_paths else None
                                )
                                source = FactSource(
                                    type="upload",
                                    ref=primary_ref,
                                    preset="extract_info",
                                    run_id=agent_run_id_snap,
                                )
                                notes = (
                                    f"extract_info examined {len(read_paths)} files"
                                    if len(read_paths) > 1 else None
                                )
                                ledger_writes = 0
                                for fact_path, value in hard_facts:
                                    try:
                                        entry = record_fact_in_metadata(
                                            existing_meta_for_ledger,
                                            fact_path=fact_path,
                                            value=value,
                                            source=source,
                                            confidence=0.85,
                                            notes=notes,
                                        )
                                        if entry is not None:
                                            ledger_writes += 1
                                    except Exception:
                                        _log.warning(
                                            "fact_manager: record_fact_in_metadata "
                                            "failed for %r", fact_path,
                                            exc_info=True,
                                        )
                                if ledger_writes:
                                    entity_row.metadata_json = json.dumps(
                                        existing_meta_for_ledger,
                                        ensure_ascii=False,
                                    )
                                    entity_row.updated_at = utc_now()
                                    await db.commit()
                                    _log.info(
                                        "extract_info retrofit: %d ledger "
                                        "entries recorded for entity %s",
                                        ledger_writes, job.entity_id,
                                    )
                        except Exception:
                            _log.warning(
                                "extract_info fact-ledger retrofit failed",
                                exc_info=True,
                            )
                    except Exception:
                        _log.warning(
                            "Failed to sync extract_info metadata", exc_info=True,
                        )
                        sync_warnings.append(
                            "Metadata sync to entity failed — check Company Profile.json"
                        )
                else:
                    # Fallback: agent forgot workspace_write_file. Try to salvage
                    # by parsing JSON from the agent's final text reply.
                    _log.warning(
                        "extract_info agent did not write Company Profile.json "
                        "— attempting fallback parse of agent reply"
                    )
                    salvaged = None
                    try:
                        salvaged = parse_json_loose(deliverable_body)
                    except (json.JSONDecodeError, ValueError):
                        salvaged = None

                    if isinstance(salvaged, dict) and salvaged:
                        try:
                            raw_payload_for_claims = salvaged
                            facts_payload, signals_payload = (
                                split_extract_info_payload(salvaged)
                            )
                            validated, v_warnings = validate_entity_metadata(
                                facts_payload,
                            )
                            sync_warnings.extend(v_warnings)
                            validated["_extracted_at"] = utc_now().isoformat()
                            validated["_extraction_version"] = 1
                            read_paths: List[str] = []
                            _seen: set[str] = set()
                            for msg in status_trace:
                                m = re.match(
                                    r"^Reading (.+?)\.\.\.$", msg or "",
                                )
                                if m and m.group(1) not in _seen:
                                    _seen.add(m.group(1))
                                    read_paths.append(m.group(1))
                            validated["_files_examined"] = read_paths
                            signals_files_examined = list(read_paths)

                            # Write the file ourselves as a system recovery
                            corrected_body = json.dumps(
                                validated, indent=2, ensure_ascii=False,
                            )
                            profile_node = await workspace_service.write_file(
                                db,
                                job.entity_id,
                                "Company Profile.json",
                                corrected_body.encode("utf-8"),
                                "application/json",
                                Actor(type="system", ref=f"preset:{preset_id}"),
                            )
                            entity_row = await _get_entity(db, job.entity_id)
                            entity_row.metadata_json = json.dumps(
                                validated, ensure_ascii=False,
                            )
                            entity_row.updated_at = utc_now()
                            await db.commit()
                            await db.refresh(profile_node)
                            sync_warnings.append(
                                "Agent skipped workspace_write_file — metadata "
                                "recovered from final text reply"
                            )
                            _log.info(
                                "Recovered extract_info metadata from agent "
                                "reply for entity %s", job.entity_id,
                            )
                        except Exception:
                            _log.warning(
                                "Fallback recovery failed", exc_info=True,
                            )
                            profile_node = None
                            sync_warnings.append(
                                "Agent did not write Company Profile.json and "
                                "fallback parse failed — no metadata synced"
                            )
                    else:
                        sync_warnings.append(
                            "Agent did not write Company Profile.json and no "
                            "JSON was found in the reply — no metadata synced"
                        )

                # Split off: write signals file to Deliverables/Analysis/ so
                # Company Profile.json stays pure facts. Runs once, covers both
                # the happy path and the fallback recovery path.
                if signals_payload and has_any_signal(signals_payload):
                    signals_doc = build_signals_document(
                        signals_payload,
                        run_id=agent_run_id_snap,
                        files_examined=signals_files_examined,
                    )
                    try:
                        await workspace_service.write_file(
                            db,
                            job.entity_id,
                            SIGNALS_WORKSPACE_PATH,
                            json.dumps(
                                signals_doc, indent=2, ensure_ascii=False,
                            ).encode("utf-8"),
                            "application/json",
                            Actor(type="system", ref=f"preset:{preset_id}"),
                        )
                        await db.commit()
                    except Exception:
                        _log.warning(
                            "Failed to write %s", SIGNALS_WORKSPACE_PATH,
                            exc_info=True,
                        )
                        sync_warnings.append(
                            f"Signals sidecar write failed — {SIGNALS_WORKSPACE_PATH}"
                        )

                # Recover any fact_claims[] the agent left in its final JSON
                # that weren't already surfaced via propose_fact_update.
                if raw_payload_for_claims is not None:
                    try:
                        claim_warnings = await _apply_fact_claims_from_payload(
                            db, job.entity_id,
                            raw_payload_for_claims,
                            preset_id, agent_run_id_snap,
                        )
                        if claim_warnings:
                            await db.commit()
                            sync_warnings.extend(claim_warnings)
                    except Exception:
                        _log.warning(
                            "fact_claims recovery failed", exc_info=True,
                        )

                # Build chat message: artifact_card on success, plain-text on
                # failure (prevents raw JSON showing in the chat UI).
                summary = deliverable_body.strip() or "Extraction complete."
                if sync_warnings:
                    summary += "\n\nWarnings:\n" + "\n".join(
                        f"- {w}" for w in sync_warnings
                    )

                if profile_node is not None:
                    content = json.dumps({
                        "_vc_chat": "artifact_card",
                        "node_id": profile_node.id,
                        "entity_id": job.entity_id,
                        "preset_label": preset_label_snap,
                        "deliverable_type": "other",
                        "artifact_title": artifact_title_snap,
                        "version": profile_node.version,
                        "status": "draft",
                        "summary": summary,
                        "path": profile_node.path,
                    })
                else:
                    # Plain text — renders as a normal assistant message
                    content = (
                        f"**Extract Info failed** — {preset_label_snap} "
                        f"could not produce a profile.\n\n{summary}"
                    )
                assistant_msg = ConversationMessage(
                    id=str(uuid.uuid4()),
                    session_id=job.session_id,
                    role="assistant",
                    content=content,
                    model_profile_id=normalize_profile_id(model_profile_id_snap),
                )
                db.add(assistant_msg)

                res2 = await db.execute(
                    select(ChatCompletionJob).where(
                        ChatCompletionJob.id == job_id
                    )
                )
                job2 = res2.scalar_one_or_none()
                if job2 and job2.status not in TERMINAL_JOB_STATUSES:
                    job2.assistant_message_id = assistant_msg.id
                    job2.status = "succeeded"
                    job2.step_detail = "Done"
                    tool_trace: dict = {
                        "status_trace": status_trace[-40:],
                        "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
                    }
                    if isinstance(raw, dict):
                        tool_trace["keys"] = list(raw.keys())
                        tool_trace["message_count"] = len(
                            raw.get("messages") or []
                        )
                    job2.tool_trace_json = json.dumps(tool_trace)
                    job2.updated_at = utc_now()

                sess = await _get_session(db, job.entity_id, job.session_id)
                sess.updated_at = utc_now()
                await db.commit()

            try:
                from app.routers.entity_news import maybe_bootstrap_after_preset
                await maybe_bootstrap_after_preset(
                    job.entity_id, trigger_preset="extract_info",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "news_web auto-bootstrap (extract_info) failed",
                    exc_info=True,
                )

            return  # Skip general deliverable post-processing

        # --- legal_review: sync Legal Review.json → Entity.metadata_json ---
        if preset_id == "legal_review":
            _log = logging.getLogger(__name__)
            async with AsyncSessionLocal() as db:
                # Prefer root-level Legal Review.json (convention). Accept a
                # non-root write as a fallback with a warning so an agent that
                # stashed the file in Deliverables/ still succeeds, and the
                # authoritative copy gets re-persisted at root below.
                sync_warnings: List[str] = []
                review_node = None
                ops_res = await db.execute(
                    select(WorkspaceOp.node_id)
                    .where(
                        WorkspaceOp.entity_id == job.entity_id,
                        WorkspaceOp.actor_type == "agent",
                        WorkspaceOp.actor_ref == agent_run_id_snap,
                        WorkspaceOp.op_type.in_(["create_file", "overwrite"]),
                    )
                )
                written_ids = [r[0] for r in ops_res.all() if r[0]]
                if written_ids:
                    root_res = await db.execute(
                        select(WorkspaceNode)
                        .where(
                            WorkspaceNode.id.in_(written_ids),
                            WorkspaceNode.name == "Legal Review.json",
                            WorkspaceNode.parent_id.is_(None),
                            WorkspaceNode.deleted_at.is_(None),
                        )
                    )
                    review_node = root_res.scalars().first()
                    if review_node is None:
                        any_res = await db.execute(
                            select(WorkspaceNode)
                            .where(
                                WorkspaceNode.id.in_(written_ids),
                                WorkspaceNode.name == "Legal Review.json",
                                WorkspaceNode.deleted_at.is_(None),
                            )
                        )
                        review_node = any_res.scalars().first()
                        if review_node is not None:
                            sync_warnings.append(
                                f"Agent wrote Legal Review.json at "
                                f"{review_node.path!r} — expected workspace root; "
                                "re-persisting authoritative copy at root"
                            )

                # Load current checklist version once for annotating reviews.
                from app.services.legal_review_checklist_config import (
                    load_legal_review_checklist,
                )
                try:
                    checklist_version = load_legal_review_checklist().version
                except Exception:
                    checklist_version = 1

                # Rebuild the shared documents_reviewed list from status_trace.
                # Two separate sources emit "Reading ..." notifications:
                #   - workspace_read_file → "Reading <path>..."
                #   - legal_template_read → "Reading template <id>..."
                # Workspace reads populate `documents_reviewed`; template reads
                # populate `reference_templates_consulted`. We exclude any
                # self-read of the output file (by basename) and drop paths
                # that don't resolve to a real workspace node (ghost reads,
                # e.g. the agent probing Inbox/Legal Review.json before writing).
                read_paths: List[str] = []
                template_ids: List[str] = []
                _seen_paths: set[str] = set()
                _seen_templates: set[str] = set()
                for msg in status_trace:
                    m = re.match(r"^Reading (.+?)\.\.\.$", msg or "")
                    if not m:
                        continue
                    target = m.group(1)
                    tpl_m = re.match(r"^template (.+)$", target)
                    if tpl_m:
                        tid = tpl_m.group(1)
                        if tid not in _seen_templates:
                            _seen_templates.add(tid)
                            template_ids.append(tid)
                    else:
                        # Self-read filter: any path ending in /Legal Review.json
                        # (agent probing for its own output anywhere in the tree)
                        basename = target.rsplit("/", 1)[-1]
                        if basename == "Legal Review.json":
                            continue
                        if target not in _seen_paths:
                            _seen_paths.add(target)
                            read_paths.append(target)

                node_lookup: dict[str, str] = {}
                if read_paths:
                    node_rows = await db.execute(
                        select(WorkspaceNode.path, WorkspaceNode.id)
                        .where(
                            WorkspaceNode.entity_id == job.entity_id,
                            WorkspaceNode.path.in_(read_paths),
                            WorkspaceNode.deleted_at.is_(None),
                        )
                    )
                    node_lookup = {p: nid for p, nid in node_rows.all()}
                # Drop unresolved paths — status_trace captures read *attempts*
                # (including failed probes), not completions. Only include real
                # workspace nodes with node_ids.
                documents_reviewed = [
                    {"path": p, "node_id": node_lookup[p]}
                    for p in read_paths
                    if p in node_lookup
                ]

                def _annotate(review: dict) -> dict:
                    out = dict(review)
                    out["review_date"] = utc_now().isoformat()
                    out["documents_reviewed"] = list(documents_reviewed)
                    out["checklist_version"] = checklist_version
                    # Merge agent's template list with server-observed reads,
                    # dedup. Server-detected templates win the tail — agent's
                    # own list (which often populates `standard_source` on
                    # unusual_terms) stays first so hand-picked ones are visible.
                    agent_tpls = out.get("reference_templates_consulted") or []
                    if not isinstance(agent_tpls, list):
                        agent_tpls = []
                    seen = {t for t in agent_tpls if isinstance(t, str)}
                    merged_tpls = [t for t in agent_tpls if isinstance(t, str)]
                    for t in template_ids:
                        if t not in seen:
                            merged_tpls.append(t)
                            seen.add(t)
                    out["reference_templates_consulted"] = merged_tpls
                    return out

                # Stage 1 — extract incoming from the written file, falling back
                # to parsing the agent's text reply if the file is missing or
                # unreadable. parse_succeeded == True means we have a trustworthy
                # incoming list (possibly empty == agent reports no applicable
                # reviews); parse_succeeded == False means we didn't get usable
                # data and should NOT touch metadata or re-persist.
                incoming_reviews: List[dict] = []
                parse_succeeded = False

                if review_node and review_node.storage_key:
                    try:
                        raw_bytes = storage.read_file_sync(review_node.storage_key)
                        review_payload = json.loads(raw_bytes.decode("utf-8"))
                        raw_list = (
                            review_payload.get("legal_reviews")
                            if isinstance(review_payload, dict)
                            else None
                        )
                        if raw_list is None:
                            raw_list = (
                                review_payload
                                if isinstance(review_payload, list)
                                else []
                            )
                        validated, v_warnings = validate_legal_reviews(raw_list)
                        sync_warnings.extend(v_warnings)
                        incoming_reviews = [_annotate(r) for r in validated]
                        parse_succeeded = True
                    except Exception:
                        _log.warning(
                            "Failed to parse Legal Review.json from agent run "
                            "— falling back to text salvage",
                            exc_info=True,
                        )
                        sync_warnings.append(
                            "Legal Review.json unreadable — attempting fallback "
                            "parse of agent reply"
                        )

                if not parse_succeeded:
                    _log.warning(
                        "legal_review: attempting salvage from agent reply "
                        "(file %s)",
                        "unreadable" if review_node else "missing",
                    )
                    salvaged = None
                    try:
                        salvaged = parse_json_loose(deliverable_body)
                    except (json.JSONDecodeError, ValueError):
                        salvaged = None
                    raw_list = None
                    if isinstance(salvaged, dict):
                        raw_list = salvaged.get("legal_reviews")
                    elif isinstance(salvaged, list):
                        raw_list = salvaged
                    if raw_list is not None:
                        try:
                            validated, v_warnings = validate_legal_reviews(raw_list)
                            sync_warnings.extend(v_warnings)
                            incoming_reviews = [_annotate(r) for r in validated]
                            parse_succeeded = True
                            sync_warnings.append(
                                "Reviews recovered from agent text reply "
                                "(file write missing or unreadable)"
                            )
                        except Exception:
                            _log.warning(
                                "legal_review salvage parse failed", exc_info=True,
                            )
                            sync_warnings.append(
                                "Agent did not produce a usable Legal Review.json "
                                "and fallback parse failed — no reviews synced"
                            )
                    else:
                        sync_warnings.append(
                            "Agent did not produce a usable Legal Review.json "
                            "and no JSON was found in the reply — no reviews synced"
                        )

                # Stage 2 — split each entry into (fact_block, opinion_block),
                # lift facts into prior_rounds[], and persist opinions to
                # Legal Review.json. Runs whenever parse_succeeded, even when
                # incoming is empty (the file is re-persisted with preserved
                # prior opinions so file + DB stay in sync).
                raw_payload_for_claims: Any = None
                if parse_succeeded:
                    entity_row = await _get_entity(db, job.entity_id)
                    existing_meta: dict = {}
                    if entity_row.metadata_json:
                        try:
                            existing_meta = json.loads(entity_row.metadata_json)
                            if not isinstance(existing_meta, dict):
                                existing_meta = {}
                        except json.JSONDecodeError:
                            existing_meta = {}
                    existing_prior_rounds = (
                        existing_meta.get("prior_rounds") or []
                    )
                    if not isinstance(existing_prior_rounds, list):
                        existing_prior_rounds = []

                    # Split each annotated incoming entry into (fact, opinion).
                    incoming_facts: List[dict] = []
                    incoming_opinions: List[dict] = []
                    for entry in incoming_reviews:
                        fact_block, opinion_block = split_legal_review_entry(entry)
                        # Opinions carry run-metadata (review_date, checklist_version,
                        # documents_reviewed, reference_templates_consulted) —
                        # _annotate already set them on the combined entry; copy
                        # across since opinion_block keys are limited.
                        opinion_block["review_date"] = entry.get("review_date")
                        opinion_block["documents_reviewed"] = (
                            entry.get("documents_reviewed") or []
                        )
                        opinion_block["reference_templates_consulted"] = (
                            entry.get("reference_templates_consulted") or []
                        )
                        opinion_block["checklist_version"] = (
                            entry.get("checklist_version") or checklist_version
                        )
                        incoming_facts.append(fact_block)
                        incoming_opinions.append(opinion_block)

                    merged_prior_rounds = merge_prior_round_facts(
                        existing_prior_rounds, incoming_facts,
                    )
                    existing_meta["prior_rounds"] = merged_prior_rounds
                    # Drop the legacy combined array — it's been superseded.
                    existing_meta.pop("legal_reviews", None)

                    # ── Fact-ledger retrofit ──────────────────────────────
                    # Route each fact_block's hard fields through fact_manager
                    # to append audit entries on ``existing_meta._ledger[]``.
                    # Source tier = legal_doc (cap_table when the reviewed
                    # doc is a cap table). Confidence 0.95 — legal docs are
                    # top-of-trust.
                    try:
                        def _legal_src_type(paths: List[str]) -> str:
                            low = " ".join((p or "").lower() for p in paths)
                            if (
                                "captable" in low or "cap_table" in low
                                or "cap-table" in low
                            ):
                                return "cap_table"
                            return "legal_doc"

                        ledger_writes = 0
                        for review_entry, fact_block in zip(
                            incoming_reviews, incoming_facts,
                        ):
                            docs_reviewed = (
                                review_entry.get("documents_reviewed") or []
                            )
                            primary_ref = (
                                f"workspace://{docs_reviewed[0]}"
                                if docs_reviewed else None
                            )
                            src_type = _legal_src_type(docs_reviewed)
                            source = FactSource(
                                type=src_type,
                                ref=primary_ref,
                                preset="legal_review",
                                run_id=agent_run_id_snap,
                            )
                            notes = (
                                f"legal_review examined {len(docs_reviewed)} docs"
                                if len(docs_reviewed) > 1 else None
                            )
                            # Wrap the fact_block so it matches the
                            # prior_rounds[] shape the extractor expects.
                            hard_facts = extract_hard_facts_from_payload(
                                {"prior_rounds": [fact_block]}
                            )
                            for fact_path, value in hard_facts:
                                try:
                                    entry_out = record_fact_in_metadata(
                                        existing_meta,
                                        fact_path=fact_path,
                                        value=value,
                                        source=source,
                                        confidence=0.95,
                                        notes=notes,
                                    )
                                    if entry_out is not None:
                                        ledger_writes += 1
                                except Exception:
                                    _log.warning(
                                        "fact_manager: record_fact failed "
                                        "for %r", fact_path, exc_info=True,
                                    )
                        if ledger_writes:
                            _log.info(
                                "legal_review retrofit: %d ledger entries "
                                "recorded for entity %s",
                                ledger_writes, job.entity_id,
                            )
                    except Exception:
                        _log.warning(
                            "legal_review fact-ledger retrofit failed",
                            exc_info=True,
                        )

                    entity_row.metadata_json = json.dumps(
                        existing_meta, ensure_ascii=False,
                    )
                    entity_row.updated_at = utc_now()

                    # Merge opinions with any prior-round opinions from the
                    # existing Legal Review.json (workspace is the source of
                    # truth for opinions now, not metadata_json).
                    existing_opinions = await _load_legal_review_opinions(
                        db, job.entity_id,
                    )
                    merged_opinions = merge_legal_review_opinions(
                        existing_opinions, incoming_opinions,
                    )

                    # Persist opinions-only at workspace root.
                    corrected_body = json.dumps(
                        {"legal_reviews": merged_opinions},
                        indent=2, ensure_ascii=False,
                    )
                    try:
                        review_node = await workspace_service.write_file(
                            db,
                            job.entity_id,
                            "Legal Review.json",
                            corrected_body.encode("utf-8"),
                            "application/json",
                            Actor(type="system", ref=f"preset:{preset_id}"),
                        )
                    except Exception:
                        _log.warning(
                            "Failed to rewrite Legal Review.json with merged opinions",
                            exc_info=True,
                        )

                    await db.commit()
                    if review_node is not None:
                        await db.refresh(review_node)
                    _log.info(
                        "Legal review sync: %d incoming, %d prior_rounds merged, "
                        "%d opinions merged (entity=%s)",
                        len(incoming_reviews), len(merged_prior_rounds),
                        len(merged_opinions), job.entity_id,
                    )

                    # Preserve the raw payload for fact_claims recovery below.
                    # We re-parse the file since `review_payload` is local to
                    # the stage-1 try block.
                    try:
                        if review_node and review_node.storage_key:
                            raw_bytes = storage.read_file_sync(
                                review_node.storage_key,
                            )
                            raw_payload_for_claims = json.loads(
                                raw_bytes.decode("utf-8"),
                            )
                    except Exception:
                        raw_payload_for_claims = None
                else:
                    # Parse failed — leave metadata and the workspace file
                    # untouched. review_node may point at the unreadable agent
                    # write; drop it so the chat message renders plain-text
                    # failure rather than a broken artifact_card.
                    review_node = None

                # Recover fact_claims[] left in the agent's JSON (belt-and-
                # suspenders; primary path is propose_fact_update during the run).
                if raw_payload_for_claims is not None:
                    try:
                        claim_warnings = await _apply_fact_claims_from_payload(
                            db, job.entity_id,
                            raw_payload_for_claims,
                            preset_id, agent_run_id_snap,
                        )
                        if claim_warnings:
                            await db.commit()
                            sync_warnings.extend(claim_warnings)
                    except Exception:
                        _log.warning(
                            "fact_claims recovery failed", exc_info=True,
                        )

                summary = deliverable_body.strip() or "Legal review complete."
                if sync_warnings:
                    summary += "\n\nWarnings:\n" + "\n".join(
                        f"- {w}" for w in sync_warnings
                    )

                if review_node is not None:
                    content = json.dumps({
                        "_vc_chat": "artifact_card",
                        "node_id": review_node.id,
                        "entity_id": job.entity_id,
                        "preset_label": preset_label_snap,
                        "deliverable_type": default_artifact_type_snap,
                        "artifact_title": artifact_title_snap,
                        "version": review_node.version,
                        "status": default_artifact_status_snap,
                        "summary": summary,
                        "path": review_node.path,
                    })
                else:
                    content = (
                        f"**Legal Review failed** — {preset_label_snap} "
                        f"could not produce a review.\n\n{summary}"
                    )
                assistant_msg = ConversationMessage(
                    id=str(uuid.uuid4()),
                    session_id=job.session_id,
                    role="assistant",
                    content=content,
                    model_profile_id=normalize_profile_id(model_profile_id_snap),
                )
                db.add(assistant_msg)

                res2 = await db.execute(
                    select(ChatCompletionJob).where(
                        ChatCompletionJob.id == job_id
                    )
                )
                job2 = res2.scalar_one_or_none()
                if job2 and job2.status not in TERMINAL_JOB_STATUSES:
                    job2.assistant_message_id = assistant_msg.id
                    job2.status = "succeeded"
                    job2.step_detail = "Done"
                    tool_trace: dict = {
                        "status_trace": status_trace[-40:],
                        "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
                    }
                    if isinstance(raw, dict):
                        tool_trace["keys"] = list(raw.keys())
                        tool_trace["message_count"] = len(
                            raw.get("messages") or []
                        )
                    job2.tool_trace_json = json.dumps(tool_trace)
                    job2.updated_at = utc_now()

                sess = await _get_session(db, job.entity_id, job.session_id)
                sess.updated_at = utc_now()
                await db.commit()

            return  # Skip general deliverable post-processing

        # --- initial_screening: orchestrate compose + review stages ---------
        if preset_id == "initial_screening":
            from app.services.initial_screening_job import (
                run_compose_stage, run_review_stage,
                INITIAL_SCREENING_MEMO_PATH,
            )
            async with AsyncSessionLocal() as db:
                ws = WorkspaceService(storage)
                memo, warns = await run_compose_stage(
                    db, ws,
                    entity_id=job.entity_id,
                    entity_name=brief.name,
                    entity_website=brief.website,
                    agent_run_id=agent_run_id_snap,
                    on_status=on_status,
                )
                if memo:
                    review_warns = await run_review_stage(
                        db, ws,
                        entity_id=job.entity_id,
                        entity_name=brief.name,
                        entity_website=brief.website,
                        agent_run_id=agent_run_id_snap,
                        memo_draft=memo,
                        on_status=on_status,
                    )
                    warns.extend(review_warns)

                # Lift the final memo content into the assistant message.
                memo_node = await ws.get_node_by_path(
                    db, job.entity_id, INITIAL_SCREENING_MEMO_PATH,
                )
                summary_body = deliverable_body.strip() or (
                    "Initial Screening complete."
                )
                if memo_node and memo_node.storage_key:
                    try:
                        final_memo = storage.read_file_sync(
                            memo_node.storage_key,
                        ).decode("utf-8", errors="replace")
                        summary_body = (
                            final_memo.splitlines()[0][:200]
                            if final_memo else summary_body
                        )
                    except Exception:
                        pass

                if warns:
                    summary_body += "\n\nWarnings:\n" + "\n".join(
                        f"- {w}" for w in warns
                    )

                if memo_node is not None:
                    content = json.dumps({
                        "_vc_chat": "artifact_card",
                        "node_id": memo_node.id,
                        "entity_id": job.entity_id,
                        "preset_label": preset_label_snap,
                        "deliverable_type": default_artifact_type_snap,
                        "artifact_title": artifact_title_snap,
                        "version": memo_node.version,
                        "status": default_artifact_status_snap,
                        "summary": summary_body,
                        "path": memo_node.path,
                    })
                else:
                    content = (
                        f"**Initial Screening failed** — no memo was "
                        f"produced.\n\n{summary_body}"
                    )

                assistant_msg = ConversationMessage(
                    id=str(uuid.uuid4()),
                    session_id=job.session_id,
                    role="assistant",
                    content=content,
                    model_profile_id=normalize_profile_id(model_profile_id_snap),
                )
                db.add(assistant_msg)

                res2 = await db.execute(
                    select(ChatCompletionJob).where(
                        ChatCompletionJob.id == job_id,
                    )
                )
                job2 = res2.scalar_one_or_none()
                if job2 and job2.status not in TERMINAL_JOB_STATUSES:
                    job2.assistant_message_id = assistant_msg.id
                    job2.status = "succeeded"
                    job2.step_detail = "Done"
                    tool_trace = {
                        "status_trace": status_trace[-40:],
                        "recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT,
                    }
                    if isinstance(raw, dict):
                        tool_trace["keys"] = list(raw.keys())
                        tool_trace["message_count"] = len(
                            raw.get("messages") or []
                        )
                    job2.tool_trace_json = json.dumps(tool_trace)
                    job2.updated_at = utc_now()

                sess = await _get_session(db, job.entity_id, job.session_id)
                sess.updated_at = utc_now()
                await db.commit()

            try:
                from app.routers.entity_news import maybe_bootstrap_after_preset
                await maybe_bootstrap_after_preset(
                    job.entity_id, trigger_preset="initial_screening",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "news_web auto-bootstrap (initial_screening) failed",
                    exc_info=True,
                )

            return  # Skip general deliverable post-processing

        # Fallback: if the agent wrote a report via workspace tools instead of
        # returning the full content, recover it from the written file.
        if (
            output_kind_snap == "markdown"
            and len(deliverable_body.strip()) < 500
            and agent_run_id_snap
        ):
            async with AsyncSessionLocal() as db:
                ops_res = await db.execute(
                    select(WorkspaceOp.node_id)
                    .where(
                        WorkspaceOp.entity_id == job.entity_id,
                        WorkspaceOp.actor_type == "agent",
                        WorkspaceOp.actor_ref == agent_run_id_snap,
                        WorkspaceOp.op_type.in_(["create_file", "overwrite"]),
                    )
                )
                written_node_ids = [r[0] for r in ops_res.all() if r[0]]
                if written_node_ids:
                    nodes_res = await db.execute(
                        select(WorkspaceNode)
                        .where(
                            WorkspaceNode.id.in_(written_node_ids),
                            WorkspaceNode.name.like("%.md"),
                            WorkspaceNode.deleted_at.is_(None),
                        )
                        .order_by(WorkspaceNode.size_bytes.desc())
                    )
                    best = nodes_res.scalars().first()
                    if best and best.storage_key:
                        try:
                            recovered = storage.read_file_sync(
                                best.storage_key
                            ).decode("utf-8", errors="replace")
                            if len(recovered) > len(deliverable_body):
                                deliverable_body = recovered
                                logging.getLogger(__name__).info(
                                    "Recovered preset deliverable from agent-written "
                                    "file %s (%d bytes)",
                                    best.path,
                                    len(recovered),
                                )
                        except Exception:
                            pass

        # Post-process JSON presets — pretty-print the agent's JSON output.
        # extract_info / legal_review never reach here: they return earlier
        # from their dedicated post-processing blocks above.
        if output_kind_snap == "json":
            try:
                parsed = parse_json_loose(deliverable_body)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Model returned invalid JSON: {e}") from e
            deliverable_body = json.dumps(parsed, indent=2, ensure_ascii=False)

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
            description = f"{preset_label_snap}: {title}"
            node = await workspace_service.write_file(
                db,
                job.entity_id,
                path,
                deliverable_body.encode("utf-8"),
                "application/json" if file_suffix == ".json" else "text/markdown",
                actor,
                metadata={
                    "deliverable_type": dtype,
                    "status": dstatus,
                    "description": description,
                },
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
            if job2 and job2.status not in TERMINAL_JOB_STATUSES:
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

    except asyncio.CancelledError:
        await mark_job_cancelled(AsyncSessionLocal, ChatCompletionJob, job_id)
        raise
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
            if job and job.status not in TERMINAL_JOB_STATUSES:
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
    # Check for an active (pending/running) job so the frontend can resume polling.
    active_job_row = await db.execute(
        select(ChatCompletionJob.id)
        .where(
            ChatCompletionJob.session_id == session_id,
            ChatCompletionJob.status.in_(["pending", "running"]),
        )
        .order_by(ChatCompletionJob.id.desc())
        .limit(1)
    )
    active_job_id = active_job_row.scalar_one_or_none()
    return ChatSessionDetailResponse(
        session=session,
        messages=messages,
        active_job_id=active_job_id,
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
    "/entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}/cancel",
)
async def cancel_chat_message_job(
    entity_id: str,
    session_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-flight chat/agent/preset run.

    Flips the DB row to ``cancelled`` first (so any racing terminal write
    from the runner loses to us), then best-effort cancels the asyncio
    task. Idempotent: calling on an already-terminal job returns
    ``cancelled: False`` without error.
    """
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
    if job.status in TERMINAL_JOB_STATUSES:
        return {"ok": True, "cancelled": False}
    job.status = "cancelled"
    job.step_detail = "Cancelled by user"
    job.updated_at = utc_now()
    await db.commit()
    await cancel_tracked_task(f"chat:{job_id}")
    return {"ok": True, "cancelled": True}


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
    mode = _resolve_agent_mode(body.agent_mode, body.use_deep_agent)
    if mode in ("react", "deep_agent"):
        attach_preamble = ""
        warnings: List[str] = []
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

    if mode in ("react", "deep_agent"):
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
            agent_mode=mode,
        )
        db.add(job)
        session.updated_at = utc_now()
        await db.commit()
        await db.refresh(user_msg)
        launch_tracked_task(f"chat:{job.id}", lambda: run_chat_agent_job(job.id))
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
    mode = _resolve_agent_mode(body.agent_mode, body.use_deep_agent)
    # extract_info + legal_review always run in react mode — they browse the
    # workspace, call workspace_read_file on selected docs, and need a
    # guaranteed workspace_write_file for the JSON deliverable.
    if preset_id in {
        "extract_info", "legal_review",
        "initial_screening", "initial_screening_v2",
    }:
        mode = "react"

    # ----- Agent path (react or deep_agent): background job, return 202 -----
    if mode in ("react", "deep_agent"):
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
        elif preset_id == "extract_info":
            existing_meta = None
            if entity.metadata_json:
                try:
                    existing_meta = json.loads(entity.metadata_json)
                except json.JSONDecodeError:
                    pass
            task_body = render_extract_info(
                entity.name, entity.website,
                existing_metadata=existing_meta,
            )
        elif preset_id == "legal_review":
            existing_meta = None
            if entity.metadata_json:
                try:
                    existing_meta = json.loads(entity.metadata_json)
                except json.JSONDecodeError:
                    pass
            meta = existing_meta or {}
            existing_opinions = await _load_legal_review_opinions(db, entity_id)
            prior_rounds_migrated = [
                _migrate_prior_round_entry(e)
                for e in (meta.get("prior_rounds") or [])
                if isinstance(e, dict)
            ]
            task_body = render_legal_review(
                entity.name,
                entity.website,
                entity_positions=meta.get("_positions") or [],
                existing_legal_reviews=existing_opinions,
                existing_prior_rounds=prior_rounds_migrated,
                existing_fact_discrepancies=(
                    meta.get("_fact_discrepancies") or []
                ),
            )
        elif preset_id == "initial_screening":
            # Phase 1 prompt only — compose/review stages run after the
            # agent finishes (see run_preset_agent_job below).
            task_body = render_initial_screening_research(
                entity_name=entity.name,
                entity_website=entity.website,
                entity_id=entity_id,
                run_id="{{run_id}}",  # substituted at agent build time
            )
        elif preset_id == "initial_screening_v2":
            # The v2 orchestrator builds its own prompts per sub-agent; no
            # top-level task_body is used. Stub a label for the user
            # message summary line only.
            task_body = (
                "Run Initial Screening v2: split research (survey + 6 "
                "parallel section agents) → compose → fact-check."
            )
        else:
            raise HTTPException(status_code=400, detail="Preset not implemented")

        warnings: List[str] = []

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
            agent_mode=mode,
        )
        db.add(job)
        session.updated_at = utc_now()
        await db.commit()
        await db.refresh(user_msg)

        launch_tracked_task(f"chat:{job.id}", lambda: run_preset_agent_job(job.id))
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
            # Generic JSON preset path — pass the agent's output through
            # unchanged. extract_info / legal_review have dedicated post-
            # processing that routes through the async agent job, not here.
            deliverable_body = json.dumps(parsed, indent=2, ensure_ascii=False)
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
