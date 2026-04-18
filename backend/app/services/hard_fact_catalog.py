"""Hard-fact catalog — which metadata paths are *canonical, provenance-tracked*.

Hard facts go through ``fact_manager.record_fact``: append to ``_ledger[]``
(append-only history) + write the current value to the flat metadata field
(so the existing Facts tab / header read path keeps working unchanged).

Soft claims (one_liner, description, industry_tags, priority_indicators,
red_flags, competitors, etc.) skip the ledger entirely — they are per-run
opinions, written directly by the preset that produced them.

Distinction is by *field*, not by source. A deck and LinkedIn both describe
hard facts (founder title) with different evidence tiers; both flow through
the ledger. A deck also describes soft claims (market size) which never do.

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

from typing import List, Tuple

from app.services.fact_discrepancies import _parse_field_path


# ---------------------------------------------------------------------------
# Hard-fact paths
# ---------------------------------------------------------------------------
# Exact-match patterns: ``founders[name=*].title`` means "any founder's title".
# The ``*`` wildcard only applies inside array selectors (``[key=*]``); leaves
# must be named explicitly.

HARD_FACT_PATTERNS: List[str] = [
    # Identity
    "website",
    "company_name",
    "legal_name",
    "incorporation_jurisdiction",
    "incorporation_entity_type",
    "founded_date",
    "hq_location",

    # Founders — only verifiable fields (name, title, linkedin_url).
    # `background` is narrative → stays soft.
    "founders[name=*].name",
    "founders[name=*].title",
    "founders[name=*].linkedin_url",

    # Key team — same shape as founders.
    "key_team[name=*].name",
    "key_team[name=*].title",
    "key_team[name=*].linkedin_url",

    # Current raise (headline deal terms)
    "investment_stage",
    "raise_amount",
    "raise_currency",
    "raise_instrument",
    "valuation_cap",
    "pre_money_valuation",

    # Round attribution
    "referral_source",
]


# Prefix patterns: any path starting with one of these prefixes is hard.
# Used for nested term blocks where leaves vary (SAFE terms, priced-round
# terms, governance provisions, etc.).

HARD_FACT_PREFIXES: List[str] = [
    # Existing investors (list of investor names — all hard)
    "existing_investors",
    # Prior rounds: everything under a round entry is hard (terms, investors,
    # amount, date, governance, investor_rights, transfer_restrictions, etc.)
    "prior_rounds[round_name=*]",
    # Our positions: amount, valuation, date, fund_id, round_at_entry, etc.
    "_positions[fund_id=*]",
]


# ---------------------------------------------------------------------------
# Evidence tiers (for contradiction resolution)
# ---------------------------------------------------------------------------
# Higher = more authoritative. Used by fact_manager.detect_contradiction to
# decide whether a new fact should overwrite existing (stronger tier) or be
# surfaced for user adjudication (weaker or equal tier).

EVIDENCE_TIERS: dict[str, int] = {
    "cap_table":     100,
    "legal_doc":      90,
    "user":           85,
    "upload":         70,
    "third_party":    60,
    "communication":  50,
    "web":            40,
    "self_claim":     20,
}


def evidence_tier(source_type: str) -> int:
    """Return the tier rank for a source type (unknown types treated as weak)."""
    return EVIDENCE_TIERS.get(source_type, 30)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _tokenize_wildcarded(pattern: str) -> List[Tuple[str, Tuple[str, str] | None]]:
    """Tokenize a pattern that may contain ``[key=*]`` wildcards.

    Reuses ``fact_discrepancies._parse_field_path`` — ``*`` is just a literal
    selector value at parse time; we interpret it as a wildcard in
    :func:`_segments_match`.
    """
    return _parse_field_path(pattern)


def _segments_match(
    pattern_segs: List[Tuple[str, Tuple[str, str] | None]],
    actual_segs: List[Tuple[str, Tuple[str, str] | None]],
) -> bool:
    """Do pattern and actual segments match, treating ``*`` as a wildcard in
    selector values?"""
    if len(pattern_segs) != len(actual_segs):
        return False
    for (p_name, p_sel), (a_name, a_sel) in zip(pattern_segs, actual_segs):
        if p_name != a_name:
            return False
        if p_sel is None and a_sel is None:
            continue
        if p_sel is None or a_sel is None:
            return False
        p_key, p_val = p_sel
        a_key, a_val = a_sel
        if p_key != a_key:
            return False
        if p_val == "*":
            continue  # wildcard match
        if p_val != a_val:
            return False
    return True


def is_hard_fact(fact_path: str) -> bool:
    """Return True iff ``fact_path`` is a canonical hard-fact target.

    A path matches if either:
    - it exactly matches a pattern in ``HARD_FACT_PATTERNS`` (with ``*``
      wildcards on array selectors), OR
    - it starts with any prefix in ``HARD_FACT_PREFIXES`` (same wildcard
      semantics; the prefix's own segments must match the actual path's
      leading segments).

    Malformed paths return False without raising.
    """
    try:
        actual = _parse_field_path(fact_path)
    except ValueError:
        return False

    for pattern in HARD_FACT_PATTERNS:
        try:
            pattern_segs = _tokenize_wildcarded(pattern)
        except ValueError:
            continue
        if _segments_match(pattern_segs, actual):
            return True

    for prefix in HARD_FACT_PREFIXES:
        try:
            prefix_segs = _tokenize_wildcarded(prefix)
        except ValueError:
            continue
        if len(actual) < len(prefix_segs):
            continue
        if _segments_match(prefix_segs, actual[: len(prefix_segs)]):
            return True

    return False
