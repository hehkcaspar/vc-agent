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

from app.services.url_validation import is_canonical_linkedin_url

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

    # Format-check LinkedIn URLs in founders[] + key_team[]. Off-pattern URLs
    # (linkedin.com/pub/..., shortened links, non-LinkedIn domains, OCR
    # garbage) get nulled now so the Facts UI never renders a broken icon.
    # The full HEAD-check runs post-merge in chat.py (async).
    def _normalize_linkedin_in(rows_key: str) -> None:
        rows = result.get(rows_key) or []
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("linkedin_url")
            if raw is None or not isinstance(raw, str) or not raw.strip():
                continue
            if not is_canonical_linkedin_url(raw):
                row["linkedin_url"] = None
                # Stash the original for debugging without rendering it.
                row["_linkedin_url_invalid"] = raw.strip()
                warnings.append(
                    f"Non-canonical LinkedIn URL nulled in {rows_key}: {raw[:80]}"
                )

    _normalize_linkedin_in("founders")
    _normalize_linkedin_in("key_team")

    return result, warnings


async def head_validate_linkedin_urls(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Async HEAD-check on canonical LinkedIn URLs in founders[] + key_team[].

    Format-check happens in ``validate_entity_metadata`` (sync). This second
    pass runs post-merge to catch URLs that match the pattern but 404 on the
    LinkedIn server. LinkedIn's bot wall returns 999 / 403 for unauthenticated
    callers — those count as "format ok, server-side content unverifiable"
    and the URL stays intact (the human user lands on the auth wall, then
    the real profile).

    Mutates metadata in place; also returns it for chaining. Failures during
    HEAD are conservative: keep the URL, set ``_linkedin_status="unverified"``.
    """
    import httpx

    async def _check_one(url: str) -> str:
        """Return one of: format_ok | broken | unverified."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(4.0),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; vc-agent-link-check/1.0)"},
            ) as client:
                resp = await client.head(url)
                status = resp.status_code
                if status in (200, 301, 302, 401, 403, 405, 999):
                    return "format_ok"
                if 400 <= status < 500:
                    return "broken"
                return "unverified"
        except Exception:  # noqa: BLE001
            return "unverified"

    for rows_key in ("founders", "key_team"):
        rows = metadata.get(rows_key) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = row.get("linkedin_url")
            if not isinstance(url, str) or not url.strip():
                continue
            label = await _check_one(url.strip())
            if label == "broken":
                row["_linkedin_url_invalid"] = url.strip()
                row["linkedin_url"] = None
            row["_linkedin_status"] = label
    return metadata


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
