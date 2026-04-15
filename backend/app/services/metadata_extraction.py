"""Normalize extract_info JSON output + validate/merge Entity.metadata_json.

Facts only — per the Facts vs Opinions split. Signals (priority_indicators,
red_flags, competitors) split out to Deliverables/Analysis/extract_info_signals.json
via ``services.extract_info_signals``. Legal-review round-term facts lift to
``prior_rounds[]`` via ``services.legal_review_facts``; opinions stay in
``Legal Review.json``.

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier 1-3 entity metadata: validate + merge
# ---------------------------------------------------------------------------

# All known top-level keys and their default values. FACTS ONLY.
# Signals/opinions moved to workspace artifacts; see module docstring.
_ENTITY_METADATA_DEFAULTS: Dict[str, Any] = {
    # Tier 1 — Identity
    "company_name": None,
    "legal_name": None,
    "one_liner": None,
    "description": None,
    "industry_tags": [],
    "business_model": None,
    "hq_location": None,
    "website": None,
    "founded_date": None,
    "incorporation_jurisdiction": None,
    "incorporation_entity_type": None,
    # Tier 2 — Team
    "founders": [],
    "team_size": None,
    "key_team": [],
    # Tier 3 — Deal & funding
    "investment_stage": None,
    "raise_amount": None,
    "raise_currency": None,
    "raise_instrument": None,
    "valuation_cap": None,
    "pre_money_valuation": None,
    # prior_rounds[] is a per-round fact bag. Each entry carries round-level
    # facts (terms, governance, rights). extract_info writes shallow rows
    # (round_name, amount, date, lead_investor); legal_review deep-merges the
    # detailed term blocks by round_name. See legal_review_facts.py.
    "prior_rounds": [],
    "current_round_name": None,
    "existing_investors": [],
    "referral_source": None,
    # Positions (user-edited via EntityEditModal — never agent-populated, but
    # listed here so validate_entity_metadata doesn't strip it).
    "_positions": [],
    # Fact discrepancies — agent-surfaced, user-adjudicated. Never written by
    # validate/merge; mutated only via services.fact_discrepancies.
    "_fact_discrepancies": [],
    # Meta (server-set each run)
    "_extracted_at": None,
    "_extraction_version": None,
    "_files_examined": [],
}

# Keys that are "user-managed" or "system-managed" — preserved verbatim from
# existing on merge, never clobbered by agent output even when non-empty.
_PRESERVED_ON_MERGE: set = {"_positions", "_fact_discrepancies"}


def _migrate_prior_round_entry(entry: Any) -> Dict[str, Any]:
    """Normalise a single prior_rounds[] entry to the per-round fact-bag shape.

    Accepts legacy short shape ``{round, amount, date, lead_investor}`` and
    returns the full fact bag with empty term blocks. New entries are
    returned as-is (with missing keys defaulted).
    """
    if not isinstance(entry, dict):
        return {"round_name": str(entry) if entry else None}

    # Legacy short shape has `round` rather than `round_name`.
    round_name = entry.get("round_name")
    if round_name is None and entry.get("round"):
        round_name = entry.get("round")

    # Legacy short shape has `date` rather than `effective_date`.
    effective_date = entry.get("effective_date") or entry.get("date")

    out: Dict[str, Any] = {
        "round_name": round_name,
        "instrument_type": entry.get("instrument_type"),
        "scenario": entry.get("scenario"),
        "effective_date": effective_date,
        "amount": entry.get("amount"),
        "currency": entry.get("currency"),
        "lead_investor": entry.get("lead_investor"),
        "company_terms": entry.get("company_terms") or {},
        "safe_terms": entry.get("safe_terms"),
        "priced_round_terms": entry.get("priced_round_terms"),
        "governance": entry.get("governance") or {},
        "investor_rights": entry.get("investor_rights") or {},
        "transfer_restrictions": entry.get("transfer_restrictions") or {},
        "regulatory": entry.get("regulatory") or {},
        "our_position": entry.get("our_position"),
    }
    return out


def validate_entity_metadata(data: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Validate shape of agent-produced entity metadata.

    Returns (validated_dict, warnings).  Raises ValueError if data is
    fundamentally unusable (not a dict).

    Note: meta fields (_extracted_at, _files_examined) are overwritten by
    the caller with trusted server-side values — the agent's values are
    informational only.
    """
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    warnings: List[str] = []

    # Strip unknown keys — retain only the canonical schema + comment keys
    # (comment keys are used by the prompt for schema documentation only).
    known = set(_ENTITY_METADATA_DEFAULTS)
    extra_keys = {
        k for k in data
        if k not in known and not k.startswith("_comment_")
    }
    if extra_keys:
        warnings.append(f"Unknown keys ignored: {sorted(extra_keys)}")

    result: Dict[str, Any] = {}
    for key, default in _ENTITY_METADATA_DEFAULTS.items():
        result[key] = data.get(key, default)

    # Normalize _files_examined: accept either ["path"] or [{"path": "..."}] —
    # post-processing rebuilds this anyway, but be tolerant.
    fe = result.get("_files_examined") or []
    if isinstance(fe, list):
        normalized_fe: List[str] = []
        for item in fe:
            if isinstance(item, str):
                normalized_fe.append(item)
            elif isinstance(item, dict) and item.get("path"):
                normalized_fe.append(str(item["path"]))
        result["_files_examined"] = normalized_fe
    else:
        result["_files_examined"] = []

    # Migrate prior_rounds[] legacy short shape → per-round fact bag.
    pr = result.get("prior_rounds") or []
    if isinstance(pr, list):
        result["prior_rounds"] = [_migrate_prior_round_entry(e) for e in pr]
    else:
        result["prior_rounds"] = []

    # _fact_discrepancies is agent-hostile territory — agents must never write
    # here. If the incoming payload carries entries (old format, hallucinated),
    # drop them silently. The lifecycle API in services.fact_discrepancies is
    # the only sanctioned writer.
    result["_fact_discrepancies"] = []

    return result, warnings


