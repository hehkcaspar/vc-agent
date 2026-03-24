"""ReAct agent built on LangGraph — the brain of the workspace processor."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from .config import LLMSettings, WorkspaceConfig, llm_settings, load_workspace_config
from .prompts import build_system_prompt
from .tools.extract_content import extract_content
from .tools.read_artifact import read_artifact
from .tools.scan_resources import scan_resources
from .tools.search_resources import search_resources
from .tools.write_artifact import write_artifact
from .utils import ProgressCallback, with_retry
from .workspace import Workspace

logger = logging.getLogger(__name__)


def _load_memory(workspace_root: Path, cfg: WorkspaceConfig) -> Optional[str]:
    """Load all memory markdown files and concatenate."""
    memory_dir = workspace_root / cfg.artifacts_dir / "memory"
    if not memory_dir.exists():
        return None
    parts = []
    for f in sorted(memory_dir.glob("*.md")):
        parts.append(f"### {f.stem}\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(parts) if parts else None


def build_agent(
    workspace_root: Path,
    task: str,
    llm: Optional[LLMSettings] = None,
    ws_config: Optional[WorkspaceConfig] = None,
):
    """Build a ready-to-invoke ReAct agent for the given workspace and task."""
    settings = llm or llm_settings
    settings.validate()
    cfg = ws_config or load_workspace_config(workspace_root)

    # -- LLM ---------------------------------------------------------------
    model = ChatOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
        temperature=settings.temperature,
    )

    # -- Context -----------------------------------------------------------
    ws = Workspace(workspace_root, cfg.resources_dir, cfg.snapshots_dir)
    previous = ws.load_snapshot()
    current = ws.scan()

    diff_summary: Optional[str] = None
    if previous is not None:
        diff = ws.diff(current, previous)
        diff_summary = ws.format_diff_summary(diff)

    memory_content = _load_memory(workspace_root, cfg)

    system_prompt = build_system_prompt(
        workspace_root=str(workspace_root),
        task_content=task,
        diff_summary=diff_summary,
        memory_content=memory_content,
    )

    # -- Tools -------------------------------------------------------------
    tools = [
        scan_resources,
        extract_content,
        write_artifact,
        read_artifact,
        search_resources,
    ]

    # -- Agent -------------------------------------------------------------
    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=system_prompt,
    )

    return agent, current, ws, cfg


class ToolCallbackHandler(BaseCallbackHandler):
    """Callback handler that prints tool calls as they happen."""
    
    def __init__(self, progress: ProgressCallback):
        self.progress = progress
        self._current_tool: str = "unknown"
    
    def on_tool_start(
        self, 
        serialized: Dict[str, Any], 
        input_str: str,
        *,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any
    ) -> None:
        """Called when a tool starts."""
        # Handle different serialized formats
        if isinstance(serialized, dict):
            tool_name = serialized.get("name", "unknown")
        elif isinstance(serialized, str):
            tool_name = serialized
        else:
            tool_name = "unknown"
        
        self._current_tool = tool_name
        # Use inputs dict if available, otherwise parse from input_str
        tool_inputs = inputs if inputs is not None else {}
        self.progress.on_tool_start(tool_name, tool_inputs)
    
    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Called when a tool ends."""
        output_str = str(output) if output is not None else ""
        self.progress.on_tool_end(self._current_tool, output_str)


@with_retry(max_attempts=3, initial_delay=1.0)
def _invoke_agent_with_retry(agent, inputs: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the agent with retry logic for transient errors."""
    return agent.invoke(inputs, config=config)


def run_agent(
    workspace_root: Path,
    task: str,
    llm: Optional[LLMSettings] = None,
    verbose: bool = True,
    images: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Run the agent end-to-end on a workspace with a task.

    Args:
        workspace_root: Path to the workspace directory
        task: The task description/instruction
        llm: Optional LLM settings override
        verbose: Whether to print progress output
        images: Optional list of image paths to pass explicitly to the vision LLM

    Returns:
        The final agent state including messages and trace info
    """
    workspace_root = workspace_root.resolve()
    cfg = load_workspace_config(workspace_root)

    agent, manifest, ws, cfg = build_agent(workspace_root, task, llm, cfg)

    # Inject workspace_root so tools can find the workspace
    # The tools need workspace_root as their first arg; we bind it via the user message.
    user_msg_text = (
        f"The workspace root is: {workspace_root}\n\n"
        f"Please complete the following task:\n{task}"
    )

    user_msg: Any
    if images:
        import base64
        msg_list: List[Dict[str, Any]] = [{"type": "text", "text": user_msg_text}]
        for img_path in images:
            if img_path.exists():
                img_data = base64.b64encode(img_path.read_bytes()).decode("utf-8")
                ext = img_path.suffix.lower().lstrip(".")
                mime = f"image/{ext}" if ext in ["png", "jpeg", "webp", "gif"] else "image/jpeg"
                if ext == "jpg": mime = "image/jpeg"
                msg_list.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_data}"}
                })
        user_msg = msg_list
    else:
        user_msg = user_msg_text

    print(f"[agent] Starting task in {workspace_root}")
    print(f"[agent] Model: {llm_settings.model}")
    print(f"[agent] Resources: {len(manifest)} files")
    if verbose:
        print(f"[agent] Progress output enabled (use --quiet to disable)")
    print()

    # Set up progress callback
    progress = ProgressCallback(verbose=verbose)
    callbacks = [ToolCallbackHandler(progress)] if verbose else []

    # Run the agent with retry logic
    try:
        result = _invoke_agent_with_retry(
            agent,
            {"messages": [("user", user_msg)]},
            config={
                "recursion_limit": cfg.agent.max_iterations * 2,
                "callbacks": callbacks,
            },
        )
    except Exception as e:
        logger.error(f"Agent failed after retries: {e}")
        print(f"\n[error] Agent failed: {e}")
        raise

    # Save snapshot after run
    ws.save_snapshot(manifest)

    # Save trace
    if cfg.agent.trace_enabled:
        _save_trace(workspace_root, cfg, result, task)

    # Print final response
    messages = result.get("messages", [])
    if messages:
        final = messages[-1]
        content = final.content if hasattr(final, "content") else str(final)
        # Clean unicode characters that may cause encoding issues on Windows
        content = content.replace('\u2705', '[OK]').replace('\u274c', '[FAIL]').replace('\u26a0', '[WARN]').replace('\ufe0f', '')
        print("\n[agent] Final response:")
        print(content)

    return result


def _save_trace(
    workspace_root: Path,
    cfg: WorkspaceConfig,
    result: Dict[str, Any],
    task: str,
) -> None:
    """Persist execution trace to artifacts/traces/."""
    trace_dir = workspace_root / cfg.artifacts_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    trace_file = trace_dir / f"trace_{timestamp}.json"

    messages = result.get("messages", [])
    trace_entries = []
    for msg in messages:
        entry: Dict[str, Any] = {"type": type(msg).__name__}
        if hasattr(msg, "content"):
            content = msg.content
            # Truncate long content in trace
            if isinstance(content, str) and len(content) > 2000:
                content = content[:2000] + "...(truncated)"
            entry["content"] = content
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            entry["tool_calls"] = [
                {"name": tc["name"], "args": tc["args"]}
                for tc in msg.tool_calls
            ]
        if hasattr(msg, "name"):
            entry["tool_name"] = msg.name
        trace_entries.append(entry)

    trace_data = {
        "timestamp": timestamp,
        "task": task,
        "message_count": len(messages),
        "messages": trace_entries,
    }

    trace_file.write_text(
        json.dumps(trace_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[agent] Trace saved: {trace_file.relative_to(workspace_root)}")
