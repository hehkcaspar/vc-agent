"""Workspace agent tools — 13 tools for the unified workspace file system."""

from __future__ import annotations

import json
import mimetypes
from typing import Any, Callable, Optional

from app.database import SyncSessionLocal
from app.services.workspace import (
    Actor,
    ConflictError,
    NodeNotFoundError,
    ProtectedFileError,
    ValidationError,
    WorkspaceError,
    WorkspaceService,
    _parse_metadata,
)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _notify(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if on_status:
        try:
            on_status(msg)
        except Exception:
            pass


def build_workspace_tools(
    entity_id: str,
    session_id: str,
    run_id: Optional[str],
    workspace_service: WorkspaceService,
    on_status: Optional[Callable[[str], None]] = None,
) -> list:
    from langchain_core.tools import tool

    actor = Actor(type="agent", ref=run_id)

    @tool
    def workspace_get_tree(path: str = "", max_depth: int = 3) -> str:
        """Browse the workspace tree structure. Returns folders and files with descriptions.
        - path: subtree root (empty = entire workspace)
        - max_depth: how deep to recurse (default 3)"""
        _notify(on_status, "Browsing workspace tree...")
        with SyncSessionLocal() as db:
            tree = workspace_service.get_tree_sync(db, entity_id, path, max_depth)
        return _json({"ok": True, "tree": tree})

    @tool
    def workspace_list_files(path: str = "") -> str:
        """List files and folders at a specific path (single level).
        - path: folder path (empty = workspace root)"""
        _notify(on_status, f"Listing {path or 'root'}...")
        with SyncSessionLocal() as db:
            nodes = workspace_service.list_children_sync(db, entity_id, path)
            items = [
                {
                    "id": n.id, "name": n.name, "node_type": n.node_type,
                    "path": n.path, "size_bytes": n.size_bytes,
                    "mime_type": n.mime_type,
                    "description": _parse_metadata(n.metadata_json).get("description"),
                }
                for n in nodes
            ]
        return _json({"ok": True, "items": items})

    @tool
    def workspace_read_file(path: str) -> str:
        """Read the text content of a file. Returns content and checksum (for CAS writes).
        - path: file path in workspace"""
        _notify(on_status, f"Reading {path}...")
        with SyncSessionLocal() as db:
            node = workspace_service.get_node_by_path_sync(db, entity_id, path)
            if not node:
                return _json({"ok": False, "error": f"No file at '{path}'"})
            if node.node_type == "bookmark":
                return _json({"ok": True, "type": "bookmark", "url": node.url, "name": node.name})
            if node.node_type == "folder":
                return _json({"ok": False, "error": f"'{path}' is a folder, not a file"})
            if not node.storage_key:
                return _json({"ok": False, "error": f"File '{path}' has no storage key"})

            from app.services.storage import storage
            try:
                raw = storage.read_file_sync(node.storage_key)
            except Exception as e:
                return _json({"ok": False, "error": f"Read failed: {e}"})

            mime = node.mime_type or ""

            # PDF extraction
            if mime == "application/pdf":
                try:
                    from app.services.document_text import extract_pdf_text
                    text = extract_pdf_text(raw, max_chars=120_000)
                    return _json({
                        "ok": True, "path": path, "checksum": node.checksum,
                        "mime_type": mime, "content": text, "extracted": True,
                    })
                except Exception as e:
                    return _json({"ok": False, "error": f"PDF extraction failed: {e}"})

            # Office extraction
            from app.services.deep_agent_office_extractors import extract_office_text
            office = extract_office_text(raw, mime_type=mime, max_chars=120_000)
            if office is not None:
                return _json({
                    "ok": True, "path": path, "checksum": node.checksum,
                    "mime_type": mime, "content": office, "extracted": True,
                })

            # Text-based
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="replace")

            if len(text) > 120_000:
                text = text[:120_000] + "\n\n...(truncated)"

            return _json({
                "ok": True, "path": path, "checksum": node.checksum,
                "mime_type": mime, "content": text, "size_bytes": len(raw),
            })

    @tool
    def workspace_search_files(query: str, folder: str = "") -> str:
        """Search for files by name and path.
        - query: search terms (space-separated, all must match)
        - folder: restrict to this subtree (e.g., 'Deliverables/Memos/')"""
        _notify(on_status, f"Searching '{query}'...")
        with SyncSessionLocal() as db:
            results = workspace_service.search_files_sync(db, entity_id, query, folder)
            items = [
                {"id": n.id, "name": n.name, "path": n.path, "node_type": n.node_type}
                for n in results[:30]
            ]
        return _json({"ok": True, "results": items, "count": len(results)})

    @tool
    def workspace_create_folder(path: str) -> str:
        """Create a folder (and any missing parent folders).
        - path: folder path to create"""
        _notify(on_status, f"Creating folder {path}...")
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.create_folder_sync(db, entity_id, path, actor)
                db.commit()
                return _json({"ok": True, "id": node.id, "path": node.path})
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_move(from_path: str, to_path: str) -> str:
        """Move a file or folder to a new location. This is always safe — it reorganizes without changing content.
        - from_path: current path
        - to_path: destination path"""
        _notify(on_status, f"Moving {from_path} -> {to_path}...")
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.move_sync(db, entity_id, from_path, to_path, actor)
                db.commit()
                return _json({"ok": True, "id": node.id, "path": node.path})
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_rename(path: str, new_name: str) -> str:
        """Rename a file or folder in place.
        - path: current path
        - new_name: new name (not a full path)"""
        _notify(on_status, f"Renaming {path} -> {new_name}...")
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.rename_sync(db, entity_id, path, new_name, actor)
                db.commit()
                return _json({"ok": True, "id": node.id, "path": node.path})
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_write_file(
        path: str,
        content: str,
        expected_checksum: str = "",
        meta: str = "",
    ) -> str:
        """Write or overwrite a file. If the file exists, old content is automatically versioned.
        - path: file path (e.g., 'Deliverables/Memos/analysis.md')
        - content: file content as text
        - expected_checksum: optional CAS guard (from workspace_read_file)
        - meta: optional JSON metadata (e.g., '{"deliverable_type":"memo","status":"draft"}')

        Write zones:
        - You CAN freely create/edit files you created (in Deliverables/ or elsewhere)
        - You CANNOT overwrite user-uploaded files — create a derivative instead
        - You CAN move/rename any file"""
        _notify(on_status, f"Writing {path}...")
        metadata = None
        if meta:
            try:
                metadata = json.loads(meta)
            except json.JSONDecodeError:
                return _json({"ok": False, "error": "meta must be valid JSON"})
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.write_file_sync(
                    db, entity_id, path,
                    content.encode("utf-8"),
                    mimetypes.guess_type(path)[0],
                    actor,
                    expected_checksum=expected_checksum or None,
                    metadata=metadata,
                )
                db.commit()
                return _json({
                    "ok": True, "id": node.id, "path": node.path,
                    "version": node.version, "checksum": node.checksum,
                })
        except ProtectedFileError as e:
            return _json({"ok": False, "error": str(e), "error_type": "protected_file"})
        except ConflictError as e:
            return _json({"ok": False, "error": str(e), "error_type": "conflict"})
        except ValidationError as e:
            return _json({"ok": False, "error": str(e), "error_type": "validation"})
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_annotate(path: str, description: str) -> str:
        """Set a short description on a file or folder. Descriptions appear in the tree context.
        - path: file/folder path
        - description: one-line description"""
        _notify(on_status, f"Annotating {path}...")
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.annotate_sync(db, entity_id, path, description, actor)
                db.commit()
                return _json({"ok": True, "path": node.path})
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_delete(path: str) -> str:
        """Soft-delete a file or folder (recoverable from trash).
        - path: file/folder path
        Note: you cannot delete user-uploaded files."""
        _notify(on_status, f"Deleting {path}...")
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.delete_sync(db, entity_id, path, actor)
                db.commit()
                return _json({"ok": True, "path": path, "id": node.id})
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_file_versions(path: str) -> str:
        """List version history for a file.
        - path: file path"""
        _notify(on_status, f"Version history for {path}...")
        with SyncSessionLocal() as db:
            node = workspace_service.get_node_by_path_sync(db, entity_id, path)
            if not node:
                return _json({"ok": False, "error": f"No file at '{path}'"})
            versions = workspace_service.file_versions_sync(db, entity_id, node.id)
        return _json({"ok": True, "path": path, "versions": versions})

    @tool
    def workspace_restore_version(path: str, version: int) -> str:
        """Revert a file to a previous version. Current content is preserved as a new version.
        - path: file path
        - version: version number to restore"""
        _notify(on_status, f"Restoring {path} to v{version}...")
        try:
            with SyncSessionLocal() as db:
                node = workspace_service.get_node_by_path_sync(db, entity_id, path)
                if not node:
                    return _json({"ok": False, "error": f"No file at '{path}'"})
                restored = workspace_service.restore_version_sync(db, entity_id, node.id, version, actor)
                db.commit()
                return _json({
                    "ok": True, "path": restored.path,
                    "version": restored.version, "checksum": restored.checksum,
                })
        except WorkspaceError as e:
            return _json({"ok": False, "error": str(e)})

    @tool
    def workspace_history(limit: int = 20) -> str:
        """View recent workspace operations (create, edit, move, delete, etc.).
        - limit: max entries to return"""
        _notify(on_status, "Loading workspace history...")
        with SyncSessionLocal() as db:
            ops = workspace_service.history_sync(db, entity_id, min(limit, 100))
            items = [
                {
                    "op_type": op.op_type, "actor": op.actor_type,
                    "node_id": op.node_id,
                    "payload": json.loads(op.payload_json) if op.payload_json else None,
                    "created_at": op.created_at.isoformat() if op.created_at else None,
                }
                for op in ops
            ]
        return _json({"ok": True, "ops": items})

    return [
        workspace_get_tree,
        workspace_list_files,
        workspace_read_file,
        workspace_search_files,
        workspace_create_folder,
        workspace_move,
        workspace_rename,
        workspace_write_file,
        workspace_annotate,
        workspace_delete,
        workspace_file_versions,
        workspace_restore_version,
        workspace_history,
    ]
