"""In-memory registry for async metadata pre-process jobs (single-process MVP)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, Literal, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.datetime_support import utc_now, utc_now_iso
from app.models import Artifact, Entity, Resource
from app.schemas import metadata_json_to_dict
from app.services.gemini_context import build_context_parts
from app.services.gemini_runner import generate_json_with_context
from app.services.json_loose import parse_json_loose
from app.services.file_lookup_normalize import normalize_file_lookup_result
from app.services.native_file_metadata import extract_native_file_metadata
from app.services.preset_registry import load_file_lookup_preprocess_instruction
from app.services.storage import storage

TargetKind = Literal["resource", "artifact"]

MAX_JOBS = 200

_lock = asyncio.Lock()
_jobs: Dict[str, dict[str, Any]] = {}
# (entity_id, target_kind, target_id) -> job_id while pending/running
_inflight: Dict[Tuple[str, str, str], str] = {}


def _prune_locked() -> None:
    if len(_jobs) <= MAX_JOBS:
        return
    terminal = [
        (jid, rec.get("created_at"))
        for jid, rec in _jobs.items()
        if rec.get("status") in ("succeeded", "failed")
    ]
    terminal.sort(key=lambda x: x[1] or utc_now())
    while len(_jobs) > MAX_JOBS and terminal:
        jid, _ = terminal.pop(0)
        if jid in _jobs:
            del _jobs[jid]


async def create_or_reuse_job(
    entity_id: str, target_kind: TargetKind, target_id: str
) -> Tuple[str, bool]:
    """Returns (job_id, schedule_task). When reusing pending/running, schedule_task is False."""
    key = (entity_id, target_kind, target_id)
    async with _lock:
        if key in _inflight:
            jid = _inflight[key]
            rec = _jobs.get(jid)
            if rec and rec["status"] in ("pending", "running"):
                return jid, False
            del _inflight[key]
        _prune_locked()
        jid = str(uuid.uuid4())
        now = utc_now()
        _jobs[jid] = {
            "job_id": jid,
            "entity_id": entity_id,
            "target_kind": target_kind,
            "target_id": target_id,
            "status": "pending",
            "error_message": None,
            "created_at": now,
        }
        _inflight[key] = jid
        return jid, True


async def get_job_status(entity_id: str, job_id: str) -> Optional[dict[str, Any]]:
    async with _lock:
        rec = _jobs.get(job_id)
        if not rec or rec["entity_id"] != entity_id:
            return None
        out = {
            "job_id": job_id,
            "status": rec["status"],
        }
        if rec["status"] == "failed" and rec.get("error_message"):
            out["error_message"] = rec["error_message"]
        return out


def _set_job_failed_locked(job_id: str, msg: str, key: Tuple[str, str, str]) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error_message"] = msg
    if _inflight.get(key) == job_id:
        del _inflight[key]


def _set_job_succeeded_locked(job_id: str, key: Tuple[str, str, str]) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "succeeded"
    if _inflight.get(key) == job_id:
        del _inflight[key]


def _json_merge_preprocess_metadata(
    existing: Dict[str, Any],
    native_block: Dict[str, Any],
    gemini_block: Dict[str, Any],
) -> str:
    merged = {
        **existing,
        "native_file_metadata": native_block,
        "gemini_preprocessed": gemini_block,
    }
    return json.dumps(merged, ensure_ascii=False)


async def _persist_preprocess_metadata(
    db: AsyncSession,
    *,
    target_kind: TargetKind,
    entity_id: str,
    target_id: str,
    native_block: Dict[str, Any],
    gemini_block: Dict[str, Any],
) -> None:
    if target_kind == "resource":
        row = await _load_resource(db, entity_id, target_id)
        if not row:
            raise ValueError("resource_not_found")
    else:
        row = await _load_artifact(db, entity_id, target_id)
        if not row:
            raise ValueError("artifact_not_found")
    existing = metadata_json_to_dict(getattr(row, "metadata_json", None)) or {}
    row.metadata_json = _json_merge_preprocess_metadata(
        existing, native_block, gemini_block
    )
    row.updated_at = utc_now()


async def run_metadata_preprocess_job(job_id: str) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if not rec:
            return
        entity_id = rec["entity_id"]
        target_kind: TargetKind = rec["target_kind"]
        target_id = rec["target_id"]
        key = (entity_id, target_kind, target_id)
        rec["status"] = "running"

    try:
        async with AsyncSessionLocal() as db:
            await _get_entity(db, entity_id)
            system_instruction = load_file_lookup_preprocess_instruction()
            user_message = (
                "Index only the single attached file. For raster images, use image_content "
                "(OCR when text-rich; otherwise objective visual description). "
                "Output one JSON object matching the system schema."
            )

            if target_kind == "resource":
                row = await _load_resource(db, entity_id, target_id)
                if not row:
                    raise ValueError("resource_not_found")
                resources = [row]
                artifacts_list: list[Artifact] = []
                native_source: Dict[str, Any] = {
                    "kind": "resource",
                    "resource_type": row.resource_type,
                    "title": row.title,
                    "original_filename": row.original_filename,
                }
                raw_bytes: Optional[bytes] = None
                if row.resource_type == "file" and row.relative_path:
                    try:
                        raw_bytes = await storage.read_file(row.relative_path)
                    except Exception:
                        raw_bytes = None
                mime_for_native: Optional[str] = row.mime_type
                filename_hint: Optional[str] = row.original_filename or row.title
            else:
                row = await _load_artifact(db, entity_id, target_id)
                if not row:
                    raise ValueError("artifact_not_found")
                resources = []
                artifacts_list = [row]
                native_source = {
                    "kind": "artifact",
                    "artifact_type": row.artifact_type,
                    "title": row.title,
                    "version": row.version,
                }
                raw_bytes = None
                if row.relative_path:
                    try:
                        raw_bytes = await storage.read_file(row.relative_path)
                    except Exception:
                        raw_bytes = None
                mime_for_native = None
                filename_hint = row.title

            context_parts, _warnings = await build_context_parts(
                resources, artifacts_list
            )
            if not context_parts:
                raise ValueError("no_context_parts_built")

        native_block = extract_native_file_metadata(
            raw_bytes,
            mime_type=mime_for_native,
            filename_hint=filename_hint,
            source=native_source,
        )

        raw_json = await asyncio.to_thread(
            generate_json_with_context,
            system_instruction,
            [],
            user_message,
            context_parts,
            False,
            None,
        )
        try:
            parsed = parse_json_loose(raw_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid_model_json: {e}") from e
        normalized = normalize_file_lookup_result(parsed)
        model_id = (
            settings.GEMINI_METADATA_EXTRACTION_MODEL
            or settings.GEMINI_MODEL
            or ""
        )
        block = {
            "at": utc_now_iso(),
            "model": model_id,
            "kind": "file_lookup",
            "extraction": normalized,
        }

        async with AsyncSessionLocal() as db:
            await _persist_preprocess_metadata(
                db,
                target_kind=target_kind,
                entity_id=entity_id,
                target_id=target_id,
                native_block=native_block,
                gemini_block=block,
            )
            await db.commit()

        async with _lock:
            _set_job_succeeded_locked(job_id, key)
    except Exception as e:
        async with _lock:
            _set_job_failed_locked(job_id, str(e), key)


async def _get_entity(db: AsyncSession, entity_id: str) -> Entity:
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise ValueError("entity_not_found")
    return entity


async def _load_resource(
    db: AsyncSession, entity_id: str, resource_id: str
) -> Optional[Resource]:
    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.entity_id == entity_id,
        )
    )
    return result.scalar_one_or_none()


async def _load_artifact(
    db: AsyncSession, entity_id: str, artifact_id: str
) -> Optional[Artifact]:
    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.entity_id == entity_id,
        )
    )
    return result.scalar_one_or_none()
