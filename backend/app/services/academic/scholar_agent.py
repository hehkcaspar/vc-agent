"""
Academic Tracking v2 — scholar agent factory and invoker.

Uses Deep Agents SDK with CompositeBackend to give the agent
scholar-scoped file access via /dossier/ and /config/ virtual paths.
See design doc §4.1–4.2.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_core.messages import HumanMessage

from app.config import settings
from app.services.model_profiles import build_deep_agent_base_chat_model

from .domain_tools import build_scholar_tools
from .scholar_prompts import build_scholar_system_prompt

logger = logging.getLogger(__name__)


def _extract_text(content: Any) -> str:
    """Extract plain text from an LLM message content field.

    Gemini models may return content as a list of content blocks
    (e.g., [{"type": "text", "text": "..."}]) rather than a plain string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)



def create_scholar_agent(
    scholar_id: str,
    goal: str,
    model_name: Optional[str] = None,
):
    """Create a Deep Agents harness scoped to a single scholar's dossier.

    The agent sees:
      /dossier/ → data/scholars/{scholar_id}/
      /config/  → data/config/
      /workspace/ → ephemeral in-memory (StateBackend default)
    """
    scholars_dir = settings.ACADEMIC_SCHOLARS_DIR
    config_dir = settings.ACADEMIC_CONFIG_DIR

    # Ensure dossier directory exists
    dossier = scholars_dir / scholar_id
    for subdir in ("evaluations", "reports", "uploads", "agent_runs"):
        (dossier / subdir).mkdir(parents=True, exist_ok=True)

    # Ensure config directory exists with seed files
    config_dir.mkdir(parents=True, exist_ok=True)
    _ensure_seed_config(config_dir)

    # Build model — use ACADEMIC_GEMINI_MODEL (fast flash model for academic work)
    from langchain_google_genai import ChatGoogleGenerativeAI
    import os
    api_key = settings.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY") or ""
    model = ChatGoogleGenerativeAI(
        model=model_name or settings.ACADEMIC_GEMINI_MODEL,
        google_api_key=api_key,
    )

    system_prompt = build_scholar_system_prompt(goal)

    # Backend factory — create_deep_agent calls this with ToolRuntime
    def backend_factory(runtime):
        return CompositeBackend(
            default=StateBackend(runtime),
            routes={
                "/dossier/": FilesystemBackend(root_dir=str(dossier)),
                "/config/": FilesystemBackend(root_dir=str(config_dir)),
            },
        )

    # Build tools with scholar_id pre-bound (closure pattern)
    tools = build_scholar_tools(scholar_id)

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend_factory,
    )

    return agent


async def invoke_scholar_agent(
    scholar_id: str,
    goal: str,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """Create and invoke the scholar agent, returning the result.

    Also saves an agent trace to data/scholars/{scholar_id}/agent_runs/.
    Uses ainvoke() to support async domain tools (SS API, Gemini).
    """
    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc)
    goal_preview = goal[:60].replace("\n", " ")

    logger.info("[%s] Agent starting for scholar %s — %s", run_id, scholar_id, goal_preview)
    agent = create_scholar_agent(scholar_id, goal, model_name)

    # Invoke the agent — ainvoke for async tool support
    config = {"configurable": {"thread_id": f"scholar-{scholar_id}-{run_id}"}}
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=goal)]},
            config,
        )
    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.exception("[%s] Agent FAILED after %.0fs for %s: %s", run_id, elapsed, scholar_id, e)
        return {"error": str(e), "run_id": run_id}

    completed_at = datetime.now(timezone.utc)
    elapsed = (completed_at - started_at).total_seconds()

    # Extract reply text and tool calls from messages
    reply = ""
    tools_called: list[str] = []
    if result and "messages" in result:
        messages = result["messages"]
        for msg in messages:
            # Collect tool call names from AI messages
            if hasattr(msg, "tool_calls"):
                for tc in (msg.tool_calls or []):
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                    if name:
                        tools_called.append(name)
        if messages:
            last_msg = messages[-1]
            reply = _extract_text(getattr(last_msg, "content", str(last_msg)))

    logger.info(
        "[%s] Agent DONE in %.0fs for %s — %d tool calls: %s",
        run_id, elapsed, scholar_id, len(tools_called),
        ", ".join(dict.fromkeys(tools_called)) or "none",
    )

    # Detect files modified by checking dossier
    dossier = settings.ACADEMIC_SCHOLARS_DIR / scholar_id
    files_modified = []
    for f in dossier.rglob("*"):
        if f.is_file() and f.stat().st_mtime >= started_at.timestamp():
            files_modified.append(str(f.relative_to(dossier)))

    # Save enriched agent trace (design doc §4.6)
    trace = {
        "run_id": run_id,
        "goal": goal,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "model": model_name or settings.ACADEMIC_GEMINI_MODEL,
        "tools_called": tools_called,
        "files_modified": files_modified,
        "reply_preview": reply[:500] if reply else "",
    }
    trace_path = (
        settings.ACADEMIC_SCHOLARS_DIR
        / scholar_id
        / "agent_runs"
        / f"{started_at.strftime('%Y-%m-%d')}_{goal[:30].replace(' ', '_')}.json"
    )
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "run_id": run_id,
        "reply": reply,
        "trace_path": str(trace_path),
    }


