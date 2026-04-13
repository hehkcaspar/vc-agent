"""Build Gemini Parts from workspace nodes."""

from __future__ import annotations

import json
import mimetypes
from typing import Any, List, Optional, Set, Tuple

from google.genai import types

from app.config import settings
from app.models import WorkspaceNode
from app.services.office_extractors import extract_office_text
from app.services.document_text import extract_pdf_text
from app.services.storage import storage

MAX_ATTACHMENTS = 8
MAX_TEXT_CHARS = 120_000


def _truncate_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n\n…(truncated)"


def _node_label(node: WorkspaceNode) -> str:
    meta = {}
    if node.metadata_json:
        try:
            meta = json.loads(node.metadata_json)
        except Exception:
            pass
    dtype = meta.get("deliverable_type", "")
    label = f"id={node.id} file={node.name}"
    if dtype:
        label += f" type={dtype}"
    if node.version and node.version > 1:
        label += f" v{node.version}"
    return label


async def build_context_parts(
    nodes: List[WorkspaceNode],
) -> Tuple[List[types.Part], List[str]]:
    """Return multimodal parts (for the user turn) and human-readable warnings."""
    parts: List[types.Part] = []
    warnings: List[str] = []
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    n = 0

    for node in nodes:
        if n >= MAX_ATTACHMENTS:
            warnings.append("Attachment limit reached; some files omitted.")
            break

        # Bookmarks
        if node.node_type == "bookmark":
            parts.append(
                types.Part.from_text(
                    text=f"--- Bookmark {_node_label(node)} ---\n{node.url or ''}"
                )
            )
            n += 1
            continue

        # Folders — skip
        if node.node_type == "folder":
            continue

        if not node.storage_key:
            warnings.append(f"Node {node.id} has no storage key.")
            continue

        try:
            raw = await storage.read_file(node.storage_key)
        except Exception as e:
            warnings.append(f"Could not read node {node.id}: {e}")
            continue

        mime = (node.mime_type or "").strip() or mimetypes.guess_type(node.name)[0] or "application/octet-stream"

        # PDF: always compress to save tokens; handles oversized files too
        if mime == "application/pdf":
            from app.services.document_text import compress_pdf
            pdf_bytes = compress_pdf(raw, target_bytes=cap)
            if pdf_bytes is None and len(raw) <= cap:
                pdf_bytes = raw  # gs unavailable but file fits
            if pdf_bytes is not None:
                parts.append(types.Part.from_bytes(
                    data=pdf_bytes, mime_type="application/pdf",
                ))
                n += 1
            else:
                # Too large even after compression — try text extraction
                text = extract_pdf_text(raw, max_chars=MAX_TEXT_CHARS)
                if text.strip():
                    parts.append(types.Part.from_text(
                        text=f"--- {_node_label(node)} (PDF; extracted text) ---\n{_truncate_text(text)}"
                    ))
                    n += 1
                else:
                    warnings.append(f"Node {node.id} PDF too large and no extractable text.")
            continue

        if len(raw) > cap:
            warnings.append(f"Node {node.id} exceeds size limit ({cap} bytes); skipped.")
            continue

        if mime in (
            "image/png", "image/jpeg", "image/jpg",
            "image/webp", "image/gif", "audio/mpeg", "audio/mp3", "video/mp4",
        ) or mime.startswith("image/"):
            parts.append(
                types.Part.from_bytes(
                    data=raw,
                    mime_type="image/jpeg" if mime == "image/jpg" else mime,
                )
            )
            n += 1
            continue

        if mime.startswith("text/") or mime in ("application/json", "application/xml"):
            try:
                t = raw.decode("utf-8")
            except UnicodeDecodeError:
                t = raw.decode("latin-1", errors="replace")
            parts.append(
                types.Part.from_text(
                    text=f"--- {_node_label(node)} ---\n{_truncate_text(t)}"
                )
            )
            n += 1
            continue

        office_text = extract_office_text(raw, mime_type=mime, max_chars=MAX_TEXT_CHARS)
        if office_text is not None:
            if office_text.strip():
                parts.append(
                    types.Part.from_text(
                        text=f"--- {_node_label(node)} ({mime}; extracted text) ---\n{_truncate_text(office_text)}"
                    )
                )
                n += 1
            else:
                warnings.append(f"Node {node.id} ({mime}) has no extractable text.")
            continue

        try:
            t = raw.decode("utf-8")
            parts.append(
                types.Part.from_text(
                    text=f"--- {_node_label(node)} ({mime}) ---\n{_truncate_text(t)}"
                )
            )
            n += 1
        except Exception:
            warnings.append(f"Node {node.id} has unsupported type {mime}; skipped.")

    return parts, warnings


