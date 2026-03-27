import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import declarative_base, relationship

from app.datetime_support import utc_now

Base = declarative_base()


def generate_uuid():
    return str(uuid.uuid4())


class Entity(Base):
    __tablename__ = "entities"

    id = Column(String, primary_key=True, default=generate_uuid)
    type = Column(String, default="company")
    name = Column(String, nullable=False)
    website = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    resources = relationship("Resource", back_populates="entity", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="entity", cascade="all, delete-orphan")
    chat_sessions = relationship(
        "ConversationSession", back_populates="entity", cascade="all, delete-orphan"
    )


class IngestItem(Base):
    __tablename__ = "ingest_items"

    ingest_id = Column(String, primary_key=True, default=generate_uuid)
    source = Column(String, nullable=False, default="frontend")
    status = Column(String, default="parked")  # parked, resolution_required, failed, materialized
    parkinglot_path = Column(String, nullable=False)
    entity_hint_name = Column(String, nullable=True)
    entity_hint_domain = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class Resource(Base):
    __tablename__ = "resources"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    resource_type = Column(String, nullable=False)  # file, text, url
    title = Column(String, nullable=False)
    mime_type = Column(String, nullable=True)
    original_filename = Column(String, nullable=True)
    relative_path = Column(String, nullable=False)
    url = Column(String, nullable=True)
    origin_ingest_id = Column(String, ForeignKey("ingest_items.ingest_id"), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    entity = relationship("Entity", back_populates="resources")


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    artifact_type = Column(String, nullable=False)  # memo, factsheet, report, other
    title = Column(String, nullable=True)
    version = Column(Integer, default=1)
    status = Column(String, default="draft")  # draft, final
    relative_path = Column(String, nullable=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    entity = relationship("Entity", back_populates="artifacts")


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    title = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    entity = relationship("Entity", back_populates="chat_sessions")
    messages = relationship(
        "ConversationMessage",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, ForeignKey("conversation_sessions.id"), nullable=False)
    role = Column(String, nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    session = relationship("ConversationSession", back_populates="messages")


class ChatCompletionJob(Base):
    """Background deep-agent chat run; client polls for step_detail and completion."""

    __tablename__ = "chat_completion_jobs"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    session_id = Column(String, ForeignKey("conversation_sessions.id"), nullable=False)
    user_message_id = Column(String, ForeignKey("conversation_messages.id"), nullable=False)

    status = Column(String, nullable=False, default="pending")
    # Human-readable progress for the UI (tool names, phase, errors).
    step_detail = Column(String, nullable=True)
    agent_run_id = Column(String, nullable=True)
    assistant_message_id = Column(String, ForeignKey("conversation_messages.id"), nullable=True)
    error_message = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    tool_trace_json = Column(Text, nullable=True)

    resource_ids_json = Column(Text, nullable=False)
    artifact_ids_json = Column(Text, nullable=False)
    model_profile_id = Column(String, nullable=True)
    harness_extras = Column(Text, nullable=False)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class ArtifactEditEvent(Base):
    """Audit log for artifact edit attempts (Option B harness; design §8.5)."""

    __tablename__ = "artifact_edit_events"

    id = Column(String, primary_key=True, default=generate_uuid)
    correlation_id = Column(String, nullable=False, index=True)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    session_id = Column(String, ForeignKey("conversation_sessions.id"), nullable=True)
    artifact_id = Column(String, nullable=True)

    requested_mode = Column(String, nullable=True)  # versioned | overwrite
    resolved_mode = Column(String, nullable=True)
    state = Column(String, nullable=False)
    intent_summary = Column(Text, nullable=True)
    tool_context_json = Column(Text, nullable=True)
    validation_result_json = Column(Text, nullable=True)
    before_checksum = Column(String, nullable=True)
    after_checksum = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    run_id = Column(String, nullable=True)
    pipeline_version = Column(String, default="option_b")

    created_at = Column(DateTime, default=utc_now)
