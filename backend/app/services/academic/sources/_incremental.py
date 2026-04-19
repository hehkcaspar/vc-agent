"""Incremental-mode helpers shared across grounded-search sources.

Each source has two modes:

- ``bootstrap`` — full-sweep query, no prior-context exclusion. Used on
  first run of a scholar, or when the user forces a fresh evaluation.
- ``incremental`` — time-windowed query with known-items fed back as
  context. Used by heartbeat ticks. Saves tokens, sharpens recall on
  deltas, lets startups_web explicitly re-check status transitions.

The "mode" flag from ``trigger_refresh`` is advisory — the real
bootstrap condition is **no prior snapshot exists**. If
``incremental_cutoff`` returns None, callers should fall back to the
bootstrap prompt regardless of the declared mode.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..fact_store import last_snapshot_for_source


def incremental_cutoff(
    scholar_id: str,
    source_id: str,
    *,
    buffer_hours: int = 24,
) -> datetime | None:
    """Return the prior-snapshot timestamp minus a safety buffer.

    None means "no prior run" — caller should use bootstrap prompt.
    The buffer (default 24h) catches items that straddled the last
    tick: e.g. an article published 10min before the last snapshot
    but only crawled after.
    """
    snap = last_snapshot_for_source(scholar_id, source_id)
    if not snap:
        return None
    created = snap.get("created_at")
    if not isinstance(created, str):
        return None
    try:
        # `created_at` is ISO 8601 with offset, e.g. '2026-04-18T22:48:24.707749+00:00'
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt - timedelta(hours=buffer_hours)


def format_cutoff(dt: datetime | None) -> str:
    """ISO date (YYYY-MM-DD) for prompt injection. Empty str if None."""
    return dt.date().isoformat() if dt else ""


def format_known_titles(
    items: list[dict[str, Any]],
    *,
    max_items: int = 30,
    key: str = "title",
) -> str:
    """Bullet-list of the most-recent N titles, newest first.

    Accepts items sorted in any order — sorts by `published_date` /
    `id` fallback to surface recent entries. Caller is responsible
    for passing a list that has the relevant sort key.
    """
    if not items:
        return "(none yet)"
    lines: list[str] = []
    for i, it in enumerate(items[:max_items]):
        title = (it.get(key) or "").strip()
        if not title:
            continue
        src = (it.get("source") or "").strip()
        date = (it.get("published_date") or "").strip()
        tail_bits = [b for b in (src, date) if b]
        tail = f" — {' · '.join(tail_bits)}" if tail_bits else ""
        lines.append(f"- {title}{tail}")
    return "\n".join(lines) if lines else "(none yet)"


def format_known_ventures(
    items: list[dict[str, Any]],
    *,
    max_items: int = 30,
) -> str:
    """'[E0] OmniML (acquired, 2021) — edge ML optimization' lines."""
    if not items:
        return "(none yet)"
    lines: list[str] = []
    for i, it in enumerate(items[:max_items]):
        name = (it.get("name") or "").strip()
        if not name:
            continue
        status = (it.get("current_status") or "unknown").strip()
        year = it.get("founded_year")
        one_liner = (it.get("one_liner") or "").strip()
        # Keep the one-liner short — this block can get long for
        # scholars with many ventures, and we only need enough signal
        # for the LLM to identify each entry.
        if len(one_liner) > 140:
            one_liner = one_liner[:137] + "…"
        head = f"[E{i}] {name} ({status}"
        if year:
            head += f", founded {year}"
        head += ")"
        if one_liner:
            head += f" — {one_liner}"
        lines.append(head)
    return "\n".join(lines) if lines else "(none yet)"


def format_known_pending_patents(
    items: list[dict[str, Any]],
    *,
    max_items: int = 30,
) -> str:
    """Pending patent applications (no grant_date yet) for grant re-check."""
    pending = [
        it for it in items
        if not (it.get("grant_date") or "").strip()
        and (it.get("patent_number") or "").strip()
    ]
    if not pending:
        return "(none pending)"
    lines: list[str] = []
    for i, it in enumerate(pending[:max_items]):
        num = (it.get("patent_number") or "").strip()
        title = (it.get("title") or "").strip()
        filed = (it.get("filing_date") or "").strip()
        if len(title) > 100:
            title = title[:97] + "…"
        suffix = f" — filed {filed}" if filed else ""
        lines.append(f"[P{i}] {num} — \"{title}\"{suffix}")
    return "\n".join(lines)


def should_use_bootstrap(
    mode: str,
    cutoff: datetime | None,
) -> bool:
    """Decide whether to fall back to the bootstrap prompt.

    Bootstrap wins if caller explicitly asked for it OR there's no
    prior snapshot to base an incremental query on.
    """
    return mode == "bootstrap" or cutoff is None


def sort_items_recent_first(
    items: list[dict[str, Any]],
    *,
    date_key: str = "published_date",
) -> list[dict[str, Any]]:
    """Return items sorted newest-first by date_key then id fallback."""
    return sorted(
        items,
        key=lambda r: (r.get(date_key) or "", r.get("id") or ""),
        reverse=True,
    )
