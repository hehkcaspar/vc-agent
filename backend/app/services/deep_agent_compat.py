"""Legacy Deep Agent compat module — REMOVABLE.

To fully remove Deep Agent support:
1. Delete this file
2. Remove ``deepagents`` from requirements.txt
3. The rest of the system continues working (ReAct + one-shot).

This module uses ``deepagents.create_deep_agent`` which injects 9 SDK built-in
tools (read_file, write_file, ls, glob, grep, edit_file, execute, write_todos,
task) alongside the 13 workspace tools. This causes tool confusion and is why
ReAct mode was created as a replacement.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from deepagents import create_deep_agent
from langchain_core.messages import BaseMessage

from app.config import settings
from app.services.agent_harness import (
    _build_agent_core,
    _extract_last_text,
    _notify_status,
)
from app.services.prompt_assembly import EntityBrief


def create_portfolio_agent(
    *,
    entity: EntityBrief,
    system_prompt_extras: str,
    session_id: str,
    model_profile_id: Optional[str] = None,
    run_id: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
):
    """Create a Deep Agent (legacy) with SDK built-in tools + workspace tools."""
    model, tools, system_prompt = _build_agent_core(
        entity=entity,
        system_prompt_extras=system_prompt_extras,
        session_id=session_id,
        model_profile_id=model_profile_id,
        run_id=run_id,
        on_status=on_status,
    )
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
    )


def invoke_portfolio_agent(
    agent,
    lc_messages: List[BaseMessage],
    on_status: Optional[Callable[[str], None]] = None,
) -> Tuple[str, Any]:
    """Invoke a Deep Agent (legacy)."""
    _notify_status(on_status, "Model running (may take a while)...")
    cfg = {"recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT}
    result = agent.invoke({"messages": lc_messages}, config=cfg)
    _notify_status(on_status, "Composing reply...")
    messages_out = result.get("messages") or []
    text = _extract_last_text(messages_out)
    return text, result
