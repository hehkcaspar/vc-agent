"""Build Gemini Parts from entity resources and artifacts."""

from __future__ import annotations

import base64
import mimetypes
from typing import TYPE_CHECKING, Any, List, Optional, Set, Tuple

from google.genai import types

from app.config import settings
from app.models import Artifact, Resource
from app.services.deep_agent_office_extractors import extract_office_text
from app.services.document_text import extract_pdf_text
from app.services.storage import storage

if TYPE_CHECKING:
    pass

MAX_ATTACHMENTS = 8


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n\n…(truncated)"


async def build_context_parts(
    resources: List[Resource],
    artifacts: List[Artifact],
) -> Tuple[List[types.Part], List[str]]:
    """Return multimodal parts (for the user turn) and human-readable warnings."""
    parts: List[types.Part] = []
    warnings: List[str] = []
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    max_chars = settings.CHAT_MAX_ARTIFACT_CHARS
    n = 0

    for ar in artifacts:
        if n >= MAX_ATTACHMENTS:
            warnings.append("Artifact attachment limit reached; some artifacts omitted.")
            break
        try:
            raw = await storage.read_file(ar.relative_path)
        except Exception as e:
            warnings.append(f"Could not read artifact {ar.id}: {e}")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        body = _truncate_text(text, max_chars)
        parts.append(
            types.Part.from_text(
                text=(
                    f"--- Artifact id={ar.id} name={ar.title or ar.artifact_type} "
                    f"type={ar.artifact_type} v{ar.version} ---\n{body}"
                )
            )
        )
        n += 1

    for res in resources:
        if n >= MAX_ATTACHMENTS:
            warnings.append("Resource attachment limit reached; some resources omitted.")
            break
        if res.resource_type == "url":
            parts.append(
                types.Part.from_text(
                    text=f"--- Resource id={res.id} (URL) title={res.title} ---\n{res.url or ''}"
                )
            )
            n += 1
            continue

        if not res.relative_path:
            warnings.append(f"Resource {res.id} has no file path.")
            continue
        try:
            raw = await storage.read_file(res.relative_path)
        except Exception as e:
            warnings.append(f"Could not read resource {res.id}: {e}")
            continue
        if len(raw) > cap:
            warnings.append(
                f"Resource {res.id} exceeds size limit ({cap} bytes); skipped as binary."
            )
            continue

        mime = (res.mime_type or "").strip() or mimetypes.guess_type(
            res.original_filename or res.title or ""
        )[0] or "application/octet-stream"

        if mime in (
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/gif",
            "audio/mpeg",
            "audio/mp3",
            "video/mp4",
        ) or mime.startswith("image/"):
            parts.append(
                types.Part.from_bytes(
                    data=raw,
                    mime_type=mime if mime != "image/jpg" else "image/jpeg",
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
                    text=f"--- Resource id={res.id} file={res.title} ---\n{_truncate_text(t, max_chars)}"
                )
            )
            n += 1
            continue

        office_text = extract_office_text(raw, mime_type=mime, max_chars=max_chars)
        if office_text is not None:
            if office_text.strip():
                parts.append(
                    types.Part.from_text(
                        text=(
                            f"--- Resource id={res.id} file={res.title} "
                            f"({mime}; extracted text) ---\n{_truncate_text(office_text, max_chars)}"
                        )
                    )
                )
                n += 1
            else:
                warnings.append(f"Resource {res.id} ({mime}) has no extractable text.")
            continue

        # Fallback: try decode as text
        try:
            t = raw.decode("utf-8")
            parts.append(
                types.Part.from_text(
                    text=f"--- Resource id={res.id} ({mime}) ---\n{_truncate_text(t, max_chars)}"
                )
            )
            n += 1
        except Exception:
            warnings.append(
                f"Resource {res.id} has unsupported type {mime} for inline send; skip binary."
            )

    return parts, warnings


