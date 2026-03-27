"""Helpers for extracting plain text from document bytes."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


def extract_pdf_text(raw: bytes, *, max_chars: int) -> str:
    """Extract text from PDF bytes with a hard char cap."""
    reader = PdfReader(BytesIO(raw))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if not text:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunk = text[:remaining]
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    out = "\n\n".join(parts).strip()
    if len(out) >= max_chars:
        return out[: max_chars - 20] + "\n\n…(truncated)"
    return out
