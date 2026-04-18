"""Fact ledger schema — Pydantic types for canonical-fact provenance.

Stored inside ``Entity.metadata_json._ledger[]``. Each entry is append-only;
state transitions (supersede, contradict, reject) are recorded by writing a
new entry and flipping the prior entry's ``status``.

See docs/design/FACTS_VS_OPINIONS.md (facts vs opinions doctrine) and the
fact_manager module (single writer + idempotency + contradiction detection).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Source tiers are ranked in hard_fact_catalog.EVIDENCE_TIERS.
# Keep this list in sync with that mapping.
SourceType = Literal[
    "cap_table",      # Cap table / share ledger — highest tier
    "legal_doc",      # SAFE, SPA, SSA, side letter, IRA, VA
    "user",           # User-entered via UI (treated as verified)
    "upload",         # Deck, memo, data room doc (non-legal)
    "third_party",    # Crunchbase, Pitchbook, PitchBook etc.
    "communication",  # Email, meeting notes, chat
    "web",            # LinkedIn, news articles, general web / grounded search
    "self_claim",     # Unverified self-reported (lowest tier, fallback)
]


FactStatus = Literal[
    "active",        # Currently believed; flat field reflects this value
    "superseded",    # Replaced by a newer active entry at same fact_path
    "contradicted", # Conflicting entry exists; awaiting adjudication
    "proposed",      # Agent-surfaced, awaiting user Accept/Reject
    "rejected",      # User rejected
    "verified",      # Corroborated by an independent source
]


# Mapping from discrepancy confidence strings ("low"/"medium"/"high") to
# the numeric ``FactEntry.confidence`` field. Used by the shim that mirrors
# propose_fact_update into ledger entries.
CONFIDENCE_STRING_TO_FLOAT: dict[str, float] = {
    "low": 0.4,
    "medium": 0.7,
    "high": 0.9,
}


class FactSource(BaseModel):
    """Where a fact came from."""

    model_config = ConfigDict(extra="allow")

    type: SourceType
    # For ``upload``/``legal_doc``/``cap_table``: workspace node id or path
    # (``workspace://Data Room/Legal/safe.pdf``).
    # For ``web``: fully-qualified URL.
    # For ``user``: None (the source is the UI edit itself).
    ref: Optional[str] = None
    # Verbatim quote from the source that establishes the fact (optional but
    # strongly encouraged — it's what makes the ledger auditable).
    quote: Optional[str] = None
    # Which preset/run recorded this — useful for diagnostics + revocation.
    preset: Optional[str] = None
    run_id: Optional[str] = None


class FactEntry(BaseModel):
    """A single recorded fact (current or historical)."""

    model_config = ConfigDict(extra="allow")

    entry_id: str
    fact_path: str                       # e.g. "founders[name=Joe Dow].title"
    value: Any                           # JSON-serializable scalar / list / dict
    source: FactSource
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    # When the source stated the fact was true (ISO 8601). Nullable — a source
    # may not tell us a date.
    as_of: Optional[str] = None
    # When we wrote the fact to the ledger (ISO 8601 UTC).
    recorded_at: str
    # Entry_id of the prior active entry at this fact_path, if any.
    supersedes: Optional[str] = None
    status: FactStatus = "active"
    # Free-form reviewer/agent note (e.g. "corroborated by LinkedIn 2026-02-10").
    notes: Optional[str] = None
    # Present when this entry was created by ``propose_fact_update`` as the
    # ledger mirror of a row in ``_fact_discrepancies[]``. Lets accept/reject
    # promote or retire the corresponding ledger entry without a separate
    # search. Cleared on user-originated writes.
    linked_discrepancy_id: Optional[str] = None
