"""Chat service — background job execution for scholar chat sessions."""

from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select

from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import AcademicChatJob, AcademicChatMessage

logger = logging.getLogger(__name__)


async def run_chat_job(job_id: str) -> None:
    """Background task: invoke the scholar agent for a chat message."""
    from app.services.academic.scholar_agent import invoke_scholar_chat

    async with AcademicAsyncSessionLocal() as db:
        job = await db.get(AcademicChatJob, job_id)
        if not job:
            return

        job.status = "running"
        job.step_detail = "Agent working..."
        await db.commit()

        # Load session message history
        result = await db.execute(
            select(AcademicChatMessage)
            .where(AcademicChatMessage.session_id == job.session_id)
            .order_by(AcademicChatMessage.created_at.asc())
        )
        db_messages = result.scalars().all()

        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in db_messages
        ]

        try:
            agent_result = await invoke_scholar_chat(
                scholar_id=job.scholar_id,
                messages=messages,
                session_id=job.session_id,
            )

            if agent_result.get("error"):
                job.status = "failed"
                job.error_message = agent_result["error"]
                await db.commit()
                return

            reply = agent_result.get("reply", "")

            assistant_msg = AcademicChatMessage(
                id=str(_uuid.uuid4()),
                session_id=job.session_id,
                role="assistant",
                content=reply,
            )
            db.add(assistant_msg)
            await db.flush()

            job.status = "succeeded"
            job.assistant_message_id = assistant_msg.id
            job.agent_run_id = agent_result.get("run_id")
            job.step_detail = None
            await db.commit()

        except Exception as e:
            logger.exception("Chat job %s failed: %s", job_id, e)
            job.status = "failed"
            job.error_message = str(e)
            await db.commit()
