"""Canonical artifact creation (shared by REST and chat presets)."""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Artifact, Entity
import app.services.storage as storage_module


async def create_artifact_for_entity(
    db: AsyncSession,
    entity_id: str,
    artifact_type: Literal["memo", "factsheet", "report", "other"],
    content: str,
    status: Literal["draft", "final"] = "draft",
    title: Optional[str] = None,
    file_suffix: str = ".md",
) -> Artifact:
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    if not result.scalar_one_or_none():
        raise ValueError("Entity not found")

    q = (
        select(Artifact)
        .where(Artifact.entity_id == entity_id)
        .where(Artifact.artifact_type == artifact_type)
    )
    if title is None:
        q = q.where(Artifact.title.is_(None))
    else:
        q = q.where(Artifact.title == title)
    q = q.order_by(Artifact.version.desc()).limit(1)
    result = await db.execute(q)
    latest = result.scalars().first()
    version = (latest.version + 1) if latest else 1

    artifact_id = str(uuid.uuid4())
    relative_path = f"{entity_id}/artifacts/{artifact_id}/v{version}{file_suffix}"

    st = storage_module.storage
    await st.ensure_dir(f"{entity_id}/artifacts/{artifact_id}")
    await st.write_file(relative_path, content.encode("utf-8"))

    artifact = Artifact(
        id=artifact_id,
        entity_id=entity_id,
        artifact_type=artifact_type,
        title=title,
        version=version,
        status=status,
        relative_path=relative_path,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


def _lineage_next_version(
    db: Session, entity_id: str, artifact_type: str, title: Optional[str]
) -> int:
    q = (
        select(Artifact)
        .where(Artifact.entity_id == entity_id)
        .where(Artifact.artifact_type == artifact_type)
    )
    if title is None:
        q = q.where(Artifact.title.is_(None))
    else:
        q = q.where(Artifact.title == title)
    q = q.order_by(Artifact.version.desc())
    latest = db.execute(q).scalars().first()
    return (latest.version + 1) if latest else 1


def artifact_file_suffix(relative_path: str) -> str:
    for suf in (".json", ".md", ".txt"):
        if relative_path.endswith(suf):
            return suf
    return ".md"


def create_artifact_for_entity_sync(
    db: Session,
    entity_id: str,
    artifact_type: Literal["memo", "factsheet", "report", "other"],
    content: str,
    status: Literal["draft", "final"] = "draft",
    title: Optional[str] = None,
    file_suffix: str = ".md",
) -> Artifact:
    if (
        db.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
        is None
    ):
        raise ValueError("Entity not found")

    version = _lineage_next_version(db, entity_id, artifact_type, title)
    artifact_id = str(uuid.uuid4())
    relative_path = f"{entity_id}/artifacts/{artifact_id}/v{version}{file_suffix}"

    st = storage_module.storage
    st.ensure_dir_sync(f"{entity_id}/artifacts/{artifact_id}")
    st.write_file_sync(relative_path, content.encode("utf-8"))

    artifact = Artifact(
        id=artifact_id,
        entity_id=entity_id,
        artifact_type=artifact_type,
        title=title,
        version=version,
        status=status,
        relative_path=relative_path,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def overwrite_artifact_content_sync(
    db: Session, entity_id: str, artifact_id: str, content: str
) -> Artifact:
    row = db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id, Artifact.entity_id == entity_id
        )
    ).scalar_one_or_none()
    if not row:
        raise ValueError("Artifact not found")

    storage_module.storage.write_file_sync(row.relative_path, content.encode("utf-8"))
    from datetime import datetime

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row
