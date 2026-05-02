"""Initial Screening v2 — split Phase-1 research into survey + 6 parallel
section agents, then reuse v1's compose + review stages.

Rationale: v1's monolithic Phase-1 agent owns one global tool budget for
six different research domains, and a rich workspace (e.g. CyberNexus's
Series Pre-A closing binder) can exhaust the LangGraph recursion limit
before every section gets a write. v2 gives each section its own tight
budget and runs them concurrently — total wall-time is bounded by the
slowest section agent, not the sum.

Architecture:

1. **Survey agent** (sequential, ~8 tool calls)
   - Tools: workspace_* (no web_search, no writes).
   - Output: JSON blob identifying primary source docs + per-section hints.

2. **6 section agents in parallel** (~12-15 tool calls each)
   - Tools: workspace_* + web_search + propose_fact_update.
   - Each writes exactly one JSON: ``Deliverables/Analysis/initial_screening_v2/{section}.json``.
   - Failure isolation — one section failing still lets the other five write.

3. **Compose** — reuses ``initial_screening_job.run_compose_stage`` with
   the v2 analysis dir + memo path.

4. **Review** — reuses ``initial_screening_job.run_review_stage`` with
   the v2 memo + review-notes paths.

All agents share the same entity brief / history but have independent
LangChain agent instances with their own system prompts + recursion limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import Entity
from app.services.agent_harness import (
    create_react_portfolio_agent,
    history_to_lc_messages,
    invoke_react_portfolio_agent,
)
from app.services.initial_screening_job import (
    run_compose_stage,
    run_review_stage,
)
from app.services.preset_registry import (
    V2_SECTION_IDS,
    render_initial_screening_v2_section,
    render_initial_screening_v2_survey,
)
from app.services.prompt_assembly import EntityBrief
from app.services.storage import storage
from app.services.workspace import Actor, WorkspaceService

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

V2_ANALYSIS_DIR = "Deliverables/Analysis/initial_screening_v2"
V2_MEMO_PATH = "Deliverables/Memos/initial_screening_v2.md"
V2_REVIEW_NOTES_PATH = "Deliverables/Memos/initial_screening_v2_review_notes.md"

# Tight per-agent recursion limits. Each agent's tool allowlist is
# narrow enough that 30 calls is a real signal of thrashing, not just
# a default ceiling.
SURVEY_RECURSION_LIMIT = 25
# Section limit: LangGraph counts every node traversal, so each agent tool
# call ≈ 2-3 steps (agent → tool → agent). 45 gives ~14-15 tool calls of
# headroom, enough for 2 reads + 3-4 searches + write + think-steps, while
# still catching pathological thrashing.
SECTION_RECURSION_LIMIT = 45

# Minimal toolkits — section agents that can't browse the tree can't
# burn their budget exploring a 45-file data room. Survey can browse but
# can't write or search the web.
_SURVEY_TOOL_ALLOWLIST: tuple[str, ...] = (
    "workspace_get_tree",
    "workspace_list_files",
    "workspace_read_file",
    "workspace_search_files",
)
_SECTION_TOOL_ALLOWLIST: tuple[str, ...] = (
    "workspace_read_file",
    "workspace_write_file",
    "propose_fact_update",
    "web_search",
)
# Section agents have BOTH paths: preferred is workspace_write_file
# (which matches the tool-calling pattern most Gemini reasoning traces
# converge on), but they may also emit the JSON object as their final
# reply text. The orchestrator uses freshness-aware verification:
# whichever path produces a fresh file wins. Structured-output-only
# was tried and saw 3/5 agents produce empty replies — the write-tool
# scaffolding helps the agent converge on an actual deliverable.


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SurveyResult:
    primary_docs: List[Dict[str, Any]]
    section_hints: Dict[str, List[str]]
    notes: str
    raw_reply: str


@dataclass
class SectionResult:
    section_id: str
    succeeded: bool
    error: Optional[str] = None
    wall_seconds: float = 0.0


@dataclass
class V2ResearchOutcome:
    survey: Optional[SurveyResult]
    sections: List[SectionResult]
    total_seconds: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _notify(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if not on_status:
        return
    try:
        on_status(msg)
    except Exception:  # noqa: BLE001
        pass


def _prefixed_status(
    parent: Optional[Callable[[str], None]], tag: str,
) -> Callable[[str], None]:
    """Return a status callback that prepends ``[tag]`` so the front-end
    can tell which parallel agent is speaking."""
    def _cb(msg: str) -> None:
        _notify(parent, f"[{tag}] {msg}")
    return _cb


def _parse_survey_reply(reply: str) -> SurveyResult:
    """Extract the JSON handoff from the survey agent's final message.

    The survey prompt instructs the agent to emit the JSON as its sole
    final reply, but models sometimes wrap in a code fence or add a
    leading acknowledgement. We strip both.
    """
    if not reply:
        raise ValueError("survey: empty reply")
    text = reply.strip()
    # Strip a leading ```json … ``` fence if present.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch regex salvage — find the first balanced {...} block.
        match = re.search(r"\{[\s\S]+\}", text)
        if not match:
            raise ValueError(f"survey: reply is not JSON: {text[:200]!r}")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError(f"survey: reply is not an object: {type(data).__name__}")

    primary = data.get("primary_docs") or []
    hints = data.get("section_hints") or {}
    notes = data.get("notes") or ""

    if not isinstance(primary, list):
        primary = []
    if not isinstance(hints, dict):
        hints = {}

    # Normalise section_hints: every known section gets a (possibly empty) list.
    normalised_hints: Dict[str, List[str]] = {}
    for sec in V2_SECTION_IDS:
        raw = hints.get(sec) or []
        if not isinstance(raw, list):
            raw = []
        normalised_hints[sec] = [p for p in raw if isinstance(p, str) and p.strip()]

    return SurveyResult(
        primary_docs=[p for p in primary if isinstance(p, dict)],
        section_hints=normalised_hints,
        notes=str(notes)[:500],
        raw_reply=reply,
    )


def _render_section_system_extras(
    *,
    section_id: str,
    entity_name: str,
    entity_website: Optional[str],
    run_id: str,
    survey: SurveyResult,
) -> str:
    """Compose the system prompt extras for one section agent: the
    section-specific instructions + the survey handoff scoped to this
    section + the workspace write path the agent MUST use."""
    body = render_initial_screening_v2_section(
        section_id=section_id,
        entity_name=entity_name,
        entity_website=entity_website,
        run_id=run_id,
    )
    section_primary = survey.section_hints.get(section_id) or []
    survey_block = {
        "section_id": section_id,
        "section_hints": section_primary,
        "primary_docs": survey.primary_docs,
        "survey_notes": survey.notes,
    }
    extras = (
        f"{body}\n\n---\n\n## Survey handoff (primary docs to read)\n\n"
        f"```json\n{json.dumps(survey_block, ensure_ascii=False, indent=2)}\n```\n"
    )
    return extras


# ---------------------------------------------------------------------------
# Survey stage
# ---------------------------------------------------------------------------


async def run_survey_stage(
    *,
    brief: EntityBrief,
    session_id: str,
    run_id: str,
    history: List[Tuple[str, str]],
    workspace_context: str,
    pointer_list: str,
    model_profile_id: Optional[str],
    on_status: Optional[Callable[[str], None]] = None,
) -> SurveyResult:
    """Run the lightweight survey agent; returns parsed handoff JSON.

    The survey agent has workspace tools only — NO web_search — and a tight
    recursion limit. Its sole output is a JSON object the caller parses.
    """
    _notify(on_status, "[survey] Starting workspace survey...")

    system_extras = render_initial_screening_v2_survey(
        entity_name=brief.name,
        entity_website=brief.website,
        entity_id=brief.entity_id,
        run_id=run_id,
    )
    # Workspace tree + pointer list go inline with the user turn so the
    # survey can see the full file list without additional tool calls.
    user_turn_parts: List[str] = []
    if workspace_context:
        user_turn_parts.append(workspace_context)
    if pointer_list:
        user_turn_parts.append(pointer_list)
    user_turn_parts.append(
        "Scan the workspace tree above, then emit the survey JSON as "
        "your final reply (no markdown fence, no prose).",
    )
    user_turn = "\n\n".join(user_turn_parts)

    status_cb = _prefixed_status(on_status, "survey")
    agent = create_react_portfolio_agent(
        entity=brief,
        system_prompt_extras=system_extras,
        session_id=session_id,
        model_profile_id=model_profile_id,
        run_id=run_id,
        on_status=status_cb,
        preset_id="initial_screening_v2",
        include_web_search=False,
        tool_allowlist=_SURVEY_TOOL_ALLOWLIST,
    )
    lc_messages = history_to_lc_messages(history, user_turn)

    text, _raw = await asyncio.to_thread(
        invoke_react_portfolio_agent,
        agent,
        lc_messages,
        status_cb,
        SURVEY_RECURSION_LIMIT,
    )
    return _parse_survey_reply(text)


# ---------------------------------------------------------------------------
# Section stage
# ---------------------------------------------------------------------------


async def _peek_updated_at(entity_id: str, path: str):
    """Return the ``updated_at`` timestamp of a workspace node (or None if
    it doesn't exist or is soft-deleted)."""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models import WorkspaceNode

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.path == path,
                WorkspaceNode.deleted_at.is_(None),
            )
        )
        node = res.scalar_one_or_none()
        return node.updated_at if node else None


