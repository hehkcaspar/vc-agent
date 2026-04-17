"""Legal-review splitter: separate per-round facts from opinions.

The legal_review preset emits a combined per-round payload today. Post-processing
splits each entry into:

- **Fact block** — round terms, governance, rights, our_position. Lifts into
  ``Entity.metadata_json.prior_rounds[]`` keyed by ``round_name``. Deep-merged
  with extract_info's shallow round rows.
- **Opinion block** — ``unusual_terms``, ``red_flags``, ``priority_indicators``,
  ``killer_questions``, ``narrative_summary`` + run-metadata (``review_date``,
  ``documents_reviewed``, ``reference_templates_consulted``,
  ``checklist_version``). Stays in ``Legal Review.json`` at workspace root.

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

_LEGAL_REVIEW_SCENARIOS = {"new_investment", "follow_on", "retrospective"}
_LEGAL_REVIEW_INSTRUMENTS = {"safe", "convertible_note", "priced_round"}
_LEGAL_REVIEW_SEVERITIES = {"low", "medium", "high", "critical"}

# Per-entry opinion fields that stay in the workspace Legal Review.json
# (post split). These are agent assessments + run metadata — never synced to
# Entity.metadata_json (which is facts-only; see FACTS_VS_OPINIONS.md).
_OPINION_FIELDS = (
    "review_date",
    "documents_reviewed",
    "reference_templates_consulted",
    "checklist_version",
    "unusual_terms",
    "red_flags",
    "priority_indicators",
    "killer_questions",
    "narrative_summary",
)

# Per-entry fact fields that lift into metadata_json.prior_rounds[round_name=…]
# via merge_prior_round_facts. our_position is also a fact bag (our shares +
# amount + rights changes) and rides along.
_FACT_NESTED = (
    "company_terms",
    "safe_terms",
    "convertible_note_terms",
    "priced_round_terms",
    "governance",
    "investor_rights",
    "transfer_restrictions",
    "regulatory",
)


# ---------------------------------------------------------------------------
# Validation — combined agent output
# ---------------------------------------------------------------------------


def validate_legal_reviews(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate the agent's combined legal_reviews[] output (fact + opinion fields).

    Drops malformed entries (missing required keys, bad scenario/instrument values)
    and returns them as warnings instead of raising. An entry needs at minimum
    a ``round_name`` (non-empty string) to survive — everything else is leniently
    defaulted, since the agent may legitimately produce null blocks for
    non-applicable sections (e.g. ``priced_round_terms`` on a SAFE).

    Returns the normalised combined entries; callers then call
    :func:`split_legal_review_entry` on each to separate facts from opinions.
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

        scenario = entry.get("scenario")
        if scenario not in _LEGAL_REVIEW_SCENARIOS:
            warnings.append(
                f"review[{round_name!r}]: unknown scenario {scenario!r} — set to null"
            )
            scenario = None

        instrument = entry.get("instrument_type")
        if instrument not in _LEGAL_REVIEW_INSTRUMENTS and instrument is not None:
            warnings.append(
                f"review[{round_name!r}]: unknown instrument_type "
                f"{instrument!r} — set to null"
            )
            instrument = None

        normalised = dict(entry)
        # New prompt nests fact blocks under `proposed_facts`; lift them up to
        # the entry root so the downstream splitter is shape-agnostic.
        pf = normalised.pop("proposed_facts", None)
        if isinstance(pf, dict):
            for k in _FACT_NESTED + ("our_position",):
                if pf.get(k) is not None and normalised.get(k) is None:
                    normalised[k] = pf[k]

        normalised["round_name"] = round_name.strip()
        normalised["scenario"] = scenario
        normalised["instrument_type"] = instrument

        # Server-overwritten keys: start clean (post-processor clobbers anyway).
        normalised.setdefault("documents_reviewed", [])
        normalised.setdefault("review_date", "")
        normalised.setdefault("reference_templates_consulted", [])
        normalised.setdefault("unusual_terms", [])
        normalised.setdefault("red_flags", [])
        normalised.setdefault("priority_indicators", [])
        normalised.setdefault("killer_questions", [])
        normalised.setdefault("narrative_summary", None)
        normalised.setdefault("our_position", None)

        # Red-flag severities: normalise to the known set.
        rfs = normalised.get("red_flags") or []
        cleaned_rfs: List[Dict[str, Any]] = []
        for rf in rfs:
            if not isinstance(rf, dict):
                continue
            sev = rf.get("severity")
            if sev not in _LEGAL_REVIEW_SEVERITIES:
                warnings.append(
                    f"review[{round_name!r}]: red_flag severity {sev!r} "
                    "unknown — set to 'medium'"
                )
                rf = dict(rf)
                rf["severity"] = "medium"
            cleaned_rfs.append(rf)
        normalised["red_flags"] = cleaned_rfs

        out.append(normalised)

    return out, warnings


# ---------------------------------------------------------------------------
# Split — combined entry → (fact_block, opinion_block)
# ---------------------------------------------------------------------------


def split_legal_review_entry(
    entry: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split a validated combined entry into ``(fact_block, opinion_block)``.

    - ``fact_block`` has the shape of a ``prior_rounds[]`` entry (round_name +
      instrument_type + scenario + effective_date + amount + currency +
      lead_investor + nested term blocks + our_position). Suitable to merge
      into ``Entity.metadata_json.prior_rounds[]`` via
      :func:`merge_prior_round_facts`.
    - ``opinion_block`` has the shape of a per-round entry in the workspace
      ``Legal Review.json`` file (round_name join key + run metadata + opinion
      arrays + narrative summary).
    """
    round_name = entry.get("round_name")
    company_terms = entry.get("company_terms") or {}

    fact_block: Dict[str, Any] = {
        "round_name": round_name,
        "instrument_type": entry.get("instrument_type"),
        "scenario": entry.get("scenario"),
        # Derive convenience fact-bag fields from company_terms where present.
        "effective_date": company_terms.get("effective_date"),
        "amount": company_terms.get("new_money_amount"),
        "currency": company_terms.get("currency"),
        # legal_review docs don't identify the round lead; extract_info fills it.
        "lead_investor": None,
        "company_terms": company_terms,
        "safe_terms": entry.get("safe_terms"),
        "convertible_note_terms": entry.get("convertible_note_terms"),
        "priced_round_terms": entry.get("priced_round_terms"),
        "governance": entry.get("governance") or {},
        "investor_rights": entry.get("investor_rights") or {},
        "transfer_restrictions": entry.get("transfer_restrictions") or {},
        "regulatory": entry.get("regulatory") or {},
        "our_position": entry.get("our_position"),
    }

    opinion_block: Dict[str, Any] = {"round_name": round_name}
    for key in _OPINION_FIELDS:
        opinion_block[key] = entry.get(key)

    return fact_block, opinion_block


