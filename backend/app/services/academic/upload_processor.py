"""Process user-uploaded dossier documents via a single structured call.

Path 1 — one generate_structured call returns a structured update plan
(fields to merge into profile.json + events to append). No agent loop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .fact_store import record_snapshot
from .file_utils import dossier_path, read_json, write_json
from .llm_client import generate_structured

logger = logging.getLogger(__name__)


class ProfilePatch(BaseModel):
    """Shallow-merge keys to apply to profile.json."""

    affiliation: dict[str, Any] | None = None
    research_areas: list[str] | None = None
    identity: dict[str, Any] | None = None
    user_notes: str | None = None


class TimelineEvent(BaseModel):
    event_type: str
    title: str
    summary: str = ""
    source_url: str = ""
    significance: str = "low"


class UploadUpdatePlan(BaseModel):
    profile_patch: ProfilePatch | None = None
    events: list[TimelineEvent] = Field(default_factory=list)
    notes: str = ""


_PROMPT = """\
You are processing user-uploaded documents about an academic scholar
for a VC dossier. Extract only investment-relevant signals:
affiliations, roles, funding, partnerships, patents, career changes,
ventures, awards. Do NOT fabricate URLs or figures you didn't see.

Return an UploadUpdatePlan JSON with:
- profile_patch: shallow merge to apply to profile.json (optional)
- events: timeline events worth appending (investment significance)
- notes: short free-text summary of what you found

Document contents:
"""


async def process_uploads(
    scholar_id: str,
    files: list[Path],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    from ...config import settings

    blobs: list[str] = []
    for f in files:
        try:
            blobs.append(f"--- FILE: {f.name} ---\n{f.read_text(encoding='utf-8', errors='replace')[:20000]}")
        except Exception as e:
            logger.warning("upload_processor: could not read %s: %s", f, e)

    if not blobs:
        return {"skipped": True, "reason": "no_readable_files"}

    try:
        plan = await generate_structured(
            model=model or settings.ACADEMIC_GEMINI_MODEL,
            prompt_parts=[_PROMPT, "\n\n".join(blobs)],
            response_schema=UploadUpdatePlan,
        )
    except Exception as e:
        logger.exception("upload_processor: LLM call failed for %s", scholar_id)
        return {"error": str(e)}

    # Apply profile patch (shallow merge).
    if plan.profile_patch:
        profile_path = dossier_path(scholar_id) / "profile.json"
        profile = read_json(profile_path) or {}
        patch = plan.profile_patch.model_dump(exclude_none=True)
        profile = {**profile, **patch}
        write_json(profile_path, profile)

    # Append events to SQL (visible in timeline + signal feed).
    from .events_sync import log_event

    for e in plan.events:
        await log_event(
            scholar_id,
            event_type=e.event_type,
            title=e.title,
            significance=e.significance,
            payload={"url": e.source_url, "summary": e.summary} if e.source_url else {},
        )

    snapshot_id = await record_snapshot(
        scholar_id,
        "upload_processor",
        detail={
            "file_count": len(blobs),
            "events_added": len(plan.events),
            "patched_profile": plan.profile_patch is not None,
        },
    )

    return {
        "snapshot_id": snapshot_id,
        "events_added": len(plan.events),
        "notes": plan.notes,
        "patched": plan.profile_patch is not None,
    }
