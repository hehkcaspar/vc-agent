"""Normalize Gemini JSON extraction output to a stable VC metadata shape (extract_info).

Also provides validate/merge helpers for the Tier 1-3 entity metadata schema
written by the extract_info agent and synced to Entity.metadata_json.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legacy one-shot normalization (kept for backward compat, dead code path now)
# ---------------------------------------------------------------------------


def normalize_extraction_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0]
    if not isinstance(result, dict):
        return _default_extraction_result()

    return {
        "company_name": result.get("company_name")
        or {"value": None, "confidence": "low"},
        "founders": result.get("founders") or [],
        "industry_tags": result.get("industry_tags") or [],
        "investment_stage": result.get("investment_stage")
        or {"value": "unknown", "confidence": "low"},
        "company_description": result.get("company_description")
        or {"value": None, "confidence": "low"},
        "company_website": result.get("company_website"),
        "funding_ask": result.get("funding_ask"),
        "referral_source": result.get("referral_source"),
        "priority_indicators": result.get("priority_indicators") or [],
        "red_flags": result.get("red_flags") or [],
        "competitors_mentioned": result.get("competitors_mentioned") or [],
    }


def _default_extraction_result() -> Dict[str, Any]:
    return {
        "company_name": {"value": None, "confidence": "low"},
        "founders": [],
        "industry_tags": [],
        "investment_stage": {"value": "unknown", "confidence": "low"},
        "company_description": {"value": None, "confidence": "low"},
        "company_website": None,
        "funding_ask": None,
        "referral_source": None,
        "priority_indicators": [],
        "red_flags": [],
        "competitors_mentioned": [],
    }


# ---------------------------------------------------------------------------
# Tier 1-3 entity metadata: validate + merge
# ---------------------------------------------------------------------------

# All known top-level keys and their default values.
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
    "prior_rounds": [],
    "existing_investors": [],
    "referral_source": None,
    # Signals
    "priority_indicators": [],
    "red_flags": [],
    "competitors": [],
    # Legal reviews (agent-populated by the legal_review preset, one entry per round)
    "legal_reviews": [],
    # Positions (user-edited via EntityEditModal — never agent-populated, but
    # listed here so validate_entity_metadata doesn't strip it on extract_info
    # runs that happen to echo the field back).
    "_positions": [],
    # Meta (set by agent)
    "_extracted_at": None,
    "_extraction_version": None,
    "_files_examined": [],
}


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

    return result, warnings


def merge_entity_metadata(
    existing: Dict[str, Any] | None,
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    """Top-level merge: incoming values win. Null/empty incoming does NOT
    overwrite existing non-null/non-empty values (allows partial updates).

    Arrays are replaced entirely when incoming is non-empty.
    Meta keys (_extracted_at, _extraction_version, _files_examined) always
    overwrite since they reflect the latest run.
    """
    if not existing:
        return dict(incoming)

    merged = dict(existing)
    meta_keys = {"_extracted_at", "_extraction_version", "_files_examined"}

    for key, new_val in incoming.items():
        if key in meta_keys:
            # Meta keys always overwrite
            merged[key] = new_val
        elif new_val is None:
            # Null incoming doesn't clobber existing
            pass
        elif isinstance(new_val, list) and len(new_val) == 0:
            # Empty list doesn't clobber existing non-empty
            pass
        else:
            merged[key] = new_val

    return merged


# ---------------------------------------------------------------------------
# Legal review: per-round validate + merge
# ---------------------------------------------------------------------------

_LEGAL_REVIEW_SCENARIOS = {"new_investment", "follow_on", "retrospective"}
_LEGAL_REVIEW_INSTRUMENTS = {"safe", "convertible_note", "priced_round"}
_LEGAL_REVIEW_SEVERITIES = {"low", "medium", "high", "critical"}


def validate_legal_reviews(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate a list of legal-review entries produced by the legal_review preset.

    Drops malformed entries (missing required keys, bad scenario/instrument values)
    and returns them as warnings instead of raising. An entry needs at minimum
    a ``round_name`` (non-empty string) to survive — everything else is
    leniently defaulted, since the agent may legitimately produce null blocks
    for non-applicable sections (e.g. ``priced_round_terms`` on a SAFE).
    """
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, list):
        raise ValueError(f"Expected list of reviews, got {type(data).__name__}")

    warnings: List[str] = []
    out: List[Dict[str, Any]] = []

    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            warnings.append(f"review[{idx}]: not a dict, dropped")
            continue
        round_name = entry.get("round_name")
        if not isinstance(round_name, str) or not round_name.strip():
            warnings.append(f"review[{idx}]: missing/empty round_name, dropped")
            continue

        # Normalise enum-ish fields without dropping the entry on a soft mismatch.
        scenario = entry.get("scenario")
        if scenario not in _LEGAL_REVIEW_SCENARIOS:
            warnings.append(
                f"review[{round_name!r}]: unknown scenario {scenario!r} — set to null"
            )
            scenario = None

        instrument = entry.get("instrument_type")
        if instrument not in _LEGAL_REVIEW_INSTRUMENTS and instrument is not None:
            warnings.append(
                f"review[{round_name!r}]: unknown instrument_type {instrument!r} — set to null"
            )
            instrument = None

        normalised = dict(entry)
        normalised["round_name"] = round_name.strip()
        normalised["scenario"] = scenario
        normalised["instrument_type"] = instrument

        # documents_reviewed is rebuilt server-side from status_trace; start clean.
        normalised.setdefault("documents_reviewed", [])
        # review_date is always overwritten server-side; leave whatever the agent
        # produced (or empty string) in place — the post-processor clobbers it.
        normalised.setdefault("review_date", "")
        normalised.setdefault("reference_templates_consulted", [])
        normalised.setdefault("unusual_terms", [])
        normalised.setdefault("red_flags", [])
        normalised.setdefault("priority_indicators", [])
        normalised.setdefault("killer_questions", [])
        normalised.setdefault("narrative_summary", None)
        normalised.setdefault("our_position", None)

        # Red-flag severities: normalise to the known set (drop unknowns with warning).
        rfs = normalised.get("red_flags") or []
        cleaned_rfs: List[Dict[str, Any]] = []
        for rf in rfs:
            if not isinstance(rf, dict):
                continue
            sev = rf.get("severity")
            if sev not in _LEGAL_REVIEW_SEVERITIES:
                warnings.append(
                    f"review[{round_name!r}]: red_flag severity {sev!r} unknown — set to 'medium'"
                )
                rf = dict(rf)
                rf["severity"] = "medium"
            cleaned_rfs.append(rf)
        normalised["red_flags"] = cleaned_rfs

        out.append(normalised)

    return out, warnings


def merge_legal_reviews(
    existing: List[Dict[str, Any]] | None,
    incoming: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge a new batch of legal_reviews into the existing array, keyed by round_name.

    Entries in *incoming* replace entries in *existing* with the same round_name.
    Other existing entries are preserved verbatim. Order: incoming entries appear
    first (matching agent output order), then any unaffected prior rounds.

    When *incoming* itself contains duplicates for the same round_name (agent
    emitted the same round twice), the LAST occurrence wins — consistent with
    standard merge semantics where later writes overwrite earlier ones.
    """
    # Dedupe within incoming first (last-wins).
    deduped: List[Dict[str, Any]] = []
    seen_rounds: set[str] = set()
    for entry in reversed(list(incoming)):
        if not isinstance(entry, dict):
            continue
        name = entry.get("round_name")
        if not name or name in seen_rounds:
            continue
        seen_rounds.add(name)
        deduped.append(entry)
    deduped.reverse()  # restore original order

    if not existing:
        return deduped
    preserved = [
        r for r in existing
        if isinstance(r, dict) and r.get("round_name") not in seen_rounds
    ]
    return deduped + preserved
