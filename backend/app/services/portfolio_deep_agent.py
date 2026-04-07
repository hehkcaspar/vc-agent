"""Deep Agents harness: workspace-scoped tools + invoke wrapper."""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional, Sequence, Tuple

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.config import settings
from app.services.model_profiles import build_deep_agent_base_chat_model
from app.services.prompt_assembly import EntityBrief, build_deep_agent_system_prompt
from app.services.storage import storage
from app.services.workspace import WorkspaceService
from app.services.workspace_tools import build_workspace_tools


def _notify_status(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if not on_status:
        return
    try:
        on_status(msg)
    except Exception:
        pass


def history_to_lc_messages(
    history: Sequence[Tuple[str, str]], user_text: str
) -> List[BaseMessage]:
    msgs: List[BaseMessage] = []
    for role, text in history:
        if role == "user":
            msgs.append(HumanMessage(content=text))
        else:
            msgs.append(AIMessage(content=text))
    msgs.append(HumanMessage(content=user_text))
    return msgs


def create_portfolio_agent(
    *,
    entity: EntityBrief,
    system_prompt_extras: str,
    session_id: str,
    model_profile_id: Optional[str] = None,
    run_id: Optional[str] = None,
    initial_user_text: str = "",
    on_status: Optional[Callable[[str], None]] = None,
):
    model = build_deep_agent_base_chat_model(profile_id=model_profile_id)
    ws = WorkspaceService(storage)
    tools = build_workspace_tools(
        entity.entity_id,
        session_id,
        run_id,
        ws,
        on_status=on_status,
    )
    system_prompt = build_deep_agent_system_prompt(
        entity,
        extras=system_prompt_extras,
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
    user_multimodal_parts: Optional[List[dict[str, Any]]] = None,
) -> Tuple[str, Any]:
    if user_multimodal_parts:
        last = lc_messages[-1] if lc_messages else None
        user_text = ""
        if isinstance(last, HumanMessage) and isinstance(last.content, str):
            user_text = last.content
            lc_messages = lc_messages[:-1]
        blocks: List[dict[str, Any]] = [{"type": "text", "text": user_text}]
        blocks.extend(user_multimodal_parts)
        lc_messages = [*lc_messages, HumanMessage(content=blocks)]
    _notify_status(on_status, "Model running (may take a while)...")
    cfg = {"recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT}
    result = agent.invoke({"messages": lc_messages}, config=cfg)
    _notify_status(on_status, "Composing reply...")
    messages_out = result.get("messages") or []
    last = messages_out[-1] if messages_out else None
    text = ""
    if last is not None:
        c = getattr(last, "content", None)
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            parts = []
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts)
    return text, result
