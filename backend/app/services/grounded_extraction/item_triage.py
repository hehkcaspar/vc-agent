"""Pure decision layer: given a verification result, decide what to
do with the item.

Three actions:

    KEEP   — item stays in its current ledger; verification's
             authoritative URL is adopted downstream.
    DROP   — item is rejected (claim fabricated OR category wrong with
             no known destination). Source soft-deletes + tombstones.
    ROUTE  — claim is real but belongs in a different ledger. Source
             tombstones (prevents re-emission) and hands the item to
             the destination's ``accept_into()``. The destination
             decides whether to actually store it — triage never
             touches another ledger directly.

Pure function, no I/O, easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .item_verification import VerifyResult

Action = Literal["keep", "drop", "route"]

# Categories the refinement pipeline can hand off to via accept_into().
# Keep in sync with ``services.academic.destinations.accept_into`` dispatch.
_KNOWN_DESTINATIONS = {"news", "patents", "startups", "red_flags", "papers"}


@dataclass(frozen=True)
class TriageDecision:
    action: Action
    reason: str = ""
    # Only populated when action == "route". Names the destination
    # ledger (e.g. "papers"). Must be in _KNOWN_DESTINATIONS.
    destination: str = ""


def triage(
    item: dict,
    vr: VerifyResult,
    source_category: str,
) -> TriageDecision:
    """Decide KEEP / DROP / ROUTE for ``item``.

    Rules, in order:
        1. Verification errored           → KEEP (flake, not hallucination).
        2. verdict == "unconfirmed"       → DROP (claim hallucinated OR
                                            subject identity mismatch).
        3. category_correct is False:
            3a. has a known suggested_category different from source
                → ROUTE to that destination.
            3b. otherwise                 → DROP (category wrong, no home).
        4. Otherwise                      → KEEP.
    """
    if vr.error:
        return TriageDecision(
            action="keep",
            reason=f"verify_error: {vr.error[:120]}",
        )

    if vr.verdict == "unconfirmed":
        return TriageDecision(
            action="drop",
            reason=vr.evidence or "claim unconfirmed by independent search",
        )

    if not vr.category_correct:
        dest = (vr.suggested_category or "").strip().lower()
        if dest and dest != source_category and dest in _KNOWN_DESTINATIONS:
            return TriageDecision(
                action="route",
                destination=dest,
                reason=vr.correction_note or f"belongs in {dest}",
            )
        note = vr.correction_note or f"not a {source_category}"
        return TriageDecision(action="drop", reason=note)

    return TriageDecision(action="keep")


__all__ = ["triage", "TriageDecision", "Action"]
