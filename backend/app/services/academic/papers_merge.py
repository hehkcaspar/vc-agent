"""Source-aware merge for ``papers.json``.

papers.json is written by three producers:

    google_scholar — primary, fresh papers via SerpAPI / direct scrape
    semantic_scholar — enrichment, rich metadata (authorId, DOI,
                       s2_fields, influential_citations)
    stub — routed from destinations._accept_into_papers when
           verify/triage decides a non-paper source's item is actually
           a paper (e.g. a patent that's really a research paper)

Every write goes through ``_merge_papers_by_priority`` so the
destination-autonomy promise holds: once a row is in papers.json, the
priority rules decide what survives a later write.

Priority matrix (match key = normalized title):

    existing   | incoming=GS         | incoming=SS                      | incoming=stub
    -----------|---------------------|----------------------------------|--------------
    (none)     | append              | append                           | append
    GS         | overwrite (refresh) | SS enriches in place; GS keeps   | no-op
               |                     | title/year/citations/venue       |
    SS         | GS wins recency;    | overwrite (refresh)              | no-op
               | SS authorId/DOI/    |                                  |
               | s2_fields preserved |                                  |
    stub       | GS wins wholesale;  | SS wins wholesale;               | no-op
               | audit preserved     | audit preserved                  |
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Literal

IncomingSource = Literal["google_scholar", "semantic_scholar", "stub"]

_TITLE_WS = re.compile(r"\s+")

# Fields SS is authoritative for — enrichment that GS cannot produce.
# When SS writes INTO an existing GS row, these are the fields it adds
# (without touching the GS row's recency fields).
_SS_ENRICHMENT_FIELDS = (
    "authorId",  # on each author item, handled separately
    "external_ids",
    "s2_fields",
    "influential_citations",
    "fields",
    "publication_types",
    "publication_date",
    "journal",
)

# Audit fields propagated across merges so we never lose provenance.
_STUB_AUDIT_FIELDS = ("_origin", "_original_url", "_routed_at")


def _normalize_title(title: str) -> str:
    """Match key for paper dedup across sources + tombstones."""
    if not title:
        return ""
    t = title.strip().lower()
    t = _TITLE_WS.sub(" ", t)
    return t.strip('.,;:!?"\'()[]{}- ')


def _classify_existing(item: dict[str, Any]) -> str:
    """Return ``'gs' | 'ss' | 'stub'``. SS rows historically lack a
    ``_source`` marker, so we treat unmarked rows as SS."""
    if not isinstance(item, dict):
        return "ss"
    if item.get("_stub"):
        return "stub"
    if item.get("_source") == "google_scholar":
        return "gs"
    return "ss"


def _has_author_ids(authors: Any) -> bool:
    if not isinstance(authors, list):
        return False
    return any(
        isinstance(a, dict) and a.get("authorId") for a in authors
    )


def _copy_stub_audit(src: dict[str, Any], dest: dict[str, Any]) -> None:
    for k in _STUB_AUDIT_FIELDS:
        if k in src:
            dest[k] = src[k]


def _merge_gs_over_ss(
    existing_ss: dict[str, Any], incoming_gs: dict[str, Any],
) -> dict[str, Any]:
    """GS wins on recency (title/year/citations/venue/authors); SS's
    enrichment fields (authorId / DOI / s2_fields / …) are preserved.
    """
    new = dict(incoming_gs)
    new["_source"] = "google_scholar"
    for f in _SS_ENRICHMENT_FIELDS:
        if f in existing_ss and f not in new:
            new[f] = existing_ss[f]
    # Prefer SS's authors list when it carries authorId (needed for
    # attributed_metrics). GS-derived _author_position stays on the row.
    if _has_author_ids(existing_ss.get("authors")):
        new["authors"] = existing_ss["authors"]
    new["_was_ss"] = True
    # Carry routing-stub audit through if the SS row inherited it.
    _copy_stub_audit(existing_ss, new)
    if existing_ss.get("_was_stub"):
        new["_was_stub"] = True
    return new


def _merge_ss_over_gs(
    existing_gs: dict[str, Any], incoming_ss: dict[str, Any],
) -> dict[str, Any]:
    """SS enriches the GS row IN PLACE. GS keeps recency authority
    (title/year/citations/venue/_author_position)."""
    new = dict(existing_gs)
    for f in _SS_ENRICHMENT_FIELDS:
        if f in incoming_ss:
            new[f] = incoming_ss[f]
    if _has_author_ids(incoming_ss.get("authors")):
        new["authors"] = incoming_ss["authors"]
    new["_was_ss"] = True
    return new


def _merge_over_stub(
    existing_stub: dict[str, Any],
    incoming: dict[str, Any],
    incoming_source: IncomingSource,
) -> dict[str, Any]:
    """Either SS or GS wins wholesale over a stub; routing audit is
    preserved, `_stub` flag dropped, `_was_stub=True` set."""
    new = dict(incoming)
    new["_source"] = incoming_source
    _copy_stub_audit(existing_stub, new)
    new["_was_stub"] = True
    new.pop("_stub", None)
    return new


def _refresh_same_source(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    incoming_source: IncomingSource,
) -> dict[str, Any]:
    """GS-over-GS or SS-over-SS — overwrite but preserve audit flags."""
    new = dict(incoming)
    new["_source"] = incoming_source
    _copy_stub_audit(existing, new)
    for audit in ("_was_stub", "_was_ss"):
        if existing.get(audit):
            new[audit] = True
    return new


def _apply_source_marker(row: dict[str, Any], source: IncomingSource) -> dict[str, Any]:
    r = dict(row)
    r["_source"] = source
    return r


def _apply_priority(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    existing_type: str,
    incoming_source: IncomingSource,
) -> dict[str, Any]:
    # stub incoming never overwrites an existing row — destinations.py
    # already ran its own accept policy before we ever saw this call.
    if incoming_source == "stub":
        return existing

    if existing_type == "stub":
        return _merge_over_stub(existing, incoming, incoming_source)

    if existing_type == "ss" and incoming_source == "google_scholar":
        return _merge_gs_over_ss(existing, incoming)

    if existing_type == "gs" and incoming_source == "semantic_scholar":
        return _merge_ss_over_gs(existing, incoming)

    # Same-source refresh (GS-over-GS, SS-over-SS).
    return _refresh_same_source(existing, incoming, incoming_source)


def normalize_ledger_row(row: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a pre-existing papers.json row before it goes into
    the merge. Pre-refactor rows were written without a ``_source``
    marker; this helper stamps them as ``semantic_scholar`` so
    downstream consumers and :func:`_classify_existing` see consistent
    provenance. Idempotent — rows that already carry ``_source`` or
    ``_stub`` are returned unchanged.

    Callers (source modules that read ``papers.json`` off disk) must
    map this over the existing-rows list before calling the merge.
    The merge itself stays pure.
    """
    if row.get("_source") or row.get("_stub"):
        return row
    out = dict(row)
    out["_source"] = "semantic_scholar"
    return out


