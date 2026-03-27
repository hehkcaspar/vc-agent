"""Deep Agents harness: portfolio-scoped tools + invoke wrapper."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, List, Literal, Optional, Sequence, Tuple

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.config import settings
from app.database import SyncSessionLocal
from app.services.artifact_service import create_artifact_for_entity_sync
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


def _looks_like_create_intent(text: str) -> bool:
    """Heuristic: user wants a new persisted note / artifact, not necessarily precise wording."""
    raw = (text or "").strip()
    if not raw:
        return False
    s = raw.casefold()

    en_patterns = (
        r"\bsave\b.*\bartifact\b",
        r"\bcreate\b.*\bartifact\b",
        r"\bnew\b.*\bartifact\b",
        r"\bstore\b.*\bartifact\b",
        r"\brecord\b.*\bartifact\b",
        r"\btake a note\b",
        r"\bjot (this )?down\b",
        r"\bfor (the )?record\b",
        r"\bkeep\b.+\bon file\b",
        r"\bkeep\b.+\bin (the )?record\b",
        r"\bkeep\b.+\bin (the )?workspace\b",
        r"\bkeep\b.+\bit somewhere\b",
        r"\bput\b.+\bon record\b",
        r"\bwrite (this )?up\b",
        r"\bcapture (this )?discussion\b",
        r"\bpersist\b",
        r"(?<![a-z])memo(?![a-z])",  # e.g. "帮我memo一下" (word boundaries fail beside CJK)
    )
    zh_patterns = (
        r"存下来",
        r"保存",
        r"记下来",
        r"备忘",
        r"留底",
        r"存档",
        r"归档",
        r"记在",
        r"写个备忘",
        r"帮我记",
        r"留在档案",
        r"记在档案",
        r"留个记录",
        r"workspace里(?:面)?(?:记|存|留)",
        r"工作区(?:里|中)(?:记|存|留)",
        r"整理(?:一下)?(?:好)?(?:并|再)?[记存留]",
        r"新建.*artifact",
        r"创建.*artifact",
        r"memo一下",
    )
    if any(re.search(p, s, flags=re.IGNORECASE) for p in en_patterns):
        return True
    if any(re.search(p, raw) for p in zh_patterns):
        return True
    # "Summarize X and keep …" / "总结…存|记" without the word "artifact"
    if re.search(r"\bsummarize\b", s) and (
        re.search(r"\b(keep|store|save|record|file|memo|note)\b", s)
        or re.search(r"记|存|档案|备忘|留底|留下来", raw)
    ):
        return True
    if re.search(r"总结", raw) and re.search(
        r"(?:记|存|留|备忘|档案|留下来|放(?:在|到)?(?:工作区|档案|记录))",
        raw,
    ):
        return True
    return False


def _looks_like_explicit_edit_intent(text: str) -> bool:
    s = (text or "").strip().casefold()
    raw = (text or "").strip()
    if not s:
        return False
    en_patterns = (
        r"\bedit\b",
        r"\bupdate\b",
        r"\brevise\b",
        r"\bmodify\b",
        r"\boverwrite\b",
        r"\bnew version\b",
        r"\bpatch\b",
        r"\bappend\b",
        r"\badd to\b",
    )
    zh_patterns = (
        r"修改",
        r"更新",
        r"覆盖",
        r"新版本",
        r"加上",
        r"补充",
        r"增补",
        r"修订",
        r"改正",
    )
    if any(re.search(p, s, flags=re.IGNORECASE) for p in en_patterns):
        return True
    return any(re.search(p, raw) for p in zh_patterns)


def build_portfolio_tools(
    entity_id: str,
    session_id: str,
    session_artifact_ids: Sequence[str],
    run_id: Optional[str],
    initial_user_text: str = "",
    on_status: Optional[Callable[[str], None]] = None,
) -> list:
    from langchain_core.tools import tool

    hints = list(session_artifact_ids)
    user_create_intent = _looks_like_create_intent(initial_user_text)
    explicit_target_in_turn = bool(hints)

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
    def portfolio_create_artifact(
        content: str,
        artifact_type: Literal["memo", "factsheet", "report", "other"] = "memo",
        title: Optional[str] = None,
        status: Literal["draft", "final"] = "draft",
        format: Literal["markdown", "json", "text"] = "markdown",
    ) -> str:
        """Create a new artifact (independent lineage) for this entity."""
        _notify_status(on_status, "Creating artifact…")
        suffix = ".md"
        if format == "json":
            suffix = ".json"
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                return _json({"ok": False, "error": f"invalid_json: {e}"})
        elif format == "text":
            suffix = ".txt"
        with SyncSessionLocal() as db:
            try:
                created = create_artifact_for_entity_sync(
                    db,
                    entity_id=entity_id,
                    artifact_type=artifact_type,
                    content=content,
                    status=status,
                    title=title,
                    file_suffix=suffix,
                )
            except ValueError as e:
                return _json({"ok": False, "error": str(e)})
        return _json(
            {
                "ok": True,
                "artifact_id": created.id,
                "artifact_type": created.artifact_type,
                "title": created.title,
                "version": created.version,
                "status": created.status,
                "relative_path": created.relative_path,
                "mode": "created_new",
            }
        )

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
        if (
            settings.CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY == "create_new"
            and user_create_intent
            and not explicit_target_in_turn
            and not _looks_like_explicit_edit_intent(user_context)
            and not _looks_like_explicit_edit_intent(initial_user_text)
        ):
            return _json(
                {
                    "ok": False,
                    "error": "create_intent_requires_create_tool",
                    "hint": (
                        "User intent indicates creating/saving a new artifact without "
                        "an explicit target. Use portfolio_create_artifact instead."
                    ),
                }
            )
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
        portfolio_create_artifact,
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
    initial_user_text: str = "",
    on_status: Optional[Callable[[str], None]] = None,
):
    model = build_deep_agent_base_chat_model(profile_id=model_profile_id)
    tools = build_portfolio_tools(
        entity.entity_id,
        session_id,
        session_artifact_ids,
        run_id,
        initial_user_text=initial_user_text,
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
        # Replace final user text message with multimodal blocks.
        last = lc_messages[-1] if lc_messages else None
        user_text = ""
        if isinstance(last, HumanMessage) and isinstance(last.content, str):
            user_text = last.content
            lc_messages = lc_messages[:-1]
        blocks: List[dict[str, Any]] = [{"type": "text", "text": user_text}]
        blocks.extend(user_multimodal_parts)
        lc_messages = [*lc_messages, HumanMessage(content=blocks)]
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
