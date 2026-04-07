"""Build Gemini Parts from workspace nodes."""

from __future__ import annotations

import base64
import json
import mimetypes
from typing import TYPE_CHECKING, Any, List, Optional, Set, Tuple

from google.genai import types

from app.config import settings
from app.models import WorkspaceNode
from app.services.deep_agent_office_extractors import extract_office_text
from app.services.document_text import extract_pdf_text
from app.services.storage import storage

if TYPE_CHECKING:
    pass

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

        if len(raw) > cap:
            warnings.append(f"Node {node.id} exceeds size limit ({cap} bytes); skipped.")
            continue

        mime = (node.mime_type or "").strip() or mimetypes.guess_type(node.name)[0] or "application/octet-stream"

        if mime in (
            "application/pdf", "image/png", "image/jpeg", "image/jpg",
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
        if len(raw) > cap:
            warnings.append(f"Node {node.id} exceeds size limit ({cap} bytes); omitted.")
            continue

        if mime in (
            "application/pdf", "image/png", "image/jpeg", "image/jpg",
            "image/webp", "image/gif", "audio/mpeg", "audio/mp3", "video/mp4",
        ) or mime.startswith("image/"):
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


def build_deep_agent_multimodal_parts(
    nodes: List[WorkspaceNode], profile_id: Optional[str],
) -> Tuple[List[dict[str, Any]], Set[str], List[str]]:
    """Build LangChain HumanMessage content blocks for native multimodal turns."""
    if profile_id not in ("gemini_google", "kimi_moonshot"):
        return [], set(), []

    blocks: List[dict[str, Any]] = []
    used_node_ids: Set[str] = set()
    warnings: List[str] = []
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    count = 0

    for node in nodes:
        if count >= MAX_ATTACHMENTS:
            warnings.append("Attachment limit reached; some files omitted.")
            break
        if node.node_type != "file" or not node.storage_key:
            continue
        try:
            raw = storage.read_file_sync(node.storage_key)
        except Exception as e:
            warnings.append(f"Could not read node {node.id}: {e}")
            continue
        if len(raw) > cap:
            warnings.append(f"Node {node.id} exceeds size limit ({cap} bytes); skipped.")
            continue

        mime = (node.mime_type or "").strip() or mimetypes.guess_type(node.name)[0] or "application/octet-stream"
        if mime not in (
            "application/pdf", "image/png", "image/jpeg", "image/jpg",
            "image/webp", "image/gif", "audio/mpeg", "audio/mp3", "video/mp4",
        ) and not mime.startswith("image/"):
            continue

        normalized_mime = "image/jpeg" if mime == "image/jpg" else mime
        b64 = base64.b64encode(raw).decode("ascii")

        if profile_id == "gemini_google":
            blocks.append({"type": "media", "mime_type": normalized_mime, "data": b64})
            used_node_ids.add(node.id)
            count += 1
            continue

        if normalized_mime.startswith("image/"):
            blocks.append({"type": "image_url", "image_url": {"url": f"data:{normalized_mime};base64,{b64}"}})
            used_node_ids.add(node.id)
            count += 1
            continue
        if normalized_mime.startswith("video/"):
            blocks.append({"type": "video_url", "video_url": {"url": f"data:{normalized_mime};base64,{b64}"}})
            used_node_ids.add(node.id)
            count += 1
            continue

    return blocks, used_node_ids, warnings
