"""Office document text extractors.

OOXML formats (docx/pptx/xlsx) are extracted directly via zipfile XML parsing.
Legacy binary formats (doc/ppt/xls) are converted to OOXML via LibreOffice
headless, then extracted with the same OOXML functions.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_XML_TAG_RE = re.compile(r"\{.*\}")


def _local(tag: str) -> str:
    return _XML_TAG_RE.sub("", tag)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n\n…(truncated)"


def extract_docx_text(raw: bytes, *, max_chars: int) -> str:
    out: list[str] = []
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    for elem in root.iter():
        if _local(elem.tag) == "t" and elem.text:
            out.append(elem.text)
    return _truncate("\n".join(out).strip(), max_chars)


def extract_pptx_text(raw: bytes, *, max_chars: int) -> str:
    texts: list[str] = []
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        slide_names = sorted(
            name
            for name in zf.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
        for idx, name in enumerate(slide_names, start=1):
            try:
                xml = zf.read(name)
            except KeyError:
                continue
            root = ET.fromstring(xml)
            slide_bits: list[str] = []
            for elem in root.iter():
                if _local(elem.tag) == "t" and elem.text:
                    slide_bits.append(elem.text)
            if slide_bits:
                texts.append(f"[Slide {idx}] " + " ".join(slide_bits))
    return _truncate("\n\n".join(texts).strip(), max_chars)


def extract_xlsx_text(raw: bytes, *, max_chars: int) -> str:
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in [e for e in shared_root if _local(e.tag) == "si"]:
                chunks: list[str] = []
                for t in [e for e in si.iter() if _local(e.tag) == "t" and e.text]:
                    chunks.append(t.text or "")
                shared.append("".join(chunks))

        sheet_names = sorted(
            name
            for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )

        lines: list[str] = []
        for i, sheet_name in enumerate(sheet_names, start=1):
            root = ET.fromstring(zf.read(sheet_name))
            lines.append(f"[Sheet {i}]")
            for c in root.iter():
                if _local(c.tag) != "c":
                    continue
                cell_ref = c.attrib.get("r", "")
                cell_type = c.attrib.get("t", "")
                value = ""
                v_elem = next((e for e in c if _local(e.tag) == "v"), None)
                if cell_type == "inlineStr":
                    is_elem = next((e for e in c if _local(e.tag) == "is"), None)
                    if is_elem is not None:
                        t_chunks = [
                            e.text or ""
                            for e in is_elem.iter()
                            if _local(e.tag) == "t" and e.text
                        ]
                        value = "".join(t_chunks)
                else:
                    if v_elem is None or v_elem.text is None:
                        continue
                    raw_v = v_elem.text
                    if cell_type == "s":
                        try:
                            idx = int(raw_v)
                            value = shared[idx] if 0 <= idx < len(shared) else raw_v
                        except Exception:
                            value = raw_v
                    else:
                        value = raw_v
                if value.strip():
                    lines.append(f"{cell_ref}: {value}")
    return _truncate("\n".join(lines).strip(), max_chars)


# ── Legacy binary format conversion via LibreOffice ──────────────────

_LEGACY_CONVERSIONS: dict[str, tuple] = {
    "application/msword": ("doc", "docx", extract_docx_text),
    "application/vnd.ms-powerpoint": ("ppt", "pptx", extract_pptx_text),
    "application/vnd.ms-excel": ("xls", "xlsx", extract_xlsx_text),
}


def _convert_and_extract(
    raw: bytes, src_ext: str, dst_ext: str, extractor, *, max_chars: int,
) -> str | None:
    """Convert a legacy office file to modern format via LibreOffice, then extract text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / f"input.{src_ext}"
        src.write_bytes(raw)
        try:
            # Each invocation gets its own profile dir to avoid LibreOffice's
            # single-instance profile lock when multiple conversions run concurrently.
            profile_dir = Path(tmpdir) / "profile"
            profile_dir.mkdir()
            subprocess.run(
                ["soffice", "--headless",
                 f"-env:UserInstallation=file://{profile_dir}",
                 "--convert-to", dst_ext,
                 "--outdir", tmpdir, str(src)],
                capture_output=True, timeout=30,
            )
        except FileNotFoundError:
            logger.debug("soffice not installed — cannot convert .%s", src_ext)
            return None
        except subprocess.TimeoutExpired:
            logger.warning("soffice timed out converting .%s", src_ext)
            return None
        converted = Path(tmpdir) / f"input.{dst_ext}"
        if not converted.exists():
            logger.warning("soffice conversion produced no output for .%s", src_ext)
            return None
        try:
            return extractor(converted.read_bytes(), max_chars=max_chars)
        except Exception:
            logger.exception("Extraction failed after converting .%s → .%s", src_ext, dst_ext)
            return None


def extract_office_text(
    raw: bytes, *, mime_type: str, max_chars: int
) -> str | None:
    mime = (mime_type or "").strip().lower()
    # Modern OOXML — direct extraction
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx_text(raw, max_chars=max_chars)
    if mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return extract_pptx_text(raw, max_chars=max_chars)
    if mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return extract_xlsx_text(raw, max_chars=max_chars)
    # Legacy binary — convert via LibreOffice then extract
    conv = _LEGACY_CONVERSIONS.get(mime)
    if conv:
        src_ext, dst_ext, extractor = conv
        return _convert_and_extract(raw, src_ext, dst_ext, extractor, max_chars=max_chars)
    return None
