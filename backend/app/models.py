import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text, Index, text
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
    metadata_json = Column(Text, nullable=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    workspace_nodes = relationship(
        "WorkspaceNode", back_populates="entity", cascade="all, delete-orphan",
        foreign_keys="WorkspaceNode.entity_id",
    )
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


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    title = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    # Gemini Interactions API chain bookmark
    last_gemini_interaction_id = Column(String, nullable=True)
    last_gemini_interaction_at = Column(DateTime, nullable=True)

    entity = relationship("Entity", back_populates="chat_sessions")
    messages = relationship(
        "ConversationMessage",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    @property
    def has_gemini_chain(self) -> bool:
        """Whether this session has a valid (non-expired) Gemini Interactions API chain."""
        if not self.last_gemini_interaction_id or not self.last_gemini_interaction_at:
            return False
        from app.config import settings
        age = (utc_now() - self.last_gemini_interaction_at).days
        return age < settings.GEMINI_INTERACTION_TTL_DAYS


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, ForeignKey("conversation_sessions.id"), nullable=False)
    role = Column(String, nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    model_profile_id = Column(String, nullable=True)   # "gemini_google" | "kimi_moonshot"
    node_ids_json = Column(Text, nullable=True)         # JSON array of attached workspace node IDs
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

    node_ids_json = Column(Text, nullable=False, default="[]")
    model_profile_id = Column(String, nullable=True)
    harness_extras = Column(Text, nullable=False)

    # "react" | "deep_agent". Determines which agent factory the job worker uses.
    agent_mode = Column(String, nullable=True, default="react")

    # When non-null, this row is a preset shortcut run (Red Team etc.) and the
    # worker is run_preset_agent_job. JSON: {preset_id, deliverable_type,
    # deliverable_status, industry, stage}.
    preset_payload_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


# ---------------------------------------------------------------------------
# Workspace (hierarchical file system per entity)
# ---------------------------------------------------------------------------

class WorkspaceNode(Base):
    """A file, folder, or bookmark in an entity's workspace tree."""

    __tablename__ = "workspace_nodes"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False, index=True)

    # Tree structure
    node_type = Column(String, nullable=False)          # file | folder | bookmark
    name = Column(String, nullable=False)               # display name
    path = Column(String, nullable=False)               # materialized: "Data Room/Financials/Q4.xlsx"
    parent_id = Column(String, ForeignKey("workspace_nodes.id"), nullable=True)

    # File-specific (ignored for folders and bookmarks)
    mime_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    checksum = Column(String, nullable=True)            # SHA-256 of current content
    storage_key = Column(String, nullable=True)         # path-independent blob key
    url = Column(String, nullable=True)                 # bookmark nodes only

    # Versioning
    version = Column(Integer, default=1)

    # Provenance
    origin_type = Column(String, nullable=True)         # upload | agent | ingest | shared | user
    origin_ref = Column(String, nullable=True)          # ingest_id, agent_run_id, etc.

    # Metadata (deliverable type/status, descriptions, etc.)
    metadata_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)        # soft delete

    entity = relationship("Entity", back_populates="workspace_nodes")

    __table_args__ = (
        Index(
            "uq_entity_path", "entity_id", "path",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_workspace_parent", "entity_id", "parent_id"),
    )


class WorkspaceOp(Base):
    """Audit log for workspace mutations (create, overwrite, move, delete, etc.)."""

    __tablename__ = "workspace_ops"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False, index=True)
    batch_id = Column(String, nullable=True, index=True)    # group for atomic undo

    op_type = Column(String, nullable=False)       # create_file | create_folder | overwrite |
                                                    # move | rename | copy | delete | restore |
                                                    # upload_tree | extract_zip
    actor_type = Column(String, nullable=False)    # user | agent | system
    actor_ref = Column(String, nullable=True)

    node_id = Column(String, nullable=True)
    payload_json = Column(Text, nullable=False)    # op-specific data
    inverse_json = Column(Text, nullable=True)     # for undo

    # Versioning checkpoints
    before_checksum = Column(String, nullable=True)
    after_checksum = Column(String, nullable=True)

    undone_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
