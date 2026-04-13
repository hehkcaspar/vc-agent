"""In-memory registry for async metadata pre-process jobs (single-process MVP)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.datetime_support import utc_now, utc_now_iso
from app.models import Entity, WorkspaceNode
from app.schemas import metadata_json_to_dict
from app.services.gemini_context import build_context_parts
from app.services.direct_llm import generate_json_one_shot
from app.services.json_loose import parse_json_loose
from app.services.file_lookup_normalize import normalize_file_lookup_result
from app.services.native_file_metadata import extract_native_file_metadata
from app.services.storage import storage

MAX_JOBS = 200

_lock = asyncio.Lock()
_jobs: Dict[str, dict[str, Any]] = {}
_inflight: Dict[Tuple[str, str], str] = {}  # (entity_id, node_id) -> job_id

# ── Batch-level extraction progress (per entity) ───────────────────────
_batches: Dict[str, dict[str, Any]] = {}  # entity_id -> batch state


async def register_extraction_batch(
    entity_id: str, node_ids: List[str], node_names: Dict[str, str],
) -> None:
    """Register a batch of files for background extraction progress tracking."""
    async with _lock:
        existing = _batches.get(entity_id)
        if existing and existing["remaining_ids"]:
            # Merge into an already-running batch
            existing["total"] += len(node_ids)
            existing["remaining_ids"].extend(node_ids)
            existing["node_names"].update(node_names)
        else:
            _batches[entity_id] = {
                "total": len(node_ids),
                "completed": 0,
                "failed": 0,
                "remaining_ids": list(node_ids),
                "current_node_id": node_ids[0] if node_ids else None,
                "current_name": node_names.get(node_ids[0]) if node_ids else None,
                "node_names": dict(node_names),
                "errors": [],  # [{name, error}] — last 10
            }


async def update_batch_progress(
    entity_id: str, node_id: str, *, success: bool, error_msg: str | None = None,
) -> None:
    """Called after each file extraction completes (success or failure)."""
    async with _lock:
        batch = _batches.get(entity_id)
        if not batch:
            return
        if node_id in batch["remaining_ids"]:
            batch["remaining_ids"].remove(node_id)
        if success:
            batch["completed"] += 1
        else:
            batch["failed"] += 1
            name = batch["node_names"].get(node_id, node_id[:8])
            batch["errors"].append({"name": name, "error": error_msg or "unknown"})
            batch["errors"] = batch["errors"][-10:]
        # Advance current pointer
        if batch["remaining_ids"]:
            nxt = batch["remaining_ids"][0]
            batch["current_node_id"] = nxt
            batch["current_name"] = batch["node_names"].get(nxt)
        else:
            batch["current_node_id"] = None
            batch["current_name"] = None


async def get_extraction_progress(entity_id: str) -> dict[str, Any] | None:
    """Return extraction progress for an entity, or None if idle."""
    async with _lock:
        batch = _batches.get(entity_id)
        if not batch:
            return None
        remaining = len(batch["remaining_ids"])
        if remaining == 0 and batch["current_node_id"] is None:
            # Batch finished — snapshot then clean up
            result: dict[str, Any] = {
                "status": "done",
                "total": batch["total"],
                "completed": batch["completed"],
                "failed": batch["failed"],
                "remaining": 0,
                "current_file": None,
                "errors": batch["errors"],
            }
            del _batches[entity_id]
            return result
        return {
            "status": "running",
            "total": batch["total"],
            "completed": batch["completed"],
            "failed": batch["failed"],
            "remaining": remaining,
            "current_file": batch["current_name"],
            "errors": batch["errors"],
        }


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
    entity_id: str, node_id: str,
) -> Tuple[str, bool]:
    key = (entity_id, node_id)
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
            "node_id": node_id,
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
        out = {"job_id": job_id, "status": rec["status"]}
        if rec["status"] == "failed" and rec.get("error_message"):
            out["error_message"] = rec["error_message"]
        return out


def _set_job_failed_locked(job_id: str, msg: str, key: Tuple[str, str]) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error_message"] = msg
    if _inflight.get(key) == job_id:
        del _inflight[key]


def _set_job_succeeded_locked(job_id: str, key: Tuple[str, str]) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "succeeded"
    if _inflight.get(key) == job_id:
        del _inflight[key]


async def run_metadata_preprocess_job(job_id: str) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if not rec:
            return
        entity_id = rec["entity_id"]
        node_id = rec["node_id"]
        key = (entity_id, node_id)
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

            node = await _load_node(db, entity_id, node_id)
            if not node:
                raise ValueError("node_not_found")

            native_source: Dict[str, Any] = {
                "kind": node.node_type,
                "name": node.name,
                "path": node.path,
                "mime_type": node.mime_type,
            }
            raw_bytes: Optional[bytes] = None
            if node.storage_key:
                try:
                    raw_bytes = await storage.read_file(node.storage_key)
                except Exception:
                    raw_bytes = None

            context_parts, _warnings = await build_context_parts([node])
            if not context_parts:
                raise ValueError("no_context_parts_built")

        native_block = extract_native_file_metadata(
            raw_bytes,
            mime_type=node.mime_type,
            filename_hint=node.name,
            source=native_source,
        )

        raw_json = await asyncio.to_thread(
            generate_json_one_shot,
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
            node = await _load_node(db, entity_id, node_id)
            if not node:
                raise ValueError("node_not_found")
            existing = metadata_json_to_dict(getattr(node, "metadata_json", None)) or {}
            merged = {
                **existing,
                "native_file_metadata": native_block,
                "gemini_preprocessed": block,
            }
            # Surface one_liner as description for tree/agent context
            # (skip if user already set a manual description via annotate)
            one_liner = (normalized.get("one_liner") or "").strip()
            if one_liner and not existing.get("description"):
                merged["description"] = one_liner
            node.metadata_json = json.dumps(merged, ensure_ascii=False)
            node.updated_at = utc_now()
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


async def _load_node(
    db: AsyncSession, entity_id: str, node_id: str,
) -> Optional[WorkspaceNode]:
    result = await db.execute(
        select(WorkspaceNode).where(
            WorkspaceNode.id == node_id,
            WorkspaceNode.entity_id == entity_id,
            WorkspaceNode.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


# Re-import at bottom to avoid circular
from app.services.preset_registry import load_file_lookup_preprocess_instruction