async def invoke_scholar_chat(
    scholar_id: str,
    messages: list[dict[str, str]],
    session_id: str = "",
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """Invoke the scholar agent for a multi-turn chat conversation.

    Args:
        scholar_id: Scholar UUID.
        messages: List of {"role": "user"/"assistant", "content": "..."} dicts.
        session_id: Chat session ID (used for thread isolation).
        model_name: Optional model override.

    Returns:
        {"reply": str, "run_id": str, "trace_path": str}
    """
    from langchain_core.messages import AIMessage

    from .scholar_prompts import GOAL_CHAT_SYSTEM

    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc)

    agent = create_scholar_agent(scholar_id, GOAL_CHAT_SYSTEM, model_name)

    # Convert history to LangChain messages
    lc_messages = []
    for msg in messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    if not lc_messages:
        return {"error": "No messages provided", "run_id": run_id}

    thread_id = f"scholar-chat-{scholar_id}-{session_id or run_id}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = await agent.ainvoke({"messages": lc_messages}, config)
    except Exception as e:
        logger.exception("Scholar chat agent failed for %s: %s", scholar_id, e)
        return {"error": str(e), "run_id": run_id}

    completed_at = datetime.now(timezone.utc)

    # Extract reply
    reply = ""
    tools_called: list[str] = []
    if result and "messages" in result:
        for msg in result["messages"]:
            if hasattr(msg, "tool_calls"):
                for tc in (msg.tool_calls or []):
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                    if name:
                        tools_called.append(name)
        if result["messages"]:
            last_msg = result["messages"][-1]
            reply = _extract_text(getattr(last_msg, "content", str(last_msg)))

    # Save agent trace
    trace = {
        "run_id": run_id,
        "goal": "chat",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "model": model_name or settings.ACADEMIC_GEMINI_MODEL,
        "tools_called": tools_called,
        "reply_preview": reply[:500] if reply else "",
    }
    trace_path = (
        settings.ACADEMIC_SCHOLARS_DIR
        / scholar_id
        / "agent_runs"
        / f"{started_at.strftime('%Y-%m-%d')}_chat_{run_id}.json"
    )
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "run_id": run_id,
        "reply": reply,
        "trace_path": str(trace_path),
    }


def _ensure_seed_config(config_dir: Path) -> None:
    """Create seed config files if they don't exist."""
    # Field archetypes (design doc §6.2)
    archetypes_path = config_dir / "field_archetypes.json"
    if not archetypes_path.exists():
        archetypes = {
            "archetypes": {
                "stem_applied": {
                    "match_fields": ["Computer Science", "Robotics", "Engineering", "AI",
                                     "Machine Learning", "Artificial Intelligence"],
                    "signals": "Patents, startups, VC funding, licensing, industry R&D partnerships, open-source adoption.",
                    "examples": "Abbeel → Covariant; Leskovec → multiple startups from graph ML.",
                },
                "biomedical": {
                    "match_fields": ["Neuroscience", "Biology", "Chemistry", "Medicine",
                                     "Biomedical Engineering", "Genetics"],
                    "signals": "Clinical trials, FDA filings, biotech licensing, method patents, tool companies.",
                    "examples": "Deisseroth → optogenetics method licensed widely.",
                },
                "social_science_policy": {
                    "match_fields": ["Economics", "Political Science", "Public Policy",
                                     "Sociology", "Business"],
                    "signals": "Advisory roles, consulting, policy influence, think tanks, books.",
                    "examples": "Mazzucato → UCL IIPP, bestselling books, EU/UN advisory.",
                },
                "pure_science": {
                    "match_fields": ["Mathematics", "Theoretical Physics", "Cosmology",
                                     "Pure Mathematics"],
                    "signals": "Foundational contributions enabling downstream applications, prestige, textbooks.",
                    "examples": "Tao → foundational math enabling signal processing, cryptography.",
                },
                "data_platform": {
                    "match_fields": ["Data Science", "Environmental Science",
                                     "Computational Biology", "Statistics"],
                    "signals": "Public datasets, data tools/platforms, open-source tools, media/publishing.",
                    "examples": "Ritchie → Our World in Data, bestselling book.",
                },
            }
        }
        archetypes_path.write_text(
            json.dumps(archetypes, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Heartbeat config (design doc §5.1) — all disabled initially
    heartbeat_path = config_dir / "heartbeat.json"
    if not heartbeat_path.exists():
        heartbeat = {
            "checks": [
                {"id": "channel_poll", "enabled": False, "interval_minutes": 5,
                 "action": "poll_due_channels"},
                {"id": "high_priority_refresh", "enabled": False, "interval_minutes": 10080,
                 "action": "refresh_stale_scholars",
                 "filter": {"tracking_priority": "high", "stale_days": 7}},
                {"id": "weekly_digest", "enabled": False, "interval_minutes": 10080,
                 "action": "generate_digest"},
            ]
        }
        heartbeat_path.write_text(
            json.dumps(heartbeat, ensure_ascii=False, indent=2), encoding="utf-8"
        )