# ---------------------------------------------------------------------------
# Merge — prior_rounds[] deep-merge by round_name
# ---------------------------------------------------------------------------


def _deep_merge_dict(existing: Any, incoming: Any) -> Any:
    """Non-destructive dict merge: non-null incoming wins, nested dicts recurse.

    Returns a new dict (never mutates inputs). If types mismatch, incoming wins.
    """
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        if incoming is None:
            return existing
        if isinstance(incoming, list) and len(incoming) == 0:
            return existing
        return incoming

    out = dict(existing)
    for k, v in incoming.items():
        if v is None:
            continue
        if isinstance(v, list) and len(v) == 0 and out.get(k):
            # Non-empty existing array > empty incoming
            continue
        if isinstance(v, dict):
            out[k] = _deep_merge_dict(out.get(k), v)
        else:
            out[k] = v
    return out


def _merge_one_round(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one fact bag into another, keyed by round_name.

    Scalar fields: non-null incoming wins. Nested term blocks: deep-merge.
    """
    out = dict(existing)
    for key, new_val in incoming.items():
        if key == "round_name":
            # Join key; keep existing (they match).
            continue
        if new_val is None:
            continue
        if key in _FACT_NESTED or key == "our_position":
            out[key] = _deep_merge_dict(out.get(key), new_val)
        elif isinstance(new_val, list) and len(new_val) == 0 and out.get(key):
            continue
        else:
            out[key] = new_val
    return out


def merge_prior_round_facts(
    existing: List[Dict[str, Any]] | None,
    incoming: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge a new batch of per-round fact bags into ``prior_rounds[]``.

    Entries are keyed by ``round_name``. Existing entries with a matching
    ``round_name`` are deep-merged with the incoming entry (scalars: non-null
    incoming wins; nested term blocks: field-level deep merge). Existing
    entries without a match are preserved verbatim. New incoming entries
    (no existing match) are appended.

    Incoming duplicates for the same ``round_name``: last-wins (standard merge
    semantics — later writes overwrite earlier).

    Self-healing on legacy shape: entries with ``round`` / ``date`` keys
    (pre-refactor schema) are migrated to the fact-bag shape via
    :func:`~app.services.metadata_extraction._migrate_prior_round_entry`
    **before** keying, so legacy rows join correctly with new deep rows
    instead of accumulating duplicates.
    """
    # Local import avoids a top-level circular with metadata_extraction,
    # which imports merge_prior_round_facts lazily at merge time.
    from app.services.metadata_extraction import _migrate_prior_round_entry

    def _migrate_list(items: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for entry in items or []:
            if not isinstance(entry, dict):
                continue
            out.append(_migrate_prior_round_entry(entry))
        return out

    migrated_existing = _migrate_list(existing)
    migrated_incoming = _migrate_list(incoming)

    # Dedupe incoming within itself (last-wins).
    deduped: List[Dict[str, Any]] = []
    seen_rounds: set[str] = set()
    for entry in reversed(migrated_incoming):
        name = entry.get("round_name")
        if not name or name in seen_rounds:
            continue
        seen_rounds.add(name)
        deduped.append(entry)
    deduped.reverse()

    # Build result: touched existing entries replaced with merged versions,
    # untouched existing preserved, net-new incoming appended.
    incoming_by_name = {e["round_name"]: e for e in deduped}
    out: List[Dict[str, Any]] = []
    touched: set[str] = set()

    for e in migrated_existing:
        name = e.get("round_name")
        if name and name in incoming_by_name:
            out.append(_merge_one_round(e, incoming_by_name[name]))
            touched.add(name)
        else:
            out.append(e)

    for e in deduped:
        if e["round_name"] not in touched:
            out.append(e)

    return out


# ---------------------------------------------------------------------------
# Validation — opinions-only workspace payload
# ---------------------------------------------------------------------------


def validate_legal_review_opinions(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate the opinion-only shape written to Legal Review.json after refactor.

    Shape: ``{ "legal_reviews": [ <opinion_block> ... ] }`` where each opinion
    block has ``round_name`` + the _OPINION_FIELDS keys. Used by the UI / read
    path; post-processing writes the file via the split pipeline so this
    validator guards against manual edits + legacy files.
    """
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        data = data.get("legal_reviews")
    if not isinstance(data, list):
        raise ValueError(
            f"Expected list of opinion entries, got {type(data).__name__}"
        )

    warnings: List[str] = []
    out: List[Dict[str, Any]] = []

    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            warnings.append(f"opinion[{idx}]: not a dict, dropped")
            continue
        round_name = entry.get("round_name")
        if not isinstance(round_name, str) or not round_name.strip():
            warnings.append(f"opinion[{idx}]: missing round_name, dropped")
            continue

        normalised: Dict[str, Any] = {"round_name": round_name.strip()}
        for key in _OPINION_FIELDS:
            normalised[key] = entry.get(key)
        # Defaults for empty-ish types
        normalised.setdefault("review_date", normalised.get("review_date") or "")
        if normalised.get("documents_reviewed") is None:
            normalised["documents_reviewed"] = []
        if normalised.get("reference_templates_consulted") is None:
            normalised["reference_templates_consulted"] = []
        for arr_key in ("unusual_terms", "red_flags", "priority_indicators",
                        "killer_questions"):
            if normalised.get(arr_key) is None:
                normalised[arr_key] = []
        out.append(normalised)

    return out, warnings


# ---------------------------------------------------------------------------
# Merge — opinion blocks by round_name (for the workspace file)
# ---------------------------------------------------------------------------


def merge_legal_review_opinions(
    existing: List[Dict[str, Any]] | None,
    incoming: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge opinion blocks by ``round_name``. Incoming wins on a round_name hit.

    Unlike :func:`merge_prior_round_facts`, opinions are REPLACED wholesale per
    round (not field-level merged) — each legal_review run produces a complete
    opinion for that round, so incoming represents the latest review.
    """
    if not existing:
        existing = []

    # Dedupe incoming within itself (last-wins).
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for e in reversed(list(incoming)):
        if not isinstance(e, dict):
            continue
        name = e.get("round_name")
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(e)
    deduped.reverse()

    preserved = [
        e for e in existing
        if isinstance(e, dict) and e.get("round_name") not in seen
    ]
    # Order: incoming first (matching extract_info's fresh output order), then
    # untouched prior-round opinions.
    return deduped + preserved
