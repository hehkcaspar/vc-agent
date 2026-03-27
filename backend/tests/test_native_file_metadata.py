"""Programmatic native_file_metadata extraction (no LLM)."""

from __future__ import annotations

import io
import zipfile

from app.services.native_file_metadata import extract_native_file_metadata


def test_native_metadata_without_raw_bytes():
    out = extract_native_file_metadata(
        None,
        mime_type="text/plain",
        filename_hint=None,
        source={"kind": "resource", "resource_type": "url"},
    )
    assert out["size_bytes"] is None
    assert out["sha256"] is None
    assert out["source"]["resource_type"] == "url"


def test_ooxml_core_properties_in_docx_zip():
    core = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>Spec Title</dc:title>
  <dc:creator>Alice Q</dc:creator>
  <cp:lastModifiedBy>Bob Editor</cp:lastModifiedBy>
</cp:coreProperties>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("docProps/core.xml", core)
    raw = buf.getvalue()

    out = extract_native_file_metadata(
        raw,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename_hint="memo.docx",
        source={"kind": "resource", "resource_type": "file"},
    )
    assert out["size_bytes"] == len(raw)
    assert "office_core" in out
    assert out["office_core"]["title"] == "Spec Title"
    assert out["office_core"]["creator"] == "Alice Q"
    assert out["office_core"]["last_modified_by"] == "Bob Editor"
