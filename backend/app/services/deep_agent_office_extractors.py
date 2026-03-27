"""Deep Agent office extractors for DOCX/PPTX/XLSX resources."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET


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


def extract_office_text(
    raw: bytes, *, mime_type: str, max_chars: int
) -> str | None:
    mime = (mime_type or "").strip().lower()
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx_text(raw, max_chars=max_chars)
    if mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return extract_pptx_text(raw, max_chars=max_chars)
    if mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return extract_xlsx_text(raw, max_chars=max_chars)
    return None
