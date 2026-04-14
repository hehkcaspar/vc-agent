"""Loader + atomic writer + startup seed for `data/config/legal_templates.json`.

Tier R1 of the legal-review reference system: catalogs raw template files
shipped under `backend/app/legal_templates/`. The file stores only metadata
(id, label, category, file paths) — never raw content. Agents fetch the
actual text via `legal_template_read(template_id)` in
`services/legal_template_tools.py`.

Mirrors the `funds_config.py` pattern (Pydantic validation + atomic
`tmp → os.replace` write).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings


_ID_RE = re.compile(r"^[a-z0-9_]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegalTemplate(_Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    category: Literal["safe", "priced_round", "side_letter", "guidance"]
    round_type: Literal["seed", "series_a_plus", "any"] = "any"
    instrument_types: list[Literal["safe", "convertible_note", "priced_round"]] = Field(
        default_factory=list,
    )
    description: str = Field(min_length=1, max_length=500)
    source_file: str = Field(min_length=1, max_length=256)
    text_file: str = Field(min_length=1, max_length=256)


class LegalTemplatesConfig(_Strict):
    version: int = 1
    templates: list[LegalTemplate] = Field(default_factory=list)


def _validate_ids(cfg: LegalTemplatesConfig) -> None:
    seen: set[str] = set()
    for t in cfg.templates:
        if not _ID_RE.match(t.id):
            raise ValueError(f"template id {t.id!r} must be snake_case [a-z0-9_]+")
        if t.id in seen:
            raise ValueError(f"duplicate template id {t.id!r}")
        seen.add(t.id)


def _default_config() -> dict[str, Any]:
    """Seed catalog covering every file extracted under legal_templates/."""
    return {
        "version": 1,
        "templates": [
            # ── YC Post-Money SAFE variants ──
            {
                "id": "yc_safe_cap_only",
                "label": "YC Post-Money SAFE — Valuation Cap Only",
                "category": "safe",
                "round_type": "seed",
                "instrument_types": ["safe"],
                "description": (
                    "Standard YC post-money SAFE where conversion is capped at a "
                    "pre-negotiated valuation (no discount, no MFN). Single-economic-"
                    "feature baseline."
                ),
                "source_file": "yc_safe/postmoney_safe_valuation_cap_only.docx",
                "text_file": "yc_safe/postmoney_safe_valuation_cap_only.txt",
            },
            {
                "id": "yc_safe_discount_only",
                "label": "YC Post-Money SAFE — Discount Only",
                "category": "safe",
                "round_type": "seed",
                "instrument_types": ["safe"],
                "description": (
                    "Standard YC post-money SAFE where investor receives a discount "
                    "on the next priced round (no cap, no MFN)."
                ),
                "source_file": "yc_safe/postmoney_safe_discount_only.docx",
                "text_file": "yc_safe/postmoney_safe_discount_only.txt",
            },
            {
                "id": "yc_safe_mfn_only",
                "label": "YC Post-Money SAFE — MFN Only",
                "category": "safe",
                "round_type": "seed",
                "instrument_types": ["safe"],
                "description": (
                    "Standard YC post-money SAFE with Most-Favored-Nation protection "
                    "only (no cap, no discount). Investor auto-matches better terms "
                    "from later SAFE holders."
                ),
                "source_file": "yc_safe/postmoney_safe_mfn_only.docx",
                "text_file": "yc_safe/postmoney_safe_mfn_only.txt",
            },
            {
                "id": "yc_pro_rata_side_letter",
                "label": "YC Pro-Rata Side Letter",
                "category": "side_letter",
                "round_type": "seed",
                "instrument_types": ["safe"],
                "description": (
                    "Separate YC side letter granting SAFE investors pro-rata "
                    "participation rights in future priced rounds. Not included by "
                    "default in the post-money SAFE."
                ),
                "source_file": "yc_safe/pro_rata_side_letter.docx",
                "text_file": "yc_safe/pro_rata_side_letter.txt",
            },
            {
                "id": "yc_safe_user_guide",
                "label": "YC SAFE User Guide",
                "category": "guidance",
                "round_type": "seed",
                "instrument_types": ["safe"],
                "description": (
                    "YC's comprehensive guide to SAFE mechanics, conversion triggers, "
                    "cap/discount math, and founder/investor considerations."
                ),
                "source_file": "yc_safe/safe_user_guide.pdf",
                "text_file": "yc_safe/safe_user_guide.txt",
            },
            # ── NVCA priced-round suite ──
            {
                "id": "nvca_term_sheet_2020",
                "label": "NVCA Model Term Sheet (2020)",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA industry-standard term sheet summarising economic + "
                    "governance terms for a Series A (or later) priced equity round."
                ),
                "source_file": "nvca/term_sheet_2020.docx",
                "text_file": "nvca/term_sheet_2020.txt",
            },
            {
                "id": "nvca_stock_purchase_agreement_2025",
                "label": "NVCA Stock Purchase Agreement (2025)",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA stock-purchase agreement for the issuance of preferred "
                    "stock: purchase price, closing conditions, reps & warranties."
                ),
                "source_file": "nvca/stock_purchase_agreement_2025.docx",
                "text_file": "nvca/stock_purchase_agreement_2025.txt",
            },
            {
                "id": "nvca_certificate_of_incorporation_2025",
                "label": "NVCA Amended Certificate of Incorporation (2025)",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA corporate charter establishing preferred-stock classes, "
                    "liquidation preferences, voting rights, board composition, "
                    "and anti-dilution mechanics."
                ),
                "source_file": "nvca/certificate_of_incorporation_2025.docx",
                "text_file": "nvca/certificate_of_incorporation_2025.txt",
            },
            {
                "id": "nvca_voting_agreement_2025",
                "label": "NVCA Voting Agreement (2025)",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA consent agreement governing investor voting, director "
                    "election, and protective provisions."
                ),
                "source_file": "nvca/voting_agreement_2025.docx",
                "text_file": "nvca/voting_agreement_2025.txt",
            },
            {
                "id": "nvca_investors_rights_agreement_2025",
                "label": "NVCA Investors' Rights Agreement (2025)",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA agreement covering information rights, registration "
                    "rights, and investor drag-along / co-sale obligations."
                ),
                "source_file": "nvca/investors_rights_agreement_2025.docx",
                "text_file": "nvca/investors_rights_agreement_2025.txt",
            },
            {
                "id": "nvca_rofr_co_sale_agreement_2026",
                "label": "NVCA ROFR / Co-Sale Agreement (2026)",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA Right of First Refusal (on founder secondary sales) and "
                    "Co-Sale Agreement (tag-along rights for investors)."
                ),
                "source_file": "nvca/rofr_co_sale_agreement_2026.docx",
                "text_file": "nvca/rofr_co_sale_agreement_2026.txt",
            },
            {
                "id": "nvca_management_rights_letter",
                "label": "NVCA Management Rights Letter",
                "category": "side_letter",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA letter granting ERISA-governed institutional investors "
                    "observation rights and disclosure protections."
                ),
                "source_file": "nvca/management_rights_letter.docx",
                "text_file": "nvca/management_rights_letter.txt",
            },
            {
                "id": "nvca_model_legal_opinion",
                "label": "NVCA Model Legal Opinion",
                "category": "guidance",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "Standard counsel legal opinion covering incorporation, "
                    "authorization, and compliance representations."
                ),
                "source_file": "nvca/model_legal_opinion.doc",
                "text_file": "nvca/model_legal_opinion.txt",
            },
            {
                "id": "nvca_indemnification_agreement",
                "label": "NVCA Indemnification Agreement",
                "category": "priced_round",
                "round_type": "series_a_plus",
                "instrument_types": ["priced_round"],
                "description": (
                    "NVCA indemnification provisions protecting officers and "
                    "directors from shareholder liability."
                ),
                "source_file": "nvca/indemnification_agreement.docx",
                "text_file": "nvca/indemnification_agreement.txt",
            },
        ],
    }


def ensure_legal_templates_seed() -> None:
    """Write the default catalog if the config file is missing. Safe to call on every startup."""
    p = settings.LEGAL_TEMPLATES_CONFIG_PATH
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _default_config()
    # Validate the seed itself — fail loudly at startup if we shipped bad defaults.
    cfg = LegalTemplatesConfig.model_validate(data)
    _validate_ids(cfg)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def load_legal_templates_config() -> LegalTemplatesConfig:
    p = settings.LEGAL_TEMPLATES_CONFIG_PATH
    if not p.exists():
        return LegalTemplatesConfig(templates=[])
    raw = json.loads(p.read_text(encoding="utf-8"))
    try:
        cfg = LegalTemplatesConfig.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"legal_templates.json is invalid:\n{e}") from e
    _validate_ids(cfg)
    return cfg


def load_raw_legal_templates() -> dict[str, Any]:
    p = settings.LEGAL_TEMPLATES_CONFIG_PATH
    if not p.exists():
        return {"version": 1, "templates": []}
    return json.loads(p.read_text(encoding="utf-8"))


def write_legal_templates_config(data: dict[str, Any]) -> LegalTemplatesConfig:
    cfg = LegalTemplatesConfig.model_validate(data)
    _validate_ids(cfg)
    p = settings.LEGAL_TEMPLATES_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return cfg


def get_template(template_id: str) -> LegalTemplate | None:
    for t in load_legal_templates_config().templates:
        if t.id == template_id:
            return t
    return None


def read_template_text(template_id: str) -> str:
    """Return the extracted-text contents for a catalogued template."""
    tpl = get_template(template_id)
    if tpl is None:
        raise ValueError(f"unknown legal template id: {template_id!r}")
    path = settings.LEGAL_TEMPLATES_DIR / tpl.text_file
    if not path.exists():
        raise FileNotFoundError(
            f"template {template_id!r} references missing file: {path}"
        )
    return path.read_text(encoding="utf-8")
