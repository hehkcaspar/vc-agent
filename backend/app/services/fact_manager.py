"""Fact ledger manager — single writer for canonical hard facts.

Maintains two representations inside ``Entity.metadata_json``:

1. **Flat fields** (``founders[]``, ``prior_rounds[]``, etc.) — the primary
   read-path used by the frontend Facts tab, entity header, and chat
   context. Unchanged by design: existing consumers keep working.
2. **``_ledger[]``** — append-only provenance log of ``FactEntry`` objects.
   Records every hard-fact write with source, confidence, timestamp, and
   ``supersedes`` pointer for timeline reconstruction.

``record_fact`` is the single writer. Callers should prefer it over direct
``metadata_json`` mutation for any path in ``hard_fact_catalog.HARD_FACT_*``.

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datetime_support import utc_now
from app.models import Entity
from app.services.fact_discrepancies import (
    _apply_field_path,
    _read_field_path,
)
from app.services.fact_ledger_schema import FactEntry, FactSource, FactStatus
from app.services.hard_fact_catalog import evidence_tier, is_hard_fact

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _new_entry_id() -> str:
    return f"fle-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return utc_now().isoformat()


def _json_safe(value: Any) -> Any:
    """Coerce to a JSON-serializable form (shallow)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _load_metadata(entity: Entity) -> Dict[str, Any]:
    if not entity.metadata_json:
        return {}
    try:
        data = json.loads(entity.metadata_json)
    except json.JSONDecodeError:
        _log.warning("Entity %s has corrupt metadata_json", entity.id)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _get_ledger(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    arr = meta.get("_ledger")
    if not isinstance(arr, list):
        meta["_ledger"] = []
        return meta["_ledger"]
    return arr


def _find_active_entry(
    ledger: List[Dict[str, Any]], fact_path: str
) -> Optional[Dict[str, Any]]:
    """Return the newest active entry for ``fact_path``, or None.

    We scan from newest to oldest — if multiple active entries exist at the
    same path due to a bug or manual edit, the newest wins.
    """
    for entry in reversed(ledger):
        if not isinstance(entry, dict):
            continue
        if entry.get("fact_path") == fact_path and entry.get("status") == "active":
            return entry
    return None


def _retire_stale_proposed(
    ledger: List[Dict[str, Any]],
    fact_path: str,
    *,
    new_active_value: Any,
) -> int:
    """Sweep proposed entries at the same ``fact_path`` when a new active
    value is being written.

    Proposals whose value matches the new active value are flipped to
    ``superseded`` (the new entry captures the same claim, so the proposal
    is redundant). Proposals whose value differs are flipped to ``rejected``
    (the field moved on, the proposal is stale).

    Returns the count of entries retired. This is the fallback for direct
    ``record_fact`` writes that happen to resolve an open proposal — the
    formal path (``accept_discrepancy`` → ``promote_proposed_to_active``)
    still applies when the user adjudicates explicitly.
    """
    retired = 0
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "proposed":
            continue
        if entry.get("fact_path") != fact_path:
            continue
        if entry.get("value") == new_active_value:
            entry["status"] = "superseded"
        else:
            entry["status"] = "rejected"
        retired += 1
    return retired


def _find_entry_by_id(
    ledger: List[Dict[str, Any]], entry_id: str
) -> Optional[Dict[str, Any]]:
    for entry in ledger:
        if isinstance(entry, dict) and entry.get("entry_id") == entry_id:
            return entry
    return None


def _normalise_source(source: FactSource | Dict[str, Any]) -> FactSource:
    if isinstance(source, FactSource):
        return source
    return FactSource(**source)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def _apply_record_to_meta(
    meta: Dict[str, Any],
    *,
    fact_path: str,
    value: Any,
    source: FactSource,
    confidence: float,
    as_of: Optional[str],
    notes: Optional[str],
    status: FactStatus,
    linked_discrepancy_id: Optional[str] = None,
) -> Optional[FactEntry]:
    """Shared core of ``record_fact`` / ``record_fact_in_metadata``.

    Mutates ``meta`` in place: appends a ledger entry, flips prior entries
    as needed, syncs the flat field for active/verified writes.

    Returns the new ``FactEntry`` — or the existing active entry on an
    idempotent no-op. Returns ``None`` if the path is not a hard fact.

    Rules (kept in one place so DB + pure variants never drift):

    - Non-hard path → silent no-op (``None``). This is a guard, not an error.
    - Idempotency (``status="active"``): same value + same source type+ref
      as the current active entry → return the existing entry, no write.
    - Corroboration (``status="active"``): same value, *different* source
      → append a new entry with ``status="verified"``. The prior active
      stays active. ``supersedes`` is NULL — this is corroboration, not
      replacement.
    - Supersession (``status="active"``): different value → append a new
      active entry, flip the prior to ``superseded`` and set
      ``supersedes`` to the prior's entry_id. Rewrite the flat field.
    - Proposed (``status="proposed"``): append-only, flat field untouched,
      prior active left in place. Used by the discrepancy shim.
    - Writing a new active value also retires any matching ``proposed``
      entries at the same path (``_retire_stale_proposed``). This stops
      propose_fact_update → record_fact from leaving orphaned proposals.
    """
    if not is_hard_fact(fact_path):
        _log.debug(
            "fact_manager: %r is not a hard fact; skipped", fact_path,
        )
        return None

    ledger = _get_ledger(meta)
    current = _find_active_entry(ledger, fact_path)
    safe_value = _json_safe(value)

    # Idempotency — only on active writes with matching value+source.
    if current and status == "active":
        cur_src = current.get("source") or {}
        if (
            current.get("value") == safe_value
            and cur_src.get("type") == source.type
            and cur_src.get("ref") == source.ref
        ):
            return FactEntry(**current)

    final_status: FactStatus = status
    supersedes: Optional[str] = None

    if status == "active":
        is_corroboration = (
            current is not None
            and current.get("value") == safe_value
        )
        if is_corroboration:
            # Corroboration, not supersession: the prior active STAYS active,
            # and we append a `verified` entry alongside it. No supersedes
            # pointer — the two entries coexist as independent attestations
            # of the same value from different sources.
            final_status = "verified"
            supersedes = None
        elif current:
            supersedes = current.get("entry_id")
            current["status"] = "superseded"

        # Retire any open proposals at this path now that an active write
        # lands. Matching-value proposals → superseded (the new entry
        # captures the same claim); non-matching → rejected (the claim is
        # stale now that the field has moved).
        _retire_stale_proposed(ledger, fact_path, new_active_value=safe_value)

    entry = FactEntry(
        entry_id=_new_entry_id(),
        fact_path=fact_path,
        value=safe_value,
        source=source,
        confidence=confidence,
        as_of=as_of,
        recorded_at=_now_iso(),
        supersedes=supersedes,
        status=final_status,
        notes=notes,
        linked_discrepancy_id=linked_discrepancy_id,
    )
    ledger.append(entry.model_dump())

    # Flat-field sync: only for entries that represent "this is the value now".
    if final_status in ("active", "verified"):
        try:
            _apply_field_path(meta, fact_path, safe_value)
        except (ValueError, TypeError) as exc:
            _log.warning(
                "fact_manager: flat-field sync failed for %s: %s",
                fact_path, exc,
            )

    return entry


async def record_fact(
    db: AsyncSession,
    entity_id: str,
    *,
    fact_path: str,
    value: Any,
    source: FactSource | Dict[str, Any],
    confidence: float = 0.8,
    as_of: Optional[str] = None,
    notes: Optional[str] = None,
    status: FactStatus = "active",
    commit: bool = True,
) -> Optional[FactEntry]:
    """Record a hard fact against an entity row.

    Thin wrapper around :func:`_apply_record_to_meta`: loads the entity's
    metadata, applies the write, persists metadata_json + updated_at.

    See ``_apply_record_to_meta`` for semantics.

    Set ``commit=False`` when batching many writes within one transaction —
    the caller is responsible for the final ``await db.commit()``. Batched
    callers should prefer :func:`record_fact_in_metadata` when they already
    hold the metadata dict in memory (avoids a SELECT per fact).
    """
    if not is_hard_fact(fact_path):
        return None
    src = _normalise_source(source)

    ent_res = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = ent_res.scalar_one_or_none()
    if entity is None:
        raise ValueError(f"record_fact: entity not found: {entity_id}")

    meta = _load_metadata(entity)
    entry = _apply_record_to_meta(
        meta,
        fact_path=fact_path,
        value=value,
        source=src,
        confidence=confidence,
        as_of=as_of,
        notes=notes,
        status=status,
    )
    entity.metadata_json = json.dumps(meta, ensure_ascii=False)
    entity.updated_at = utc_now()
    if commit:
        await db.commit()
    return entry


async def get_current(
    db: AsyncSession, entity_id: str, fact_path: str,
) -> Optional[FactEntry]:
    """Return the newest active entry at ``fact_path``, or None."""
    ent_res = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = ent_res.scalar_one_or_none()
    if entity is None:
        return None
    meta = _load_metadata(entity)
    ledger = _get_ledger(meta)
    cur = _find_active_entry(ledger, fact_path)
    return FactEntry(**cur) if cur else None


async def get_history(
    db: AsyncSession, entity_id: str, fact_path: str,
) -> List[FactEntry]:
    """Return all entries (any status) at ``fact_path``, oldest first."""
    ent_res = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = ent_res.scalar_one_or_none()
    if entity is None:
        return []
    meta = _load_metadata(entity)
    ledger = _get_ledger(meta)
    return [
        FactEntry(**e)
        for e in ledger
        if isinstance(e, dict) and e.get("fact_path") == fact_path
    ]


async def get_provenance(
    db: AsyncSession, entity_id: str,
) -> Dict[str, Dict[str, Any]]:
    """Return ``{fact_path: {current: FactEntry, history: [FactEntry…]}}``.

    Grouped + JSON-serializable; suitable for the provenance API endpoint.
    Only fact_paths with at least one ledger entry appear.
    """
    ent_res = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = ent_res.scalar_one_or_none()
    if entity is None:
        return {}
    meta = _load_metadata(entity)
    ledger = _get_ledger(meta)

    out: Dict[str, Dict[str, Any]] = {}
    for raw in ledger:
        if not isinstance(raw, dict):
            continue
        path = raw.get("fact_path")
        if not isinstance(path, str):
            continue
        bucket = out.setdefault(path, {"current": None, "history": []})
        try:
            entry = FactEntry(**raw).model_dump()
        except Exception:
            continue
        bucket["history"].append(entry)
        if entry.get("status") == "active":
            bucket["current"] = entry
    return out


TierComparison = Literal["stronger", "equal", "weaker"]


async def detect_contradiction(
    db: AsyncSession,
    entity_id: str,
    *,
    fact_path: str,
    proposed_value: Any,
    proposed_source: FactSource | Dict[str, Any],
) -> Optional[Tuple[FactEntry, TierComparison]]:
    """If an active entry at ``fact_path`` contradicts ``proposed_value``,
    return ``(existing_entry, tier_comparison)``.

    Returns ``None`` when there is no active entry OR the existing value
    already matches the proposed value (corroboration, not contradiction).

    ``tier_comparison`` compares the proposed source's evidence tier against
    the existing entry's source tier.
    """
    src = _normalise_source(proposed_source)
    current = await get_current(db, entity_id, fact_path)
    if current is None:
        return None
    if current.value == _json_safe(proposed_value):
        return None

    existing_tier = evidence_tier(current.source.type)
    new_tier = evidence_tier(src.type)
    if new_tier > existing_tier:
        cmp: TierComparison = "stronger"
    elif new_tier < existing_tier:
        cmp = "weaker"
    else:
        cmp = "equal"
    return current, cmp


# ---------------------------------------------------------------------------
# Bulk helpers (used by preset post-processors)
# ---------------------------------------------------------------------------


async def record_many(
    db: AsyncSession,
    entity_id: str,
    *,
    entries: List[Dict[str, Any]],
    default_source: FactSource | Dict[str, Any],
    default_confidence: float = 0.8,
    commit: bool = True,
) -> List[FactEntry]:
    """Record a batch of ``{fact_path, value, [source, confidence, as_of, notes]}``.

    Non-hard paths are filtered silently. Per-entry ``source`` / ``confidence``
    override the defaults. One commit at the end unless ``commit=False``.
    """
    out: List[FactEntry] = []
    for spec in entries:
        fact_path = spec.get("fact_path")
        if not isinstance(fact_path, str) or not fact_path:
            continue
        entry = await record_fact(
            db,
            entity_id,
            fact_path=fact_path,
            value=spec.get("value"),
            source=spec.get("source", default_source),
            confidence=spec.get("confidence", default_confidence),
            as_of=spec.get("as_of"),
            notes=spec.get("notes"),
            status=spec.get("status", "active"),
            commit=False,
        )
        if entry is not None:
            out.append(entry)
    if commit:
        await db.commit()
    return out


# ---------------------------------------------------------------------------
# Test / dev helpers — pure-dict variants that don't touch the DB
# ---------------------------------------------------------------------------


def record_fact_in_metadata(
    meta: Dict[str, Any],
    *,
    fact_path: str,
    value: Any,
    source: FactSource | Dict[str, Any],
    confidence: float = 0.8,
    as_of: Optional[str] = None,
    notes: Optional[str] = None,
    status: FactStatus = "active",
) -> Optional[FactEntry]:
    """Pure-dict variant of :func:`record_fact`. Mutates ``meta`` in place.

    Preferred for batched writes where the caller already holds metadata in
    memory (preset post-processors, unit tests) — avoids a SELECT/UPDATE per
    fact. Same semantics as the DB-bound version; see :func:`_apply_record_to_meta`.
    """
    return _apply_record_to_meta(
        meta,
        fact_path=fact_path,
        value=value,
        source=_normalise_source(source),
        confidence=confidence,
        as_of=as_of,
        notes=notes,
        status=status,
    )


def read_flat_value(meta: Dict[str, Any], fact_path: str) -> Any:
    """Read the current flat value at ``fact_path`` (or None if absent)."""
    return deepcopy(_read_field_path(meta, fact_path))


# ---------------------------------------------------------------------------
# Payload → list of (fact_path, value) pairs for hard fields
# ---------------------------------------------------------------------------
#
# Used by preset post-processors (extract_info, legal_review) to translate a
# flat metadata dict into an ordered list of ledger writes. Preserves the
# hard-fact catalog as the single source of truth: we enumerate *candidates*
# from the payload's shape and filter through ``is_hard_fact``.


_SCALAR_HARD_KEYS: tuple[str, ...] = (
    "website",
    "company_name",
    "legal_name",
    "founded_date",
    "hq_location",
    "incorporation_jurisdiction",
    "incorporation_entity_type",
    "investment_stage",
    "raise_amount",
    "raise_currency",
    "raise_instrument",
    "valuation_cap",
    "pre_money_valuation",
    "referral_source",
)


def _is_emptyish(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def extract_hard_facts_from_payload(
    payload: Dict[str, Any],
) -> List[Tuple[str, Any]]:
    """Flatten ``payload`` into ``[(fact_path, value), ...]`` for every hard
    field with a non-empty value.

    Designed for extract_info / legal_review payloads — walks the known
    canonical shape (founders[], key_team[], prior_rounds[], _positions[])
    and emits dotted paths keyed by each array's selector. Everything not
    matching the hard-fact catalog is silently dropped (the catalog stays
    authoritative; this function only knows the payload *shape*).

    Paths:

    - Top-level scalars → ``{key}``  (``website``, ``founded_date``, …)
    - Founders  → ``founders[name={name}].{leaf}`` for ``title`` / ``linkedin_url``
    - Key team  → ``key_team[name={name}].{leaf}`` (same)
    - Existing investors → ``existing_investors`` (whole list)
    - Prior rounds → recursive walk under
      ``prior_rounds[round_name={rn}]``; list-valued leaves (e.g.
      ``participating_investors``) are emitted as a single value, dicts
      are flattened further.
    - Positions → ``_positions[fund_id={fid}].{leaf}``
    """
    if not isinstance(payload, dict):
        return []

    out: List[Tuple[str, Any]] = []

    for k in _SCALAR_HARD_KEYS:
        v = payload.get(k)
        if _is_emptyish(v):
            continue
        if is_hard_fact(k):
            out.append((k, v))

    def _emit_person_list(key: str) -> None:
        items = payload.get(key)
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            for leaf in ("title", "linkedin_url"):
                v = item.get(leaf)
                if _is_emptyish(v):
                    continue
                path = f"{key}[name={name.strip()}].{leaf}"
                if is_hard_fact(path):
                    out.append((path, v))

    _emit_person_list("founders")
    _emit_person_list("key_team")

    existing_investors = payload.get("existing_investors")
    if (
        isinstance(existing_investors, list)
        and existing_investors
        and is_hard_fact("existing_investors")
    ):
        out.append(("existing_investors", existing_investors))

    rounds = payload.get("prior_rounds")
    if isinstance(rounds, list):
        for rd in rounds:
            if not isinstance(rd, dict):
                continue
            rn = rd.get("round_name") or rd.get("round")
            if not isinstance(rn, str) or not rn.strip():
                continue
            prefix = f"prior_rounds[round_name={rn.strip()}]"
            # Depth-first walk. Recurse into plain dicts; list-valued leaves
            # are emitted as a single value (atomic from the ledger's view).
            stack: List[Tuple[str, Any]] = [(prefix, rd)]
            while stack:
                current_path, node = stack.pop()
                if not isinstance(node, dict):
                    continue
                for child_k, child_v in node.items():
                    if child_k in ("round_name", "round"):
                        continue
                    if _is_emptyish(child_v):
                        continue
                    child_path = f"{current_path}.{child_k}"
                    if isinstance(child_v, dict):
                        stack.append((child_path, child_v))
                    else:
                        if is_hard_fact(child_path):
                            out.append((child_path, child_v))

    positions = payload.get("_positions")
    if isinstance(positions, list):
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            fid = pos.get("fund_id")
            if not isinstance(fid, str) or not fid.strip():
                continue
            for leaf, v in pos.items():
                if leaf == "fund_id" or _is_emptyish(v):
                    continue
                path = f"_positions[fund_id={fid.strip()}].{leaf}"
                if is_hard_fact(path):
                    out.append((path, v))

    return out


# ---------------------------------------------------------------------------
# Discrepancy shim — promote/reject proposed ledger entries
# ---------------------------------------------------------------------------


def promote_proposed_to_active(
    meta: Dict[str, Any],
    discrepancy_id: str,
) -> Optional[FactEntry]:
    """Flip the proposed ledger entry linked to ``discrepancy_id`` to active,
    superseding any prior active entry at the same ``fact_path``.

    Returns the promoted entry, or None if no linked proposed entry exists
    (e.g. pre-shim discrepancies that were filed before dual-write landed).

    The caller is responsible for persisting ``meta`` — we only mutate.
    """
    ledger = meta.get("_ledger") or []
    if not isinstance(ledger, list):
        return None

    proposed_idx: Optional[int] = None
    for i, entry in enumerate(ledger):
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("status") == "proposed"
            and entry.get("linked_discrepancy_id") == discrepancy_id
        ):
            proposed_idx = i
            break
    if proposed_idx is None:
        return None

    proposed = ledger[proposed_idx]
    path = proposed.get("fact_path")

    # Supersede any prior active entry at the same path
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("fact_path") == path
            and entry.get("status") == "active"
            and entry is not proposed
        ):
            entry["status"] = "superseded"
            proposed["supersedes"] = entry.get("entry_id")

    proposed["status"] = "active"
    proposed["recorded_at"] = _now_iso()
    # accept_discrepancy already wrote the value to the flat field, but we
    # re-apply here for safety — idempotent.
    try:
        _apply_field_path(meta, path, proposed.get("value"))
    except (ValueError, TypeError):
        pass

    return FactEntry(**proposed)


