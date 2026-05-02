"""High-level read API over Layer 1 dossier files.

This module is the single entry point Layer 3 uses to read scholar
state. It projects the append-only logs (red_flags.jsonl) and bundles
them with the rewritten-wholesale JSON files (profile, papers, grants,
patents, startups, attributed_metrics) into a single snapshot object.

Writing is handled by the source modules under `sources/` — this
module is read-oriented plus snapshot bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .file_utils import (
    append_record,
    dossier_path,
    fold_records,
    latest_record,
    read_json,
    read_records,
)

# Cap how many recent news items Layer 3 dim runners see. news.jsonl
# grows unbounded over a scholar's lifetime; scorers only need the
# recent window to judge commercial/award/appointment signal.
_NEWS_WINDOW = 30


# ── Red-flag projection (Concept 7) ───────────────────────────────────


def _red_flags_reducer(state: dict[str, dict[str, Any]] | None, rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Fold red-flag events into `{flag_id: current_state}`.

    Active flags = all `flag` events minus those whose id appears as a
    `target_id` in a `dismissal` event, with `resolution` events applied.
    """
    state = state or {}
    t = rec.get("type")
    if t == "flag":
        state[rec["id"]] = {**rec, "status": "active"}
    elif t == "dismissal":
        target = rec.get("target_id")
        if target and target in state:
            state[target]["status"] = "dismissed"
    elif t == "resolution":
        target = rec.get("target_id")
        if target and target in state:
            state[target]["status"] = rec.get("new_status", "resolved")
    return state


def active_red_flags(scholar_id: str) -> list[dict[str, Any]]:
    """Return the list of currently active red-flag events."""
    projection = fold_records(scholar_id, "red_flags", _red_flags_reducer, {}) or {}
    return [f for f in projection.values() if f.get("status") == "active"]


# ── Red-flag severity caps (Concept 7) ────────────────────────────────

_SEVERITY_CAP: dict[str, int | None] = {
    "low": None,       # note only
    "medium": 85,
    "high": 70,
    "critical": 49,    # `<50` bucket
}


def apply_red_flag_caps(
    score: int,
    dim_id: str,
    scholar_id: str,
) -> tuple[int, list[str], str | None]:
    """Apply active red-flag caps to *score* for the given dim.

    Returns (capped_score, applied_flag_notes, forced_uncertainty).
    Multiple flags stack; the lowest cap wins. `forced_uncertainty`
    is set to "high" if any high/critical flag applied.
    """
    flags = [
        f
        for f in active_red_flags(scholar_id)
        if dim_id in (f.get("affected_dimensions") or [])
    ]
    if not flags:
        return score, [], None

    capped = score
    notes: list[str] = []
    forced: str | None = None
    for f in flags:
        sev = (f.get("severity") or "low").lower()
        cap = _SEVERITY_CAP.get(sev)
        if cap is not None and capped > cap:
            capped = cap
        if sev in ("high", "critical"):
            forced = "high"
        notes.append(
            f"red flag ({sev}): {f.get('claim') or f.get('category') or 'unspecified'}"
        )
    return capped, notes, forced


# ── Snapshot bookkeeping (Concept 6) ──────────────────────────────────


async def record_snapshot(
    scholar_id: str,
    source_id: str,
    detail: dict[str, Any] | None = None,
) -> str:
    """Append a snapshot marker to snapshot_log.jsonl. Return the id."""
    return await append_record(
        scholar_id,
        "snapshot_log",
        {
            "source": source_id,
            "detail": detail or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def last_snapshot_for_source(
    scholar_id: str, source_id: str
) -> dict[str, Any] | None:
    """Most-recent snapshot entry for a given source, or None.

    PERF note: O(n) in the size of `snapshot_log.jsonl`. Per-scholar
    logs stay small (one entry per source per refresh) so this is
    fine for v1 — years of 7-day refreshes on 10 sources comes out
    to ~5000 entries per scholar. If logs ever grow large we can
    either cache the tail in-memory or index by source in SQL.
    """
    for rec in reversed(read_records(scholar_id, "snapshot_log")):
        if rec.get("source") == source_id:
            return rec
    return None


# ── Aggregate read-only snapshot for dim runners ──────────────────────


@dataclass
class FactStoreSnapshot:
    scholar_id: str
    profile: dict[str, Any]
    papers: list[dict[str, Any]]
    grants: list[dict[str, Any]]
    patents: list[dict[str, Any]]
    startups: list[dict[str, Any]]
    attributed_metrics: dict[str, Any]
    peer_group: dict[str, Any] | None
    red_flags_active: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)


def _read_list_json(scholar_id: str, name: str) -> list[dict[str, Any]]:
    """Load a `{name}.json` file that stores a list (or {items: [...]})."""
    data = read_json(dossier_path(scholar_id) / f"{name}.json")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items
    return []


def _recent_news(scholar_id: str, limit: int) -> list[dict[str, Any]]:
    """Return the most recent *limit* news items, sorted newest-first.

    Sorts by `published_date` with `id` (ISO-timestamp of append) as
    the fallback, so items the LLM couldn't date still land in
    discovery order. Hides records the refinement pipeline rejected
    (``_rejected: True``) — they stay on disk as audit trail but
    shouldn't reach Layer 3 dim runners.
    """
    items = read_records(scholar_id, "news")
    if not items:
        return []
    items = [it for it in items if not it.get("_rejected")]
    items.sort(
        key=lambda r: (r.get("published_date") or "", r.get("id") or ""),
        reverse=True,
    )
    return items[:limit]


def current_state(scholar_id: str) -> FactStoreSnapshot:
    """Bundle everything Layer 3 needs about a scholar into one object."""
    return FactStoreSnapshot(
        scholar_id=scholar_id,
        profile=read_json(dossier_path(scholar_id) / "profile.json"),
        papers=_read_list_json(scholar_id, "papers"),
        grants=_read_list_json(scholar_id, "grants"),
        patents=_read_list_json(scholar_id, "patents"),
        startups=_read_list_json(scholar_id, "startups"),
        attributed_metrics=read_json(
            dossier_path(scholar_id) / "attributed_metrics.json"
        ),
        peer_group=latest_record(scholar_id, "peer_group"),
        red_flags_active=active_red_flags(scholar_id),
        news=_recent_news(scholar_id, _NEWS_WINDOW),
    )
