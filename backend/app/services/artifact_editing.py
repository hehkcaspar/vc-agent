"""Option B artifact edit pipeline: resolve, validate, apply, audit (design §8)."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from typing import Any, List, Literal, Optional, Sequence, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Artifact, ArtifactEditEvent, Resource
import app.services.storage as storage_module
from app.services.artifact_service import (
    artifact_file_suffix,
    create_artifact_for_entity_sync,
    overwrite_artifact_content_sync,
)


def content_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _json_dumps_bounded(obj: Optional[dict], max_len: int = 8000) -> Optional[str]:
    if obj is None:
        return None
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def record_edit_event(
    db: Session,
    *,
    correlation_id: str,
    entity_id: str,
    session_id: Optional[str],
    artifact_id: Optional[str],
    state: str,
    requested_mode: Optional[str] = None,
    resolved_mode: Optional[str] = None,
    intent_summary: Optional[str] = None,
    tool_context: Optional[dict] = None,
    validation: Optional[dict] = None,
    before_checksum: Optional[str] = None,
    after_checksum: Optional[str] = None,
    error_message: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    ev = ArtifactEditEvent(
        correlation_id=correlation_id,
        entity_id=entity_id,
        session_id=session_id,
        artifact_id=artifact_id,
        requested_mode=requested_mode,
        resolved_mode=resolved_mode,
        state=state,
        intent_summary=intent_summary,
        tool_context_json=_json_dumps_bounded(tool_context),
        validation_result_json=_json_dumps_bounded(validation),
        before_checksum=before_checksum,
        after_checksum=after_checksum,
        error_message=error_message,
        run_id=run_id,
        pipeline_version="option_b",
    )
    db.add(ev)
    db.commit()


def resolve_snapshot(res: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe resolve result (strip SQLAlchemy artifact ORM)."""
    art = res.get("artifact")
    return {
        "ok": res["ok"],
        "confidence": res["confidence"],
        "candidates": res.get("candidates") or [],
        "reason": res.get("reason"),
        "resolved_artifact_id": art.id if art is not None else None,
    }


def list_entity_artifacts_sync(db: Session, entity_id: str) -> List[Artifact]:
    q = (
        select(Artifact)
        .where(Artifact.entity_id == entity_id)
        .order_by(Artifact.updated_at.desc())
    )
    return list(db.execute(q).scalars().all())


def list_entity_resources_sync(db: Session, entity_id: str) -> List[Resource]:
    q = (
        select(Resource)
        .where(Resource.entity_id == entity_id)
        .order_by(Resource.updated_at.desc())
    )
    return list(db.execute(q).scalars().all())


