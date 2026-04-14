"""Agent tool for fetching raw legal-template text (Tier R1 reference corpus).

Separate from workspace tools because templates are entity-agnostic and
catalogued in `data/config/legal_templates.json`, not in a workspace tree.
Always included in the agent tool set — read-only and harmless for presets
that don't need it.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from app.services.legal_templates_config import (
    load_legal_templates_config,
    read_template_text,
)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _notify(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if on_status:
        try:
            on_status(msg)
        except Exception:
            pass


def build_legal_template_tools(
    on_status: Optional[Callable[[str], None]] = None,
) -> list:
    from langchain_core.tools import tool

    @tool
    def legal_template_read(template_id: str) -> str:
        """Fetch the extracted text of a legal reference template by id.

        Use this tool when a term in a deal document looks unusual and you
        need the precise wording from an industry-standard template (YC SAFE,
        NVCA priced-round doc, side letter, etc.) to compare against.

        The catalog of available templates is in your system prompt under
        "Reference template catalog" — pick an id from there.

        - template_id: id from the catalog (e.g. "nvca_term_sheet_2020",
          "yc_safe_cap_only", "yc_pro_rata_side_letter")

        Returns the full extracted text of the template, or an error if the
        id is unknown or the underlying file is missing.
        """
        _notify(on_status, f"Reading template {template_id}...")
        try:
            text = read_template_text(template_id)
        except (ValueError, FileNotFoundError) as exc:
            known = [t.id for t in load_legal_templates_config().templates]
            return _json({"ok": False, "error": str(exc), "known_template_ids": known})
        return _json({"ok": True, "template_id": template_id, "text": text})

    return [legal_template_read]
