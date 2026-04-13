"""ReAct agent harness: workspace-scoped tools + invoke wrapper.

Uses ``langchain.agents.create_agent`` with cherry-picked middleware
(SummarizationMiddleware + PatchToolCallsMiddleware) — only workspace tools,
no SDK built-ins.

The legacy Deep Agent harness lives in ``deep_agent_compat.py`` and can be
removed entirely by deleting that file + ``deepagents`` from requirements.txt.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence, Tuple

from langchain.agents import create_agent as create_langchain_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.config import settings
from app.services.model_profiles import build_agent_chat_model
from app.services.prompt_assembly import EntityBrief, build_agent_system_prompt
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


def _extract_last_text(messages: Sequence[BaseMessage]) -> str:
    """Extract text content from the last message in the list.

    Handles both plain string content and list-of-blocks content (Gemini
    returns ``[{"type": "text", "text": "...", "extras": {...}}]``).
    """
    import logging
    _log = logging.getLogger(__name__)

    last = messages[-1] if messages else None
    if last is None:
        return ""
    c = getattr(last, "content", None)
    if isinstance(c, str):
        if c.strip():
            return c
    elif isinstance(c, list):
        parts = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        text = "\n".join(parts)
        if text.strip():
            return text

    # Last message has no text — log diagnostic info for debugging
    _log.warning(
        "Agent returned empty text from last message. "
        "Total messages: %d, last type: %s, last content type: %s, "
        "has tool_calls: %s, message types: %s",
        len(messages),
        type(last).__name__,
        type(c).__name__ if c is not None else "None",
        bool(getattr(last, "tool_calls", None)),
        [type(m).__name__ for m in messages[-5:]],
    )
    return ""


def _build_agent_core(
    *,
    entity: EntityBrief,
    system_prompt_extras: str,
    session_id: str,
    model_profile_id: Optional[str] = None,
    run_id: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
):
    """Build the shared components (model, tools, prompt) for any agent mode."""
    model = build_agent_chat_model(profile_id=model_profile_id)
    ws = WorkspaceService(storage)
    tools = build_workspace_tools(
        entity.entity_id,
        session_id,
        run_id,
        ws,
        on_status=on_status,
        model_profile_id=model_profile_id,
    )
    system_prompt = build_agent_system_prompt(
        entity,
        extras=system_prompt_extras,
    )
    return model, tools, system_prompt


def create_react_portfolio_agent(
    *,
    entity: EntityBrief,
    system_prompt_extras: str,
    session_id: str,
    model_profile_id: Optional[str] = None,
    run_id: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
):
    """Create a ReAct agent with only workspace tools (no SDK built-ins).

    Uses ``langchain.agents.create_agent`` with cherry-picked middleware:
    - SummarizationMiddleware — auto-compacts context when tokens exceed threshold
    - PatchToolCallsMiddleware — fixes dangling tool calls from interruptions
    """
    from deepagents.backends.protocol import (
        BackendProtocol,
        EditResult,
        FileDownloadResponse,
        WriteResult,
    )
    from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
    from deepagents.middleware.summarization import SummarizationMiddleware

    class _NoOpBackend(BackendProtocol):
        """Backend that silently accepts writes but stores nothing.

        The SummarizationMiddleware requires a backend to offload compacted
        history.  We don't need offloading — summarized messages stay in the
        LangChain message list, and cross-turn history lives in the SQL
        conversation_messages table.  Using StateBackend caused bugs: the
        agent would try to read the offloaded files via workspace tools
        (which can't access StateBackend), wasting steps and producing 500s.
        """

        def write(self, file_path, content):
            return WriteResult()

        def edit(self, file_path, old_string, new_string, replace_all=False):
            return EditResult()

        def read(self, file_path, offset=0, limit=2000):
            return ""

        def download_files(self, paths):
            return [
                FileDownloadResponse(path=p, error="file_not_found")
                for p in paths
            ]

        def ls_info(self, path):
            return []

        def glob_info(self, pattern, path="/"):
            return []

        def grep_raw(self, pattern, path=None, glob=None):
            return []

        def upload_files(self, files):
            return []

    model, tools, system_prompt = _build_agent_core(
        entity=entity,
        system_prompt_extras=system_prompt_extras,
        session_id=session_id,
        model_profile_id=model_profile_id,
        run_id=run_id,
        on_status=on_status,
    )
    # Use absolute token trigger instead of fraction — ChatGoogleGenerativeAI
    # doesn't expose model metadata, so fraction-based triggers never fire.
    # Gemini 3.1 Pro has 1M context; trigger compaction at 800K tokens.
    # NoOpBackend: summarized messages stay in-place; no file offloading.
    middleware = [
        SummarizationMiddleware(
            model=model,
            backend=_NoOpBackend(),
            trigger=("tokens", 800_000),
            keep=("tokens", 100_000),
        ),
        PatchToolCallsMiddleware(),
    ]
    return create_langchain_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
    )


def invoke_react_portfolio_agent(
    agent,
    lc_messages: List[BaseMessage],
    on_status: Optional[Callable[[str], None]] = None,
) -> Tuple[str, Any]:
    """Invoke a ReAct agent."""
    _notify_status(on_status, "Model running (may take a while)...")
    cfg = {"recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT}
    result = agent.invoke({"messages": lc_messages}, config=cfg)
    _notify_status(on_status, "Composing reply...")
    messages_out = result.get("messages") or []
    text = _extract_last_text(messages_out)
    return text, result