async def _delete_if_exists(entity_id: str, path: str) -> None:
    """Soft-delete the workspace node at ``path`` if it exists. Used to
    clear stale section JSONs before dispatching a fresh section agent
    so the post-run existence check is authoritative (no file = agent
    didn't deliver)."""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models import WorkspaceNode
    from app.datetime_support import utc_now

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.entity_id == entity_id,
                WorkspaceNode.path == path,
                WorkspaceNode.deleted_at.is_(None),
            )
        )
        node = res.scalar_one_or_none()
        if node is not None:
            node.deleted_at = utc_now()
            await db.commit()


def _parse_section_json(reply: str, section_id: str) -> Optional[dict]:
    """Extract the section's JSON object from the agent's final text.

    Agents are instructed to emit a bare JSON object as their final
    reply. We strip common markdown-fence wrappers and fall back to a
    brace-to-brace regex salvage for models that add stray prose.
    Returns the parsed dict, or None if unparseable.
    """
    if not reply or not reply.strip():
        return None
    text = reply.strip()
    # Strip ```json … ``` fence.
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    # Sanity: the section field, if present, should match. Don't error on
    # mismatch — just log.
    declared = data.get("section")
    if declared and declared != section_id:
        _log.warning(
            "initial_screening_v2: %s agent declared section=%r",
            section_id, declared,
        )
    return data


