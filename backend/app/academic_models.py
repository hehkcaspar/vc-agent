"""
Academic Tracking v2 models — 3 SQL tables as a lightweight index.

Full scholar state lives in JSON/JSONL/markdown files on disk under
``data/scholars/{scholar_id}/``.  These SQL tables exist only for
cross-scholar queries, scheduling, and signal feeds.

See docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.academic_database import AcademicBase
from app.datetime_support import utc_now


def _uuid() -> str:
    return str(uuid.uuid4())


class Scholar(AcademicBase):
    """Cross-scholar index row.  Source of truth is ``data/scholars/{id}/profile.json``."""

    __tablename__ = "scholars"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    status = Column(String, default="active")               # active | evaluating | paused | archived
    tracking_priority = Column(String, default="medium")     # high | medium | low
    tags = Column(Text, nullable=True)                       # JSON array
    entity_id = Column(String, nullable=True)                # FK to portfolio entity (nullable)
    dossier_path = Column(String, nullable=False)            # data/scholars/{id}/
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    events = relationship(
        "ScholarEvent",
        back_populates="scholar",
        cascade="all, delete-orphan",
    )
    channels = relationship(
        "Channel",
        back_populates="scholar",
        cascade="all, delete-orphan",
    )
    chat_sessions = relationship(
        "AcademicChatSession",
        back_populates="scholar",
        cascade="all, delete-orphan",
    )


class ScholarEvent(AcademicBase):
    """SQL mirror of events.jsonl — key fields only, for cross-scholar queries."""

    __tablename__ = "scholar_events"

    id = Column(String, primary_key=True, default=_uuid)
    scholar_id = Column(String, ForeignKey("scholars.id"), nullable=False)
    event_type = Column(String, nullable=False)
    significance = Column(String, default="medium")          # high | medium | low
    title = Column(String, nullable=True)
    is_read = Column(Boolean, default=False)
    source_url = Column(String, nullable=True)
    event_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    scholar = relationship("Scholar", back_populates="events")


class Channel(AcademicBase):
    """Monitoring channel — scheduling fields only; full snapshots live in channels.json."""

    __tablename__ = "channels"

    id = Column(String, primary_key=True, default=_uuid)
    scholar_id = Column(String, ForeignKey("scholars.id"), nullable=False)
    channel_type = Column(String, nullable=False)
    url = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    polling_interval_hours = Column(Integer, default=168)     # 1 week
    last_polled_at = Column(DateTime, nullable=True)
    last_changed_at = Column(DateTime, nullable=True)
    poll_error_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)

    scholar = relationship("Scholar", back_populates="channels")


class AcademicChatSession(AcademicBase):
    """Per-scholar chat session."""

    __tablename__ = "academic_chat_sessions"

    id = Column(String, primary_key=True, default=_uuid)
    scholar_id = Column(String, ForeignKey("scholars.id"), nullable=False)
    title = Column(String, nullable=True)
    # V2 — Gemini Interactions API server-side session id. Nullable;
    # the first turn of a session has no previous_interaction_id and
    # this field is populated once the first turn completes.
    last_interaction_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    scholar = relationship("Scholar", back_populates="chat_sessions")
    messages = relationship(
        "AcademicChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AcademicChatMessage.created_at",
    )


class AcademicChatMessage(AcademicBase):
    """Individual message in a scholar chat session."""

    __tablename__ = "academic_chat_messages"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, ForeignKey("academic_chat_sessions.id"), nullable=False)
    role = Column(String, nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    session = relationship("AcademicChatSession", back_populates="messages")


class AcademicChatJob(AcademicBase):
    """Background job tracking for async scholar chat agent runs."""

    __tablename__ = "academic_chat_jobs"

    id = Column(String, primary_key=True, default=_uuid)
    scholar_id = Column(String, ForeignKey("scholars.id"), nullable=False)
    session_id = Column(String, ForeignKey("academic_chat_sessions.id"), nullable=False)
    user_message_id = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | running | succeeded | failed
    step_detail = Column(String, nullable=True)
    assistant_message_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    agent_run_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
