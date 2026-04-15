"""Fact discrepancy lifecycle — agent surfaces, user adjudicates.

When an opinion run (legal_review, extract_info) reads source material that
disagrees with canonical ``Entity.metadata_json``, it calls
``propose_fact_update`` (see workspace_tools) which appends a discrepancy row.
The UI banner shows pending rows; the user accepts (applies to canonical) or
rejects (dismisses with optional reason). Agents never silently mutate facts.

See docs/design/FACTS_VS_OPINIONS.md for the full design.
"""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_VALID_CONFIDENCE = {"low", "medium", "high"}
_VALID_STATUS = {"pending", "accepted", "rejected"}

# Required fields on a new discrepancy payload (before server adds id/timestamp).
_REQUIRED_ON_APPEND = (
    "detected_by",
    "field_path",
    "current_value",
    "proposed_value",
    "source_doc_node_id",
    "confidence",
    "rationale",
)

# Array selectors map shorthand `list[X]` to an implicit key match.
_DEFAULT_ARRAY_KEYS = {
    "prior_rounds": "round_name",
    "_positions": "fund_id",
    "founders": "name",
    "key_team": "name",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Path parser — "prior_rounds[Series A].safe_terms.valuation_cap"
# ---------------------------------------------------------------------------

# Matches `name` or `name[selector]` at each segment.
_SEGMENT_RE = re.compile(r"""
    ^
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    (?:\[(?P<selector>[^\]]+)\])?
    $
""", re.VERBOSE)


def _parse_field_path(field_path: str) -> List[Tuple[str, Optional[Tuple[str, str]]]]:
    """Parse a dotted field_path into segments.

    Each segment is ``(name, selector_or_None)``. Selector is ``(key, value)``
    when the segment ends in ``[...]``. Bracket content supports:

    - ``[Series A]`` — implicit key from ``_DEFAULT_ARRAY_KEYS`` for the segment
      name (e.g. ``round_name`` for ``prior_rounds``)
    - ``[round_name=Series A]`` — explicit key=value

    Raises ``ValueError`` on malformed paths or unknown shorthand arrays.
    """
    segs: List[Tuple[str, Optional[Tuple[str, str]]]] = []
    for raw in field_path.split("."):
        m = _SEGMENT_RE.match(raw.strip())
        if not m:
            raise ValueError(f"Malformed field_path segment: {raw!r}")
        name = m.group("name")
        sel = m.group("selector")
        if sel is None:
            segs.append((name, None))
            continue
        if "=" in sel:
            key, _, val = sel.partition("=")
            key, val = key.strip(), val.strip()
            if not key or not val:
                raise ValueError(f"Malformed selector in {raw!r}")
            segs.append((name, (key, val)))
        else:
            # Shorthand — name must have a default array key
            default_key = _DEFAULT_ARRAY_KEYS.get(name)
            if not default_key:
                raise ValueError(
                    f"No default key for array {name!r}; "
                    f"use [key=value] syntax"
                )
            segs.append((name, (default_key, sel.strip())))
    if not segs:
        raise ValueError("Empty field_path")
    return segs


def _apply_field_path(
    metadata: Dict[str, Any],
    field_path: str,
    value: Any,
) -> None:
    """Apply ``value`` at ``field_path`` inside ``metadata`` (mutates in place).

    Creates intermediate dicts when missing. For array selectors, appends a new
    entry if no match — e.g. accepting a discrepancy on a brand-new round
    lifts it into ``prior_rounds[]``.
    """
    segs = _parse_field_path(field_path)
    cursor: Any = metadata

    for i, (name, selector) in enumerate(segs):
        is_last = i == len(segs) - 1
        if not isinstance(cursor, dict):
            raise ValueError(
                f"Cannot traverse non-dict at segment {i} of {field_path!r}"
            )

        if selector is None:
            if is_last:
                cursor[name] = value
                return
            if name not in cursor or cursor[name] is None:
                cursor[name] = {}
            cursor = cursor[name]
        else:
            key, match_val = selector
            if name not in cursor or not isinstance(cursor[name], list):
                cursor[name] = []
            arr: List[Dict[str, Any]] = cursor[name]
            hit_idx = next(
                (j for j, e in enumerate(arr)
                 if isinstance(e, dict) and str(e.get(key)) == match_val),
                None,
            )
            if hit_idx is None:
                # Create a stub entry with just the match key.
                arr.append({key: match_val})
                hit_idx = len(arr) - 1
            if is_last:
                # Last segment is the array selector itself — replace the entry.
                if isinstance(value, dict):
                    arr[hit_idx] = {**value, key: match_val}
                else:
                    arr[hit_idx] = value
                return
            cursor = arr[hit_idx]


def _read_field_path(
    metadata: Dict[str, Any],
    field_path: str,
) -> Any:
    """Read the value at ``field_path`` — returns ``None`` if any segment misses."""
    try:
        segs = _parse_field_path(field_path)
    except ValueError:
        return None
    cursor: Any = metadata
    for name, selector in segs:
        if not isinstance(cursor, dict) or name not in cursor:
            return None
        cursor = cursor[name]
        if selector is not None:
            if not isinstance(cursor, list):
                return None
            key, match_val = selector
            hit = next(
                (e for e in cursor
                 if isinstance(e, dict) and str(e.get(key)) == match_val),
                None,
            )
            if hit is None:
                return None
            cursor = hit
    return cursor


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _coerce_status(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ensure metadata._fact_discrepancies is a list (mutates in place)."""
    arr = metadata.get("_fact_discrepancies")
    if not isinstance(arr, list):
        metadata["_fact_discrepancies"] = []
    return metadata["_fact_discrepancies"]


def list_discrepancies(
    metadata: Dict[str, Any],
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List discrepancies, optionally filtered by status."""
    arr = metadata.get("_fact_discrepancies") or []
    if not isinstance(arr, list):
        return []
    if status is None or status == "all":
        return [dict(e) for e in arr if isinstance(e, dict)]
    return [dict(e) for e in arr if isinstance(e, dict) and e.get("status") == status]


def append_discrepancy(
    metadata: Dict[str, Any],
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate and append a new discrepancy. Returns the committed entry.

    Server-adds ``id``, ``detected_at``, and initial lifecycle fields. Raises
    ``ValueError`` on a malformed payload.
    """
    missing = [k for k in _REQUIRED_ON_APPEND if k not in entry]
    if missing:
        raise ValueError(f"Missing required discrepancy fields: {missing}")

    field_path = str(entry["field_path"]).strip()
    if not field_path:
        raise ValueError("field_path must be non-empty")
    # Parse to surface malformed paths now, not at accept time.
    _parse_field_path(field_path)

    confidence = entry.get("confidence")
    if confidence not in _VALID_CONFIDENCE:
        raise ValueError(
            f"confidence must be one of {sorted(_VALID_CONFIDENCE)}, "
            f"got {confidence!r}"
        )

    committed: Dict[str, Any] = {
        "id": entry.get("id") or str(uuid.uuid4()),
        "detected_at": entry.get("detected_at") or _utc_now_iso(),
        "detected_by": str(entry["detected_by"]),
        "source_run": entry.get("source_run") or {},
        "field_path": field_path,
        "round_name": entry.get("round_name"),
        "current_value": entry.get("current_value"),
        "proposed_value": entry.get("proposed_value"),
        "source_doc_node_id": str(entry["source_doc_node_id"]),
        "source_doc_quote": entry.get("source_doc_quote"),
        "confidence": confidence,
        "rationale": str(entry["rationale"]),
        "status": "pending",
        "resolved_at": None,
        "resolved_by": None,
        "dismiss_reason": None,
    }

    arr = _coerce_status(metadata)
    arr.append(committed)
    return committed


def _find_index(arr: List[Dict[str, Any]], discrepancy_id: str) -> int:
    for i, e in enumerate(arr):
        if isinstance(e, dict) and e.get("id") == discrepancy_id:
            return i
    raise KeyError(f"No discrepancy with id {discrepancy_id!r}")


def accept_discrepancy(
    metadata: Dict[str, Any],
    discrepancy_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply a pending discrepancy to canonical facts.

    Mutates ``metadata`` in place:
    - writes ``proposed_value`` to the ``field_path``
    - marks the discrepancy ``status="accepted"`` + resolution metadata
    - if the accepted change mutated a row's selector key (e.g.
      ``_positions[fund_id=X].fund_id``), rewrites pending sibling
      discrepancies so their selectors still resolve to the same row.
      Without this, accepting the rename first would cause subsequent
      sibling accepts to create new stub rows instead of updating in place.

    Idempotent on already-accepted rows (returns the existing entry). Raises
    ``KeyError`` when the id is unknown, ``ValueError`` when the field_path is
    malformed or the discrepancy is rejected.
    """
    arr = _coerce_status(metadata)
    idx = _find_index(arr, discrepancy_id)
    entry = dict(arr[idx])

    if entry.get("status") == "accepted":
        return metadata, entry
    if entry.get("status") == "rejected":
        raise ValueError(f"Cannot accept a rejected discrepancy ({discrepancy_id})")

    # Apply the proposed value at the field path.
    _apply_field_path(metadata, entry["field_path"], entry["proposed_value"])

    entry["status"] = "accepted"
    entry["resolved_at"] = _utc_now_iso()
    entry["resolved_by"] = "user"
    arr[idx] = entry

    # Cascade: if the accepted leaf WAS the row's selector key, sibling pending
    # discrepancies targeting the same row still reference the OLD selector
    # value. Rewrite them so their next accept lands on the same renamed row.
    _rewrite_pending_selectors(metadata, entry)

    return metadata, entry


def _rewrite_pending_selectors(
    metadata: Dict[str, Any],
    accepted_entry: Dict[str, Any],
) -> None:
    """After an accept that mutated an array row's selector key, update pending
    sibling discrepancies so their field_paths still resolve.

    Example (cascades 3 pending into 1 row's updates):

    - accepted: ``_positions[fund_id=taihill_v3_lp].fund_id`` →
      ``"taihill_venture_seed_iii_lp"``
    - pending:  ``_positions[fund_id=taihill_v3_lp].invested_amount`` → 300000
    - pending:  ``_positions[fund_id=taihill_v3_lp].round_at_entry`` → "Series Angel-1"

    After cascade, both pending rows read
    ``_positions[fund_id=taihill_venture_seed_iii_lp].<leaf>`` so their next
    accept updates the same (renamed) row.
    """
    try:
        segs = _parse_field_path(accepted_entry.get("field_path") or "")
    except ValueError:
        return
    # Must be exactly [array-selector, leaf] — i.e., the leaf IS the selector key.
    if len(segs) != 2:
        return
    (arr_name, selector), (leaf_name, leaf_sel) = segs
    if selector is None or leaf_sel is not None:
        return
    sel_key, sel_old = selector
    if leaf_name != sel_key:
        return  # Leaf isn't the selector key; no rewrite needed.
    proposed = accepted_entry.get("proposed_value")
    if proposed is None:
        return
    sel_new = str(proposed)
    if sel_new == sel_old:
        return

    old_key_v = f"{arr_name}[{sel_key}={sel_old}]"
    new_key_v = f"{arr_name}[{sel_key}={sel_new}]"
    default_key = _DEFAULT_ARRAY_KEYS.get(arr_name)
    old_short = f"{arr_name}[{sel_old}]" if default_key == sel_key else None
    new_short = f"{arr_name}[{sel_new}]" if default_key == sel_key else None

    arr = metadata.get("_fact_discrepancies") or []
    for d in arr:
        if not isinstance(d, dict) or d.get("status") != "pending":
            continue
        fp = d.get("field_path") or ""
        if fp == old_key_v or fp.startswith(old_key_v + "."):
            d["field_path"] = new_key_v + fp[len(old_key_v):]
        elif old_short is not None and (
            fp == old_short or fp.startswith(old_short + ".")
        ):
            d["field_path"] = new_short + fp[len(old_short):]


def reject_discrepancy(
    metadata: Dict[str, Any],
    discrepancy_id: str,
    reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Mark a pending discrepancy as rejected (canonical facts untouched).

    Idempotent on already-rejected rows. Raises ``KeyError`` on unknown id,
    ``ValueError`` on already-accepted (undo is out of scope).
    """
    arr = _coerce_status(metadata)
    idx = _find_index(arr, discrepancy_id)
    entry = dict(arr[idx])

    if entry.get("status") == "rejected":
        return metadata, entry
    if entry.get("status") == "accepted":
        raise ValueError(
            f"Cannot reject an accepted discrepancy ({discrepancy_id})"
        )

    entry["status"] = "rejected"
    entry["resolved_at"] = _utc_now_iso()
    entry["resolved_by"] = "user"
    entry["dismiss_reason"] = (reason or None) and str(reason)
    arr[idx] = entry
    return metadata, entry


# ---------------------------------------------------------------------------
# Read-side helper: surface the canonical value for UI context
# ---------------------------------------------------------------------------


def resolve_current_value(
    metadata: Dict[str, Any],
    field_path: str,
) -> Any:
    """Look up the *current* value at ``field_path``, independent of what the
    agent recorded. Useful on the read side — the agent's snapshot of
    ``current_value`` can go stale if the user edited positions since.
    """
    return deepcopy(_read_field_path(metadata, field_path))
