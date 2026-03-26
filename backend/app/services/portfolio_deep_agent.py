"""Deep Agents harness: portfolio-scoped tools + invoke wrapper."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, List, Literal, Optional, Sequence, Tuple

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.config import settings
from app.database import SyncSessionLocal
from app.services.artifact_editing import (
    apply_artifact_edit,
    list_entity_artifacts_sync,
    list_entity_resources_sync,
    read_artifact_text_sync,
    read_resource_payload_sync,
    record_edit_event,
    resolve_artifact_target,
    resolve_snapshot,
    validate_edit_payload,
)
from app.services.model_profiles import build_deep_agent_base_chat_model
from app.services.prompt_assembly import EntityBrief, build_deep_agent_system_prompt


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _notify_status(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if not on_status:
        return
    try:
        on_status(msg)
    except Exception:
        pass


def build_portfolio_tools(
    entity_id: str,
    session_id: str,
    session_artifact_ids: Sequence[str],
    run_id: Optional[str],
    on_status: Optional[Callable[[str], None]] = None,
) -> list:
    from langchain_core.tools import tool

    hints = list(session_artifact_ids)

    @tool
    def portfolio_list_artifacts(limit: int = 30) -> str:
        """List saved artifacts for this entity (id, type, title, version, updated)."""
        _notify_status(on_status, "Listing artifacts…")
        lim = max(1, min(int(limit), 100))
        with SyncSessionLocal() as db:
            rows = list_entity_artifacts_sync(db, entity_id)[:lim]
            out = [
                {
                    "id": a.id,
                    "artifact_type": a.artifact_type,
                    "title": a.title,
                    "version": a.version,
                    "status": a.status,
                    "relative_path": a.relative_path,
                }
                for a in rows
            ]
        return _json({"ok": True, "artifacts": out})

    @tool
    def portfolio_resolve_artifact_target(
        artifact_id: Optional[str] = None,
        title_hint: Optional[str] = None,
        artifact_type_hint: Optional[str] = None,
    ) -> str:
        """Resolve which artifact to edit. Returns confidence and candidates; does not write."""
        _notify_status(on_status, "Resolving artifact target…")
        with SyncSessionLocal() as db:
            res = resolve_artifact_target(
                db,
                entity_id,
                artifact_id=artifact_id,
                title_hint=title_hint,
                artifact_type_hint=artifact_type_hint,
                session_artifact_ids=hints,
            )
        return _json(resolve_snapshot(res))

    @tool
    def portfolio_list_resources(limit: int = 40) -> str:
        """List uploaded resources for this entity (file, text, url metadata)."""
        _notify_status(on_status, "Listing resources…")
        lim = max(1, min(int(limit), 100))
        with SyncSessionLocal() as db:
            rows = list_entity_resources_sync(db, entity_id)[:lim]
            out = [
                {
                    "id": r.id,
                    "resource_type": r.resource_type,
                    "title": r.title,
                    "mime_type": r.mime_type,
                    "url": r.url,
                    "relative_path": r.relative_path,
                }
                for r in rows
            ]
        return _json({"ok": True, "resources": out})

    @tool
    def portfolio_read_resource(resource_id: str) -> str:
        """Read text content of a resource by id (URLs return the URL string; PDF/images are rejected in-tool)."""
        _notify_status(on_status, f"Reading resource {resource_id[:8]}…")
        with SyncSessionLocal() as db:
            data = read_resource_payload_sync(db, entity_id, resource_id)
        return _json(data)

    @tool
    def portfolio_read_artifact(artifact_id: str) -> str:
        """Read full text content of an artifact by id."""
        _notify_status(on_status, f"Reading artifact {artifact_id[:8]}…")
        with SyncSessionLocal() as db:
            data = read_artifact_text_sync(db, entity_id, artifact_id)
        return _json(data)

    @tool
    def portfolio_validate_artifact_edit(artifact_id: str, new_content: str) -> str:
        """Validate new content (size, JSON shape) for an artifact without applying."""
        _notify_status(on_status, f"Validating edit for {artifact_id[:8]}…")
        with SyncSessionLocal() as db:
            res = resolve_artifact_target(
                db,
                entity_id,
                artifact_id=artifact_id,
                session_artifact_ids=hints,
            )
            if not res["ok"] or res["artifact"] is None:
                return _json(
                    {
                        "ok": False,
                        "error": "target_not_resolved",
                        "resolve": resolve_snapshot(res),
                    }
                )
            target = res["artifact"]
            v = validate_edit_payload(new_content, target=target)
        return _json({"ok": v["ok"], "validation": v})

    @tool
    def portfolio_apply_artifact_edit(
        artifact_id: str,
        new_content: str,
        mode: Literal["versioned", "overwrite"] = "versioned",
        user_context: str = "",
    ) -> str:
        """
        Apply an edit after validation (Option B). Use only when content is final.
        mode: versioned (new artifact row) or overwrite (same file; server must allow).
        """
        _notify_status(on_status, f"Applying artifact edit ({mode})…")
        correlation_id = str(uuid.uuid4())
        with SyncSessionLocal() as db:
            res = resolve_artifact_target(
                db,
                entity_id,
                artifact_id=artifact_id,
                session_artifact_ids=hints,
            )
            if not res["ok"] or res["artifact"] is None:
                record_edit_event(
                    db,
                    correlation_id=correlation_id,
                    entity_id=entity_id,
                    session_id=session_id,
                    artifact_id=artifact_id,
                    state="failed",
                    error_message=res.get("reason", "unresolved_target"),
                    run_id=run_id,
                    tool_context=resolve_snapshot(res),
                )
                return _json(
                    {
                        "ok": False,
                        "error": "target_not_resolved",
                        "reason": res.get("reason"),
                        "candidates": res.get("candidates"),
                    }
                )
            target = res["artifact"]
            out = apply_artifact_edit(
                db,
                correlation_id=correlation_id,
                entity_id=entity_id,
                session_id=session_id,
                target=target,
                new_content=new_content,
                mode=mode,
                user_text_for_mode=user_context,
                explicit_mode=mode,
                run_id=run_id,
            )
        return _json(out)

    return [
        portfolio_list_artifacts,
        portfolio_list_resources,
        portfolio_resolve_artifact_target,
        portfolio_read_resource,
        portfolio_read_artifact,
        portfolio_validate_artifact_edit,
        portfolio_apply_artifact_edit,
    ]


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
    session_artifact_ids: Sequence[str],
    model_profile_id: Optional[str] = None,
    run_id: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
):
    model = build_deep_agent_base_chat_model(profile_id=model_profile_id)
    tools = build_portfolio_tools(
        entity.entity_id,
        session_id,
        session_artifact_ids,
        run_id,
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
) -> Tuple[str, Any]:
    _notify_status(on_status, "Model running (may take a while)…")
    cfg = {"recursion_limit": settings.CHAT_AGENT_RECURSION_LIMIT}
    result = agent.invoke({"messages": lc_messages}, config=cfg)
    _notify_status(on_status, "Composing reply…")
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
