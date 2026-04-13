"""Workspace API — hierarchical file system per entity."""

import json
import mimetypes
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Entity, WorkspaceNode
from app.schemas import (
    WorkspaceAnnotateRequest,
    WorkspaceCopyRequest,
    WorkspaceMoveRequest,
    WorkspaceNodeResponse,
    WorkspaceOpResponse,
    WorkspaceRenameRequest,
    WorkspaceTreeNode,
    MetadataPreprocessAccepted,
    MetadataPreprocessJobStatus,
    InboxProcessAccepted,
    InboxProcessJobStatus,
)
from app.config import settings
from app.services.storage import storage
from app.services.workspace import (
    Actor,
    ConflictError,
    NodeNotFoundError,
    ProtectedFileError,
    ValidationError,
    WorkspaceError,
    workspace_service,
)

router = APIRouter(prefix="/entities/{entity_id}/workspace", tags=["workspace"])


async def _require_entity(db: AsyncSession, entity_id: str) -> Entity:
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


def _handle_ws_error(e: WorkspaceError):
    if isinstance(e, NodeNotFoundError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ProtectedFileError):
        raise HTTPException(status_code=403, detail=str(e))
    if isinstance(e, ConflictError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, ValidationError):
        raise HTTPException(status_code=422, detail=str(e))
    raise HTTPException(status_code=400, detail=str(e))


# ── Tree ──────────────────────────────────────────────────────────────

