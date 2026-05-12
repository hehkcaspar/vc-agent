"""Chat service — background job execution for scholar chat sessions.

V2 rewrite: delegates to `scholar_chat.send_chat_turn` (Gemini
Interactions API). Persists the returned `interaction_id` on the
session row so the next turn continues the server-side chain.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import AcademicChatJob, AcademicChatMessage, AcademicChatSession

from .scholar_chat import send_chat_turn

logger = logging.getLogger(__name__)


_TERMINAL = ("succeeded", "failed", "cancelled")


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
                await db.refresh(job)
                if job.status not in _TERMINAL:
                    job.status = "failed"
                    job.error_message = "session not found"
                    await db.commit()
                return

            user_msg = None
            if job.user_message_id:
                user_msg = await db.get(AcademicChatMessage, job.user_message_id)
            if user_msg is None:
                await db.refresh(job)
                if job.status not in _TERMINAL:
                    job.status = "failed"
                    job.error_message = "user message not found"
                    await db.commit()
                return

            try:
                reply, new_interaction_id = await send_chat_turn(
                    scholar_id=job.scholar_id,
                    user_message=user_msg.content,
                    previous_interaction_id=session.last_interaction_id,
                )
            except Exception as e:
                logger.exception("chat_service: chat turn failed for job %s", job_id)
                await db.refresh(job)
                if job.status not in _TERMINAL:
                    job.status = "failed"
                    job.error_message = str(e)
                    await db.commit()
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

            await db.refresh(job)
            if job.status not in _TERMINAL:
                job.status = "succeeded"
                job.assistant_message_id = assistant_msg.id
                job.agent_run_id = new_interaction_id or None
                job.step_detail = None
                await db.commit()
    except asyncio.CancelledError:
        async with AcademicAsyncSessionLocal() as db:
            job = await db.get(AcademicChatJob, job_id)
            if job and job.status not in _TERMINAL:
                job.status = "cancelled"
                job.step_detail = "Cancelled by user"
                await db.commit()
        raise
