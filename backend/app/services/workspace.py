"""Hierarchical workspace file system per entity.

One unified tree replaces the old Resources + Artifacts dual model.
Physical storage uses path-independent blob keys; moves/renames are DB-only.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.datetime_support import utc_now, utc_now_iso
from app.models import WorkspaceNode, WorkspaceOp, generate_uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class Actor:
    type: str  # user | agent | system
    ref: Optional[str] = None


class WorkspaceError(Exception):
    pass


class NodeNotFoundError(WorkspaceError):
    pass


class ProtectedFileError(WorkspaceError):
    pass


class ConflictError(WorkspaceError):
    pass


class ValidationError(WorkspaceError):
    pass


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sanitize_name(name: str) -> str:
    """Strip dangerous chars from filenames for storage keys."""
    return name.replace("/", "_").replace("\\", "_").replace("\x00", "")


def _suggest_derivative_path(original_path: str) -> str:
    stem = PurePosixPath(original_path).stem
    return f"Deliverables/{stem}-analysis.md"


def _parse_metadata(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _dump_metadata(meta: dict) -> str:
    return json.dumps(meta, ensure_ascii=False, default=str)


# Pre-write validation hooks
def _validate_json_content(content: bytes) -> tuple[bool, str]:
    try:
        json.loads(content)
        return True, ""
    except (json.JSONDecodeError, ValueError) as e:
        return False, f"Invalid JSON: {e}"


WRITE_VALIDATORS: dict[str, Any] = {
    ".json": _validate_json_content,
}


# Routing taxonomy for intake (Path A Pass 2, Path B Step B1).
#
# NOT a scaffolding list — folders materialize lazily via _ensure_parents when
# files actually land in them. Only Inbox/ and WORKSPACE_NOTES.md exist on day 1.
#
# Each entry has a `description` and `examples` so the routing prompts can
# convey VC semantics to Gemini. The critical distinction:
#   - Data Room = INBOUND material from the portfolio company (their docs)
#   - Deliverables = OUTBOUND artifacts produced by the VC team about the company
# Without this, Gemini misroutes pitch decks etc. into Deliverables based on
# the English connotation of "deliverable".
WORKSPACE_TAXONOMY_ENTRIES: list[dict] = [
    {
        "path": "Data Room",
        "description": "Inbound source materials FROM the portfolio company. Use as the default parent for company-produced documents (pitch decks, business plans, product docs, market materials) when no more specific subfolder fits.",
        "examples": ["pitch deck", "business plan", "product overview", "market research from company", "intro materials"],
    },
    {
        "path": "Data Room/Financials",
        "description": "Financial source documents FROM the company: P&Ls, balance sheets, cap tables, financial models, projections, audit reports.",
        "examples": ["cap table", "P&L", "financial model", "projections", "audit report", "Q4 financials"],
    },
    {
        "path": "Data Room/Legal",
        "description": "Legal source documents FROM the company: term sheets, SAFEs, SPAs, board consents, IP assignments, employment agreements, charters, by-laws.",
        "examples": ["term sheet", "SPA", "SAFE", "board consent", "IP assignment", "incorporation docs"],
    },
    {
        "path": "Technical",
        "description": "Technical and engineering source materials FROM the company: architecture diagrams, codebases, technical specs, research papers authored by the team.",
        "examples": ["architecture doc", "tech spec", "engineering RFC", "research paper from team"],
    },
    {
        "path": "Deliverables",
        "description": "OUTBOUND artifacts CREATED BY the VC team about this company. NOT inbound materials. Use as the default parent for VC-produced outputs when no more specific subfolder fits.",
        "examples": ["VC team's working notes", "diligence questionnaire drafted by VC", "internal analysis"],
    },
    {
        "path": "Deliverables/Memos",
        "description": "Investment memos written BY the VC team: IC memos, recommendation memos, follow-on memos. NOT memos sent FROM the company.",
        "examples": ["IC memo", "investment recommendation", "follow-on memo"],
    },
    {
        "path": "Deliverables/Reports",
        "description": "Diligence and analysis reports written BY the VC team: red-team reports, market analyses, competitive landscape reports, technical diligence write-ups.",
        "examples": ["red-team report", "diligence report", "market analysis", "competitive landscape"],
    },
    {
        "path": "Deliverables/Factsheets",
        "description": "Standardized one-pagers / factsheets summarizing the company that the VC team has written.",
        "examples": ["one-pager", "factsheet", "company summary"],
    },
]

# Backwards-compatible flat list (used by validation guards in
# inbox_processing_jobs.py — `_is_under_taxonomy` only needs the path strings).
WORKSPACE_TAXONOMY: list[str] = [e["path"] for e in WORKSPACE_TAXONOMY_ENTRIES]


# Singleton instance — declared at module bottom after WorkspaceService is defined.
workspace_service: "WorkspaceService"  # forward declaration for type checkers


# ---------------------------------------------------------------------------
# WorkspaceService
# ---------------------------------------------------------------------------

class WorkspaceService:
    def __init__(self, storage):
        self.storage = storage

    # ── Tree queries ──────────────────────────────────────────────────

    def _alive_filter(self):
        return WorkspaceNode.deleted_at.is_(None)

    def get_node_by_path_sync(self, db: Session, entity_id: str, path: str) -> Optional[WorkspaceNode]:
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.path == path,
                self._alive_filter(),
            )
        )
        return result.scalars().first()

    def get_node_by_id_sync(self, db: Session, entity_id: str, node_id: str) -> Optional[WorkspaceNode]:
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id == node_id,
                self._alive_filter(),
            )
        )
        return result.scalars().first()

    def list_children_sync(self, db: Session, entity_id: str, path: str = "") -> list[WorkspaceNode]:
        if path:
            parent = self.get_node_by_path_sync(db, entity_id, path)
            if not parent:
                return []
            parent_id = parent.id
        else:
            parent_id = None
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.parent_id == parent_id if parent_id else WorkspaceNode.parent_id.is_(None),
                self._alive_filter(),
            ).order_by(WorkspaceNode.node_type.desc(), WorkspaceNode.name)  # folders first
        )
        return list(result.scalars().all())

    def get_all_nodes_sync(self, db: Session, entity_id: str) -> list[WorkspaceNode]:
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                self._alive_filter(),
            ).order_by(WorkspaceNode.path)
        )
        return list(result.scalars().all())

    def get_tree_sync(self, db: Session, entity_id: str, path: str = "", max_depth: int = 10) -> list[dict]:
        """Build a nested tree structure from flat node list."""
        nodes = self.get_all_nodes_sync(db, entity_id)
        if path:
            prefix = path.rstrip("/") + "/"
            nodes = [n for n in nodes if n.path == path or n.path.startswith(prefix)]

        by_id: dict[str, dict] = {}
        roots: list[dict] = []

        for n in nodes:
            meta = _parse_metadata(n.metadata_json)
            entry = {
                "id": n.id,
                "name": n.name,
                "node_type": n.node_type,
                "path": n.path,
                "size_bytes": n.size_bytes,
                "mime_type": n.mime_type,
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

        def _trim_depth(tree: list[dict], depth: int):
            if depth <= 0:
                for item in tree:
                    item["children"] = []
                return
            for item in tree:
                _trim_depth(item["children"], depth - 1)

        _trim_depth(roots, max_depth)
        return roots

    def search_files_sync(self, db: Session, entity_id: str, query: str, folder: str = "") -> list[WorkspaceNode]:
        """Search by filename pattern matching."""
        all_nodes = self.get_all_nodes_sync(db, entity_id)
        query_lower = query.lower()
        terms = query_lower.split()
        results = []
        for n in all_nodes:
            if folder and not n.path.startswith(folder):
                continue
            name_lower = n.name.lower()
            path_lower = n.path.lower()
            if all(t in name_lower or t in path_lower for t in terms):
                results.append(n)
        return results

    # ── Mutations ─────────────────────────────────────────────────────

    def _ensure_parents_sync(self, db: Session, entity_id: str, path: str, actor: Actor) -> Optional[str]:
        """Create intermediate folder nodes. Returns parent_id for the given path."""
        parts = PurePosixPath(path).parts
        if len(parts) <= 1:
            return None  # root-level item, no parent

        parent_id = None
        for i in range(len(parts) - 1):
            folder_path = "/".join(parts[: i + 1])
            folder_name = parts[i]
            existing = self.get_node_by_path_sync(db, entity_id, folder_path)
            if existing:
                parent_id = existing.id
            else:
                folder = WorkspaceNode(
                    id=generate_uuid(),
                    entity_id=entity_id,
                    node_type="folder",
                    name=folder_name,
                    path=folder_path,
                    parent_id=parent_id,
                    origin_type=actor.type,
                    origin_ref=actor.ref,
                )
                db.add(folder)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    existing = self.get_node_by_path_sync(db, entity_id, folder_path)
                    if existing:
                        parent_id = existing.id
                        continue
                    raise
                self._log_op(db, entity_id, "create_folder", actor, folder.id,
                             payload={"path": folder_path})
                parent_id = folder.id
        return parent_id

    def _check_provenance(self, node: WorkspaceNode, actor: Actor, operation: str):
        """Raise ProtectedFileError if agent tries to mutate user-uploaded content."""
        if actor.type != "agent":
            return
        protected_origins = {"upload", "ingest"}
        if operation in ("overwrite", "delete"):
            if node.origin_type in protected_origins:
                raise ProtectedFileError(
                    f"Cannot {operation} user-uploaded file '{node.path}'. "
                    f"Create a derivative (e.g., '{_suggest_derivative_path(node.path)}') "
                    f"or ask the user for explicit permission."
                )
        # shared files (WORKSPACE_NOTES.md) are writable by agents

    def _log_op(self, db: Session, entity_id: str, op_type: str, actor: Actor,
                node_id: Optional[str], *, payload: dict,
                inverse: Optional[dict] = None,
                before_checksum: Optional[str] = None,
                after_checksum: Optional[str] = None,
                batch_id: Optional[str] = None):
        op = WorkspaceOp(
            id=generate_uuid(),
            entity_id=entity_id,
            batch_id=batch_id,
            op_type=op_type,
            actor_type=actor.type,
            actor_ref=actor.ref,
            node_id=node_id,
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
            inverse_json=json.dumps(inverse, ensure_ascii=False, default=str) if inverse else None,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )
        db.add(op)

    def write_file_sync(self, db: Session, entity_id: str, path: str,
                        content: bytes, mime_type: Optional[str], actor: Actor,
                        expected_checksum: Optional[str] = None,
                        metadata: Optional[dict] = None,
                        origin_type: Optional[str] = None) -> WorkspaceNode:
        """Create or overwrite a file. Auto-versions on overwrite."""
        # 1. Pre-write validation
        suffix = PurePosixPath(path).suffix.lower()
        validator = WRITE_VALIDATORS.get(suffix)
        if validator:
            ok, err = validator(content)
            if not ok:
                raise ValidationError(err)

        node = self.get_node_by_path_sync(db, entity_id, path)
        new_checksum = _sha256(content)

        if node:
            # OVERWRITE existing file
            if node.node_type != "file":
                raise WorkspaceError(f"Cannot write to non-file node '{path}' (type={node.node_type})")

            # 2. Provenance check
            self._check_provenance(node, actor, "overwrite")

            # 3. CAS lock
            if expected_checksum and node.checksum != expected_checksum:
                raise ConflictError(
                    f"File changed since you read it. "
                    f"Expected {expected_checksum[:8]}..., current is {node.checksum[:8]}..."
                )

            # 4. Snapshot old version
            old_checksum = node.checksum
            self._snapshot_version_sync(entity_id, node)

            # 5. Write new content
            self.storage.write_file_sync(node.storage_key, content)
            node.version += 1
            node.checksum = new_checksum
            node.size_bytes = len(content)
            node.updated_at = utc_now()
            if mime_type:
                node.mime_type = mime_type
            if metadata:
                existing_meta = _parse_metadata(node.metadata_json)
                existing_meta.update(metadata)
                node.metadata_json = _dump_metadata(existing_meta)

            self._log_op(db, entity_id, "overwrite", actor, node.id,
                         payload={"path": path, "version": node.version},
                         before_checksum=old_checksum, after_checksum=new_checksum)
        else:
            # CREATE new file
            name = PurePosixPath(path).name
            parent_id = self._ensure_parents_sync(db, entity_id, path, actor)
            node_id = generate_uuid()
            storage_key = f"{entity_id}/workspace/blobs/{node_id}/{_sanitize_name(name)}"

            self.storage.write_file_sync(storage_key, content)

            if not mime_type:
                mime_type = mimetypes.guess_type(name)[0]

            meta = metadata or {}
            node = WorkspaceNode(
                id=node_id,
                entity_id=entity_id,
                node_type="file",
                name=name,
                path=path,
                parent_id=parent_id,
                mime_type=mime_type,
                size_bytes=len(content),
                checksum=new_checksum,
                storage_key=storage_key,
                version=1,
                origin_type=origin_type or actor.type,
                origin_ref=actor.ref,
                metadata_json=_dump_metadata(meta) if meta else None,
            )
            db.add(node)
            db.flush()

            self._log_op(db, entity_id, "create_file", actor, node.id,
                         payload={"path": path, "size": len(content)},
                         after_checksum=new_checksum)

        return node

    def create_folder_sync(self, db: Session, entity_id: str, path: str,
                           actor: Actor) -> WorkspaceNode:
        existing = self.get_node_by_path_sync(db, entity_id, path)
        if existing:
            if existing.node_type == "folder":
                return existing  # idempotent
            raise WorkspaceError(f"Path '{path}' already exists as {existing.node_type}")

        name = PurePosixPath(path).name
        parent_id = self._ensure_parents_sync(db, entity_id, path, actor)

        folder = WorkspaceNode(
            id=generate_uuid(),
            entity_id=entity_id,
            node_type="folder",
            name=name,
            path=path,
            parent_id=parent_id,
            origin_type=actor.type,
            origin_ref=actor.ref,
        )
        db.add(folder)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = self.get_node_by_path_sync(db, entity_id, path)
            if existing and existing.node_type == "folder":
                return existing
            raise
        self._log_op(db, entity_id, "create_folder", actor, folder.id,
                     payload={"path": path})
        return folder

    def create_bookmark_sync(self, db: Session, entity_id: str, path: str,
                             url: str, actor: Actor,
                             metadata: Optional[dict] = None) -> WorkspaceNode:
        name = PurePosixPath(path).name
        parent_id = self._ensure_parents_sync(db, entity_id, path, actor)

        meta = metadata or {}
        node = WorkspaceNode(
            id=generate_uuid(),
            entity_id=entity_id,
            node_type="bookmark",
            name=name,
            path=path,
            parent_id=parent_id,
            url=url,
            origin_type=actor.type,
            origin_ref=actor.ref,
            metadata_json=_dump_metadata(meta) if meta else None,
        )
        db.add(node)
        db.flush()
        self._log_op(db, entity_id, "create_file", actor, node.id,
                     payload={"path": path, "url": url})
        return node

    def move_sync(self, db: Session, entity_id: str, from_path: str,
                  to_path: str, actor: Actor, batch_id: Optional[str] = None) -> WorkspaceNode:
        node = self.get_node_by_path_sync(db, entity_id, from_path)
        if not node:
            raise NodeNotFoundError(f"No node at '{from_path}'")

        # Check destination doesn't exist
        existing = self.get_node_by_path_sync(db, entity_id, to_path)
        if existing:
            raise WorkspaceError(f"Destination '{to_path}' already exists")

        old_path = node.path
        new_name = PurePosixPath(to_path).name
        parent_id = self._ensure_parents_sync(db, entity_id, to_path, actor)

        # Update this node
        node.path = to_path
        node.name = new_name
        node.parent_id = parent_id
        node.updated_at = utc_now()

        # Cascade: update all descendant paths
        if node.node_type == "folder":
            old_prefix = old_path + "/"
            new_prefix = to_path + "/"
            descendants = db.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.entity_id == entity_id,
                    WorkspaceNode.path.startswith(old_prefix),
                    self._alive_filter(),
                )
            ).scalars().all()
            for desc in descendants:
                desc.path = new_prefix + desc.path[len(old_prefix):]
                desc.updated_at = utc_now()

        self._log_op(db, entity_id, "move", actor, node.id,
                     payload={"from": old_path, "to": to_path},
                     inverse={"from": to_path, "to": old_path},
                     batch_id=batch_id)
        db.flush()
        return node

    def rename_sync(self, db: Session, entity_id: str, path: str,
                    new_name: str, actor: Actor) -> WorkspaceNode:
        node = self.get_node_by_path_sync(db, entity_id, path)
        if not node:
            raise NodeNotFoundError(f"No node at '{path}'")

        parts = PurePosixPath(path).parts
        new_path = "/".join(parts[:-1] + (new_name,)) if len(parts) > 1 else new_name

        # Check new path doesn't conflict
        existing = self.get_node_by_path_sync(db, entity_id, new_path)
        if existing and existing.id != node.id:
            raise WorkspaceError(f"Path '{new_path}' already exists")

        old_path = node.path
        old_name = node.name
        node.name = new_name
        node.path = new_path
        node.updated_at = utc_now()

        # Cascade descendants
        if node.node_type == "folder":
            old_prefix = old_path + "/"
            new_prefix = new_path + "/"
            descendants = db.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.entity_id == entity_id,
                    WorkspaceNode.path.startswith(old_prefix),
                    self._alive_filter(),
                )
            ).scalars().all()
            for desc in descendants:
                desc.path = new_prefix + desc.path[len(old_prefix):]
                desc.updated_at = utc_now()

        self._log_op(db, entity_id, "rename", actor, node.id,
                     payload={"path": new_path, "old_name": old_name, "new_name": new_name},
                     inverse={"path": old_path, "old_name": new_name, "new_name": old_name})
        db.flush()
        return node

    def delete_sync(self, db: Session, entity_id: str, path: str,
                    actor: Actor) -> WorkspaceNode:
        """Soft delete a node (and descendants if folder)."""
        node = self.get_node_by_path_sync(db, entity_id, path)
        if not node:
            raise NodeNotFoundError(f"No node at '{path}'")

        self._check_provenance(node, actor, "delete")

        now = utc_now()
        node.deleted_at = now
        node.updated_at = now

        # Cascade soft delete to descendants
        if node.node_type == "folder":
            descendants = db.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.entity_id == entity_id,
                    WorkspaceNode.path.startswith(path + "/"),
                    self._alive_filter(),
                )
            ).scalars().all()
            for desc in descendants:
                desc.deleted_at = now
                desc.updated_at = now

        self._log_op(db, entity_id, "delete", actor, node.id,
                     payload={"path": path, "node_type": node.node_type})
        db.flush()
        return node

    def restore_sync(self, db: Session, entity_id: str, node_id: str,
                     actor: Actor) -> WorkspaceNode:
        """Restore a soft-deleted node."""
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id == node_id,
                WorkspaceNode.deleted_at.isnot(None),
            )
        )
        node = result.scalars().first()
        if not node:
            raise NodeNotFoundError(f"No deleted node with id '{node_id}'")

        # Check path isn't taken by another live node
        conflict = self.get_node_by_path_sync(db, entity_id, node.path)
        if conflict:
            raise WorkspaceError(f"Cannot restore: path '{node.path}' is already occupied")

        node.deleted_at = None
        node.updated_at = utc_now()

        self._log_op(db, entity_id, "restore", actor, node.id,
                     payload={"path": node.path})
        db.flush()
        return node

    def annotate_sync(self, db: Session, entity_id: str, path: str,
                      description: str, actor: Actor) -> WorkspaceNode:
        node = self.get_node_by_path_sync(db, entity_id, path)
        if not node:
            raise NodeNotFoundError(f"No node at '{path}'")

        meta = _parse_metadata(node.metadata_json)
        meta["description"] = description
        node.metadata_json = _dump_metadata(meta)
        node.updated_at = utc_now()

        self._log_op(db, entity_id, "annotate", actor, node.id,
                     payload={"path": path, "description": description})
        db.flush()
        return node

    # ── Versioning ────────────────────────────────────────────────────

    def _snapshot_version_sync(self, entity_id: str, node: WorkspaceNode):
        """Save current content as a version snapshot before overwrite.

        Best-effort: if snapshot fails, the overwrite still proceeds (data
        loss of the old version is preferable to blocking the write entirely).
        """
        if not node.storage_key:
            return
        try:
            old_content = self.storage.read_file_sync(node.storage_key)
        except Exception:
            return  # can't snapshot what we can't read
        suffix = PurePosixPath(node.name).suffix
        version_key = (
            f"{entity_id}/workspace/.versions/{node.id}/"
            f"v{node.version}_{utc_now_iso().replace(':', '-')}{suffix}"
        )
        self.storage.write_file_sync(version_key, old_content)

        # Update manifest
        manifest_key = f"{entity_id}/workspace/.versions/{node.id}/manifest.json"
        try:
            manifest_data = self.storage.read_file_sync(manifest_key)
            manifest = json.loads(manifest_data)
        except Exception:
            manifest = []

        manifest.append({
            "version": node.version,
            "timestamp": utc_now_iso(),
            "checksum": node.checksum,
            "size": node.size_bytes,
            "key": version_key,
        })
        self.storage.write_file_sync(manifest_key, json.dumps(manifest, indent=2).encode())

    def file_versions_sync(self, db: Session, entity_id: str, node_id: str) -> list[dict]:
        node = self.get_node_by_id_sync(db, entity_id, node_id)
        if not node:
            raise NodeNotFoundError(f"No node with id '{node_id}'")

        manifest_key = f"{entity_id}/workspace/.versions/{node_id}/manifest.json"
        try:
            data = self.storage.read_file_sync(manifest_key)
            manifest = json.loads(data)
        except Exception:
            manifest = []

        # Add current version
        manifest.append({
            "version": node.version,
            "timestamp": node.updated_at.isoformat() if node.updated_at else None,
            "checksum": node.checksum,
            "size": node.size_bytes,
            "current": True,
        })
        return manifest

    def restore_version_sync(self, db: Session, entity_id: str, node_id: str,
                             version: int, actor: Actor) -> WorkspaceNode:
        node = self.get_node_by_id_sync(db, entity_id, node_id)
        if not node:
            raise NodeNotFoundError(f"No node with id '{node_id}'")
        if node.node_type != "file":
            raise WorkspaceError("Can only restore versions of files")

        manifest_key = f"{entity_id}/workspace/.versions/{node_id}/manifest.json"
        try:
            data = self.storage.read_file_sync(manifest_key)
            manifest = json.loads(data)
        except Exception:
            raise NodeNotFoundError(f"No version history for node '{node_id}'")

        target = next((v for v in manifest if v["version"] == version), None)
        if not target:
            raise NodeNotFoundError(f"Version {version} not found")

        # Snapshot current before restoring
        self._snapshot_version_sync(entity_id, node)

        # Read old version content and write as current
        old_content = self.storage.read_file_sync(target["key"])
        self.storage.write_file_sync(node.storage_key, old_content)

        old_checksum = node.checksum
        node.version += 1
        node.checksum = _sha256(old_content)
        node.size_bytes = len(old_content)
        node.updated_at = utc_now()

        self._log_op(db, entity_id, "restore_version", actor, node.id,
                     payload={"path": node.path, "restored_version": version, "new_version": node.version},
                     before_checksum=old_checksum, after_checksum=node.checksum)
        return node

    # ── History + trash ───────────────────────────────────────────────

    def history_sync(self, db: Session, entity_id: str, limit: int = 50) -> list[WorkspaceOp]:
        result = db.execute(
            select(WorkspaceOp).where(
                WorkspaceOp.entity_id == entity_id,
            ).order_by(WorkspaceOp.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    def list_trash_sync(self, db: Session, entity_id: str) -> list[WorkspaceNode]:
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.deleted_at.isnot(None),
            ).order_by(WorkspaceNode.deleted_at.desc())
        )
        return list(result.scalars().all())

    # ── Copy ──────────────────────────────────────────────────────────

    def copy_sync(self, db: Session, entity_id: str, from_path: str,
                  to_path: str, actor: Actor) -> WorkspaceNode:
        """Deep-copy a file or folder to a new path."""
        src = self.get_node_by_path_sync(db, entity_id, from_path)
        if not src:
            raise NodeNotFoundError(f"No node at '{from_path}'")
        existing = self.get_node_by_path_sync(db, entity_id, to_path)
        if existing:
            raise WorkspaceError(f"Destination '{to_path}' already exists")

        if src.node_type == "file":
            if not src.storage_key:
                raise WorkspaceError(f"Source file '{from_path}' has no storage key")
            content = self.storage.read_file_sync(src.storage_key)
            node = self.write_file_sync(
                db, entity_id, to_path, content, src.mime_type, actor,
                metadata=_parse_metadata(src.metadata_json) or None,
            )
            self._log_op(db, entity_id, "copy", actor, node.id,
                         payload={"from": from_path, "to": to_path})
            return node

        if src.node_type == "bookmark":
            node = self.create_bookmark_sync(
                db, entity_id, to_path, src.url or "", actor,
                metadata=_parse_metadata(src.metadata_json) or None,
            )
            return node

        # Folder: deep copy recursively
        new_folder = self.create_folder_sync(db, entity_id, to_path, actor)
        descendants = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.path.startswith(from_path + "/"),
                self._alive_filter(),
            ).order_by(WorkspaceNode.path)
        ).scalars().all()
        for desc in descendants:
            rel = desc.path[len(from_path):]  # e.g. "/sub/file.md"
            dest = to_path + rel
            if desc.node_type == "folder":
                self.create_folder_sync(db, entity_id, dest, actor)
            elif desc.node_type == "bookmark":
                self.create_bookmark_sync(
                    db, entity_id, dest, desc.url or "", actor,
                    metadata=_parse_metadata(desc.metadata_json) or None,
                )
            elif desc.node_type == "file" and desc.storage_key:
                content = self.storage.read_file_sync(desc.storage_key)
                self.write_file_sync(
                    db, entity_id, dest, content, desc.mime_type, actor,
                    metadata=_parse_metadata(desc.metadata_json) or None,
                )
        self._log_op(db, entity_id, "copy", actor, new_folder.id,
                     payload={"from": from_path, "to": to_path, "type": "folder"})
        return new_folder

    # ── Hard delete ───────────────────────────────────────────────────

    def hard_delete_sync(self, db: Session, entity_id: str, node_id: str) -> None:
        """Permanently delete a soft-deleted node and its storage."""
        result = db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id == node_id,
                WorkspaceNode.deleted_at.isnot(None),
            )
        )
        node = result.scalars().first()
        if not node:
            raise NodeNotFoundError(f"No deleted node with id '{node_id}'")

        # Delete storage blob
        if node.storage_key:
            try:
                self.storage.write_file_sync  # existence check
                import os
                full = self.storage.get_full_path(node.storage_key)
                if full.exists():
                    os.remove(full)
            except Exception:
                pass

        # Delete version history
        versions_prefix = f"{entity_id}/workspace/.versions/{node_id}"
        try:
            from pathlib import Path
            vdir = self.storage.get_full_path(versions_prefix)
            if vdir.exists():
                import shutil
                shutil.rmtree(vdir, ignore_errors=True)
        except Exception:
            pass

        db.delete(node)
        db.flush()

    # ── Undo ──────────────────────────────────────────────────────────

    def undo_sync(self, db: Session, entity_id: str, op_id: str,
                  actor: Actor) -> dict:
        """Undo a workspace operation using its inverse_json."""
        result = db.execute(
            select(WorkspaceOp).where(
                WorkspaceOp.entity_id == entity_id,
                WorkspaceOp.id == op_id,
                WorkspaceOp.undone_at.is_(None),
            )
        )
        op = result.scalars().first()
        if not op:
            raise NodeNotFoundError(f"No undoable op with id '{op_id}'")

        inverse = json.loads(op.inverse_json) if op.inverse_json else None
        op_type = op.op_type
        undone_detail = {"op_id": op_id, "op_type": op_type}

        if op_type == "move" and inverse:
            # inverse has {from, to} which reverses the move
            try:
                self.move_sync(db, entity_id, inverse["from"], inverse["to"], actor)
                undone_detail["action"] = f"moved back {inverse['from']} -> {inverse['to']}"
            except WorkspaceError as e:
                raise WorkspaceError(f"Cannot undo move: {e}")

        elif op_type == "rename" and inverse:
            try:
                self.rename_sync(db, entity_id, inverse["path"], inverse["new_name"], actor)
                undone_detail["action"] = f"renamed back to {inverse['new_name']}"
            except WorkspaceError as e:
                raise WorkspaceError(f"Cannot undo rename: {e}")

        elif op_type == "delete" and op.node_id:
            try:
                self.restore_sync(db, entity_id, op.node_id, actor)
                undone_detail["action"] = "restored from trash"
            except WorkspaceError as e:
                raise WorkspaceError(f"Cannot undo delete: {e}")

        elif op_type in ("create_file", "create_folder") and op.node_id:
            payload = json.loads(op.payload_json) if op.payload_json else {}
            path = payload.get("path", "")
            if path:
                try:
                    self.delete_sync(db, entity_id, path, actor)
                    undone_detail["action"] = f"deleted {path}"
                except WorkspaceError as e:
                    raise WorkspaceError(f"Cannot undo create: {e}")
            else:
                raise WorkspaceError("Cannot undo: missing path in op payload")

        elif op_type == "overwrite" and op.node_id and op.before_checksum:
            # Restore previous version via version history
            node = self.get_node_by_id_sync(db, entity_id, op.node_id)
            if node:
                payload = json.loads(op.payload_json) if op.payload_json else {}
                prev_version = payload.get("version", node.version) - 1
                if prev_version >= 1:
                    try:
                        self.restore_version_sync(db, entity_id, op.node_id, prev_version, actor)
                        undone_detail["action"] = f"restored to v{prev_version}"
                    except WorkspaceError as e:
                        raise WorkspaceError(f"Cannot undo overwrite: {e}")
                else:
                    raise WorkspaceError("Cannot undo overwrite: no previous version")
            else:
                raise WorkspaceError("Cannot undo overwrite: node not found")
        else:
            raise WorkspaceError(f"Undo not supported for op_type '{op_type}'")

        op.undone_at = utc_now()
        db.flush()
        return undone_detail

    # ── Template ──────────────────────────────────────────────────────

    def scaffold_workspace_sync(self, db: Session, entity_id: str) -> list[WorkspaceNode]:
        """Minimal scaffold: only Inbox/ and WORKSPACE_NOTES.md.

        Taxonomy folders (Data Room, Deliverables, etc.) materialize lazily via
        `_ensure_parents_sync` when files land in them during intake.
        """
        actor = Actor(type="system", ref="scaffold")
        created = [self.create_folder_sync(db, entity_id, "Inbox", actor)]

        notes_content = "# Workspace Notes\n\n_Add cross-file context here._\n"
        notes_node = self.write_file_sync(
            db, entity_id, "WORKSPACE_NOTES.md",
            notes_content.encode("utf-8"), "text/markdown",
            actor, metadata={"description": "Cross-file context and notes"},
            origin_type="shared",
        )
        created.append(notes_node)
        return created

    # ── Agent context builder ─────────────────────────────────────────

    def build_annotated_tree_text_sync(self, db: Session, entity_id: str) -> str:
        """Build three-layer context: tree + descriptions + workspace notes."""
        nodes = self.get_all_nodes_sync(db, entity_id)
        if not nodes:
            return "(empty workspace)"

        # Sort by path for consistent output
        nodes.sort(key=lambda n: n.path)

        # Build tree text
        lines = []
        for n in nodes:
            if n.name == "WORKSPACE_NOTES.md":
                continue  # rendered separately
            meta = _parse_metadata(n.metadata_json)
            desc = meta.get("description", "")
            indent = "  " * (n.path.count("/"))
            if n.node_type == "folder":
                suffix = "/"
                size_str = ""
            elif n.node_type == "bookmark":
                suffix = f" -> {n.url}" if n.url else ""
                size_str = ""
            else:
                suffix = ""
                if n.size_bytes and n.size_bytes > 1024 * 1024:
                    size_str = f"  ({n.size_bytes / (1024*1024):.1f}MB)"
                elif n.size_bytes and n.size_bytes > 1024:
                    size_str = f"  ({n.size_bytes / 1024:.0f}KB)"
                else:
                    size_str = ""
            desc_str = f" — {desc}" if desc else ""
            lines.append(f"{indent}{n.name}{suffix}{size_str}{desc_str}")

        tree_text = "\n".join(lines)

        # Read workspace notes
        notes_node = self.get_node_by_path_sync(db, entity_id, "WORKSPACE_NOTES.md")
        notes_text = ""
        if notes_node and notes_node.storage_key:
            try:
                raw = self.storage.read_file_sync(notes_node.storage_key)
                notes_text = raw.decode("utf-8", errors="replace").strip()
                # Strip the markdown header
                if notes_text.startswith("# Workspace Notes"):
                    notes_text = notes_text.split("\n", 1)[-1].strip()
            except Exception:
                pass

        # Compose
        file_count = sum(1 for n in nodes if n.node_type == "file")
        folder_count = sum(1 for n in nodes if n.node_type == "folder")
        header = f"=== Entity Workspace ({file_count} files, {folder_count} folders) ==="

        result = f"{header}\n\n{tree_text}"
        if notes_text and notes_text != "_Add cross-file context here._":
            result += f"\n\n--- Workspace Notes ---\n{notes_text}"
        return result

    async def build_annotated_tree_text(self, db, entity_id: str) -> str:
        """Async version of build_annotated_tree_text_sync."""
        nodes = await self.get_all_nodes(db, entity_id)
        if not nodes:
            return "(empty workspace)"

        nodes.sort(key=lambda n: n.path)
        lines = []
        for n in nodes:
            if n.name == "WORKSPACE_NOTES.md":
                continue
            meta = _parse_metadata(n.metadata_json)
            desc = meta.get("description", "")
            indent = "  " * (n.path.count("/"))
            if n.node_type == "folder":
                suffix = "/"
                size_str = ""
            elif n.node_type == "bookmark":
                suffix = f" -> {n.url}" if n.url else ""
                size_str = ""
            else:
                suffix = ""
                if n.size_bytes and n.size_bytes > 1024 * 1024:
                    size_str = f"  ({n.size_bytes / (1024*1024):.1f}MB)"
                elif n.size_bytes and n.size_bytes > 1024:
                    size_str = f"  ({n.size_bytes / 1024:.0f}KB)"
                else:
                    size_str = ""
            desc_str = f" — {desc}" if desc else ""
            lines.append(f"{indent}{n.name}{suffix}{size_str}{desc_str}")

        tree_text = "\n".join(lines)

        notes_node = await self.get_node_by_path(db, entity_id, "WORKSPACE_NOTES.md")
        notes_text = ""
        if notes_node and notes_node.storage_key:
            try:
                raw = await self.storage.read_file(notes_node.storage_key)
                notes_text = raw.decode("utf-8", errors="replace").strip()
                if notes_text.startswith("# Workspace Notes"):
                    notes_text = notes_text.split("\n", 1)[-1].strip()
            except Exception:
                pass

        file_count = sum(1 for n in nodes if n.node_type == "file")
        folder_count = sum(1 for n in nodes if n.node_type == "folder")
        header = f"=== Entity Workspace ({file_count} files, {folder_count} folders) ==="

        result = f"{header}\n\n{tree_text}"
        if notes_text and notes_text != "_Add cross-file context here._":
            result += f"\n\n--- Workspace Notes ---\n{notes_text}"
        return result

    # ── Async wrappers (for router endpoints) ─────────────────────────

    async def _ensure_parents(self, db, entity_id: str, path: str, actor: Actor) -> Optional[str]:
        """Async: create intermediate folder nodes. Returns parent_id."""
        parts = PurePosixPath(path).parts
        if len(parts) <= 1:
            return None
        parent_id = None
        for i in range(len(parts) - 1):
            folder_path = "/".join(parts[: i + 1])
            folder_name = parts[i]
            existing = await self.get_node_by_path(db, entity_id, folder_path)
            if existing:
                parent_id = existing.id
            else:
                folder = WorkspaceNode(
                    id=generate_uuid(), entity_id=entity_id,
                    node_type="folder", name=folder_name, path=folder_path,
                    parent_id=parent_id, origin_type=actor.type, origin_ref=actor.ref,
                )
                db.add(folder)
                try:
                    await db.flush()
                except IntegrityError:
                    await db.rollback()
                    existing = await self.get_node_by_path(db, entity_id, folder_path)
                    if existing:
                        parent_id = existing.id
                        continue
                    raise
                self._log_op(db, entity_id, "create_folder", actor, folder.id,
                             payload={"path": folder_path})
                parent_id = folder.id
        return parent_id

    async def get_node_by_path(self, db, entity_id: str, path: str):
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.path == path,
                self._alive_filter(),
            )
        )
        return result.scalars().first()

    async def get_node_by_id(self, db, entity_id: str, node_id: str):
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id == node_id,
                self._alive_filter(),
            )
        )
        return result.scalars().first()

    async def get_all_nodes(self, db, entity_id: str):
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                self._alive_filter(),
            ).order_by(WorkspaceNode.path)
        )
        return list(result.scalars().all())

    async def list_children(self, db, entity_id: str, path: str = ""):
        if path:
            parent = await self.get_node_by_path(db, entity_id, path)
            if not parent:
                return []
            parent_id = parent.id
        else:
            parent_id = None
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.parent_id == parent_id if parent_id else WorkspaceNode.parent_id.is_(None),
                self._alive_filter(),
            ).order_by(WorkspaceNode.node_type.desc(), WorkspaceNode.name)
        )
        return list(result.scalars().all())

    async def write_file(self, db, entity_id: str, path: str,
                         content: bytes, mime_type: Optional[str], actor: Actor,
                         expected_checksum: Optional[str] = None,
                         metadata: Optional[dict] = None,
                         origin_type: Optional[str] = None) -> WorkspaceNode:
        """Async write_file — delegates to storage async methods."""
        suffix = PurePosixPath(path).suffix.lower()
        validator = WRITE_VALIDATORS.get(suffix)
        if validator:
            ok, err = validator(content)
            if not ok:
                raise ValidationError(err)

        node = await self.get_node_by_path(db, entity_id, path)
        new_checksum = _sha256(content)

        if node:
            if node.node_type != "file":
                raise WorkspaceError(f"Cannot write to non-file node '{path}'")
            self._check_provenance(node, actor, "overwrite")
            if expected_checksum and node.checksum != expected_checksum:
                raise ConflictError(
                    f"File changed since you read it. "
                    f"Expected {expected_checksum[:8]}..., current is {node.checksum[:8]}..."
                )

            # Snapshot old version
            if node.storage_key:
                old_content = await self.storage.read_file(node.storage_key)
                v_suffix = PurePosixPath(node.name).suffix
                version_key = (
                    f"{entity_id}/workspace/.versions/{node.id}/"
                    f"v{node.version}_{utc_now_iso().replace(':', '-')}{v_suffix}"
                )
                await self.storage.write_file(version_key, old_content)
                # Update manifest
                manifest_key = f"{entity_id}/workspace/.versions/{node.id}/manifest.json"
                try:
                    mdata = await self.storage.read_file(manifest_key)
                    manifest = json.loads(mdata)
                except Exception:
                    manifest = []
                manifest.append({
                    "version": node.version,
                    "timestamp": utc_now_iso(),
                    "checksum": node.checksum,
                    "size": node.size_bytes,
                    "key": version_key,
                })
                await self.storage.write_file(manifest_key, json.dumps(manifest, indent=2).encode())

            old_checksum = node.checksum
            await self.storage.write_file(node.storage_key, content)
            node.version += 1
            node.checksum = new_checksum
            node.size_bytes = len(content)
            node.updated_at = utc_now()
            if mime_type:
                node.mime_type = mime_type
            if metadata:
                existing_meta = _parse_metadata(node.metadata_json)
                existing_meta.update(metadata)
                node.metadata_json = _dump_metadata(existing_meta)

            self._log_op(db, entity_id, "overwrite", actor, node.id,
                         payload={"path": path, "version": node.version},
                         before_checksum=old_checksum, after_checksum=new_checksum)
        else:
            name = PurePosixPath(path).name
            parent_id = await self._ensure_parents(db, entity_id, path, actor)

            node_id = generate_uuid()
            storage_key = f"{entity_id}/workspace/blobs/{node_id}/{_sanitize_name(name)}"
            await self.storage.write_file(storage_key, content)
            if not mime_type:
                mime_type = mimetypes.guess_type(name)[0]

            meta = metadata or {}
            node = WorkspaceNode(
                id=node_id, entity_id=entity_id, node_type="file",
                name=name, path=path, parent_id=parent_id,
                mime_type=mime_type, size_bytes=len(content), checksum=new_checksum,
                storage_key=storage_key, version=1,
                origin_type=origin_type or actor.type, origin_ref=actor.ref,
                metadata_json=_dump_metadata(meta) if meta else None,
            )
            db.add(node)
            await db.flush()
            self._log_op(db, entity_id, "create_file", actor, node.id,
                         payload={"path": path, "size": len(content)},
                         after_checksum=new_checksum)

        return node

    async def create_folder(self, db, entity_id: str, path: str, actor: Actor) -> WorkspaceNode:
        existing = await self.get_node_by_path(db, entity_id, path)
        if existing:
            if existing.node_type == "folder":
                return existing
            raise WorkspaceError(f"Path '{path}' already exists as {existing.node_type}")

        name = PurePosixPath(path).name
        parent_id = await self._ensure_parents(db, entity_id, path, actor)

        folder = WorkspaceNode(
            id=generate_uuid(), entity_id=entity_id,
            node_type="folder", name=name, path=path,
            parent_id=parent_id, origin_type=actor.type, origin_ref=actor.ref,
        )
        db.add(folder)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await self.get_node_by_path(db, entity_id, path)
            if existing and existing.node_type == "folder":
                return existing
            raise
        self._log_op(db, entity_id, "create_folder", actor, folder.id,
                     payload={"path": path})
        return folder

    async def scaffold_workspace(self, db, entity_id: str) -> list[WorkspaceNode]:
        """Async minimal scaffold: only Inbox/ and WORKSPACE_NOTES.md."""
        actor = Actor(type="system", ref="scaffold")
        created = [await self.create_folder(db, entity_id, "Inbox", actor)]

        notes_content = "# Workspace Notes\n\n_Add cross-file context here._\n"
        notes_node = await self.write_file(
            db, entity_id, "WORKSPACE_NOTES.md",
            notes_content.encode("utf-8"), "text/markdown",
            actor, metadata={"description": "Cross-file context and notes"},
            origin_type="shared",
        )
        created.append(notes_node)
        return created

    async def create_bookmark(self, db, entity_id: str, path: str,
                              url: str, actor: Actor,
                              metadata: Optional[dict] = None) -> WorkspaceNode:
        name = PurePosixPath(path).name
        parent_id = await self._ensure_parents(db, entity_id, path, actor)

        meta = metadata or {}
        node = WorkspaceNode(
            id=generate_uuid(), entity_id=entity_id,
            node_type="bookmark", name=name, path=path,
            parent_id=parent_id, url=url,
            origin_type=actor.type, origin_ref=actor.ref,
            metadata_json=_dump_metadata(meta) if meta else None,
        )
        db.add(node)
        await db.flush()
        self._log_op(db, entity_id, "create_file", actor, node.id,
                     payload={"path": path, "url": url})
        return node

    async def delete_node(self, db, entity_id: str, path: str, actor: Actor) -> WorkspaceNode:
        node = await self.get_node_by_path(db, entity_id, path)
        if not node:
            raise NodeNotFoundError(f"No node at '{path}'")
        self._check_provenance(node, actor, "delete")

        now = utc_now()
        node.deleted_at = now
        node.updated_at = now

        if node.node_type == "folder":
            descendants = await db.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.entity_id == entity_id,
                    WorkspaceNode.path.startswith(path + "/"),
                    self._alive_filter(),
                )
            )
            for desc in descendants.scalars().all():
                desc.deleted_at = now
                desc.updated_at = now

        self._log_op(db, entity_id, "delete", actor, node.id,
                     payload={"path": path, "node_type": node.node_type})
        return node

    async def move(self, db, entity_id: str, from_path: str,
                   to_path: str, actor: Actor) -> WorkspaceNode:
        node = await self.get_node_by_path(db, entity_id, from_path)
        if not node:
            raise NodeNotFoundError(f"No node at '{from_path}'")

        existing = await self.get_node_by_path(db, entity_id, to_path)
        if existing:
            raise WorkspaceError(f"Destination '{to_path}' already exists")

        old_path = node.path
        new_name = PurePosixPath(to_path).name
        parent_id = await self._ensure_parents(db, entity_id, to_path, actor)

        node.path = to_path
        node.name = new_name
        node.parent_id = parent_id
        node.updated_at = utc_now()

        if node.node_type == "folder":
            old_prefix = old_path + "/"
            new_prefix = to_path + "/"
            descendants = await db.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.entity_id == entity_id,
                    WorkspaceNode.path.startswith(old_prefix),
                    self._alive_filter(),
                )
            )
            for desc in descendants.scalars().all():
                desc.path = new_prefix + desc.path[len(old_prefix):]
                desc.updated_at = utc_now()

        self._log_op(db, entity_id, "move", actor, node.id,
                     payload={"from": old_path, "to": to_path},
                     inverse={"from": to_path, "to": old_path})
        return node

    async def rename(self, db, entity_id: str, path: str,
                     new_name: str, actor: Actor) -> WorkspaceNode:
        node = await self.get_node_by_path(db, entity_id, path)
        if not node:
            raise NodeNotFoundError(f"No node at '{path}'")

        parts = PurePosixPath(path).parts
        new_path = "/".join(parts[:-1] + (new_name,)) if len(parts) > 1 else new_name

        existing = await self.get_node_by_path(db, entity_id, new_path)
        if existing and existing.id != node.id:
            raise WorkspaceError(f"Path '{new_path}' already exists")

        old_path = node.path
        node.name = new_name
        node.path = new_path
        node.updated_at = utc_now()

        if node.node_type == "folder":
            old_prefix = old_path + "/"
            new_prefix = new_path + "/"
            descendants = await db.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.entity_id == entity_id,
                    WorkspaceNode.path.startswith(old_prefix),
                    self._alive_filter(),
                )
            )
            for desc in descendants.scalars().all():
                desc.path = new_prefix + desc.path[len(old_prefix):]
                desc.updated_at = utc_now()

        self._log_op(db, entity_id, "rename", actor, node.id,
                     payload={"path": new_path, "old_name": PurePosixPath(old_path).name, "new_name": new_name})
        return node

    async def annotate(self, db, entity_id: str, path: str,
                       description: str, actor: Actor) -> WorkspaceNode:
        node = await self.get_node_by_path(db, entity_id, path)
        if not node:
            raise NodeNotFoundError(f"No node at '{path}'")

        meta = _parse_metadata(node.metadata_json)
        meta["description"] = description
        node.metadata_json = _dump_metadata(meta)
        node.updated_at = utc_now()
        self._log_op(db, entity_id, "annotate", actor, node.id,
                     payload={"path": path, "description": description})
        return node

    async def list_trash(self, db, entity_id: str) -> list[WorkspaceNode]:
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.deleted_at.isnot(None),
            ).order_by(WorkspaceNode.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def restore_from_trash(self, db, entity_id: str, node_id: str,
                                 actor: Actor) -> WorkspaceNode:
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id == node_id,
                WorkspaceNode.deleted_at.isnot(None),
            )
        )
        node = result.scalars().first()
        if not node:
            raise NodeNotFoundError(f"No deleted node with id '{node_id}'")

        conflict = await self.get_node_by_path(db, entity_id, node.path)
        if conflict:
            raise WorkspaceError(f"Cannot restore: path '{node.path}' is already occupied")

        node.deleted_at = None
        node.updated_at = utc_now()
        self._log_op(db, entity_id, "restore", actor, node.id,
                     payload={"path": node.path})
        return node

    async def history(self, db, entity_id: str, limit: int = 50) -> list[WorkspaceOp]:
        result = await db.execute(
            select(WorkspaceOp).where(
                WorkspaceOp.entity_id == entity_id,
            ).order_by(WorkspaceOp.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def search_files(self, db, entity_id: str, query: str, folder: str = "") -> list[WorkspaceNode]:
        all_nodes = await self.get_all_nodes(db, entity_id)
        query_lower = query.lower()
        terms = query_lower.split()
        results = []
        for n in all_nodes:
            if folder and not n.path.startswith(folder):
                continue
            name_lower = n.name.lower()
            path_lower = n.path.lower()
            if all(t in name_lower or t in path_lower for t in terms):
                results.append(n)
        return results

    async def load_nodes_by_ids(self, db, entity_id: str, node_ids: list[str]) -> list[WorkspaceNode]:
        """Load multiple nodes by ID, filtering to entity and alive."""
        if not node_ids:
            return []
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id.in_(node_ids),
                self._alive_filter(),
            )
        )
        return list(result.scalars().all())

    async def copy(self, db, entity_id: str, from_path: str,
                   to_path: str, actor: Actor) -> WorkspaceNode:
        """Deep-copy a file or folder to a new path."""
        src = await self.get_node_by_path(db, entity_id, from_path)
        if not src:
            raise NodeNotFoundError(f"No node at '{from_path}'")
        existing = await self.get_node_by_path(db, entity_id, to_path)
        if existing:
            raise WorkspaceError(f"Destination '{to_path}' already exists")

        if src.node_type == "file":
            if not src.storage_key:
                raise WorkspaceError(f"Source file '{from_path}' has no storage key")
            content = await self.storage.read_file(src.storage_key)
            node = await self.write_file(
                db, entity_id, to_path, content, src.mime_type, actor,
                metadata=_parse_metadata(src.metadata_json) or None,
            )
            self._log_op(db, entity_id, "copy", actor, node.id,
                         payload={"from": from_path, "to": to_path})
            return node

        if src.node_type == "bookmark":
            return await self.create_bookmark(
                db, entity_id, to_path, src.url or "", actor,
                metadata=_parse_metadata(src.metadata_json) or None,
            )

        # Folder: deep copy
        new_folder = await self.create_folder(db, entity_id, to_path, actor)
        descendants = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.path.startswith(from_path + "/"),
                self._alive_filter(),
            ).order_by(WorkspaceNode.path)
        )
        for desc in descendants.scalars().all():
            rel = desc.path[len(from_path):]
            dest = to_path + rel
            if desc.node_type == "folder":
                await self.create_folder(db, entity_id, dest, actor)
            elif desc.node_type == "bookmark":
                await self.create_bookmark(
                    db, entity_id, dest, desc.url or "", actor,
                    metadata=_parse_metadata(desc.metadata_json) or None,
                )
            elif desc.node_type == "file" and desc.storage_key:
                content = await self.storage.read_file(desc.storage_key)
                await self.write_file(
                    db, entity_id, dest, content, desc.mime_type, actor,
                    metadata=_parse_metadata(desc.metadata_json) or None,
                )
        self._log_op(db, entity_id, "copy", actor, new_folder.id,
                     payload={"from": from_path, "to": to_path, "type": "folder"})
        return new_folder

    async def hard_delete(self, db, entity_id: str, node_id: str) -> None:
        """Permanently delete a soft-deleted node and its storage."""
        result = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.id == node_id,
                WorkspaceNode.deleted_at.isnot(None),
            )
        )
        node = result.scalars().first()
        if not node:
            raise NodeNotFoundError(f"No deleted node with id '{node_id}'")

        if node.storage_key:
            try:
                await self.storage.delete_file(node.storage_key)
            except Exception:
                pass

        # Delete version history
        versions_prefix = f"{entity_id}/workspace/.versions/{node_id}"
        try:
            await self.storage.delete_recursive(versions_prefix)
        except Exception:
            pass

        await db.delete(node)
        await db.flush()

    async def undo(self, db, entity_id: str, op_id: str,
                   actor: Actor) -> dict:
        """Undo a workspace operation using its inverse_json."""
        result = await db.execute(
            select(WorkspaceOp).where(
                WorkspaceOp.entity_id == entity_id,
                WorkspaceOp.id == op_id,
                WorkspaceOp.undone_at.is_(None),
            )
        )
        op = result.scalars().first()
        if not op:
            raise NodeNotFoundError(f"No undoable op with id '{op_id}'")

        inverse = json.loads(op.inverse_json) if op.inverse_json else None
        op_type = op.op_type
        undone_detail = {"op_id": op_id, "op_type": op_type}

        if op_type == "move" and inverse:
            try:
                await self.move(db, entity_id, inverse["from"], inverse["to"], actor)
                undone_detail["action"] = f"moved back {inverse['from']} -> {inverse['to']}"
            except WorkspaceError as e:
                raise WorkspaceError(f"Cannot undo move: {e}")

        elif op_type == "rename" and inverse:
            try:
                await self.rename(db, entity_id, inverse["path"], inverse["new_name"], actor)
                undone_detail["action"] = f"renamed back to {inverse['new_name']}"
            except WorkspaceError as e:
                raise WorkspaceError(f"Cannot undo rename: {e}")

        elif op_type == "delete" and op.node_id:
            try:
                await self.restore_from_trash(db, entity_id, op.node_id, actor)
                undone_detail["action"] = "restored from trash"
            except WorkspaceError as e:
                raise WorkspaceError(f"Cannot undo delete: {e}")

        elif op_type in ("create_file", "create_folder") and op.node_id:
            payload = json.loads(op.payload_json) if op.payload_json else {}
            path = payload.get("path", "")
            if path:
                try:
                    await self.delete_node(db, entity_id, path, actor)
                    undone_detail["action"] = f"deleted {path}"
                except WorkspaceError as e:
                    raise WorkspaceError(f"Cannot undo create: {e}")
            else:
                raise WorkspaceError("Cannot undo: missing path in op payload")

        elif op_type == "overwrite" and op.node_id and op.before_checksum:
            node = await self.get_node_by_id(db, entity_id, op.node_id)
            if node:
                payload = json.loads(op.payload_json) if op.payload_json else {}
                prev_version = payload.get("version", node.version) - 1
                if prev_version >= 1:
                    # Use version history
                    manifest_key = f"{entity_id}/workspace/.versions/{op.node_id}/manifest.json"
                    try:
                        mdata = await self.storage.read_file(manifest_key)
                        manifest = json.loads(mdata)
                    except Exception:
                        raise WorkspaceError("Cannot undo overwrite: no version history")
                    target = next((v for v in manifest if v["version"] == prev_version), None)
                    if not target:
                        raise WorkspaceError(f"Cannot undo overwrite: version {prev_version} not found")
                    old_content = await self.storage.read_file(target["key"])
                    await self.write_file(db, entity_id, node.path, old_content, node.mime_type, actor)
                    undone_detail["action"] = f"restored to v{prev_version}"
                else:
                    raise WorkspaceError("Cannot undo overwrite: no previous version")
            else:
                raise WorkspaceError("Cannot undo overwrite: node not found")
        else:
            raise WorkspaceError(f"Undo not supported for op_type '{op_type}'")

        op.undone_at = utc_now()
        await db.flush()
        return undone_detail


# Module-level singleton (imported by routers and services).
from app.services.storage import storage as _storage  # noqa: E402
workspace_service = WorkspaceService(_storage)
