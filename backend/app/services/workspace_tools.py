"""Workspace agent tools — 14 tools for the unified workspace file system.

The 14th tool, ``propose_fact_update``, is the agent's only way to surface a
fact correction to the user. It appends a row to
``Entity.metadata_json._fact_discrepancies[]`` for adjudication. See
``services/fact_discrepancies.py`` and docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from typing import Any, Callable, Optional

from app.config import settings
from app.database import SyncSessionLocal
from app.models import Entity
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

logger = logging.getLogger(__name__)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _notify(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if on_status:
        try:
            on_status(msg)
        except Exception:
            pass


def _infer_source_type(workspace_path: str) -> str:
    """Classify a workspace path into a fact_ledger source tier.

    Matches on FOLDER segments (not full-string substrings) so a deck that
    happens to have "safe" in its name doesn't get misfiled as a legal doc.

    - ``cap_table`` — any segment names a cap table file or folder.
    - ``legal_doc`` — the path traverses a ``Legal`` folder, or the
      basename starts with a standard legal instrument prefix.
    - ``upload`` — anything else (deck, memo, data room doc).
    """
    if not workspace_path:
        return "upload"
    segments = [seg.strip().lower() for seg in workspace_path.split("/") if seg.strip()]
    if not segments:
        return "upload"
    basename = segments[-1]

    # Cap table: explicit naming in the filename or any folder segment.
    cap_tokens = ("cap table", "captable", "cap_table", "cap-table")
    if any(tok in s for s in segments for tok in cap_tokens):
        return "cap_table"

    # Legal folder anywhere in the path (common: `Data Room/Legal/...`).
    if any(seg == "legal" or seg.startswith("legal ") for seg in segments[:-1]):
        return "legal_doc"

    # Basename starts with a standard legal-instrument marker
    # (e.g. ``safe-...``, ``SPA - executed...``, ``Side Letter...``).
    legal_prefixes = (
        "safe", "spa", "ssa", "side letter", "side-letter",
        "subscription agreement", "investor rights", "voting agreement",
        "shareholders agreement", "share purchase agreement",
        "restricted stock", "certificate of incorporation",
    )
    for prefix in legal_prefixes:
        if basename.startswith(prefix):
            return "legal_doc"
        # Allow leading numbering like "1. SPA ..." or "1a. SAFE ..."
        for numbered_match in (f". {prefix}", f".{prefix}"):
            if numbered_match in basename:
                return "legal_doc"

    return "upload"


def build_workspace_tools(
    entity_id: str,
    session_id: str,
    run_id: Optional[str],
    workspace_service: WorkspaceService,
    on_status: Optional[Callable[[str], None]] = None,
    model_profile_id: Optional[str] = None,
    preset_id: Optional[str] = None,
) -> list:
    from langchain_core.tools import tool

    actor = Actor(type="agent", ref=run_id)
    detected_by = preset_id or "agent"

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
    def workspace_read_file(path: str) -> str | list:
        """Read a file's content. PDFs are sent as native binary; other files as extracted text.
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

            # PDF handling
            if mime == "application/pdf":
                from app.services.document_text import compress_pdf, extract_pdf_text
                # Gemini: compress and send native binary (preserves layout, tables, OCR)
                if model_profile_id != "kimi_moonshot":
                    pdf_bytes = compress_pdf(raw, target_bytes=settings.CHAT_MAX_ATTACHMENT_BYTES)
                    if pdf_bytes is None and len(raw) <= settings.CHAT_MAX_ATTACHMENT_BYTES:
                        pdf_bytes = raw  # gs unavailable but file fits
                    if pdf_bytes is not None:
                        import base64 as _b64
                        return [
                            {"type": "text", "text": _json({
                                "ok": True, "path": path, "checksum": node.checksum,
                                "mime_type": mime, "size_bytes": len(raw),
                                "note": "PDF attached as native binary below.",
                            })},
                            {"type": "file",
                             "mime_type": "application/pdf",
                             "base64": _b64.b64encode(pdf_bytes).decode()},
                        ]
                # Kimi or compression failed: text extraction fallback
                try:
                    text = extract_pdf_text(raw, max_chars=120_000)
                    return _json({
                        "ok": True, "path": path, "checksum": node.checksum,
                        "mime_type": mime, "content": text, "extracted": True,
                    })
                except Exception as e:
                    return _json({"ok": False, "error": f"PDF extraction failed: {e}"})

            # Image handling — send native binary to Gemini (OCR, visual understanding)
            if mime.startswith("image/") and model_profile_id != "kimi_moonshot":
                if len(raw) > settings.CHAT_MAX_ATTACHMENT_BYTES:
                    return _json({
                        "ok": False, "error": f"Image too large ({len(raw)} bytes).",
                    })
                import base64 as _b64
                return [
                    {"type": "text", "text": _json({
                        "ok": True, "path": path, "checksum": node.checksum,
                        "mime_type": mime, "size_bytes": len(raw),
                        "note": "Image attached as native binary below.",
                    })},
                    {"type": "file",
                     "mime_type": "image/jpeg" if mime == "image/jpg" else mime,
                     "base64": _b64.b64encode(raw).decode()},
                ]

            # Office extraction
            from app.services.office_extractors import extract_office_text
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
    def propose_fact_update(
        field_path: str,
        current_value: str,
        proposed_value: str,
        source_doc_path: str,
        confidence: str,
        rationale: str,
        round_name: str = "",
        source_doc_quote: str = "",
    ) -> str:
        """Surface a fact discrepancy for user review. NEVER silently mutate canonical facts.

        Call this when you read source material that disagrees with the canonical
        state shown in your prior-state context (e.g. _positions[], prior_rounds[],
        top-level facts like raise_amount). The user adjudicates — the proposed
        value is only applied on Accept.

        Args:
            field_path: dotted path, e.g. "raise_amount",
              "prior_rounds[Series A].safe_terms.valuation_cap",
              "_positions[fund_id=taihill_iii].invested_amount".
              Shorthand [X] matches round_name / fund_id / name per array.
              Use [key=value] for explicit match.
            current_value: JSON-stringified current value (what the metadata says today).
            proposed_value: JSON-stringified proposed value (what the source doc suggests).
            source_doc_path: workspace path of the doc that evidences the correction
              (e.g. "Data Room/Legal/SAFE - Series A-5.pdf").
            confidence: "low" | "medium" | "high".
            rationale: 1-3 sentences explaining why you think the proposed value is correct.
            round_name: optional — required when field_path enters prior_rounds[...].
            source_doc_quote: optional short excerpt from the doc (≤200 chars).
        """
        _notify(on_status, f"Proposing fact update: {field_path}")
        # Parse JSON values; fall back to literal string when not JSON.
        def _parse(raw: str) -> Any:
            raw_stripped = (raw or "").strip()
            if not raw_stripped:
                return None
            try:
                return json.loads(raw_stripped)
            except (json.JSONDecodeError, ValueError):
                return raw_stripped

        try:
            with SyncSessionLocal() as db:
                node = workspace_service.get_node_by_path_sync(
                    db, entity_id, source_doc_path,
                )
                if not node:
                    return _json({
                        "ok": False,
                        "error": f"source_doc_path not found: {source_doc_path!r}",
                    })

                entity = db.query(Entity).filter(Entity.id == entity_id).first()
                if entity is None:
                    return _json({"ok": False, "error": "entity not found"})

                metadata: dict
                if entity.metadata_json:
                    try:
                        metadata = json.loads(entity.metadata_json)
                        if not isinstance(metadata, dict):
                            metadata = {}
                    except json.JSONDecodeError:
                        metadata = {}
                else:
                    metadata = {}

                from app.services.fact_discrepancies import append_discrepancy
                committed = append_discrepancy(
                    metadata,
                    {
                        "detected_by": detected_by,
                        "field_path": field_path,
                        "current_value": _parse(current_value),
                        "proposed_value": _parse(proposed_value),
                        "source_doc_node_id": node.id,
                        "source_doc_quote": (source_doc_quote or None) or None,
                        "confidence": confidence,
                        "rationale": rationale,
                        "round_name": (round_name or None) or None,
                        "source_run": {
                            "agent_run_id": run_id,
                            "preset_id": preset_id,
                        },
                    },
                )

                # Ledger shim: mirror hard-fact discrepancies as a proposed
                # entry so the frontend can show provenance + source-tier
                # pills even before the user adjudicates. No-op for soft-path
                # discrepancies.
                from app.services.fact_ledger_schema import (
                    CONFIDENCE_STRING_TO_FLOAT, FactSource,
                )
                from app.services.fact_manager import (
                    record_proposed_for_discrepancy,
                )
                src_type = _infer_source_type(node.path)
                record_proposed_for_discrepancy(
                    metadata,
                    discrepancy_id=committed["id"],
                    fact_path=field_path,
                    proposed_value=_parse(proposed_value),
                    source=FactSource(
                        type=src_type,
                        ref=f"workspace://{node.path}",
                        quote=(source_doc_quote or None) or None,
                        preset=preset_id or detected_by,
                        run_id=run_id,
                    ),
                    confidence=CONFIDENCE_STRING_TO_FLOAT.get(confidence, 0.7),
                    notes=f"proposed_via:propose_fact_update/{detected_by}",
                )

                entity.metadata_json = json.dumps(metadata, ensure_ascii=False)
                db.commit()
                return _json({"ok": True, "discrepancy_id": committed["id"]})
        except ValueError as e:
            return _json({"ok": False, "error": str(e)})
        except Exception as e:
            logger.warning("propose_fact_update failed", exc_info=True)
            return _json({"ok": False, "error": f"internal error: {e}"})

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
        propose_fact_update,
    ]
