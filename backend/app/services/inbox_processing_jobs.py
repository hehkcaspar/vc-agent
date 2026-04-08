"""In-memory registry for Process Inbox batch jobs.

Two routing paths:
- Path A: loose Inbox files → per-file extraction (Pass 1) → synoptic
  grouping + destination-aware routing (Pass 2) → moves.
- Path B: user-uploaded folders → fast structure-only routing (B1) →
  place_whole / join_existing / needs_sampling / unpack / needs_triage.
  Per-file metadata extraction enqueued post-placement, non-blocking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.datetime_support import utc_now, utc_now_iso
from app.models import Entity, WorkspaceNode
from app.schemas import metadata_json_to_dict
from app.services.direct_llm import generate_json_one_shot
from app.services.json_loose import parse_json_loose
from app.services.preset_registry import (
    load_inbox_folder_routing_instruction,
    load_inbox_grouping_instruction,
)
from app.services.storage import storage
from app.services.workspace import (
    Actor,
    WORKSPACE_TAXONOMY,
    WORKSPACE_TAXONOMY_ENTRIES,
    WorkspaceError,
    workspace_service,
)

logger = logging.getLogger(__name__)

MAX_JOBS = 100
INBOX_PATH = "Inbox"
NOTES_PATH = "WORKSPACE_NOTES.md"

_lock = asyncio.Lock()
_jobs: Dict[str, dict[str, Any]] = {}
_inflight: Dict[str, str] = {}  # entity_id -> job_id (only one active per entity)


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────

def _prune_locked() -> None:
    if len(_jobs) <= MAX_JOBS:
        return
    terminal = [
        (jid, rec.get("created_at"))
        for jid, rec in _jobs.items()
        if rec.get("status") in ("succeeded", "failed")
    ]
    terminal.sort(key=lambda x: x[1] or utc_now())
    while len(_jobs) > MAX_JOBS and terminal:
        jid, _ = terminal.pop(0)
        if jid in _jobs:
            del _jobs[jid]


def _new_job_record(entity_id: str) -> dict[str, Any]:
    jid = str(uuid.uuid4())
    return {
        "job_id": jid,
        "entity_id": entity_id,
        "status": "pending",
        "created_at": utc_now(),
        "total_items": 0,
        "processed_items": 0,
        "current_item": None,
        "moved": [],            # [{from, to, kind, batch_name}]
        "needs_triage": [],     # [{path, reason}]
        "errors": [],           # [{path, error}]
        "folder_decisions": [], # [{folder, action, destination, reason}]
        "error_message": None,
    }


async def create_inbox_job(entity_id: str) -> Tuple[str, bool]:
    """Create a new inbox processing job for an entity. Returns (job_id, scheduled).

    Only one job may run per entity at a time. If one is already in flight,
    returns its id with scheduled=False.
    """
    async with _lock:
        existing = _inflight.get(entity_id)
        if existing:
            rec = _jobs.get(existing)
            if rec and rec["status"] in ("pending", "running"):
                return existing, False
            del _inflight[entity_id]
        _prune_locked()
        rec = _new_job_record(entity_id)
        _jobs[rec["job_id"]] = rec
        _inflight[entity_id] = rec["job_id"]
        return rec["job_id"], True


async def get_inbox_job_status(entity_id: str, job_id: str) -> Optional[dict[str, Any]]:
    async with _lock:
        rec = _jobs.get(job_id)
        if not rec or rec["entity_id"] != entity_id:
            return None
        return {
            "job_id": rec["job_id"],
            "status": rec["status"],
            "total_items": rec["total_items"],
            "processed_items": rec["processed_items"],
            "current_item": rec["current_item"],
            "moved": list(rec["moved"]),
            "needs_triage": list(rec["needs_triage"]),
            "errors": list(rec["errors"]),
            "folder_decisions": list(rec["folder_decisions"]),
            "error_message": rec.get("error_message"),
        }


async def _set_status(job_id: str, **fields: Any) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if rec:
            rec.update(fields)


async def _set_succeeded(job_id: str, entity_id: str) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if rec:
            rec["status"] = "succeeded"
            rec["current_item"] = None
        if _inflight.get(entity_id) == job_id:
            del _inflight[entity_id]


async def _set_failed(job_id: str, entity_id: str, msg: str) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if rec:
            rec["status"] = "failed"
            rec["error_message"] = msg
            rec["current_item"] = None
        if _inflight.get(entity_id) == job_id:
            del _inflight[entity_id]


async def _append(job_id: str, key: str, item: dict[str, Any]) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if rec:
            rec[key].append(item)


async def _bump_processed(job_id: str, current: Optional[str]) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if rec:
            rec["processed_items"] += 1
            rec["current_item"] = current


# ──────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────

async def run_inbox_job(job_id: str) -> None:
    async with _lock:
        rec = _jobs.get(job_id)
        if not rec:
            return
        entity_id = rec["entity_id"]
        rec["status"] = "running"

    try:
        async with AsyncSessionLocal() as db:
            await _require_entity(db, entity_id)
            children = await workspace_service.list_children(db, entity_id, INBOX_PATH)

        if not children:
            await _set_succeeded(job_id, entity_id)
            return

        await _set_status(job_id, total_items=len(children))

        # Split into loose files vs folders
        loose_files = [c for c in children if c.node_type == "file"]
        folders = [c for c in children if c.node_type == "folder"]

        # Path B: process each folder one at a time
        for folder in folders:
            await _set_status(job_id, current_item=folder.path)
            try:
                await _run_path_b(job_id, entity_id, folder.id)
            except Exception as e:
                logger.exception("Path B failed for %s", folder.path)
                await _append(job_id, "errors", {"path": folder.path, "error": str(e)})
            await _bump_processed(job_id, None)

        # Path A: extract loose files first, then group + route
        if loose_files:
            await _run_path_a(job_id, entity_id, [f.id for f in loose_files])

        await _set_succeeded(job_id, entity_id)
    except Exception as e:
        logger.exception("Inbox job %s failed", job_id)
        await _set_failed(job_id, entity_id, str(e))


# ──────────────────────────────────────────────────────────────────────
# Path A: loose files
# ──────────────────────────────────────────────────────────────────────

async def _run_path_a(job_id: str, entity_id: str, file_ids: List[str]) -> None:
    """Per-file extraction (Pass 1) → synoptic grouping (Pass 2) → moves."""
    # Pass 1: extract each file's metadata
    summaries: List[dict[str, Any]] = []
    failed_extraction: List[Tuple[str, str, str]] = []  # (node_id, path, reason)
    for node_id in file_ids:
        async with AsyncSessionLocal() as db:
            node = await workspace_service.get_node_by_id(db, entity_id, node_id)
            if not node:
                # Node disappeared between iteration and now — count as processed.
                await _bump_processed(job_id, None)
                continue
            current_path = node.path
        await _set_status(job_id, current_item=current_path)
        try:
            extraction = await _extract_file_metadata(entity_id, node_id)
            if extraction is not None:
                summaries.append({
                    "id": node_id,
                    "name": _path_basename(current_path),
                    "path": current_path,
                    "one_liner": extraction.get("one_liner") or "",
                    "summary": extraction.get("summary") or "",
                    "document_kind": extraction.get("document_kind") or "unknown",
                    "primary_topics": extraction.get("primary_topics") or [],
                    "key_entities_or_parties": extraction.get("key_entities_or_parties") or [],
                })
                # Surface one_liner as the description so build_annotated_tree_text
                # shows it in agent context (the whole point of extraction).
                one_liner = (extraction.get("one_liner") or "").strip()
                if one_liner:
                    await _merge_metadata_field(entity_id, node_id, "description", one_liner)
            else:
                failed_extraction.append((node_id, current_path, "extraction_returned_none"))
        except Exception as e:
            logger.exception("Pass 1 extraction failed for node %s", node_id)
            await _append(job_id, "errors", {"path": current_path, "error": f"extract: {e}"})
            failed_extraction.append((node_id, current_path, f"extract: {e}"))
        await _bump_processed(job_id, None)

    # Stamp failed extractions so they don't sit in limbo without intake_routing.
    for fid, fpath, reason in failed_extraction:
        await _mark_needs_triage(entity_id, fid, reason, job_id, status="error")
        await _append(job_id, "needs_triage", {"path": fpath, "reason": reason})

    if not summaries:
        return

    # Pass 2: synoptic grouping
    async with AsyncSessionLocal() as db:
        destination_state = await _build_destination_state(db, entity_id)
        notes = await _load_workspace_notes(db, entity_id)

    decision = await asyncio.to_thread(
        _call_grouping_pass2, summaries, destination_state, notes,
    )
    if not decision:
        # If Pass 2 failed entirely, mark all as needs_triage
        for s in summaries:
            await _mark_needs_triage(entity_id, s["id"], "pass2_failed", job_id)
            await _append(job_id, "needs_triage", {"path": s["path"], "reason": "pass2_failed"})
        return

    # Execute groups
    accounted: set[str] = set()
    groups = decision.get("groups") or []
    for group in groups:
        file_ids_in_group = group.get("file_ids") or []
        accounted.update(file_ids_in_group)
        await _execute_group(job_id, entity_id, group)

    # Handle needs_triage
    for triage in decision.get("needs_triage") or []:
        fid = triage.get("file_id")
        if not fid:
            continue
        accounted.add(fid)
        reason = triage.get("reason") or "model_uncertain"
        path_taken = "loose"
        await _mark_needs_triage(entity_id, fid, reason, job_id, path_taken=path_taken)
        async with AsyncSessionLocal() as db:
            node = await workspace_service.get_node_by_id(db, entity_id, fid)
            if node:
                await _append(job_id, "needs_triage", {"path": node.path, "reason": reason})

    # Any file not accounted for: mark needs_triage as a safety net
    for s in summaries:
        if s["id"] not in accounted:
            await _mark_needs_triage(entity_id, s["id"], "unaccounted", job_id)
            await _append(job_id, "needs_triage", {"path": s["path"], "reason": "unaccounted"})


def _call_grouping_pass2(
    summaries: List[dict[str, Any]],
    destination_state: dict[str, Any],
    notes: str,
) -> Optional[dict[str, Any]]:
    """Synchronous Gemini call for Path A Pass 2."""
    system_instruction = load_inbox_grouping_instruction()
    payload = {
        "taxonomy": WORKSPACE_TAXONOMY_ENTRIES,
        "destination_state": destination_state,
        "workspace_notes": notes,
        "files": summaries,
    }
    user_message = (
        "Group these loose Inbox files into batches and route each batch into the "
        "workspace taxonomy. Join existing subfolders when possible. Return JSON "
        "matching the system schema.\n\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = generate_json_one_shot(system_instruction, [], user_message, None, False, None)
        parsed = parse_json_loose(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        logger.exception("Pass 2 grouping call failed")
    return None


def _is_under_taxonomy(path: str) -> bool:
    """True if `path` equals a taxonomy parent or is nested directly inside one.

    Used to validate LLM-supplied destinations and existing_folder targets so a
    bad model output can't escape the taxonomy (e.g. into Inbox/ or root).
    """
    if not path:
        return False
    for parent in WORKSPACE_TAXONOMY:
        if path == parent or path.startswith(parent + "/"):
            return True
    return False


async def _execute_group(
    job_id: str,
    entity_id: str,
    group: dict[str, Any],
) -> None:
    """Apply one group decision: create/join folder, move files, stamp metadata."""
    parent = group.get("parent")
    name = group.get("name")
    existing_folder = group.get("existing_folder")
    file_ids = group.get("file_ids") or []
    reason = group.get("reason") or ""
    confidence = group.get("confidence") or "medium"

    async def _triage_all(reason_str: str) -> None:
        for fid in file_ids:
            await _mark_needs_triage(entity_id, fid, reason_str, job_id)
            async with AsyncSessionLocal() as db:
                n = await workspace_service.get_node_by_id(db, entity_id, fid)
                if n:
                    await _append(job_id, "needs_triage",
                                  {"path": n.path, "reason": reason_str})

    # Resolve destination + validate against taxonomy.
    if existing_folder:
        if not _is_under_taxonomy(existing_folder):
            await _triage_all(f"invalid_existing_folder: {existing_folder}")
            return
        destination = existing_folder
        batch_name = _path_basename(existing_folder)
        joined_existing = True
    else:
        if parent not in WORKSPACE_TAXONOMY:
            await _triage_all(f"invalid_parent: {parent}")
            return
        destination = f"{parent}/{name}" if name else parent
        batch_name = name
        joined_existing = False

    # Track names already used inside this group's destination so two files
    # with the same basename in the same group don't collide on move.
    used_in_destination: set[str] = set()
    for fid in file_ids:
        try:
            async with AsyncSessionLocal() as db:
                node = await workspace_service.get_node_by_id(db, entity_id, fid)
                if not node:
                    continue
                from_path = node.path
                target_name = await _resolve_collision_async(
                    db, entity_id, destination, node.name, used_in_destination,
                )
                used_in_destination.add(target_name)
                target_path = f"{destination}/{target_name}"
                actor = Actor(type="system", ref=f"intake:{job_id}")
                await workspace_service.move(db, entity_id, from_path, target_path, actor)
                await db.commit()

            await _stamp_intake_routing(
                entity_id, fid,
                run_id=job_id,
                path_taken="loose",
                batch_name=batch_name,
                destination=destination,
                joined_existing=joined_existing,
                confidence=confidence,
                reason=reason,
                status="routed",
            )
            await _append(job_id, "moved", {
                "from": from_path,
                "to": target_path,
                "batch_name": batch_name,
                "joined_existing": joined_existing,
            })
        except WorkspaceError as e:
            await _append(job_id, "errors", {"path": fid, "error": f"move: {e}"})
        except Exception as e:
            logger.exception("Move failed for %s", fid)
            await _append(job_id, "errors", {"path": fid, "error": str(e)})


async def _resolve_collision_async(
    db, entity_id: str, parent_path: str, name: str, already_used: set[str],
) -> str:
    """Return a name that doesn't collide with existing nodes at `parent_path`
    or with names already taken in this batch (`already_used`)."""
    candidate = name
    if candidate not in already_used:
        existing = await workspace_service.get_node_by_path(
            db, entity_id, f"{parent_path}/{candidate}",
        )
        if not existing:
            return candidate
    stem, dot, ext = name.partition(".")
    suffix_template = "{stem} ({n}){dot}{ext}" if dot else "{stem} ({n})"
    n = 1
    while True:
        candidate = suffix_template.format(stem=stem, n=n, dot=dot, ext=ext)
        if candidate in already_used:
            n += 1
            continue
        existing = await workspace_service.get_node_by_path(
            db, entity_id, f"{parent_path}/{candidate}",
        )
        if not existing:
            return candidate
        n += 1


# ──────────────────────────────────────────────────────────────────────
# Path B: user-uploaded folders
# ──────────────────────────────────────────────────────────────────────

async def _run_path_b(job_id: str, entity_id: str, folder_id: str) -> None:
    async with AsyncSessionLocal() as db:
        folder = await workspace_service.get_node_by_id(db, entity_id, folder_id)
        if not folder or folder.node_type != "folder":
            return
        folder_name = folder.name
        folder_path = folder.path
        descendants = await _get_folder_descendants(db, entity_id, folder_path)
        tree_listing = _build_tree_listing(folder_path, descendants)
        destination_state = await _build_destination_state(db, entity_id)
        notes = await _load_workspace_notes(db, entity_id)

    # Step B1: fast routing
    decision = await asyncio.to_thread(
        _call_folder_routing_b1, folder_name, tree_listing, destination_state, notes,
    )
    if not decision:
        await _append(job_id, "needs_triage",
                      {"path": folder_path, "reason": "b1_failed"})
        return

    action = decision.get("action") or "needs_triage"

    # Sampling fallback
    if action == "needs_sampling":
        sampled = await _run_path_b_sampling(
            job_id, entity_id, folder_id, folder_path, descendants,
        )
        # Re-run B1 with samples
        async with AsyncSessionLocal() as db:
            destination_state = await _build_destination_state(db, entity_id)
            notes = await _load_workspace_notes(db, entity_id)
        decision = await asyncio.to_thread(
            _call_folder_routing_b1_with_samples,
            folder_name, tree_listing, destination_state, notes, sampled,
        )
        if not decision:
            await _append(job_id, "needs_triage",
                          {"path": folder_path, "reason": "b1_resample_failed"})
            return
        action = decision.get("action") or "needs_triage"

    await _append(job_id, "folder_decisions", {
        "folder": folder_path,
        "action": action,
        "destination": decision.get("destination"),
        "join_existing": decision.get("join_existing"),
        "rename_root_to": decision.get("rename_root_to"),
        "reason": decision.get("reason"),
    })

    if action == "place_whole":
        await _execute_path_b_place_whole(job_id, entity_id, folder_id, decision)
    elif action == "join_existing":
        await _execute_path_b_join(job_id, entity_id, folder_id, decision)
    elif action == "unpack":
        await _execute_path_b_unpack(job_id, entity_id, folder_id, folder_path)
    else:  # needs_triage or unknown
        await _append(job_id, "needs_triage",
                      {"path": folder_path, "reason": decision.get("reason") or "needs_triage"})


def _call_folder_routing_b1(
    folder_name: str,
    tree_listing: str,
    destination_state: dict[str, Any],
    notes: str,
) -> Optional[dict[str, Any]]:
    return _call_folder_routing_b1_with_samples(
        folder_name, tree_listing, destination_state, notes, None,
    )


def _call_folder_routing_b1_with_samples(
    folder_name: str,
    tree_listing: str,
    destination_state: dict[str, Any],
    notes: str,
    samples: Optional[List[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    system_instruction = load_inbox_folder_routing_instruction()
    payload: dict[str, Any] = {
        "top_folder_name": folder_name,
        "tree_listing": tree_listing,
        "taxonomy": WORKSPACE_TAXONOMY_ENTRIES,
        "destination_state": destination_state,
        "workspace_notes": notes,
    }
    if samples is not None:
        payload["samples"] = samples
        user_message = (
            "Re-route this folder. You previously requested sampling; the sampled file "
            "extractions are now provided. Return JSON matching the system schema.\n\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
        )
    else:
        user_message = (
            "Route this user-uploaded folder. Decide place_whole / join_existing / "
            "needs_sampling / unpack / needs_triage based on structure alone. Return "
            "JSON matching the system schema.\n\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
        )
    try:
        raw = generate_json_one_shot(system_instruction, [], user_message, None, False, None)
        parsed = parse_json_loose(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        logger.exception("B1 folder routing call failed")
    return None


async def _run_path_b_sampling(
    job_id: str,
    entity_id: str,
    folder_id: str,
    folder_path: str,
    descendants: List[WorkspaceNode],
) -> List[dict[str, Any]]:
    """Sample up to N files from the folder, extract their metadata, return summaries."""
    files = [d for d in descendants if d.node_type == "file"]
    n = min(settings.WORKSPACE_INTAKE_SAMPLE_SIZE, len(files))
    if n == 0:
        return []
    # Diversify across subfolders: sort by parent path then sample
    files.sort(key=lambda f: f.path)
    if n >= len(files):
        sampled_nodes = files
    else:
        # Spread by index
        step = len(files) / n
        sampled_nodes = [files[int(i * step)] for i in range(n)]

    samples: List[dict[str, Any]] = []
    for node in sampled_nodes:
        try:
            extraction = await _extract_file_metadata(entity_id, node.id)
            if extraction is not None:
                samples.append({
                    "id": node.id,
                    "path": node.path,
                    "one_liner": extraction.get("one_liner") or "",
                    "summary": extraction.get("summary") or "",
                    "document_kind": extraction.get("document_kind") or "unknown",
                })
        except Exception:
            logger.exception("Sample extraction failed for %s", node.id)
    return samples


async def _execute_path_b_place_whole(
    job_id: str,
    entity_id: str,
    folder_id: str,
    decision: dict[str, Any],
) -> None:
    destination = decision.get("destination")
    rename_root_to = decision.get("rename_root_to")
    reason = decision.get("reason") or ""
    confidence = decision.get("confidence") or "medium"

    if destination not in WORKSPACE_TAXONOMY:
        await _append(job_id, "errors",
                      {"path": folder_id, "error": f"invalid destination: {destination}"})
        return

    async with AsyncSessionLocal() as db:
        folder = await workspace_service.get_node_by_id(db, entity_id, folder_id)
        if not folder:
            return
        from_path = folder.path
        new_name = rename_root_to or folder.name
        target_path = f"{destination}/{new_name}"
        actor = Actor(type="system", ref=f"intake:{job_id}")
        try:
            await workspace_service.move(db, entity_id, from_path, target_path, actor)
            await db.commit()
        except WorkspaceError as e:
            await _append(job_id, "errors", {"path": from_path, "error": str(e)})
            return

        # Stamp metadata on every contained file
        descendants = await _get_folder_descendants(db, entity_id, target_path)

    file_ids = [d.id for d in descendants if d.node_type == "file"]
    for fid in file_ids:
        await _stamp_intake_routing(
            entity_id, fid,
            run_id=job_id,
            path_taken="folder_place_whole",
            batch_name=new_name,
            destination=target_path,
            joined_existing=False,
            confidence=confidence,
            reason=reason,
            status="routed",
        )

    await _append(job_id, "moved",
                  {"from": from_path, "to": target_path, "batch_name": new_name,
                   "joined_existing": False})

    # Background extraction for every file inside
    await _enqueue_background_extraction(entity_id, file_ids)


async def _execute_path_b_join(
    job_id: str,
    entity_id: str,
    folder_id: str,
    decision: dict[str, Any],
) -> None:
    """Merge a folder's contents into an existing destination subfolder.

    Strategy: move each direct child of the source folder under the destination.
    If a child name collides, suffix it with the source folder name to disambiguate.
    Then delete the now-empty source folder.
    """
    target = decision.get("join_existing")
    reason = decision.get("reason") or ""
    confidence = decision.get("confidence") or "medium"

    if not target or not _is_under_taxonomy(target):
        async with AsyncSessionLocal() as db:
            folder = await workspace_service.get_node_by_id(db, entity_id, folder_id)
            folder_path = folder.path if folder else folder_id
        await _append(job_id, "needs_triage",
                      {"path": folder_path, "reason": f"invalid join target: {target}"})
        return

    async with AsyncSessionLocal() as db:
        folder = await workspace_service.get_node_by_id(db, entity_id, folder_id)
        if not folder:
            return
        target_node = await workspace_service.get_node_by_path(db, entity_id, target)
        if not target_node or target_node.node_type != "folder":
            await _append(job_id, "errors",
                          {"path": folder.path, "error": f"join target missing: {target}"})
            return
        source_path = folder.path
        source_name = folder.name
        children = await workspace_service.list_children(db, entity_id, source_path)
        actor = Actor(type="system", ref=f"intake:{job_id}")

        moved_paths: List[Tuple[str, str]] = []
        for child in children:
            base_target = f"{target}/{child.name}"
            existing = await workspace_service.get_node_by_path(db, entity_id, base_target)
            if existing:
                # Disambiguate
                stem, dot, ext = child.name.partition(".")
                disambig_name = f"{stem} ({source_name}){dot}{ext}" if dot else f"{child.name} ({source_name})"
                base_target = f"{target}/{disambig_name}"
            try:
                await workspace_service.move(db, entity_id, child.path, base_target, actor)
                moved_paths.append((child.path, base_target))
            except WorkspaceError as e:
                await _append(job_id, "errors", {"path": child.path, "error": str(e)})

        # Delete now-empty source
        try:
            remaining = await workspace_service.list_children(db, entity_id, source_path)
            if not remaining:
                await workspace_service.delete_node(db, entity_id, source_path, actor)
        except Exception:
            logger.exception("Failed to delete empty source folder %s", source_path)
        await db.commit()

        # Collect file ids inside target for stamping
        descendants = await _get_folder_descendants(db, entity_id, target)

    file_ids = [d.id for d in descendants if d.node_type == "file"]
    for fid in file_ids:
        await _stamp_intake_routing(
            entity_id, fid,
            run_id=job_id,
            path_taken="folder_join",
            batch_name=_path_basename(target),
            destination=target,
            joined_existing=True,
            confidence=confidence,
            reason=reason,
            status="routed",
            overwrite_only_if_missing=True,  # don't clobber existing files' routing
        )

    for src, dst in moved_paths:
        await _append(job_id, "moved",
                      {"from": src, "to": dst, "batch_name": _path_basename(target),
                       "joined_existing": True})

    # Background extraction for newly placed files only
    new_file_ids = await _filter_files_under_paths(entity_id, [dst for _, dst in moved_paths])
    await _enqueue_background_extraction(entity_id, new_file_ids)


async def _execute_path_b_unpack(
    job_id: str,
    entity_id: str,
    folder_id: str,
    folder_path: str,
) -> None:
    """Flatten a dumping-ground folder: move every contained file to Inbox/.

    On filename collision in Inbox, suffix with the source folder name.
    Then delete the now-empty source. Subsequent Process Inbox runs will pick
    up the loose files via Path A.
    """
    actor = Actor(type="system", ref=f"intake:{job_id}")
    async with AsyncSessionLocal() as db:
        descendants = await _get_folder_descendants(db, entity_id, folder_path)
        files = [d for d in descendants if d.node_type == "file"]
        moved_paths: List[Tuple[str, str]] = []
        source_name = _path_basename(folder_path)
        for f in files:
            base_target = f"{INBOX_PATH}/{f.name}"
            existing = await workspace_service.get_node_by_path(db, entity_id, base_target)
            if existing:
                stem, dot, ext = f.name.partition(".")
                disambig_name = f"{stem} ({source_name}){dot}{ext}" if dot else f"{f.name} ({source_name})"
                base_target = f"{INBOX_PATH}/{disambig_name}"
            try:
                await workspace_service.move(db, entity_id, f.path, base_target, actor)
                moved_paths.append((f.path, base_target))
            except WorkspaceError as e:
                await _append(job_id, "errors", {"path": f.path, "error": str(e)})
        # Delete the source folder tree (now containing only empty subfolders)
        try:
            await workspace_service.delete_node(db, entity_id, folder_path, actor)
        except Exception:
            logger.exception("Failed to delete unpacked folder %s", folder_path)
        await db.commit()

    for src, dst in moved_paths:
        await _append(job_id, "moved",
                      {"from": src, "to": dst, "batch_name": None, "joined_existing": False})


# ──────────────────────────────────────────────────────────────────────
# Per-file metadata extraction (shared by Path A Pass 1 + Path B sampling)
# ──────────────────────────────────────────────────────────────────────

async def _extract_file_metadata(entity_id: str, node_id: str) -> Optional[dict[str, Any]]:
    """Run the existing single-file metadata preprocess job inline.

    Returns the normalized extraction dict (gemini_preprocessed.extraction)
    or None on failure.
    """
    from app.services.metadata_preprocess_jobs import (
        create_or_reuse_job,
        run_metadata_preprocess_job,
    )
    job_id, scheduled = await create_or_reuse_job(entity_id, node_id)
    if scheduled:
        await run_metadata_preprocess_job(job_id)
    else:
        # Wait briefly for an in-flight job (shouldn't normally happen here)
        for _ in range(60):
            from app.services.metadata_preprocess_jobs import get_job_status
            status = await get_job_status(entity_id, job_id)
            if status and status.get("status") in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.5)

    # Read back the extraction from the node's metadata_json
    async with AsyncSessionLocal() as db:
        node = await workspace_service.get_node_by_id(db, entity_id, node_id)
        if not node:
            return None
        meta = metadata_json_to_dict(getattr(node, "metadata_json", None)) or {}
        gp = meta.get("gemini_preprocessed") or {}
        extraction = gp.get("extraction")
        if isinstance(extraction, dict):
            return extraction
    return None


async def _enqueue_background_extraction(entity_id: str, node_ids: List[str]) -> None:
    """Fire-and-forget per-file extraction for a list of node ids.

    Uses asyncio.create_task to avoid blocking the inbox job. Sequential
    inside the task to respect Gemini rate limits.
    """
    if not node_ids:
        return

    async def _runner(ids: List[str]) -> None:
        from app.services.metadata_preprocess_jobs import (
            create_or_reuse_job,
            run_metadata_preprocess_job,
        )
        for nid in ids:
            try:
                jid, scheduled = await create_or_reuse_job(entity_id, nid)
                if scheduled:
                    await run_metadata_preprocess_job(jid)
            except Exception:
                logger.exception("Background extraction failed for %s", nid)

    asyncio.create_task(_runner(list(node_ids)))


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

async def _require_entity(db, entity_id: str) -> Entity:
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise ValueError("entity_not_found")
    return entity


async def _build_destination_state(db, entity_id: str) -> dict[str, Any]:
    """For each taxonomy parent, list its existing immediate subfolders.

    Returns: {parent_path: {"existing_subfolders": [list of full paths]}}
    """
    state: dict[str, Any] = {}
    all_nodes = await workspace_service.get_all_nodes(db, entity_id)
    by_path = {n.path: n for n in all_nodes}
    for parent in WORKSPACE_TAXONOMY:
        existing_subfolders: List[str] = []
        if parent in by_path:
            prefix = parent + "/"
            for n in all_nodes:
                if n.node_type != "folder":
                    continue
                if not n.path.startswith(prefix):
                    continue
                # Only direct children
                tail = n.path[len(prefix):]
                if "/" not in tail:
                    existing_subfolders.append(n.path)
        state[parent] = {"existing_subfolders": sorted(existing_subfolders)}
    return state


async def _load_workspace_notes(db, entity_id: str) -> str:
    node = await workspace_service.get_node_by_path(db, entity_id, NOTES_PATH)
    if not node or not node.storage_key:
        return ""
    try:
        raw = await storage.read_file(node.storage_key)
        text = raw.decode("utf-8", errors="replace").strip()
        if text.startswith("# Workspace Notes"):
            text = text.split("\n", 1)[-1].strip()
        if text == "_Add cross-file context here._":
            return ""
        return text
    except Exception:
        return ""


async def _get_folder_descendants(
    db, entity_id: str, folder_path: str,
) -> List[WorkspaceNode]:
    all_nodes = await workspace_service.get_all_nodes(db, entity_id)
    prefix = folder_path.rstrip("/") + "/"
    return [n for n in all_nodes if n.path.startswith(prefix)]


def _build_tree_listing(folder_path: str, descendants: List[WorkspaceNode]) -> str:
    """Build a human-readable indented listing for the folder routing prompt."""
    prefix = folder_path.rstrip("/") + "/"
    lines: List[str] = []
    sorted_desc = sorted(descendants, key=lambda n: n.path)
    for n in sorted_desc:
        rel = n.path[len(prefix):]
        if n.node_type == "folder":
            lines.append(f"{rel}/")
        elif n.node_type == "file":
            size = _format_size(n.size_bytes)
            mime = n.mime_type or "application/octet-stream"
            lines.append(f"{rel}  ({size}, {mime})")
        else:  # bookmark
            lines.append(f"{rel}  (url)")
    return "\n".join(lines)


def _format_size(size_bytes: Optional[int]) -> str:
    if not size_bytes:
        return "0B"
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    return f"{size_bytes / (1024 * 1024):.1f}MB"


def _path_basename(path: str) -> str:
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


async def _filter_files_under_paths(
    entity_id: str, paths: List[str],
) -> List[str]:
    if not paths:
        return []
    ids: List[str] = []
    async with AsyncSessionLocal() as db:
        for p in paths:
            node = await workspace_service.get_node_by_path(db, entity_id, p)
            if node and node.node_type == "file":
                ids.append(node.id)
            elif node and node.node_type == "folder":
                descendants = await _get_folder_descendants(db, entity_id, p)
                ids.extend(d.id for d in descendants if d.node_type == "file")
    return ids


async def _stamp_intake_routing(
    entity_id: str,
    node_id: str,
    *,
    run_id: str,
    path_taken: str,
    batch_name: Optional[str],
    destination: str,
    joined_existing: bool,
    confidence: str,
    reason: str,
    status: str,
    overwrite_only_if_missing: bool = False,
) -> None:
    async with AsyncSessionLocal() as db:
        node = await workspace_service.get_node_by_id(db, entity_id, node_id)
        if not node:
            return
        meta = metadata_json_to_dict(getattr(node, "metadata_json", None)) or {}
        if overwrite_only_if_missing and meta.get("intake_routing"):
            return
        meta["intake_routing"] = {
            "at": utc_now_iso(),
            "run_id": run_id,
            "path_taken": path_taken,
            "batch_name": batch_name,
            "destination": destination,
            "joined_existing": joined_existing,
            "confidence": confidence,
            "reason": reason,
            "status": status,
        }
        node.metadata_json = json.dumps(meta, ensure_ascii=False)
        node.updated_at = utc_now()
        await db.commit()


async def _mark_needs_triage(
    entity_id: str,
    node_id: str,
    reason: str,
    job_id: str,
    *,
    path_taken: str = "loose",
    status: str = "needs_triage",
) -> None:
    async with AsyncSessionLocal() as db:
        node = await workspace_service.get_node_by_id(db, entity_id, node_id)
        if not node:
            return
        meta = metadata_json_to_dict(getattr(node, "metadata_json", None)) or {}
        meta["intake_routing"] = {
            "at": utc_now_iso(),
            "run_id": job_id,
            "path_taken": path_taken,
            "batch_name": None,
            "destination": node.path,
            "joined_existing": False,
            "confidence": "low",
            "reason": reason,
            "status": status,
        }
        node.metadata_json = json.dumps(meta, ensure_ascii=False)
        node.updated_at = utc_now()
        await db.commit()


async def _merge_metadata_field(
    entity_id: str,
    node_id: str,
    field: str,
    value: Any,
) -> None:
    """Merge a single key into a node's metadata_json without disturbing other fields.

    Used to surface extraction.one_liner as metadata.description so the agent
    context tree (build_annotated_tree_text) shows file summaries inline.
    """
    async with AsyncSessionLocal() as db:
        node = await workspace_service.get_node_by_id(db, entity_id, node_id)
        if not node:
            return
        meta = metadata_json_to_dict(getattr(node, "metadata_json", None)) or {}
        meta[field] = value
        node.metadata_json = json.dumps(meta, ensure_ascii=False)
        node.updated_at = utc_now()
        await db.commit()
