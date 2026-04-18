"""Per-scholar interactive chat via the Gemini Interactions API.

Path 3 from Concept 8. Unlike the scoring/classifier paths, chat is
**agentic**: the model can call a small set of scholar-scoped
functions to read the fact store, trigger refreshes, and log
notes/events. The loop is client-driven per the Interactions API
spec: we send input → model emits `function_call` outputs → we
execute locally → we send `function_result` back with the previous
interaction id → repeat until the model returns only text.

Server-side session state is carried via `previous_interaction_id` —
each user turn starts from the last interaction id we persisted for
the session, and at the end we store the id of the LAST interaction
in the loop so the next user message continues the chain.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .fact_store import current_state
from .file_utils import latest_record, read_records
from .llm_client import (
    extract_function_calls,
    extract_interaction_text,
    interactions_create,
    parse_function_call_args,
)
from .refresh_dispatcher import trigger_refresh

logger = logging.getLogger(__name__)

# Safety cap on the agentic loop — prevents runaway tool use.
_MAX_TOOL_ITERATIONS = 6


_CHAT_SYSTEM_PROMPT_TEMPLATE = """\
You are the research analyst assigned to scholar `{scholar_id}` in a
VC portfolio. Answer analyst questions grounded in this scholar's
dossier. Be evidence-based, concise, and flag anything unknown
honestly.

You have tools:
- `read_fact_store` — read the scholar's current profile, attributed
  metrics, peer group, active red flags, latest per-dim evaluations,
  and the latest narrative. Call this first if the analyst asks about
  current state.
- `read_dim_history(dim_id)` — full JSONL history for one dimension
  (use when the analyst asks how a score changed over time).
- `trigger_refresh(source_id, reason)` — kick a Layer 2 refresher
  (`semantic_scholar_papers`, `google_scholar_stats`, `patents_web`,
  `news_web`, `startups_web`, `red_flags_watch`). Only call
  this when the analyst explicitly asks for fresher data or you have
  a clear reason to believe the fact store is stale for the question.
- `log_event(event_type, title, summary, significance)` — append a
  note to events.jsonl. Use this for user corrections
  ("flag is actually dismissed", "phase is wrong", new info analyst
  shares). Keep titles short.
- `google_search` (built-in) — for facts not in the fact store.

Rules:
- Ground every factual claim in fact-store data or grounded search.
- Do NOT fabricate scores, citations, or URLs.
- Keep answers short by default; expand only when asked.
- When correcting the dossier, use `log_event`; never claim a change
  happened silently.
