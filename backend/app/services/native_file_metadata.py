"""Programmatic file metadata (size, hash, embedded PDF/OOXML properties) — no LLM."""

from __future__ import annotations

import hashlib
import mimetypes
import zipfile
from io import BytesIO
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from app.datetime_support import utc_now_iso

# OOXML MIME types that use docProps/core.xml
_OOXML_MIMES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _resolve_mime_for_row(mime_type: Optional[str], filename_or_title: Optional[str]) -> str:
    m = (mime_type or "").strip()
    if m:
        return m if m != "image/jpg" else "image/jpeg"
    guess = mimetypes.guess_type(filename_or_title or "")[0]
    return (guess or "application/octet-stream").replace("image/jpg", "image/jpeg")


def _pdf_embedded_metadata(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        reader = PdfReader(BytesIO(raw), strict=False)
    except Exception:
        return None
    out: Dict[str, Any] = {}
    try:
        out["page_count"] = len(reader.pages)
    except Exception:
        out["page_count"] = None

    md = reader.metadata
    if md:
        for attr, key in (
            ("title", "title"),
            ("author", "author"),
            ("subject", "subject"),
            ("creator", "creator"),
            ("producer", "producer"),
            ("creation_date", "creation_date"),
            ("modification_date", "modification_date"),
        ):
            v = _clean_str(getattr(md, attr, None))
            if v:
                out[key] = v
        if len(out) <= 1 and hasattr(md, "get"):
            alt = [
                ("/Title", "title"),
                ("/Author", "author"),
                ("/Subject", "subject"),
                ("/Creator", "creator"),
                ("/Producer", "producer"),
                ("/CreationDate", "creation_date"),
                ("/ModDate", "modification_date"),
            ]
            for pdf_k, out_k in alt:
                v = md.get(pdf_k)
                v = _clean_str(v)
                if v and out_k not in out:
                    out[out_k] = v

    return out or None


def _ooxml_core_properties(raw: bytes, mime: str) -> Optional[Dict[str, str]]:
    if mime not in _OOXML_MIMES:
        return None
    if not zipfile.is_zipfile(BytesIO(raw)):
        return None
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            if "docProps/core.xml" not in zf.namelist():
                return None
            xml = zf.read("docProps/core.xml")
    except Exception:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    # Collect last non-empty text per local tag (handles multiple namespaces).
    by_tag: Dict[str, str] = {}
    for el in root.iter():
        loc = _local_tag(el.tag)
        if el.text and el.text.strip():
            by_tag[loc] = el.text.strip()

    key_map = {
        "title": "title",
        "subject": "subject",
        "creator": "creator",
        "description": "description",
        "keywords": "keywords",
        "lastModifiedBy": "last_modified_by",
        "lastmodifiedby": "last_modified_by",
        "revision": "revision",
        "created": "created",
        "modified": "modified",
    }
    out: Dict[str, str] = {}
    for raw_tag, out_key in key_map.items():
        if raw_tag in by_tag:
            out[out_key] = by_tag[raw_tag]
    return out or None


def _plaintext_line_stats(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1", errors="replace")
        except Exception:
            return None
    lines = text.splitlines()
    nonempty = sum(1 for ln in lines if ln.strip())
    return {
        "line_count": len(lines),
        "non_empty_line_count": nonempty,
        "char_count": len(text),
    }


def extract_native_file_metadata(
    raw: Optional[bytes],
    *,
    mime_type: Optional[str],
    filename_hint: Optional[str],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    """Return JSON-serializable native metadata for one file-backed row."""
    mime = _resolve_mime_for_row(mime_type, filename_hint)
    block: Dict[str, Any] = {
        "at": utc_now_iso(),
        "source": source,
        "mime_type": mime,
        "size_bytes": len(raw) if raw is not None else None,
        "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
    }

    if raw is None:
        return block

    if mime == "application/pdf":
        pm = _pdf_embedded_metadata(raw)
        if pm:
            block["pdf"] = pm

    oc = _ooxml_core_properties(raw, mime)
    if oc:
        block["office_core"] = oc

    if mime.startswith("text/") or mime in ("application/json", "application/xml"):
        stats = _plaintext_line_stats(raw)
        if stats:
            block["text_stats"] = stats

    return block
