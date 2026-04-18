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
from app.services.legal_template_tools import build_legal_template_tools
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
    preset_id: Optional[str] = None,
    include_web_search: bool = False,
    tool_allowlist: Optional[Sequence[str]] = None,
):
    """Build the shared components (model, tools, prompt) for any agent mode.

    ``include_web_search``: when True, appends a Gemini-grounded web search
    function tool. Opt-in (Initial Screening and future research presets).

    ``tool_allowlist``: when provided, only tools whose name appears in
    this set are kept. Use to narrow a research sub-agent's attack
    surface (e.g. v2 section agents get read/write/search only — no tree
    browsing, no file management — so they can't burn their recursion
    budget exploring the data room). ``None`` means "all tools".
    """
    model = build_agent_chat_model(profile_id=model_profile_id)
    ws = WorkspaceService(storage)
    tools = build_workspace_tools(
        entity.entity_id,
        session_id,
        run_id,
        ws,
        on_status=on_status,
        model_profile_id=model_profile_id,
        preset_id=preset_id,
    )
    # Add the entity-agnostic legal-template reader (Tier R1 reference corpus).
    # Read-only and harmless for presets that don't use it.
    tools = tools + build_legal_template_tools(on_status=on_status)
    if include_web_search:
        from app.services.web_search_tool import build_web_search_tool
        tools = tools + [build_web_search_tool(on_status=on_status)]

    if tool_allowlist is not None:
        allowed = set(tool_allowlist)
        tools = [t for t in tools if getattr(t, "name", None) in allowed]

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
    preset_id: Optional[str] = None,
    include_web_search: bool = False,
    tool_allowlist: Optional[Sequence[str]] = None,
):
    """Create a ReAct agent with only workspace tools (no SDK built-ins).

    Uses ``langchain.agents.create_agent`` with cherry-picked middleware:
    - SummarizationMiddleware — auto-compacts context when tokens exceed threshold
    - PatchToolCallsMiddleware — fixes dangling tool calls from interruptions

    ``include_web_search`` opts in a Gemini-grounded web search function tool
    for research-oriented presets (Initial Screening).
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
        preset_id=preset_id,
        include_web_search=include_web_search,
        tool_allowlist=tool_allowlist,
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
    recursion_limit: Optional[int] = None,
) -> Tuple[str, Any]:
    """Invoke a ReAct agent.

    ``recursion_limit`` overrides the env-configured default for presets
    that need more headroom (Initial Screening's multi-section research
    needs ~60-80 tool calls in a rich workspace).
    """
    _notify_status(on_status, "Model running (may take a while)...")
    limit = (
        recursion_limit
        if recursion_limit and recursion_limit > 0
        else settings.CHAT_AGENT_RECURSION_LIMIT
    )
    cfg = {"recursion_limit": limit}
    result = agent.invoke({"messages": lc_messages}, config=cfg)
    _notify_status(on_status, "Composing reply...")
    messages_out = result.get("messages") or []
    text = _extract_last_text(messages_out)
    return text, result
