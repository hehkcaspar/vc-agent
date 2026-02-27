import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import declarative_base, relationship

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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    resources = relationship("Resource", back_populates="entity", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="entity", cascade="all, delete-orphan")


class IngestItem(Base):
    __tablename__ = "ingest_items"

    ingest_id = Column(String, primary_key=True, default=generate_uuid)
    source = Column(String, nullable=False, default="frontend")
    status = Column(String, default="parked")  # parked, resolution_required, failed, materialized
    parkinglot_path = Column(String, nullable=False)
    entity_hint_name = Column(String, nullable=True)
    entity_hint_domain = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entity = relationship("Entity", back_populates="resources")


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True, default=generate_uuid)
    entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    artifact_type = Column(String, nullable=False)  # memo, factsheet, report, other
    version = Column(Integer, default=1)
    status = Column(String, default="draft")  # draft, final
    relative_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entity = relationship("Entity", back_populates="artifacts")