def reject_proposed(
    meta: Dict[str, Any],
    discrepancy_id: str,
) -> Optional[FactEntry]:
    """Flip the proposed ledger entry linked to ``discrepancy_id`` to rejected.

    Returns the updated entry, or None when no linked proposed entry exists.
    """
    ledger = meta.get("_ledger") or []
    if not isinstance(ledger, list):
        return None
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("status") == "proposed"
            and entry.get("linked_discrepancy_id") == discrepancy_id
        ):
            entry["status"] = "rejected"
            return FactEntry(**entry)
    return None


def record_proposed_for_discrepancy(
    meta: Dict[str, Any],
    *,
    discrepancy_id: str,
    fact_path: str,
    proposed_value: Any,
    source: FactSource | Dict[str, Any],
    confidence: float = 0.7,
    notes: Optional[str] = None,
) -> Optional[FactEntry]:
    """Mirror a ``_fact_discrepancies[]`` row as a ``status=proposed`` ledger
    entry linked to the discrepancy id.

    No-op when ``fact_path`` is not a hard fact (the discrepancy still exists
    in ``_fact_discrepancies[]`` — it just isn't tracked in the ledger).
    """
    return _apply_record_to_meta(
        meta,
        fact_path=fact_path,
        value=proposed_value,
        source=_normalise_source(source),
        confidence=confidence,
        as_of=None,
        notes=notes,
        status="proposed",
        linked_discrepancy_id=discrepancy_id,
    )
