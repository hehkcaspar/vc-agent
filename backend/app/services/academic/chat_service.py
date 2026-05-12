"""Chat service — background job execution for scholar chat sessions.

V2 rewrite: delegates to `scholar_chat.send_chat_turn` (Gemini
Interactions API). Persists the returned `interaction_id` on the
session row so the next turn continues the server-side chain.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

from sqlalchemy import update

from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import AcademicChatJob, AcademicChatMessage, AcademicChatSession
from app.services.job_tasks import TERMINAL_JOB_STATUSES, mark_job_cancelled

from .scholar_chat import send_chat_turn

logger = logging.getLogger(__name__)


async def _set_job_status_if_not_terminal(db, job_id: str, **values) -> None:
    """SQL-level guard: only write if status is still non-terminal.

    Race-proof against the cancel endpoint flipping status to ``cancelled``
    on a separate session: a stale in-memory ``job`` won't clobber it.
    """
    await db.execute(
        update(AcademicChatJob)
        .where(AcademicChatJob.id == job_id)
        .where(AcademicChatJob.status.notin_(TERMINAL_JOB_STATUSES))
        .values(**values)
    )
    await db.commit()


async def run_chat_job(job_id: str) -> None:
    try:
        async with AcademicAsyncSessionLocal() as db:
            job = await db.get(AcademicChatJob, job_id)
            if not job:
                return

            job.status = "running"
            job.step_detail = "Scholar chat working..."
            await db.commit()

            session = await db.get(AcademicChatSession, job.session_id)
            if not session:
                await _set_job_status_if_not_terminal(
                    db, job_id, status="failed", error_message="session not found"
                )
                return

            user_msg = (
                await db.get(AcademicChatMessage, job.user_message_id)
                if job.user_message_id else None
            )
            if user_msg is None:
                await _set_job_status_if_not_terminal(
                    db, job_id, status="failed", error_message="user message not found"
                )
                return

            try:
                reply, new_interaction_id = await send_chat_turn(
                    scholar_id=job.scholar_id,
                    user_message=user_msg.content,
                    previous_interaction_id=session.last_interaction_id,
                )
            except Exception as e:
                logger.exception("chat_service: chat turn failed for job %s", job_id)
                await _set_job_status_if_not_terminal(
                    db, job_id, status="failed", error_message=str(e)
                )
                return

            assistant_msg = AcademicChatMessage(
                id=str(_uuid.uuid4()),
                session_id=job.session_id,
                role="assistant",
                content=reply or "(no reply)",
            )
            db.add(assistant_msg)
            if new_interaction_id:
                session.last_interaction_id = new_interaction_id
            await db.flush()

            await _set_job_status_if_not_terminal(
                db, job_id,
                status="succeeded",
                assistant_message_id=assistant_msg.id,
                agent_run_id=new_interaction_id or None,
                step_detail=None,
            )
    except asyncio.CancelledError:
        await mark_job_cancelled(AcademicAsyncSessionLocal, AcademicChatJob, job_id)
        raise