def read_resource_payload_sync(
    db: Session, entity_id: str, resource_id: str
) -> dict[str, Any]:
    """Return text/url payload for agent tools; enforces size and text/binary policy."""
    row = db.execute(
        select(Resource).where(
            Resource.id == resource_id, Resource.entity_id == entity_id
        )
    ).scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "not_found"}
    if row.resource_type == "url":
        return {
            "ok": True,
            "resource_id": row.id,
            "title": row.title,
            "resource_type": "url",
            "url": row.url or "",
        }
    if not row.relative_path:
        return {"ok": False, "error": "no_path"}
    try:
        raw = storage_module.storage.read_file_sync(row.relative_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    cap = settings.CHAT_MAX_ATTACHMENT_BYTES
    if len(raw) > cap:
        return {
            "ok": False,
            "error": "file_too_large",
            "max_bytes": cap,
        }
    mime = (row.mime_type or "").strip() or mimetypes.guess_type(
        row.original_filename or row.title or ""
    )[0] or "application/octet-stream"
    if mime == "application/pdf" or mime.startswith("image/"):
        return {
            "ok": False,
            "error": "binary_not_supported_in_tool",
            "mime_type": mime,
            "hint": "Binary was omitted; ask the user to refer to the uploaded file or use legacy chat for multimodal context.",
        }
    if mime.startswith("text/") or mime in ("application/json", "application/xml"):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
    else:
        try:
            text = raw.decode("utf-8")
        except Exception:
            return {
                "ok": False,
                "error": "binary_not_supported_in_tool",
                "mime_type": mime,
            }
    max_c = settings.CHAT_MAX_ARTIFACT_CHARS
    if len(text) > max_c:
        text = text[: max_c - 40] + "\n\n…(truncated)"
    return {
        "ok": True,
        "resource_id": row.id,
        "title": row.title,
        "resource_type": row.resource_type,
        "mime_type": mime,
        "text": text,
    }


def resolve_artifact_target(
    db: Session,
    entity_id: str,
    *,
    artifact_id: Optional[str] = None,
    title_hint: Optional[str] = None,
    artifact_type_hint: Optional[str] = None,
    session_artifact_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Deterministic target resolution; no writes. Returns ok, confidence, artifact ORM or None."""
    session_artifact_ids = list(session_artifact_ids or [])
    rows = list_entity_artifacts_sync(db, entity_id)
    if not rows:
        return {
            "ok": False,
            "confidence": 0.0,
            "artifact": None,
            "candidates": [],
            "reason": "no_artifacts",
        }

    if artifact_id:
        for a in rows:
            if a.id == artifact_id:
                return {
                    "ok": True,
                    "confidence": 1.0,
                    "artifact": a,
                    "candidates": [a.id],
                    "reason": "id_match",
                }
        return {
            "ok": False,
            "confidence": 0.0,
            "artifact": None,
            "candidates": [],
            "reason": "id_not_found",
        }

    def score_row(a: Artifact) -> float:
        s = 0.0
        th = (title_hint or "").strip().casefold()
        if th:
            tit = (a.title or "").strip().casefold()
            if tit == th:
                s = max(s, 0.92)
            elif th and tit and (th in tit or tit in th):
                s = max(s, 0.78)
            elif th and tit:
                if any(len(tok) > 2 and tok in tit for tok in th.split()):
                    s = max(s, 0.55)
        if artifact_type_hint and a.artifact_type == artifact_type_hint:
            s = max(s, (s + 0.25) if s > 0 else 0.45)
        if a.id in session_artifact_ids:
            s += 0.12
        if s == 0.0:
            s = 0.15
        return min(1.0, s)

    scored = [(score_row(a), a) for a in rows]
    scored.sort(key=lambda x: (-x[0], -x[1].updated_at.timestamp()))

    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    min_score = settings.CHAT_ARTIFACT_RESOLVE_MIN_SCORE
    if best_score < min_score:
        return {
            "ok": False,
            "confidence": best_score,
            "artifact": None,
            "candidates": [a.id for _, a in scored[:5]],
            "reason": "below_threshold",
        }
    if best_score - second_score < 0.05 and second_score > 0:
        tied = [a.id for s, a in scored if s >= best_score - 0.05][:5]
        if len(tied) > 1:
            return {
                "ok": False,
                "confidence": best_score,
                "artifact": None,
                "candidates": tied,
                "reason": "ambiguous",
            }

    return {
        "ok": True,
        "confidence": best_score,
        "artifact": best,
        "candidates": [best.id],
        "reason": "ranked",
    }


def resolve_edit_mode(
    *,
    user_text: str = "",
    explicit_mode: Optional[Literal["versioned", "overwrite"]] = None,
) -> Literal["versioned", "overwrite"]:
    """Precedence: explicit param (if allowed), user phrasing, default config."""
    t = (user_text or "").casefold()

    eff_explicit = explicit_mode
    if eff_explicit == "overwrite" and not settings.CHAT_ARTIFACT_OVERWRITE_ENABLED:
        eff_explicit = None

    if eff_explicit == "overwrite":
        return "overwrite"
    if eff_explicit == "versioned":
        return "versioned"

    if any(
        k in t
        for k in (
            "overwrite file",
            "in-place",
            "in place",
            "replace in place",
            "overwrite artifact",
        )
    ):
        return "overwrite" if settings.CHAT_ARTIFACT_OVERWRITE_ENABLED else "versioned"
    if any(
        k in t
        for k in (
            "new version",
            "versioned",
            "save as new version",
            "duplicate as new",
        )
    ):
        return "versioned"

    return settings.CHAT_ARTIFACT_DEFAULT_EDIT_MODE


def validate_edit_payload(
    content: str,
    *,
    target: Optional[Artifact] = None,
) -> dict[str, Any]:
    violations: List[str] = []
    max_c = settings.CHAT_MAX_ARTIFACT_CHARS
    if len(content) > max_c:
        violations.append(f"content exceeds max length ({max_c} chars)")

    is_json_artifact = False
    if target is not None:
        is_json_artifact = target.relative_path.lower().endswith(".json")
    if is_json_artifact or (target is None and content.strip().startswith("{")):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            violations.append(f"invalid_json: {e}")

    ok = len(violations) == 0
    return {"ok": ok, "violations": violations}


def apply_artifact_edit(
    db: Session,
    *,
    correlation_id: str,
    entity_id: str,
    session_id: Optional[str],
    target: Artifact,
    new_content: str,
    mode: Literal["versioned", "overwrite"],
    user_text_for_mode: str = "",
    explicit_mode: Optional[Literal["versioned", "overwrite"]] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Full Option B pipeline with audit rows. Only performs storage/DB writes when validation passes
    and mode is allowed for overwrite.
    """
    resolved_mode = resolve_edit_mode(
        user_text=user_text_for_mode, explicit_mode=explicit_mode or mode
    )
    before = None
    try:
        raw_before = storage_module.storage.read_file_sync(target.relative_path)
        before = raw_before.decode("utf-8", errors="replace")
    except Exception:
        pass
    before_hash = content_checksum(before) if before is not None else None

    record_edit_event(
        db,
        correlation_id=correlation_id,
        entity_id=entity_id,
        session_id=session_id,
        artifact_id=target.id,
        state="intent_received",
        requested_mode=mode,
        resolved_mode=resolved_mode,
        run_id=run_id,
        tool_context={"target_id": target.id, "lineage_title": target.title},
    )

    record_edit_event(
        db,
        correlation_id=correlation_id,
        entity_id=entity_id,
        session_id=session_id,
        artifact_id=target.id,
        state="target_resolved",
        requested_mode=mode,
        resolved_mode=resolved_mode,
        run_id=run_id,
    )

    record_edit_event(
        db,
        correlation_id=correlation_id,
        entity_id=entity_id,
        session_id=session_id,
        artifact_id=target.id,
        state="mode_resolved",
        requested_mode=mode,
        resolved_mode=resolved_mode,
        run_id=run_id,
        validation={"resolved_mode": resolved_mode},
    )

    validation = validate_edit_payload(new_content, target=target)
    if not validation["ok"]:
        record_edit_event(
            db,
            correlation_id=correlation_id,
            entity_id=entity_id,
            session_id=session_id,
            artifact_id=target.id,
            state="failed",
            requested_mode=mode,
            resolved_mode=resolved_mode,
            validation=validation,
            before_checksum=before_hash,
            error_message="validation_failed",
            run_id=run_id,
        )
        return {"ok": False, "error": "validation_failed", "validation": validation}

    record_edit_event(
        db,
        correlation_id=correlation_id,
        entity_id=entity_id,
        session_id=session_id,
        artifact_id=target.id,
        state="edit_payload_validated",
        requested_mode=mode,
        resolved_mode=resolved_mode,
        validation=validation,
        before_checksum=before_hash,
        run_id=run_id,
    )

    atype = cast(
        Literal["memo", "factsheet", "report", "other"], target.artifact_type
    )
    astatus = cast(Literal["draft", "final"], target.status)

    if resolved_mode == "overwrite":
        if not settings.CHAT_ARTIFACT_OVERWRITE_ENABLED:
            record_edit_event(
                db,
                correlation_id=correlation_id,
                entity_id=entity_id,
                session_id=session_id,
                artifact_id=target.id,
                state="failed",
                requested_mode=mode,
                resolved_mode=resolved_mode,
                error_message="overwrite_disabled",
                before_checksum=before_hash,
                run_id=run_id,
            )
            return {"ok": False, "error": "overwrite_disabled"}

        try:
            updated = overwrite_artifact_content_sync(
                db, entity_id, target.id, new_content
            )
        except ValueError as e:
            record_edit_event(
                db,
                correlation_id=correlation_id,
                entity_id=entity_id,
                session_id=session_id,
                artifact_id=target.id,
                state="failed",
                error_message=str(e),
                before_checksum=before_hash,
                run_id=run_id,
            )
            return {"ok": False, "error": str(e)}
        after_hash = content_checksum(new_content)
        record_edit_event(
            db,
            correlation_id=correlation_id,
            entity_id=entity_id,
            session_id=session_id,
            artifact_id=updated.id,
            state="applied",
            resolved_mode="overwrite",
            before_checksum=before_hash,
            after_checksum=after_hash,
            run_id=run_id,
        )
        return {
            "ok": True,
            "mode": "overwrite",
            "artifact_id": updated.id,
            "version": updated.version,
        }

    # versioned: new row in same (type, title) lineage
    suffix = artifact_file_suffix(target.relative_path)
    try:
        created = create_artifact_for_entity_sync(
            db,
            entity_id,
            atype,
            new_content,
            status=astatus,
            title=target.title,
            file_suffix=suffix,
        )
    except ValueError as e:
        record_edit_event(
            db,
            correlation_id=correlation_id,
            entity_id=entity_id,
            session_id=session_id,
            artifact_id=target.id,
            state="failed",
            error_message=str(e),
            before_checksum=before_hash,
            run_id=run_id,
        )
        return {"ok": False, "error": str(e)}
    after_hash = content_checksum(new_content)
    record_edit_event(
        db,
        correlation_id=correlation_id,
        entity_id=entity_id,
        session_id=session_id,
        artifact_id=created.id,
        state="applied",
        resolved_mode="versioned",
        before_checksum=before_hash,
        after_checksum=after_hash,
        run_id=run_id,
        tool_context={"prior_artifact_id": target.id, "new_artifact_id": created.id},
    )
    return {
        "ok": True,
        "mode": "versioned",
        "artifact_id": created.id,
        "version": created.version,
    }


def read_artifact_text_sync(db: Session, entity_id: str, artifact_id: str) -> dict[str, Any]:
    row = db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id, Artifact.entity_id == entity_id
        )
    ).scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "not_found"}
    try:
        raw = storage_module.storage.read_file_sync(row.relative_path)
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "artifact_id": row.id,
        "artifact_type": row.artifact_type,
        "title": row.title,
        "version": row.version,
        "relative_path": row.relative_path,
        "content": text,
        "checksum": content_checksum(text),
    }
