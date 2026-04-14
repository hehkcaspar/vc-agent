"""Extract plain text from every source file under app/legal_templates/.

Idempotent. Re-run when source files are added/updated. The extracted .txt
(or .md for xlsx) files sit next to the sources and are committed to the
repo, so a fresh clone does not need to re-run extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the backend app package importable when run as a bare script.
HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.document_text import extract_pdf_text  # noqa: E402
from app.services.office_extractors import (  # noqa: E402
    extract_docx_text,
    extract_xlsx_text,
    extract_office_text,
)


LEGAL_TEMPLATES_DIR = BACKEND_ROOT / "app" / "legal_templates"
MAX_CHARS = 300_000


def _extract_any(source: Path) -> tuple[str, str]:
    """Return (text, output_suffix) for a given source file."""
    ext = source.suffix.lower()
    raw = source.read_bytes()
    if ext == ".docx":
        return extract_docx_text(raw, max_chars=MAX_CHARS), ".txt"
    if ext == ".doc":
        text = extract_office_text(
            raw, mime_type="application/msword", max_chars=MAX_CHARS,
        )
        if text is None:
            raise RuntimeError(
                f"{source.name}: .doc conversion failed (LibreOffice required)"
            )
        return text, ".txt"
    if ext == ".pdf":
        return extract_pdf_text(raw, max_chars=MAX_CHARS), ".txt"
    if ext == ".xlsx":
        return extract_xlsx_text(raw, max_chars=MAX_CHARS), ".md"
    raise RuntimeError(f"{source.name}: unsupported extension {ext!r}")


def main() -> int:
    if not LEGAL_TEMPLATES_DIR.exists():
        print(f"no legal_templates dir at {LEGAL_TEMPLATES_DIR}", file=sys.stderr)
        return 1

    source_exts = {".docx", ".doc", ".pdf", ".xlsx"}
    sources = sorted(
        p for p in LEGAL_TEMPLATES_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in source_exts
    )
    if not sources:
        print("no source files found", file=sys.stderr)
        return 1

    errors = 0
    for src in sources:
        try:
            text, suffix = _extract_any(src)
        except Exception as exc:
            print(f"  FAIL {src.relative_to(LEGAL_TEMPLATES_DIR)}: {exc}")
            errors += 1
            continue
        out = src.with_suffix(suffix)
        out.write_text(text + "\n", encoding="utf-8")
        rel = src.relative_to(LEGAL_TEMPLATES_DIR)
        print(f"  ok   {rel} -> {out.name} ({len(text):,} chars)")
    if errors:
        print(f"\n{errors} source(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