def build_harness_user_attachment_text(
    resources: List[Resource],
    artifacts: List[Artifact],
    *,
    skip_resource_ids: Optional[Set[str]] = None,
) -> Tuple[str, List[str]]:
    """Plain-text preamble for Deep Agent turns (no multimodal parts)."""
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    max_chars = settings.CHAT_MAX_ARTIFACT_CHARS
    n = 0
    chunks: List[str] = []
    warnings: List[str] = []

    for ar in artifacts:
        if n >= MAX_ATTACHMENTS:
            warnings.append("Artifact attachment limit reached; some artifacts omitted.")
            break
        try:
            raw = storage.read_file_sync(ar.relative_path)
        except Exception as e:
            warnings.append(f"Could not read artifact {ar.id}: {e}")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        body = _truncate_text(text, max_chars)
        chunks.append(
            f"--- Artifact id={ar.id} name={ar.title or ar.artifact_type} "
            f"type={ar.artifact_type} v{ar.version} ---\n{body}"
        )
        n += 1

    skip_resource_ids = skip_resource_ids or set()
    for res in resources:
        if n >= MAX_ATTACHMENTS:
            warnings.append("Resource attachment limit reached; some resources omitted.")
            break
        if res.id in skip_resource_ids:
            continue
        if res.resource_type == "url":
            chunks.append(
                f"--- Resource id={res.id} (URL) title={res.title} ---\n{res.url or ''}"
            )
            n += 1
            continue
        if not res.relative_path:
            warnings.append(f"Resource {res.id} has no file path.")
            continue
        try:
            raw = storage.read_file_sync(res.relative_path)
        except Exception as e:
            warnings.append(f"Could not read resource {res.id}: {e}")
            continue
        mime = (res.mime_type or "").strip() or mimetypes.guess_type(
            res.original_filename or res.title or ""
        )[0] or "application/octet-stream"
        if len(raw) > cap:
            warnings.append(
                f"Resource {res.id} exceeds size limit ({cap} bytes); binary omitted in harness text path."
            )
            continue
        if mime in (
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/gif",
            "audio/mpeg",
            "audio/mp3",
            "video/mp4",
        ) or mime.startswith("image/"):
            if mime == "application/pdf":
                try:
                    pdf_text = extract_pdf_text(raw, max_chars=max_chars)
                except Exception as e:
                    warnings.append(
                        f"Resource {res.id} PDF text extraction failed: {e}"
                    )
                    continue
                if not pdf_text.strip():
                    warnings.append(
                        f"Resource {res.id} is a PDF but no extractable text was found."
                    )
                    continue
                chunks.append(
                    f"--- Resource id={res.id} file={res.title} (application/pdf; extracted text) ---\n"
                    f"{_truncate_text(pdf_text, max_chars)}"
                )
                n += 1
                continue
            warnings.append(
                f"Resource {res.id} is binary ({mime}); Deep Agent text path cannot inline it."
            )
            continue
        if mime.startswith("text/") or mime in ("application/json", "application/xml"):
            try:
                t = raw.decode("utf-8")
            except UnicodeDecodeError:
                t = raw.decode("latin-1", errors="replace")
            chunks.append(
                f"--- Resource id={res.id} file={res.title} ---\n{_truncate_text(t, max_chars)}"
            )
            n += 1
            continue
        office_text = extract_office_text(raw, mime_type=mime, max_chars=max_chars)
        if office_text is not None:
            if office_text.strip():
                chunks.append(
                    f"--- Resource id={res.id} file={res.title} ({mime}; extracted text) ---\n"
                    f"{_truncate_text(office_text, max_chars)}"
                )
                n += 1
            else:
                warnings.append(
                    f"Resource {res.id} ({mime}) has no extractable text."
                )
            continue
        try:
            t = raw.decode("utf-8")
            chunks.append(
                f"--- Resource id={res.id} ({mime}) ---\n{_truncate_text(t, max_chars)}"
            )
            n += 1
        except Exception:
            warnings.append(
                f"Resource {res.id} has unsupported type {mime} for harness text preamble."
            )

    if not chunks:
        return "", warnings
    return "\n\n".join(chunks), warnings


def build_deep_agent_multimodal_parts(
    resources: List[Resource], profile_id: Optional[str]
) -> Tuple[List[dict[str, Any]], Set[str], List[str]]:
    """
    Build LangChain HumanMessage content blocks for native multimodal turns.
    Currently enabled for Gemini profile only.
    """
    if profile_id not in ("gemini_google", "kimi_moonshot"):
        return [], set(), []

    blocks: List[dict[str, Any]] = []
    used_resource_ids: Set[str] = set()
    warnings: List[str] = []
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    count = 0

    for res in resources:
        if count >= MAX_ATTACHMENTS:
            warnings.append("Resource attachment limit reached; some resources omitted.")
            break
        if res.resource_type == "url" or not res.relative_path:
            continue
        try:
            raw = storage.read_file_sync(res.relative_path)
        except Exception as e:
            warnings.append(f"Could not read resource {res.id}: {e}")
            continue
        if len(raw) > cap:
            warnings.append(
                f"Resource {res.id} exceeds size limit ({cap} bytes); skipped as binary."
            )
            continue
        mime = (res.mime_type or "").strip() or mimetypes.guess_type(
            res.original_filename or res.title or ""
        )[0] or "application/octet-stream"
        if mime not in (
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/gif",
            "audio/mpeg",
            "audio/mp3",
            "video/mp4",
        ) and not mime.startswith("image/"):
            continue
        normalized_mime = "image/jpeg" if mime == "image/jpg" else mime
        b64 = base64.b64encode(raw).decode("ascii")
        if profile_id == "gemini_google":
            # LangChain Google GenAI content block shape.
            blocks.append(
                {
                    "type": "media",
                    "mime_type": normalized_mime,
                    "data": b64,
                }
            )
            used_resource_ids.add(res.id)
            count += 1
            continue
        # Kimi (OpenAI-compatible): support image/video URL blocks natively.
        if normalized_mime.startswith("image/"):
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{normalized_mime};base64,{b64}",
                    },
                }
            )
            used_resource_ids.add(res.id)
            count += 1
            continue
        if normalized_mime.startswith("video/"):
            blocks.append(
                {
                    "type": "video_url",
                    "video_url": {
                        "url": f"data:{normalized_mime};base64,{b64}",
                    },
                }
            )
            used_resource_ids.add(res.id)
            count += 1
            continue
        # Audio/PDF are not wired via native Kimi URL blocks here; keep text fallback path.

    return blocks, used_resource_ids, warnings