def build_harness_user_attachment_text(
    nodes: List[WorkspaceNode],
    *,
    skip_node_ids: Optional[Set[str]] = None,
) -> Tuple[str, List[str]]:
    """Plain-text preamble for Deep Agent turns (no multimodal parts)."""
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    n = 0
    chunks: List[str] = []
    warnings: List[str] = []
    skip_node_ids = skip_node_ids or set()

    for node in nodes:
        if n >= MAX_ATTACHMENTS:
            warnings.append("Attachment limit reached; some files omitted.")
            break
        if node.id in skip_node_ids:
            continue

        if node.node_type == "bookmark":
            chunks.append(f"--- Bookmark {_node_label(node)} ---\n{node.url or ''}")
            n += 1
            continue

        if node.node_type == "folder":
            continue

        if not node.storage_key:
            warnings.append(f"Node {node.id} has no storage key.")
            continue

        try:
            raw = storage.read_file_sync(node.storage_key)
        except Exception as e:
            warnings.append(f"Could not read node {node.id}: {e}")
            continue

        mime = (node.mime_type or "").strip() or mimetypes.guess_type(node.name)[0] or "application/octet-stream"

        # PDF: text extraction works regardless of file size (pypdf reads page by page)
        if mime == "application/pdf":
            try:
                pdf_text = extract_pdf_text(raw, max_chars=MAX_TEXT_CHARS)
            except Exception as e:
                warnings.append(f"Node {node.id} PDF extraction failed: {e}")
                continue
            if not pdf_text.strip():
                warnings.append(f"Node {node.id} PDF has no extractable text.")
                continue
            chunks.append(
                f"--- {_node_label(node)} (PDF; extracted text) ---\n{_truncate_text(pdf_text)}"
            )
            n += 1
            continue

        if len(raw) > cap:
            warnings.append(f"Node {node.id} exceeds size limit ({cap} bytes); omitted.")
            continue

        if mime in (
            "image/png", "image/jpeg", "image/jpg",
            "image/webp", "image/gif", "audio/mpeg", "audio/mp3", "video/mp4",
        ) or mime.startswith("image/"):
            warnings.append(f"Node {node.id} is binary ({mime}); text path cannot inline it.")
            continue

        if mime.startswith("text/") or mime in ("application/json", "application/xml"):
            try:
                t = raw.decode("utf-8")
            except UnicodeDecodeError:
                t = raw.decode("latin-1", errors="replace")
            chunks.append(f"--- {_node_label(node)} ---\n{_truncate_text(t)}")
            n += 1
            continue

        office_text = extract_office_text(raw, mime_type=mime, max_chars=MAX_TEXT_CHARS)
        if office_text is not None:
            if office_text.strip():
                chunks.append(
                    f"--- {_node_label(node)} ({mime}; extracted text) ---\n{_truncate_text(office_text)}"
                )
                n += 1
            else:
                warnings.append(f"Node {node.id} ({mime}) has no extractable text.")
            continue

        try:
            t = raw.decode("utf-8")
            chunks.append(f"--- {_node_label(node)} ({mime}) ---\n{_truncate_text(t)}")
            n += 1
        except Exception:
            warnings.append(f"Node {node.id} has unsupported type {mime}; skipped.")

    if not chunks:
        return "", warnings
    return "\n\n".join(chunks), warnings


def build_selected_files_pointer_list(nodes: List[WorkspaceNode]) -> str:
    """Build a structured pointer list of user-selected files for agent mode.

    The agent uses workspace_read_file(path) to read files it needs.
    No file content is inlined -- only metadata for triage.
    """
    files = [n for n in nodes if n.node_type != "folder"]
    if not files:
        return ""

    lines = [
        f"## User-selected files ({len(files)} file{'s' if len(files) != 1 else ''})",
        "The user selected these files as context for this task. "
        "Use workspace_read_file(path) to read the ones relevant to the request.",
        "",
        "| # | Path | Type | Size | Description |",
        "|---|------|------|------|-------------|",
    ]
    for i, node in enumerate(files, 1):
        mime = (node.mime_type or "").strip()
        ext = mime.split("/")[-1].upper() if mime else "—"
        if node.size_bytes and node.size_bytes > 1024 * 1024:
            size = f"{node.size_bytes / (1024 * 1024):.1f}MB"
        elif node.size_bytes and node.size_bytes > 1024:
            size = f"{node.size_bytes / 1024:.0f}KB"
        elif node.size_bytes:
            size = f"{node.size_bytes}B"
        else:
            size = "—"
        desc = "—"
        if node.metadata_json:
            try:
                meta = json.loads(node.metadata_json)
                desc = meta.get("description") or "—"
            except Exception:
                pass
        if node.node_type == "bookmark":
            path = f"{node.path} -> {node.url or ''}"
        else:
            path = node.path
        lines.append(f"| {i} | {path} | {ext} | {size} | {desc} |")

    return "\n".join(lines)
