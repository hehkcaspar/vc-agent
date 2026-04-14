"""Loader + atomic writer + startup seed for `data/config/legal_review_checklist.json`.

Tier R2 of the legal-review reference system: a distilled, structured rubric
synthesised from internal checklists (3EO / Cyberdontics / InchFab xlsx files)
+ 2025-2026 VC term-sheet research. Unlike the Tier R1 raw-template catalog,
this file IS the knowledge: it captures WHAT to review, WHY it matters,
STANDARD values, and RED-FLAG patterns. Injected in full into the
legal_review prompt via `render_legal_review`.

User-tunable post-launch — edit the JSON and re-run the preset. No code deploy.
Mirrors the config-backed pattern used by `funds_config.py` +
`services/academic/continuous_config.py`.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings


_ID_RE = re.compile(r"^[a-z0-9_]+$")

Severity = Literal["low", "medium", "high", "critical"]
Instrument = Literal["safe", "convertible_note", "priced_round"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RedFlagPattern(_Strict):
    pattern: str = Field(min_length=1, max_length=200)
    severity: Severity
    note: str | None = Field(default=None, max_length=500)


class ScenarioFocus(_Strict):
    new_investment: str | None = Field(default=None, max_length=500)
    follow_on: str | None = Field(default=None, max_length=500)
    retrospective: str | None = Field(default=None, max_length=500)


class ChecklistItem(_Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=600)
    applies_to_instruments: list[Instrument] = Field(default_factory=list)
    standard_value: str | None = Field(default=None, max_length=400)
    red_flag_patterns: list[RedFlagPattern] = Field(default_factory=list)
    why_matters: str | None = Field(default=None, max_length=600)
    scenario_focus: ScenarioFocus | None = None


class ChecklistCategory(_Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=600)
    items: list[ChecklistItem] = Field(default_factory=list)


class LegalReviewChecklist(_Strict):
    version: int = 1
    updated_at: str | None = None
    categories: list[ChecklistCategory] = Field(default_factory=list)


def _validate_ids(cfg: LegalReviewChecklist) -> None:
    cat_ids: set[str] = set()
    for cat in cfg.categories:
        if not _ID_RE.match(cat.id):
            raise ValueError(f"category id {cat.id!r} must be snake_case [a-z0-9_]+")
        if cat.id in cat_ids:
            raise ValueError(f"duplicate category id {cat.id!r}")
        cat_ids.add(cat.id)
        item_ids: set[str] = set()
        for item in cat.items:
            if not _ID_RE.match(item.id):
                raise ValueError(
                    f"item id {item.id!r} in category {cat.id!r} "
                    "must be snake_case [a-z0-9_]+"
                )
            if item.id in item_ids:
                raise ValueError(
                    f"duplicate item id {item.id!r} in category {cat.id!r}"
                )
            item_ids.add(item.id)


def _default_checklist() -> dict[str, Any]:
    """Seed checklist synthesised from internal xlsx + 2025-2026 VC term-sheet norms."""
    return {
        "version": 1,
        "categories": [
            {
                "id": "economic_terms",
                "label": "Economic terms",
                "description": (
                    "Financial rights attaching to the preferred stock — "
                    "liquidation, anti-dilution, dividends, pay-to-play."
                ),
                "items": [
                    {
                        "id": "liquidation_preference_multiple",
                        "label": "Liquidation preference multiple",
                        "description": (
                            "Multiplier applied to investor's capital before "
                            "any distribution to common stock."
                        ),
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "1x",
                        "red_flag_patterns": [
                            {"pattern": "multiple > 1x", "severity": "high",
                             "note": "2x+ is aggressive at Series A; ~30% of deals"},
                            {"pattern": "multiple > 2x", "severity": "critical",
                             "note": "Highly investor-favored; rare outside distressed rounds"},
                        ],
                        "why_matters": (
                            "Most impactful term in downside / moderate-outcome "
                            "scenarios. Directly determines payout ordering."
                        ),
                        "scenario_focus": {
                            "new_investment": "Primary negotiation item — push back on anything above 1x",
                            "follow_on": "Check whether this round's pref stacks senior to ours (cascade risk)",
                            "retrospective": "Review cap-table stack; confirm our seniority",
                        },
                    },
                    {
                        "id": "liquidation_participating",
                        "label": "Participating vs non-participating liquidation",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "non-participating",
                        "red_flag_patterns": [
                            {"pattern": "participating (uncapped)", "severity": "high",
                             "note": "Double-dip; rare at Series A (~20% of deals)"},
                            {"pattern": "participating with cap", "severity": "medium",
                             "note": "Less punitive if cap is reasonable (~2-3x)"},
                        ],
                        "why_matters": (
                            "Participating = investor gets preference AND pro-rata "
                            "share of remaining proceeds. Non-participating = choice "
                            "of preference OR pro-rata."
                        ),
                        "scenario_focus": {
                            "new_investment": "Standard push for non-participating",
                            "follow_on": "Verify no clawback of prior non-participating terms",
                            "retrospective": "Flag if terms changed vs prior round",
                        },
                    },
                    {
                        "id": "anti_dilution_type",
                        "label": "Anti-dilution formula",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "broad-based weighted average",
                        "red_flag_patterns": [
                            {"pattern": "full ratchet", "severity": "critical",
                             "note": "Punitive to founders; rare outside crisis rounds"},
                            {"pattern": "narrow-based weighted average", "severity": "medium",
                             "note": "Less common; slightly founder-unfriendly"},
                        ],
                        "why_matters": (
                            "Protects investor from dilution in a down round. "
                            "Broad-based weighted average is the industry norm "
                            "and balanced; full ratchet is punitive."
                        ),
                    },
                    {
                        "id": "dividend",
                        "label": "Dividend structure",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "non-cumulative, paid if declared",
                        "red_flag_patterns": [
                            {"pattern": "cumulative", "severity": "high",
                             "note": "Dividends accrue even if not paid — can stack up meaningfully over years"},
                            {"pattern": "PIK (payment-in-kind)", "severity": "high",
                             "note": "Dividends convert to additional preferred shares — silent dilution"},
                        ],
                    },
                    {
                        "id": "pay_to_play",
                        "label": "Pay-to-play provisions",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "absent, or mild (conversion to common only)",
                        "red_flag_patterns": [
                            {"pattern": "punitive conversion to common for non-participating investors",
                             "severity": "medium",
                             "note": "Context-dependent — OK in protective recap rounds, concerning otherwise"},
                            {"pattern": "forced conversion + loss of anti-dilution",
                             "severity": "high",
                             "note": "Stacks multiple penalties; disproportionate"},
                        ],
                        "why_matters": (
                            "Pressures existing investors to participate in follow-on "
                            "rounds or lose rights. Can be fair in recap scenarios, "
                            "predatory in good-faith rounds."
                        ),
                    },
                ],
            },
            {
                "id": "governance",
                "label": "Governance & control",
                "description": "Board composition, protective provisions, voting thresholds.",
                "items": [
                    {
                        "id": "board_composition",
                        "label": "Board composition",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": (
                            "1 investor + founders + 1 independent (typical 5-seat Series A)"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "investor majority at Series A", "severity": "critical",
                             "note": "Founders lose control unusually early"},
                            {"pattern": "independent seat designated by investor",
                             "severity": "medium",
                             "note": "Effectively gives investor a second de-facto vote"},
                            {"pattern": "no founder seat for CEO-founder",
                             "severity": "high",
                             "note": "Unusual — typical terms guarantee the CEO a seat"},
                        ],
                    },
                    {
                        "id": "protective_provisions",
                        "label": "Protective provisions (investor veto rights)",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": (
                            "Limited to corporate actions — authorize/issue senior "
                            "securities, amend COI, M&A, dissolution, change class rights"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "consent required on hiring/firing",
                             "severity": "critical",
                             "note": "Operational control transferred to investor"},
                            {"pattern": "consent required on annual budget",
                             "severity": "high",
                             "note": "Operational micromanagement"},
                            {"pattern": "consent required on expenditures below $500K",
                             "severity": "medium",
                             "note": "Day-to-day encumbrance"},
                            {"pattern": "consent required on any debt issuance",
                             "severity": "medium",
                             "note": "Overly broad — typical carve-out above a threshold (e.g. $250K)"},
                        ],
                    },
                    {
                        "id": "major_investor_threshold",
                        "label": "Major Investor threshold",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": (
                            "~1% FD ownership OR $X tied to the lead investor's check size"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "threshold higher than our stake",
                             "severity": "high",
                             "note": "We lose Major Investor rights (info / pro-rata / ROFR)"},
                            {"pattern": "threshold > 2% FD",
                             "severity": "medium",
                             "note": "Tightens Major Investor tier; small investors lose rights"},
                        ],
                        "why_matters": (
                            "Many investor rights are gated on Major Investor status. "
                            "Threshold calibration determines who gets info / pro-rata / ROFR."
                        ),
                    },
                    {
                        "id": "drag_along_threshold",
                        "label": "Drag-along threshold",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": (
                            "majority preferred + majority common (double-majority)"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "threshold <50% total",
                             "severity": "high",
                             "note": "Forced exit on minority vote"},
                            {"pattern": "preferred-only drag",
                             "severity": "medium",
                             "note": "Common stockholders have no say in forced exit"},
                            {"pattern": "single-investor drag",
                             "severity": "critical",
                             "note": "One investor can force sale"},
                        ],
                    },
                ],
            },
            {
                "id": "investor_rights",
                "label": "Investor rights",
                "description": "Information, pro-rata, registration, ROFR, co-sale.",
                "items": [
                    {
                        "id": "information_rights",
                        "label": "Information rights (financial + inspection)",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "Major Investor tier (monthly/quarterly financials + annual audit + inspection)",
                        "red_flag_patterns": [
                            {"pattern": "no information rights", "severity": "critical"},
                            {"pattern": "our stake below Major Investor threshold", "severity": "high",
                             "note": "We lose financial / inspection rights this round"},
                        ],
                    },
                    {
                        "id": "pro_rata",
                        "label": "Pro-rata / participation right",
                        "applies_to_instruments": ["priced_round", "safe"],
                        "standard_value": (
                            "Major Investor tier (priced) / via Pro-Rata Side Letter (YC SAFE)"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "pro-rata absent",
                             "severity": "high",
                             "note": "Lose ability to maintain ownership in future rounds"},
                            {"pattern": "super pro-rata for lead investor",
                             "severity": "medium",
                             "note": "Lead takes outsized share in hot follow-ons; dilutes us"},
                        ],
                        "why_matters": (
                            "For existing investors, pro-rata is the single most "
                            "important right for maintaining ownership through follow-ons."
                        ),
                    },
                    {
                        "id": "rofr",
                        "label": "Right of first refusal (on company-issued stock)",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "standard NVCA",
                    },
                    {
                        "id": "rofr_on_founder_shares",
                        "label": "ROFR on founder secondary sales",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "company first, then preferred holders pro-rata (NVCA default)",
                    },
                    {
                        "id": "rofo",
                        "label": "Right of first offer",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "standard NVCA",
                    },
                    {
                        "id": "co_sale",
                        "label": "Co-sale / tag-along on founder sales",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "standard NVCA",
                    },
                    {
                        "id": "registration_rights",
                        "label": "Registration rights (IPO)",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "demand + piggyback + S-3 (NVCA default)",
                    },
                ],
            },
            {
                "id": "transfer_restrictions",
                "label": "Transfer restrictions & vesting",
                "items": [
                    {
                        "id": "founder_vesting",
                        "label": "Founder vesting",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": (
                            "4-year, 1-year cliff; double-trigger acceleration on change of control"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "founders fully vested without refresh",
                             "severity": "high",
                             "note": "No retention hook — unusual at Series A unless long-running company"},
                            {"pattern": "single-trigger acceleration",
                             "severity": "medium",
                             "note": "Can complicate M&A"},
                            {"pattern": "no vesting",
                             "severity": "critical",
                             "note": "Major founder retention risk"},
                        ],
                    },
                    {
                        "id": "employee_vesting",
                        "label": "Employee vesting",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "4-year, 1-year cliff",
                    },
                    {
                        "id": "market_standoff",
                        "label": "Market standoff / IPO lock-up",
                        "applies_to_instruments": ["priced_round"],
                        "standard_value": "180 days post-IPO",
                    },
                ],
            },
            {
                "id": "safe_specific",
                "label": "SAFE-specific terms",
                "description": "Terms unique to SAFE / convertible-note instruments.",
                "items": [
                    {
                        "id": "valuation_cap",
                        "label": "Valuation cap",
                        "applies_to_instruments": ["safe", "convertible_note"],
                        "why_matters": (
                            "Conversion floor — lower cap = more shares per dollar for investor"
                        ),
                    },
                    {
                        "id": "discount_rate",
                        "label": "Discount rate",
                        "applies_to_instruments": ["safe", "convertible_note"],
                        "standard_value": "10-25%",
                        "red_flag_patterns": [
                            {"pattern": "discount > 30%", "severity": "medium",
                             "note": "Unusually generous"},
                            {"pattern": "discount < 10%", "severity": "low",
                             "note": "Token discount"},
                        ],
                    },
                    {
                        "id": "mfn",
                        "label": "Most-favored-nation clause",
                        "applies_to_instruments": ["safe", "convertible_note"],
                        "why_matters": (
                            "Auto-upgrades our terms if later investors negotiate better terms. "
                            "Asymmetric protection for earliest SAFE holders."
                        ),
                    },
                    {
                        "id": "pro_rata_side_letter",
                        "label": "Pro-rata side letter (for YC post-money SAFE)",
                        "applies_to_instruments": ["safe"],
                        "standard_value": (
                            "Separate side letter — 67% of 2024 SAFEs include pro-rata "
                            "(up from 23% in 2020)"
                        ),
                        "why_matters": (
                            "Not included in the default YC SAFE. Critical for maintaining "
                            "ownership through priced rounds."
                        ),
                    },
                    {
                        "id": "conversion_trigger",
                        "label": "Conversion trigger (qualified financing threshold)",
                        "applies_to_instruments": ["safe", "convertible_note"],
                        "standard_value": "priced equity financing ≥ $1M (YC default)",
                        "red_flag_patterns": [
                            {"pattern": "threshold > $5M", "severity": "medium",
                             "note": "SAFE may not convert in small rounds — stranded"},
                        ],
                    },
                ],
            },
            {
                "id": "regulatory_compliance",
                "label": "Regulatory & compliance",
                "items": [
                    {
                        "id": "cfius_status",
                        "label": "CFIUS foreign-person status",
                        "why_matters": (
                            "TID U.S. businesses (critical tech / sensitive data / "
                            "critical infrastructure) trigger CFIUS review on foreign investor"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "foreign investor + TID business",
                             "severity": "high",
                             "note": "May block deal or require CFIUS filing"},
                            {"pattern": "no CFIUS representation",
                             "severity": "medium",
                             "note": "Standard NVCA docs include a CFIUS rep — check why it's missing"},
                        ],
                    },
                    {
                        "id": "ip_assignment",
                        "label": "IP assignment (founders + employees)",
                        "standard_value": (
                            "All IP assigned to company; no prior-IP carve-outs without disclosure"
                        ),
                        "red_flag_patterns": [
                            {"pattern": "broad prior-IP carve-outs", "severity": "high"},
                            {"pattern": "missing assignment for key contributor",
                             "severity": "critical",
                             "note": "IP risk — investor may require rep before closing"},
                        ],
                    },
                    {
                        "id": "indemnification",
                        "label": "D&O indemnification",
                        "standard_value": (
                            "NVCA Indemnification Agreement + COI/bylaws D&O coverage + D&O insurance"
                        ),
                    },
                ],
            },
        ],
    }


def ensure_legal_review_checklist_seed() -> None:
    """Write the default checklist if the config file is missing."""
    p = settings.LEGAL_REVIEW_CHECKLIST_CONFIG_PATH
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _default_checklist()
    # Validate the seed itself — fail loudly at startup if we shipped bad defaults.
    cfg = LegalReviewChecklist.model_validate(data)
    _validate_ids(cfg)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def load_legal_review_checklist() -> LegalReviewChecklist:
    p = settings.LEGAL_REVIEW_CHECKLIST_CONFIG_PATH
    if not p.exists():
        return LegalReviewChecklist(categories=[])
    raw = json.loads(p.read_text(encoding="utf-8"))
    try:
        cfg = LegalReviewChecklist.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"legal_review_checklist.json is invalid:\n{e}") from e
    _validate_ids(cfg)
    return cfg


def load_raw_legal_review_checklist() -> dict[str, Any]:
    p = settings.LEGAL_REVIEW_CHECKLIST_CONFIG_PATH
    if not p.exists():
        return {"version": 1, "categories": []}
    return json.loads(p.read_text(encoding="utf-8"))


def write_legal_review_checklist(data: dict[str, Any]) -> LegalReviewChecklist:
    cfg = LegalReviewChecklist.model_validate(data)
    _validate_ids(cfg)
    p = settings.LEGAL_REVIEW_CHECKLIST_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return cfg