"""


_FUNCTION_DECLARATIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "read_fact_store",
        "description": (
            "Return the current snapshot of this scholar: profile, "
            "attributed metrics, peer group, active red flags, "
            "latest per-dim evaluations, and latest narrative."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "type": "function",
        "name": "read_dim_history",
        "description": (
            "Return the full JSONL history for one dimension "
            "(every triage + scored run), most recent first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dim_id": {
                    "type": "string",
                    "description": (
                        "One of: academic_excellence, "
                        "tech_transfer_experience, founder_potential, "
                        "growth_trajectory"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max records to return. Default 10.",
                },
            },
            "required": ["dim_id"],
        },
    },
    {
        "type": "function",
        "name": "trigger_refresh",
        "description": (
            "Request a fresh run of a Layer 2 source fetcher. "
            "Use only when the analyst asks for fresh data or there "
            "is clear reason to believe the fact store is stale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": (
                        "One of: semantic_scholar_papers, "
                        "google_scholar_stats, patents_web, news_web, "
                        "startups_web, red_flags_watch"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "One-sentence reason for the refresh.",
                },
            },
            "required": ["source_id", "reason"],
        },
    },
    {
        "type": "function",
        "name": "log_event",
        "description": (
            "Append a note to the scholar's events.jsonl. Use for "
            "analyst corrections, new facts the analyst shares, or "
            "notable observations from this chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": (
                        "Short type tag: 'user_note', 'correction', "
                        "'observation', etc."
                    ),
                },
                "title": {"type": "string", "description": "Short title."},
                "summary": {"type": "string", "description": "1-3 sentence detail."},
                "significance": {
                    "type": "string",
                    "description": "low | medium | high",
                },
            },
            "required": ["event_type", "title"],
        },
    },
    {"type": "google_search"},
]


# ── Tool implementations ──────────────────────────────────────────────


def _initial_snapshot(scholar_id: str) -> dict[str, Any]:
    snap = current_state(scholar_id)
    dim_snapshots: dict[str, Any] = {}
    for dim_id in (
        "academic_excellence",
        "tech_transfer_experience",
        "founder_potential",
        "growth_trajectory",
    ):
        for rec in reversed(read_records(scholar_id, f"evaluations/{dim_id}")):
            if "score" in rec:
                dim_snapshots[dim_id] = rec
                break
    return {
        "scholar_id": scholar_id,
        "profile": {
            "name": (snap.profile or {}).get("name"),
            "affiliation": (snap.profile or {}).get("affiliation"),
            "metrics": (snap.profile or {}).get("metrics"),
            "research_areas": (snap.profile or {}).get("research_areas"),
            "identity": (snap.profile or {}).get("identity"),
        },
        "peer_group": snap.peer_group,
        "attributed_metrics": snap.attributed_metrics,
        "paper_count": len(snap.papers),
        "top_papers": sorted(
            snap.papers,
            key=lambda p: int(p.get("citations") or 0),
            reverse=True,
        )[:10],
        "active_red_flags": snap.red_flags_active,
        "latest_evals": dim_snapshots,
        "latest_narrative": latest_record(scholar_id, "narrative"),
    }


async def _tool_read_fact_store(scholar_id: str, args: dict) -> dict[str, Any]:
    return _initial_snapshot(scholar_id)


async def _tool_read_dim_history(scholar_id: str, args: dict) -> dict[str, Any]:
    dim_id = args.get("dim_id") or ""
    limit = int(args.get("limit") or 10)
    recs = read_records(scholar_id, f"evaluations/{dim_id}")
    return {"dim_id": dim_id, "items": list(reversed(recs))[:limit]}


async def _tool_trigger_refresh(scholar_id: str, args: dict) -> dict[str, Any]:
    source_id = args.get("source_id") or ""
    reason = args.get("reason") or "chat_requested"
    try:
        result = await trigger_refresh(
            scholar_id, source_id, mode="incremental", reason=reason
        )
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _tool_log_event(scholar_id: str, args: dict) -> dict[str, Any]:
    from .events_sync import log_event

    event_id = await log_event(
        scholar_id,
        event_type=args.get("event_type") or "user_note",
        title=args.get("title") or "",
        significance=args.get("significance") or "low",
        payload={"summary": args.get("summary") or ""},
    )
    return {"ok": True, "id": event_id}


_TOOL_DISPATCH = {
    "read_fact_store": _tool_read_fact_store,
    "read_dim_history": _tool_read_dim_history,
    "trigger_refresh": _tool_trigger_refresh,
    "log_event": _tool_log_event,
}


async def _execute_function_call(scholar_id: str, call: Any) -> Any:
    name = getattr(call, "name", None) or ""
    args = parse_function_call_args(call)
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    try:
        return await fn(scholar_id, args)
    except Exception as e:
        logger.exception("scholar_chat: tool %s failed", name)
        return {"ok": False, "error": str(e)}


# ── Public entry point ───────────────────────────────────────────────


def _is_stale_interaction_error(err: Exception) -> bool:
    """Heuristic: did the server reject our `previous_interaction_id`?

    Gemini uses different messages depending on the failure mode:
      - retention expiry: 'interaction not found' / 404
      - bogus id shape: 'Request contains an invalid argument.' / 400
    Callers also treat ANY initial-turn failure as stale when a
    previous_interaction_id was supplied — the retry cost is one
    extra call and it almost always indicates the chain is gone.
    """
    msg = str(err).lower()
    return (
        "previous_interaction" in msg
        or "interaction not found" in msg
        or "invalid argument" in msg
        or ("404" in msg and "interaction" in msg)
    )


async def send_chat_turn(
    scholar_id: str,
    user_message: str,
    *,
    previous_interaction_id: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Run one user turn through the agentic Interactions API loop.

    Returns (reply_text, new_interaction_id). The returned id is the
    id of the LAST interaction in the loop — store it on the session
    row and pass it as `previous_interaction_id` on the next user
    turn.
    """
    from ...config import settings

    model_id = model or settings.ACADEMIC_GEMINI_MODEL
    system_instruction = _CHAT_SYSTEM_PROMPT_TEMPLATE.format(
        scholar_id=scholar_id
    )

    # Initial create with previous_interaction_id (if any). If the
    # server has forgotten that chain (retention expiry) or rejects
    # the id, restart without it so the session recovers cleanly.
    # We fail fast on the first attempt (max_attempts=1) so the
    # fallback kicks in immediately instead of burning retries on a
    # known-fatal input error.
    if previous_interaction_id:
        try:
            last_interaction = await interactions_create(
                model=model_id,
                input=user_message,
                previous_interaction_id=previous_interaction_id,
                system_instruction=system_instruction,
                tools=_FUNCTION_DECLARATIONS,
                max_attempts=1,
            )
        except Exception as e:
            if not _is_stale_interaction_error(e):
                raise
            logger.warning(
                "scholar_chat: previous_interaction_id stale for %s (%s); restarting chain",
                scholar_id,
                e,
            )
            last_interaction = await interactions_create(
                model=model_id,
                input=user_message,
                previous_interaction_id=None,
                system_instruction=system_instruction,
                tools=_FUNCTION_DECLARATIONS,
            )
    else:
        last_interaction = await interactions_create(
            model=model_id,
            input=user_message,
            previous_interaction_id=None,
            system_instruction=system_instruction,
            tools=_FUNCTION_DECLARATIONS,
        )

    # Agentic loop: keep dispatching function_calls until the model
    # returns a turn with only text (or we hit the safety cap).
    #
    # NOTE on batching: the docs show per-call sequential create()
    # calls, but `input` is typed as an array and Gemini 3 can return
    # multiple function_calls in one turn (parallel calls). We batch
    # all function_results into a single follow-up — structurally
    # valid per the input-array type and dramatically reduces
    # round-trips for parallel calls.
    for _ in range(_MAX_TOOL_ITERATIONS):
        calls = extract_function_calls(last_interaction)
        if not calls:
            break

        function_results: list[dict[str, Any]] = []
        for call in calls:
            result = await _execute_function_call(scholar_id, call)
            # Interactions API function_result validator rejects
            # nested empty lists ("field X has an empty value"). Docs
            # explicitly list "string (like stringified JSON)" as a
            # supported shape, so we always stringify — one format,
            # no validator surprises.
            result_payload = json.dumps(result, ensure_ascii=False, default=str)
            function_results.append(
                {
                    "type": "function_result",
                    "name": getattr(call, "name", None) or "",
                    "call_id": getattr(call, "id", None) or "",
                    "result": result_payload,
                }
            )

        last_interaction = await interactions_create(
            model=model_id,
            input=function_results,
            previous_interaction_id=getattr(last_interaction, "id", None),
            system_instruction=system_instruction,
            tools=_FUNCTION_DECLARATIONS,
        )
    else:
        logger.warning(
            "scholar_chat: hit tool iteration cap (%d) for %s",
            _MAX_TOOL_ITERATIONS,
            scholar_id,
        )

    reply = extract_interaction_text(last_interaction) or "(no reply)"
    new_id = getattr(last_interaction, "id", None) or ""
    return reply, new_id