@router.get("/tree")
async def get_tree(
    entity_id: str,
    path: str = "",
    depth: int = Query(default=10, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    nodes = await workspace_service.get_all_nodes(db, entity_id)
    if path:
        prefix = path.rstrip("/") + "/"
        nodes = [n for n in nodes if n.path == path or n.path.startswith(prefix)]

    from app.services.workspace import _parse_metadata
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for n in nodes:
        meta = _parse_metadata(n.metadata_json)
        entry = {
            "id": n.id, "name": n.name, "node_type": n.node_type, "path": n.path,
            "size_bytes": n.size_bytes, "mime_type": n.mime_type,
            "description": meta.get("description"),
            "version": n.version if n.node_type == "file" else None,
            "children": [],
        }
        by_id[n.id] = entry
    for n in nodes:
        entry = by_id[n.id]
        if n.parent_id and n.parent_id in by_id:
            by_id[n.parent_id]["children"].append(entry)
        else:
            roots.append(entry)
    return roots


@router.get("/ls", response_model=List[WorkspaceNodeResponse])
async def list_dir(
    entity_id: str,
    path: str = "",
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    return await workspace_service.list_children(db, entity_id, path)


@router.get("/node/{node_id}", response_model=WorkspaceNodeResponse)
async def get_node(
    entity_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@router.get("/search", response_model=List[WorkspaceNodeResponse])
async def search(
    entity_id: str,
    q: str = "",
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    if not q.strip():
        return []
    return await workspace_service.search_files(db, entity_id, q)


# ── Files ─────────────────────────────────────────────────────────────

@router.get("/file/{node_id}")
async def download_file(
    entity_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    if node.node_type == "bookmark":
        return {"url": node.url, "type": "bookmark"}

    if node.node_type == "folder":
        raise HTTPException(status_code=400, detail="Cannot download a folder")

    if not node.storage_key:
        raise HTTPException(status_code=404, detail="File has no storage key")

    full_path = storage.get_full_path(node.storage_key)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    content_type = node.mime_type or mimetypes.guess_type(node.name)[0] or "application/octet-stream"
    return FileResponse(path=str(full_path), media_type=content_type, filename=node.name)


@router.get("/file")
async def download_file_by_path(
    entity_id: str,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_path(db, entity_id, path)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.node_type != "file" or not node.storage_key:
        raise HTTPException(status_code=400, detail="Not a downloadable file")

    full_path = storage.get_full_path(node.storage_key)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    content_type = node.mime_type or mimetypes.guess_type(node.name)[0] or "application/octet-stream"
    return FileResponse(path=str(full_path), media_type=content_type, filename=node.name)


@router.post("/file", response_model=WorkspaceNodeResponse)
async def upload_file(
    entity_id: str,
    path: str = Query(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    content = await file.read()
    mime = file.content_type or mimetypes.guess_type(file.filename or path)[0]
    actor = Actor(type="user")
    try:
        node = await workspace_service.write_file(db, entity_id, path, content, mime, actor)
        node.origin_type = "upload"
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


@router.post("/upload")
async def upload_folder(
    entity_id: str,
    files: List[UploadFile] = File(...),
    base_path: str = Query(default="Inbox"),
    db: AsyncSession = Depends(get_db),
):
    """Upload multiple files, preserving relative paths from the filename field."""
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    created = []
    for f in files:
        # Browser folder uploads put relative path in filename
        rel = f.filename or "untitled"
        full_path = f"{base_path}/{rel}" if base_path else rel
        content = await f.read()
        mime = f.content_type or mimetypes.guess_type(rel)[0]
        try:
            node = await workspace_service.write_file(db, entity_id, full_path, content, mime, actor)
            node.origin_type = "upload"
            created.append({"id": node.id, "path": node.path, "size": len(content)})
        except WorkspaceError as e:
            created.append({"path": full_path, "error": str(e)})
    await db.commit()
    return {"uploaded": len([c for c in created if "id" in c]), "results": created}


@router.post("/folder", response_model=WorkspaceNodeResponse)
async def create_folder(
    entity_id: str,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.create_folder(db, entity_id, path, actor)
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


# ── Versioning ────────────────────────────────────────────────────────

@router.get("/file/{node_id}/versions")
async def file_versions(
    entity_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    manifest_key = f"{entity_id}/workspace/.versions/{node_id}/manifest.json"
    try:
        data = await storage.read_file(manifest_key)
        manifest = json.loads(data)
    except Exception:
        manifest = []

    manifest.append({
        "version": node.version,
        "timestamp": node.updated_at.isoformat() if node.updated_at else None,
        "checksum": node.checksum,
        "size": node.size_bytes,
        "current": True,
    })
    return {"versions": manifest}


@router.post("/file/{node_id}/restore/{version}", response_model=WorkspaceNodeResponse)
async def restore_version(
    entity_id: str,
    node_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    # Read manifest
    manifest_key = f"{entity_id}/workspace/.versions/{node_id}/manifest.json"
    try:
        data = await storage.read_file(manifest_key)
        manifest = json.loads(data)
    except Exception:
        raise HTTPException(status_code=404, detail="No version history")

    target = next((v for v in manifest if v["version"] == version), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Snapshot current, then restore old
    old_content = await storage.read_file(target["key"])
    new_node = await workspace_service.write_file(
        db, entity_id, node.path, old_content, node.mime_type, actor
    )
    await db.commit()
    await db.refresh(new_node)
    return new_node


@router.get("/file/{node_id}/versions/{version}")
async def download_version(
    entity_id: str,
    node_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
):
    """Download a specific old version of a file."""
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # If requesting current version, serve the live file
    if version == node.version:
        if not node.storage_key:
            raise HTTPException(status_code=404, detail="File has no storage key")
        full_path = storage.get_full_path(node.storage_key)
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")
        content_type = node.mime_type or mimetypes.guess_type(node.name)[0] or "application/octet-stream"
        return FileResponse(path=str(full_path), media_type=content_type, filename=node.name)

    # Look up old version in manifest
    manifest_key = f"{entity_id}/workspace/.versions/{node_id}/manifest.json"
    try:
        data = await storage.read_file(manifest_key)
        manifest = json.loads(data)
    except Exception:
        raise HTTPException(status_code=404, detail="No version history")

    target = next((v for v in manifest if v["version"] == version), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    full_path = storage.get_full_path(target["key"])
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Version file not found on disk")

    content_type = node.mime_type or mimetypes.guess_type(node.name)[0] or "application/octet-stream"
    return FileResponse(
        path=str(full_path), media_type=content_type,
        filename=f"v{version}_{node.name}",
    )


@router.get("/file/{node_id}/diff")
async def version_diff(
    entity_id: str,
    node_id: str,
    v1: int = Query(...),
    v2: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Text diff between two versions of a file."""
    import difflib

    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    manifest_key = f"{entity_id}/workspace/.versions/{node_id}/manifest.json"
    try:
        data = await storage.read_file(manifest_key)
        manifest = json.loads(data)
    except Exception:
        manifest = []

    async def _read_version(ver: int) -> str:
        if ver == node.version:
            if not node.storage_key:
                raise HTTPException(status_code=404, detail="Current file has no storage key")
            raw = await storage.read_file(node.storage_key)
        else:
            target = next((v for v in manifest if v["version"] == ver), None)
            if not target:
                raise HTTPException(status_code=404, detail=f"Version {ver} not found")
            raw = await storage.read_file(target["key"])
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail=f"Version {ver} is not text — cannot diff binary files")

    text1 = await _read_version(v1)
    text2 = await _read_version(v2)

    diff_lines = list(difflib.unified_diff(
        text1.splitlines(keepends=True),
        text2.splitlines(keepends=True),
        fromfile=f"v{v1}/{node.name}",
        tofile=f"v{v2}/{node.name}",
    ))

    return {
        "node_id": node_id,
        "v1": v1,
        "v2": v2,
        "diff": "".join(diff_lines),
        "has_changes": len(diff_lines) > 0,
    }


# ── Mutations ─────────────────────────────────────────────────────────

@router.post("/move", response_model=WorkspaceNodeResponse)
async def move_node(
    entity_id: str,
    body: WorkspaceMoveRequest,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.move(db, entity_id, body.from_path, body.to_path, actor)
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


@router.post("/rename", response_model=WorkspaceNodeResponse)
async def rename_node(
    entity_id: str,
    body: WorkspaceRenameRequest,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.rename(db, entity_id, body.path, body.new_name, actor)
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


@router.delete("/node")
async def delete_node(
    entity_id: str,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.delete_node(db, entity_id, path, actor)
        await db.commit()
        return {"message": f"Deleted '{path}'", "node_id": node.id}
    except WorkspaceError as e:
        _handle_ws_error(e)


@router.post("/copy", response_model=WorkspaceNodeResponse)
async def copy_node(
    entity_id: str,
    body: WorkspaceCopyRequest,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.copy(db, entity_id, body.from_path, body.to_path, actor)
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


# ── Trash ─────────────────────────────────────────────────────────────

@router.get("/trash", response_model=List[WorkspaceNodeResponse])
async def list_trash(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    return await workspace_service.list_trash(db, entity_id)


@router.post("/trash/{node_id}/restore", response_model=WorkspaceNodeResponse)
async def restore_from_trash(
    entity_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.restore_from_trash(db, entity_id, node_id, actor)
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


@router.delete("/trash/{node_id}")
async def hard_delete_from_trash(
    entity_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a trashed node and its storage."""
    await _require_entity(db, entity_id)
    try:
        await workspace_service.hard_delete(db, entity_id, node_id)
        await db.commit()
        return {"message": "Permanently deleted", "node_id": node_id}
    except WorkspaceError as e:
        _handle_ws_error(e)


# ── History ───────────────────────────────────────────────────────────

@router.get("/ops")
async def list_ops(
    entity_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    ops = await workspace_service.history(db, entity_id, limit)
    return [
        {
            "id": op.id,
            "op_type": op.op_type,
            "actor_type": op.actor_type,
            "actor_ref": op.actor_ref,
            "node_id": op.node_id,
            "payload": json.loads(op.payload_json) if op.payload_json else None,
            "created_at": op.created_at.isoformat() if op.created_at else None,
            "undone_at": op.undone_at.isoformat() if op.undone_at else None,
        }
        for op in ops
    ]


@router.post("/ops/{op_id}/undo")
async def undo_op(
    entity_id: str,
    op_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        result = await workspace_service.undo(db, entity_id, op_id, actor)
        await db.commit()
        return result
    except WorkspaceError as e:
        _handle_ws_error(e)


# ── Metadata ──────────────────────────────────────────────────────────

@router.post("/annotate", response_model=WorkspaceNodeResponse)
async def annotate_node(
    entity_id: str,
    body: WorkspaceAnnotateRequest,
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    actor = Actor(type="user")
    try:
        node = await workspace_service.annotate(db, entity_id, body.path, body.description, actor)
        await db.commit()
        await db.refresh(node)
        return node
    except WorkspaceError as e:
        _handle_ws_error(e)


@router.patch("/node/{node_id}", response_model=WorkspaceNodeResponse)
async def update_node_metadata(
    entity_id: str,
    node_id: str,
    payload: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    if "name" in payload:
        name = str(payload["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        actor = Actor(type="user")
        try:
            await workspace_service.rename(db, entity_id, node.path, name, actor)
        except WorkspaceError as e:
            _handle_ws_error(e)

    if "metadata" in payload:
        from app.services.workspace import _parse_metadata, _dump_metadata
        meta = payload["metadata"]
        if meta is None:
            node.metadata_json = None
        elif isinstance(meta, dict):
            existing = _parse_metadata(node.metadata_json)
            existing.update(meta)
            node.metadata_json = _dump_metadata(existing)
        else:
            raise HTTPException(status_code=400, detail="metadata must be a dict or null")

    from app.datetime_support import utc_now
    node.updated_at = utc_now()
    await db.commit()
    await db.refresh(node)
    return node


@router.post(
    "/node/{node_id}/metadata-preprocess",
    response_model=MetadataPreprocessAccepted,
)
async def start_metadata_preprocess(
    entity_id: str,
    node_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Enqueue Gemini metadata extraction for a workspace node."""
    await _require_entity(db, entity_id)
    node = await workspace_service.get_node_by_id(db, entity_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.node_type != "file":
        raise HTTPException(status_code=400, detail="Metadata extraction only works on files")

    from app.services.metadata_preprocess_jobs import create_or_reuse_job, run_metadata_preprocess_job
    job_id, schedule = await create_or_reuse_job(entity_id, node_id)
    if schedule:
        background_tasks.add_task(run_metadata_preprocess_job, job_id)
    return MetadataPreprocessAccepted(job_id=job_id)


@router.get(
    "/metadata-preprocess-jobs/{job_id}",
    response_model=MetadataPreprocessJobStatus,
)
async def get_metadata_preprocess_job(
    entity_id: str,
    job_id: str,
):
    from app.services.metadata_preprocess_jobs import get_job_status
    row = await get_job_status(entity_id, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return MetadataPreprocessJobStatus(
        job_id=row["job_id"],
        status=row["status"],
        error_message=row.get("error_message"),
    )


@router.get("/extraction-progress")
async def get_extraction_progress(entity_id: str):
    """Background metadata extraction progress for this entity."""
    from app.services.metadata_preprocess_jobs import get_extraction_progress as _get
    progress = await _get(entity_id)
    if progress is None:
        return {"status": "idle"}
    return progress


# ──────────────────────────────────────────────────────────────────────
# Process Inbox (batch intake)
# ──────────────────────────────────────────────────────────────────────

@router.post("/inbox/process", response_model=InboxProcessAccepted, status_code=202)
async def start_inbox_process(
    entity_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Schedule a Process Inbox job for the entity. One job per entity at a time."""
    await _require_entity(db, entity_id)
    from app.services.inbox_processing_jobs import create_inbox_job, run_inbox_job
    job_id, scheduled = await create_inbox_job(entity_id)
    if scheduled:
        background_tasks.add_task(run_inbox_job, job_id)
    return InboxProcessAccepted(job_id=job_id)


@router.get("/inbox/process/{job_id}", response_model=InboxProcessJobStatus)
async def get_inbox_process_job(entity_id: str, job_id: str):
    from app.services.inbox_processing_jobs import get_inbox_job_status
    row = await get_inbox_job_status(entity_id, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return InboxProcessJobStatus(**row)


# ──────────────────────────────────────────────────────────────────────
# Zip upload
# ──────────────────────────────────────────────────────────────────────

@router.post("/upload-zip")
async def upload_zip(
    entity_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a zip; unpack into Inbox/<zip-basename>/<tree>.

    Guards: WORKSPACE_MAX_ZIP_BYTES on total zip; WORKSPACE_MAX_FILE_BYTES per
    entry; rejects zip-slip (entries whose resolved path escapes the base dir).
    """
    import io
    import os
    import zipfile
    from pathlib import PurePosixPath

    await _require_entity(db, entity_id)
    raw = await file.read()
    if len(raw) > settings.WORKSPACE_MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Zip exceeds {settings.WORKSPACE_MAX_ZIP_BYTES} bytes",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"Invalid zip: {e}")

    # Pre-pass: validate every entry path and gather normalized names.
    entries: list[tuple[zipfile.ZipInfo, str]] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        normalized = os.path.normpath(info.filename).replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("..") or "/../" in normalized:
            raise HTTPException(status_code=400, detail=f"Unsafe path in zip: {info.filename}")
        if info.file_size > settings.WORKSPACE_MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Zip entry exceeds per-file limit: {info.filename}",
            )
        entries.append((info, normalized))

    # Derive base name. If every entry shares a single root directory, use it
    # verbatim (no double-nesting). Otherwise wrap entries under the zip filename.
    fname = file.filename or "uploaded.zip"
    fallback_name = os.path.basename(fname)
    if fallback_name.lower().endswith(".zip"):
        fallback_name = fallback_name[:-4]
    fallback_name = fallback_name.strip() or "uploaded"

    common_root: Optional[str] = None
    if entries:
        first_root = entries[0][1].split("/", 1)[0] if "/" in entries[0][1] else None
        if first_root and all(
            "/" in n and n.split("/", 1)[0] == first_root for _, n in entries
        ):
            common_root = first_root

    if common_root:
        base_path = f"Inbox/{common_root}"
        strip_prefix = common_root + "/"
    else:
        base_path = f"Inbox/{fallback_name}"
        strip_prefix = ""

    actor = Actor(type="user")
    created = []
    for info, normalized in entries:
        rel = normalized[len(strip_prefix):] if strip_prefix else normalized
        if not rel:
            continue
        full_path = f"{base_path}/{rel}"
        try:
            entry_bytes = zf.read(info)
        except Exception as e:
            created.append({"path": full_path, "error": f"read: {e}"})
            continue
        mime = mimetypes.guess_type(normalized)[0]
        try:
            node = await workspace_service.write_file(
                db, entity_id, full_path, entry_bytes, mime, actor,
            )
            node.origin_type = "upload"
            created.append({"id": node.id, "path": node.path, "size": len(entry_bytes)})
        except WorkspaceError as e:
            created.append({"path": full_path, "error": str(e)})

    await db.commit()
    return {
        "uploaded": len([c for c in created if "id" in c]),
        "base_path": base_path,
        "results": created,
    }
