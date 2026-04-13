"""Helpers for extracting plain text from document bytes."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)


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


def compress_pdf(raw: bytes, *, target_bytes: int) -> bytes | None:
    """Compress a PDF using ghostscript to reduce token cost and fit size limits.

    Always attempts /ebook (150 DPI) first for good quality compression.
    Falls back to /screen (72 DPI) if still over target_bytes.
    Returns None if gs is not available or compression fails.
    """
    for quality in ("/ebook", "/screen"):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "input.pdf"
            dst = Path(tmpdir) / "output.pdf"
            src.write_bytes(raw)
            try:
                subprocess.run(
                    ["gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                     f"-dPDFSETTINGS={quality}", "-dNOPAUSE", "-dQUIET",
                     "-dBATCH", f"-sOutputFile={dst}", str(src)],
                    capture_output=True, timeout=60,
                )
            except FileNotFoundError:
                logger.debug("ghostscript (gs) not installed — cannot compress PDF")
                return None
            except subprocess.TimeoutExpired:
                logger.warning("ghostscript timed out compressing PDF")
                return None
            if dst.exists():
                result = dst.read_bytes()
                if len(result) <= target_bytes:
                    # Return compressed only if actually smaller
                    return result if len(result) < len(raw) else raw
    return None