async def _run_one_section(
    *,
    section_id: str,
    brief: EntityBrief,
    session_id: str,
    run_id: str,
    workspace_context: str,
    pointer_list: str,
    model_profile_id: Optional[str],
    survey: SurveyResult,
    on_status: Optional[Callable[[str], None]] = None,
) -> SectionResult:
    """Run one section agent. Returns a SectionResult; never raises.

    The agent's output contract is a **JSON object in its final text
    reply**. We parse that and the orchestrator writes it to the
    workspace. This side-steps the class of failures where the agent
    either skips workspace_write_file entirely or calls it then keeps
    thrashing into the recursion limit — here the ReAct loop naturally
    ends when the agent stops calling tools, and the write is
    orchestrator-controlled.
    """
    import time
    started = time.monotonic()
    status_cb = _prefixed_status(on_status, section_id)
    _notify(on_status, f"[{section_id}] starting section agent")

    try:
        system_extras = _render_section_system_extras(
            section_id=section_id,
            entity_name=brief.name,
            entity_website=brief.website,
            run_id=run_id,
            survey=survey,
        )
        user_turn_parts: List[str] = []
        if workspace_context:
            user_turn_parts.append(workspace_context)
        if pointer_list:
            user_turn_parts.append(pointer_list)
        user_turn_parts.append(
            f"Produce the `{section_id}` section. The primary delivery "
            f"path is to call `workspace_write_file` once with the JSON "
            f"at `{V2_ANALYSIS_DIR}/{section_id}.json`. As a fallback, "
            f"if the write tool is unavailable, emit the same JSON as "
            f"your final reply text. The orchestrator accepts either.",
        )
        user_turn = "\n\n".join(user_turn_parts)

        # Pre-run: delete any stale file at the target path so a failing
        # agent leaves an honest gap instead of the compose stage picking
        # up outdated content. This makes fresh-write verification trivial
        # (file exists AFTER run → it's fresh).
        section_path = f"{V2_ANALYSIS_DIR}/{section_id}.json"
        await _delete_if_exists(brief.entity_id, section_path)
        pre_run_updated_at = None  # just deleted, so any post-run file is fresh

        agent = create_react_portfolio_agent(
            entity=brief,
            system_prompt_extras=system_extras,
            session_id=session_id,
            model_profile_id=model_profile_id,
            run_id=f"{run_id}:{section_id}",
            on_status=status_cb,
            preset_id="initial_screening_v2",
            include_web_search=True,
            tool_allowlist=_SECTION_TOOL_ALLOWLIST,
        )
        lc_messages = history_to_lc_messages([], user_turn)

        # The agent may raise (recursion limit) AFTER writing the file.
        # Catch locally so we can still check for a fresh write — a
        # delivered file is a success regardless of whether the agent
        # subsequently thrashed.
        reply_text = ""
        invoke_error: Optional[str] = None
        try:
            reply_text, _raw = await asyncio.to_thread(
                invoke_react_portfolio_agent,
                agent,
                lc_messages,
                status_cb,
                SECTION_RECURSION_LIMIT,
            )
        except Exception as exc:  # noqa: BLE001
            invoke_error = str(exc)[:200]
            _log.warning(
                "initial_screening_v2: section %r invoke raised — "
                "will still check for fresh write: %s",
                section_id, invoke_error,
            )
        wall = time.monotonic() - started

        # Path A (preferred): agent called workspace_write_file.
        # Verify via mtime — not just existence — so a stale file from
        # a prior run can't false-positive us.
        post_run_updated_at = await _peek_updated_at(
            brief.entity_id, section_path,
        )
        wrote_fresh_file = (
            post_run_updated_at is not None
            and (pre_run_updated_at is None
                 or post_run_updated_at > pre_run_updated_at)
        )

        if wrote_fresh_file:
            # Agent delivered before hitting any error. Even if invoke
            # subsequently raised (recursion, etc.), the deliverable
            # landed — this is a success, not a failure.
            msg = f"[{section_id}] done in {wall:.1f}s"
            if invoke_error:
                msg += " (agent thrashed after write but file is good)"
            _notify(on_status, msg)
            return SectionResult(
                section_id=section_id,
                succeeded=True,
                wall_seconds=wall,
            )

        # No fresh file. If invoke raised, that's the root cause.
        if invoke_error:
            _notify(
                on_status,
                f"[{section_id}] FAILED ({invoke_error[:80]}) in {wall:.1f}s",
            )
            return SectionResult(
                section_id=section_id,
                succeeded=False,
                error=invoke_error,
                wall_seconds=wall,
            )

        # Path B (fallback): parse JSON from the agent's final reply and
        # have the orchestrator write it. Some Gemini traces prefer the
        # text-response path over the tool call.
        data = _parse_section_json(reply_text, section_id)
        if data is None:
            _notify(
                on_status,
                f"[{section_id}] FAILED (no fresh file, no parseable JSON) "
                f"in {wall:.1f}s",
            )
            return SectionResult(
                section_id=section_id,
                succeeded=False,
                error=(
                    "agent returned neither a fresh workspace_write_file "
                    f"nor a parseable JSON text reply. reply: {reply_text[:160]!r}"
                ),
                wall_seconds=wall,
            )

        try:
            from app.database import AsyncSessionLocal
            from app.services.workspace import Actor as _Actor
            async with AsyncSessionLocal() as db:
                ws = WorkspaceService(storage)
                await ws.write_file(
                    db,
                    brief.entity_id,
                    section_path,
                    json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                    _Actor(
                        type="system",
                        ref=f"preset:initial_screening_v2:{section_id}:{run_id}",
                    ),
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            return SectionResult(
                section_id=section_id,
                succeeded=False,
                error=f"orchestrator fallback write failed: {str(exc)[:200]}",
                wall_seconds=wall,
            )

        _notify(
            on_status,
            f"[{section_id}] done via text fallback in {wall:.1f}s",
        )
        return SectionResult(
            section_id=section_id,
            succeeded=True,
            wall_seconds=wall,
        )
    except Exception as exc:  # noqa: BLE001
        wall = time.monotonic() - started
        msg = str(exc)[:200]
        _log.warning(
            "initial_screening_v2: section %r failed after %.1fs: %s",
            section_id, wall, msg, exc_info=True,
        )
        _notify(on_status, f"[{section_id}] FAILED: {msg}")
        return SectionResult(
            section_id=section_id,
            succeeded=False,
            error=msg,
            wall_seconds=wall,
        )


async def run_parallel_sections(
    *,
    brief: EntityBrief,
    session_id: str,
    run_id: str,
    workspace_context: str,
    pointer_list: str,
    model_profile_id: Optional[str],
    survey: SurveyResult,
    on_status: Optional[Callable[[str], None]] = None,
) -> List[SectionResult]:
    """Kick off all six section agents concurrently and await them."""
    _notify(
        on_status,
        f"Dispatching {len(V2_SECTION_IDS)} section agents in parallel...",
    )
    tasks = [
        _run_one_section(
            section_id=sid,
            brief=brief,
            session_id=session_id,
            run_id=run_id,
            workspace_context=workspace_context,
            pointer_list=pointer_list,
            model_profile_id=model_profile_id,
            survey=survey,
            on_status=on_status,
        )
        for sid in V2_SECTION_IDS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    # (return_exceptions=False because _run_one_section catches internally)
    return list(results)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


async def run_research_v2(
    *,
    brief: EntityBrief,
    session_id: str,
    run_id: str,
    history: List[Tuple[str, str]],
    workspace_context: str,
    pointer_list: str,
    model_profile_id: Optional[str],
    on_status: Optional[Callable[[str], None]] = None,
) -> V2ResearchOutcome:
    """Run the v2 Phase-1 research pipeline: survey → 6 parallel section
    agents. Returns an outcome report (doesn't call compose/review —
    callers do that separately)."""
    import time
    started = time.monotonic()

    # Stage 1 — Survey. If this fails, sections run with an empty handoff
    # (each agent falls back to deck + memo discovery via workspace tools).
    survey: Optional[SurveyResult] = None
    try:
        survey = await run_survey_stage(
            brief=brief,
            session_id=session_id,
            run_id=run_id,
            history=history,
            workspace_context=workspace_context,
            pointer_list=pointer_list,
            model_profile_id=model_profile_id,
            on_status=on_status,
        )
        _notify(
            on_status,
            f"[survey] {len(survey.primary_docs)} primary doc(s) identified",
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "initial_screening_v2: survey failed: %s",
            exc, exc_info=True,
        )
        _notify(on_status, f"[survey] FAILED: {str(exc)[:120]}")
        survey = SurveyResult(
            primary_docs=[],
            section_hints={s: [] for s in V2_SECTION_IDS},
            notes="(survey failed; sections ran with empty handoff)",
            raw_reply="",
        )

    # Stage 2 — Six parallel section agents.
    sections = await run_parallel_sections(
        brief=brief,
        session_id=session_id,
        run_id=run_id,
        workspace_context=workspace_context,
        pointer_list=pointer_list,
        model_profile_id=model_profile_id,
        survey=survey,
        on_status=on_status,
    )

    total = time.monotonic() - started
    succeeded = sum(1 for r in sections if r.succeeded)
    _notify(
        on_status,
        f"Phase-1 v2 done: {succeeded}/{len(sections)} sections, "
        f"{total:.1f}s total",
    )
    return V2ResearchOutcome(
        survey=survey,
        sections=sections,
        total_seconds=total,
    )


# ---------------------------------------------------------------------------
# Post-processing of section JSONs
# ---------------------------------------------------------------------------


def _summarize_coinvestor(notes: dict) -> str:
    """Compress a coinvestors_notes entry into one human-readable sentence
    for the Facts-tab co-investor description popover.

    Composes from optional fields: tier, founding_year, aum, sectors,
    portfolio_recent (first 2), signal. Always returns a string (possibly
    empty) — frontend treats empty as "no description"."""
    parts: List[str] = []
    tier = (notes.get("tier") or "").strip()
    if tier and tier.lower() != "unknown":
        parts.append(tier)
    fy = notes.get("founding_year")
    if isinstance(fy, int) and 1900 < fy < 2100:
        parts.append(f"founded {fy}")
    aum = (notes.get("aum_usd_str") or "").strip()
    if aum:
        parts.append(f"AUM {aum}")
    sectors = notes.get("sectors") or []
    if isinstance(sectors, list) and sectors:
        first = [str(s) for s in sectors[:2] if str(s).strip()]
        if first:
            parts.append("sectors: " + ", ".join(first))
    recent = notes.get("portfolio_recent") or []
    if isinstance(recent, list) and recent:
        first = [str(p) for p in recent[:2] if str(p).strip()]
        if first:
            parts.append("recent: " + ", ".join(first))
    signal = (notes.get("signal") or "").strip()
    if signal:
        parts.append(signal)
    return ". ".join(parts)


async def post_process_section_jsons(
    db: AsyncSession,
    ws: WorkspaceService,
    *,
    entity_id: str,
    agent_run_id: str,
    on_status: Optional[Callable[[str], None]] = None,
) -> None:
    """Run between research and compose. Two responsibilities:

    1. **GS contract enforcement** — read team.json. For every founder card
       with ``profile_type=academic`` and a missing ``gs_metrics``, append a
       synthetic ``open_gaps[]`` row so the composed memo surfaces the gap
       even when the section agent forgot. If anything is appended, write
       team.json back.

    2. **Co-investor enrichment pipe** — read funding_traction.json's
       ``extras.coinvestors_notes[]``. Merge into the entity's
       ``metadata_json.co_investor_details`` (a name-keyed sidecar). Empty
       names are ignored. The map fully replaces any prior IS-derived
       enrichment (latest run wins).

    Failure-isolated: any error here is logged and swallowed — the compose
    stage still runs on the existing section JSONs.
    """
    actor = Actor(
        type="system",
        ref=f"preset:initial_screening_v2:post_process:{agent_run_id}",
    )

    # --- (1) Team JSON: GS contract -------------------------------------
    team_path = f"{V2_ANALYSIS_DIR}/team.json"
    try:
        node = await ws.get_node_by_path(db, entity_id, team_path)
        if node is not None and node.storage_key:
            raw = await asyncio.to_thread(
                ws.storage.read_file_sync, node.storage_key,
            )
            team = json.loads(raw.decode("utf-8"))
            facts = team.get("facts") or []
            gaps = team.get("open_gaps") or []
            if not isinstance(gaps, list):
                gaps = []
            appended = 0
            for entry in facts:
                if not isinstance(entry, dict):
                    continue
                extras = entry.get("extras") or {}
                if not isinstance(extras, dict):
                    continue
                if extras.get("profile_type") != "academic":
                    continue
                if extras.get("gs_metrics"):
                    continue
                name = (extras.get("name") or "").strip() or "(unnamed founder)"
                # Skip if a matching gap is already present so we don't
                # double-append on re-runs.
                marker = f"{name}: no Google Scholar"
                if any(marker in (str(g) or "") for g in gaps):
                    continue
                gaps.append(
                    f"{name}: no Google Scholar metrics surfaced; "
                    f"agent did not run the mandatory GS check (orchestrator-flagged)."
                )
                appended += 1
            if appended:
                team["open_gaps"] = gaps
                await ws.write_file(
                    db,
                    entity_id,
                    team_path,
                    json.dumps(team, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                    actor,
                )
                _notify(
                    on_status,
                    f"[post] team.json: appended {appended} GS-gap row(s)",
                )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "initial_screening_v2 post: team GS check failed: %s",
            exc, exc_info=True,
        )

    # --- (2) Funding/Traction JSON: pipe coinvestors → metadata ---------
    ft_path = f"{V2_ANALYSIS_DIR}/funding_traction.json"
    try:
        node = await ws.get_node_by_path(db, entity_id, ft_path)
        if node is None or not node.storage_key:
            return
        raw = await asyncio.to_thread(
            ws.storage.read_file_sync, node.storage_key,
        )
        ft = json.loads(raw.decode("utf-8"))
        notes_list = (ft.get("extras") or {}).get("coinvestors_notes") or []
        if not isinstance(notes_list, list):
            return
        details: Dict[str, Dict[str, Any]] = {}
        for n in notes_list:
            if not isinstance(n, dict):
                continue
            name = (n.get("name") or "").strip()
            if not name:
                continue
            details[name] = {
                "url": (n.get("url") or None),
                "description": _summarize_coinvestor(n),
            }
        if not details:
            return
        # Merge into entity.metadata_json.co_investor_details (latest wins).
        result = await db.execute(
            select(Entity).where(Entity.id == entity_id)
        )
        entity_row = result.scalars().first()
        if entity_row is None:
            return
        try:
            meta = json.loads(entity_row.metadata_json or "{}")
        except Exception:  # noqa: BLE001
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["co_investor_details"] = details
        entity_row.metadata_json = json.dumps(meta, ensure_ascii=False)
        await db.commit()
        _notify(
            on_status,
            f"[post] piped {len(details)} co-investor entries into entity metadata",
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "initial_screening_v2 post: coinvestor pipe failed: %s",
            exc, exc_info=True,
        )


async def run_compose_review_v2(
    db: AsyncSession,
    ws: WorkspaceService,
    *,
    entity_id: str,
    entity_name: str,
    entity_website: Optional[str],
    agent_run_id: str,
    on_status: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[str], List[str]]:
    """Run compose + review on the v2 section JSONs. Returns
    ``(final_memo, warnings)``."""
    memo, warns = await run_compose_stage(
        db, ws,
        entity_id=entity_id,
        entity_name=entity_name,
        entity_website=entity_website,
        agent_run_id=agent_run_id,
        on_status=on_status,
        analysis_dir=V2_ANALYSIS_DIR,
        memo_path=V2_MEMO_PATH,
    )
    if memo:
        rwarns = await run_review_stage(
            db, ws,
            entity_id=entity_id,
            entity_name=entity_name,
            entity_website=entity_website,
            agent_run_id=agent_run_id,
            memo_draft=memo,
            on_status=on_status,
            analysis_dir=V2_ANALYSIS_DIR,
            memo_path=V2_MEMO_PATH,
            review_notes_path=V2_REVIEW_NOTES_PATH,
        )
        warns.extend(rwarns)
    return memo, warns