def merge_entity_metadata(
    existing: Dict[str, Any] | None,
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    """Top-level merge: incoming values win. Null/empty incoming does NOT
    overwrite existing non-null/non-empty values (allows partial updates).

    Special cases:
    - ``_extracted_at`` / ``_extraction_version`` / ``_files_examined`` always
      overwrite (latest run wins)
    - ``_positions`` / ``_fact_discrepancies`` are preserved from existing
      (user-managed / lifecycle-managed — agent output must not clobber)
    - ``prior_rounds[]`` deep-merges by ``round_name`` via
      ``legal_review_facts.merge_prior_round_facts`` so extract_info's shallow
      rows don't erase legal_review's deep term blocks
    """
    if not existing:
        # Honour preservation semantics even on first-write: preserved keys
        # stay empty (they'd have no existing to preserve from anyway).
        merged = dict(incoming)
        for key in _PRESERVED_ON_MERGE:
            merged.setdefault(key, [])
        return merged

    merged = dict(existing)
    meta_keys = {"_extracted_at", "_extraction_version", "_files_examined"}

    for key, new_val in incoming.items():
        if key in _PRESERVED_ON_MERGE:
            # Never let agent output touch user/lifecycle-managed keys
            continue
        if key in meta_keys:
            merged[key] = new_val
        elif key == "prior_rounds" and isinstance(new_val, list):
            # Deep-merge by round_name
            from app.services.legal_review_facts import merge_prior_round_facts
            existing_pr = existing.get("prior_rounds") or []
            merged["prior_rounds"] = merge_prior_round_facts(existing_pr, new_val)
        elif new_val is None:
            pass
        elif isinstance(new_val, list) and len(new_val) == 0:
            pass
        else:
            merged[key] = new_val

    return merged