def _merge_papers_by_priority(
    incoming: Iterable[dict[str, Any]],
    existing: Iterable[dict[str, Any]],
    *,
    incoming_source: IncomingSource,
) -> list[dict[str, Any]]:
    """Merge a batch of incoming rows into the existing ledger under
    the source-priority matrix. Pure function: input lists are not
    mutated; a fresh list of merged rows is returned. Order: existing
    rows keep their positions; unmatched incoming rows are appended.

    Callers are responsible for running :func:`normalize_ledger_row`
    on ``existing`` first — the merge trusts that rows already carry
    ``_source`` or ``_stub`` markers and does not backfill them.
    """
    existing_list = [it for it in existing if isinstance(it, dict)]
    merged: list[dict[str, Any]] = list(existing_list)

    # Build title → position index. Later duplicates shadow earlier
    # ones — same behavior as the old _merge_with_stubs.
    idx_by_title: dict[str, int] = {}
    for i, it in enumerate(merged):
        key = _normalize_title(it.get("title") or "")
        if key:
            idx_by_title[key] = i

    for row in incoming:
        if not isinstance(row, dict):
            continue
        key = _normalize_title(row.get("title") or "")
        if not key:
            # Can't dedup without a title. Skip — safer than appending
            # an un-keyed row that will never merge later.
            continue
        pos = idx_by_title.get(key)
        if pos is None:
            new_row = _apply_source_marker(row, incoming_source)
            merged.append(new_row)
            idx_by_title[key] = len(merged) - 1
        else:
            existing_row = merged[pos]
            existing_type = _classify_existing(existing_row)
            merged[pos] = _apply_priority(
                existing_row, row, existing_type, incoming_source,
            )
    return merged


__all__ = [
    "_merge_papers_by_priority",
    "_normalize_title",
    "_classify_existing",
    "_has_author_ids",
    "normalize_ledger_row",
]
