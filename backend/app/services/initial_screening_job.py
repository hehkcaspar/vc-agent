"""Initial Screening — three-stage orchestration.

Phase 1 (ReAct agent) runs via the standard ``run_preset_agent_job`` path; it
writes six section JSONs under ``Deliverables/Analysis/initial_screening/``.

This module provides the two **one-shot** follow-ups that are invoked after
phase 1 succeeds:

- ``run_compose_stage`` — phase 2: Gemini reads ONLY the six section JSONs
  (no workspace tools, no web search) and writes the one-pager to
  ``Deliverables/Memos/initial_screening.md``.
- ``run_review_stage`` — phase 3: Gemini reads the draft + JSONs + original
  source docs and writes the revised memo + ``initial_screening_review_notes.md``.

All stages must run — a failure in phase 2 still triggers phase 3 on
whatever was produced. Status updates are forwarded to the job's step_detail
callback so the frontend shows progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.datetime_support import utc_now
from app.services.preset_registry import (
    render_initial_screening_compose,
    load_initial_screening_review_prompt,
)
from app.services.workspace import Actor, WorkspaceService
from app.services.direct_llm import generate_json_one_shot, generate_one_shot
from app.config import settings

_log = logging.getLogger(__name__)


INITIAL_SCREENING_ANALYSIS_DIR = "Deliverables/Analysis/initial_screening"
INITIAL_SCREENING_MEMO_PATH = "Deliverables/Memos/initial_screening.md"
INITIAL_SCREENING_REVIEW_PATH = (
    "Deliverables/Memos/initial_screening_review_notes.md"
)

# Matches Taihill's internal Initial Screening (Monday) template. Keep in
# lockstep with ``preset_registry.V2_SECTION_IDS`` and the section prompts.
SECTION_FILENAMES = (
    "team.json",
    "market.json",
    "product_tech.json",
    "business_model.json",
    "funding_traction.json",
)


def _notify(on_status: Optional[Callable[[str], None]], msg: str) -> None:
    if not on_status:
        return
    try:
        on_status(msg)
    except Exception:  # noqa: BLE001
        pass


async def _load_entity_context(
    db: AsyncSession, entity_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Pull entity-level context the composer needs that isn't in the
    research bundle: referral_source (Taihill's [6] Source line) + deal_stage.
    Returns ``(referral_source, deal_stage)`` with None for either when absent.
    """
    from sqlalchemy import select
    from app.models import Entity

    res = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = res.scalar_one_or_none()
    if entity is None:
        return None, None
    deal_stage = entity.deal_stage
    referral: Optional[str] = None
    if entity.metadata_json:
        try:
            meta = json.loads(entity.metadata_json)
            if isinstance(meta, dict):
                r = meta.get("referral_source")
                if isinstance(r, str) and r.strip():
                    referral = r.strip()
        except json.JSONDecodeError:
            pass
    return referral, deal_stage


async def _read_section_jsons(
    db: AsyncSession,
    ws: WorkspaceService,
    entity_id: str,
    *,
    analysis_dir: str = INITIAL_SCREENING_ANALYSIS_DIR,
) -> Tuple[dict, List[str]]:
    """Read the six section JSONs. Returns ``(bundle, missing)`` where
    bundle is a dict keyed by section name and missing is the list of files
    that weren't found (phase 2 can still run with a partial bundle).

    ``analysis_dir`` lets v2 of the preset read from a different folder
    (``Deliverables/Analysis/initial_screening_v2/``) so v1 + v2 artifacts
    coexist for side-by-side comparison.
    """
    bundle: dict = {}
    missing: List[str] = []
    for filename in SECTION_FILENAMES:
        path = f"{analysis_dir}/{filename}"
        node = await ws.get_node_by_path(db, entity_id, path)
        if node is None or not node.storage_key:
            missing.append(filename)
            continue
        try:
            raw = await asyncio.to_thread(
                ws.storage.read_file_sync, node.storage_key,
            )
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            _log.warning(
                "initial_screening: failed to parse %s", path, exc_info=True,
            )
            missing.append(filename)
            continue
        bundle[filename.replace(".json", "")] = data
    return bundle, missing


def _render_bundle_for_llm(bundle: dict) -> str:
    """Serialize the six-section bundle as the single context block the
    composer / reviewer receives."""
    return json.dumps(bundle, ensure_ascii=False, indent=2)


async def run_compose_stage(
    db: AsyncSession,
    ws: WorkspaceService,
    *,
    entity_id: str,
    entity_name: str,
    entity_website: Optional[str],
    agent_run_id: str,
    on_status: Optional[Callable[[str], None]] = None,
    analysis_dir: str = INITIAL_SCREENING_ANALYSIS_DIR,
    memo_path: str = INITIAL_SCREENING_MEMO_PATH,
) -> Tuple[Optional[str], List[str]]:
    """Run phase 2. Returns ``(memo_markdown, warnings)`` — memo is None
    when no section JSONs exist.

    ``analysis_dir`` and ``memo_path`` are overridable so the v2 orchestrator
    can route outputs to ``initial_screening_v2/`` without touching v1.
    """
    warnings: List[str] = []
    _notify(on_status, "Composing one-pager from section research...")
    bundle, missing = await _read_section_jsons(
        db, ws, entity_id, analysis_dir=analysis_dir,
    )
    if not bundle:
        warnings.append(
            "initial_screening: no section JSONs found — skipped compose stage"
        )
        return None, warnings
    if missing:
        warnings.append(
            f"initial_screening: {len(missing)} section(s) missing — "
            f"composed memo may be thin: {', '.join(missing)}"
        )

    # Surface deal-source info from the canonical metadata so the composer
    # can render [6] Source per Taihill's template (the research bundle
    # doesn't own this — it's an entity-level fact).
    referral_source, deal_stage = await _load_entity_context(db, entity_id)

    system = render_initial_screening_compose(entity_name, entity_website)
    user_msg = (
        "Research payload (the ONLY source you may draw on for sections "
        "[1]-[5]):\n\n"
        f"```json\n{_render_bundle_for_llm(bundle)}\n```\n\n"
        "Entity context (use for [6] Source and the memo header):\n"
        f"- referral_source: {referral_source or '(not recorded)'}\n"
        f"- deal_stage: {deal_stage or '(unknown)'}\n\n"
        "Produce the initial screening markdown memo now. Reply with ONLY "
        "the markdown — no wrapper, no acknowledgement."
    )

    try:
        memo = await asyncio.to_thread(
            generate_one_shot,
            system_instruction=system,
            history=[],
            user_message_text=user_msg,
            enable_google_search=False,
            # Flash is plenty for synthesis; the hard reasoning already
            # happened in phase 1.
            model=settings.GEMINI_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"initial_screening: compose failed: {exc}")
        return None, warnings

    # Strip accidental code-fence wrappers the model sometimes emits.
    memo = _strip_code_fence(memo or "")
    if not memo.strip():
        warnings.append("initial_screening: compose returned empty text")
        return None, warnings

    _notify(on_status, "Writing initial_screening memo...")
    await ws.write_file(
        db,
        entity_id,
        memo_path,
        memo.encode("utf-8"),
        "text/markdown",
        Actor(type="system", ref=f"preset:initial_screening:compose:{agent_run_id}"),
    )
    await db.commit()
    return memo, warnings


async def run_review_stage(
    db: AsyncSession,
    ws: WorkspaceService,
    *,
    entity_id: str,
    entity_name: str,
    entity_website: Optional[str],
    agent_run_id: str,
    memo_draft: str,
    on_status: Optional[Callable[[str], None]] = None,
    analysis_dir: str = INITIAL_SCREENING_ANALYSIS_DIR,
    memo_path: str = INITIAL_SCREENING_MEMO_PATH,
    review_notes_path: str = INITIAL_SCREENING_REVIEW_PATH,
) -> List[str]:
    """Run phase 3. Writes revised memo + review notes.

    ``analysis_dir`` / ``memo_path`` / ``review_notes_path`` are overridable
    so v2 routes its outputs to a dedicated folder without clobbering v1.
    """
    warnings: List[str] = []
    _notify(on_status, "Fact-checking memo against sources...")
    bundle, missing = await _read_section_jsons(
        db, ws, entity_id, analysis_dir=analysis_dir,
    )

    system = (
        load_initial_screening_review_prompt()
        .replace("{{entity_name}}", entity_name)
        .replace("{{entity_website}}", entity_website or "(not provided)")
    )
    now_iso = utc_now().isoformat()

    user_msg = (
        f"Draft memo:\n\n```markdown\n{memo_draft}\n```\n\n"
        f"Section JSONs (sole evidence base):\n\n```json\n"
        f"{_render_bundle_for_llm(bundle)}\n```\n\n"
        f"Current time (UTC): {now_iso}\n\n"
        "Return a JSON object with two keys: "
        '{"memo_md": "<revised markdown memo>", '
        '"review_notes_md": "<review log markdown>"}. '
        "Both values are plain markdown strings (not nested JSON)."
    )

    # Use the JSON-constrained one-shot to force a parseable object.
    # (Gemini's `response_mime_type: application/json` gate makes string-
    # salvage regex tricks unnecessary.)
    try:
        payload_text = await asyncio.to_thread(
            generate_json_one_shot,
            system_instruction=system,
            history=[],
            user_message_text=user_msg,
            enable_google_search=False,
            model=settings.GEMINI_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"initial_screening: review failed: {exc}")
        return warnings

    if not (payload_text or "").strip():
        warnings.append("initial_screening: review returned empty text")
        return warnings

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        # Last-ditch salvage for older models that occasionally wrap the
        # object in prose despite the JSON mime hint.
        match = re.search(r"\{[\s\S]+\}", payload_text)
        if not match:
            warnings.append(
                "initial_screening: review output unparseable, skipped write"
            )
            return warnings
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            warnings.append(
                "initial_screening: review output wasn't valid JSON; "
                "writing raw text as review notes only"
            )
            await ws.write_file(
                db, entity_id, review_notes_path,
                payload_text.encode("utf-8"), "text/markdown",
                Actor(type="system", ref=f"preset:initial_screening:review:{agent_run_id}"),
            )
            await db.commit()
            return warnings

    revised_memo = payload.get("memo_md") or memo_draft
    review_notes = payload.get("review_notes_md") or ""

    _notify(on_status, "Writing reviewed memo + review notes...")
    await ws.write_file(
        db, entity_id, memo_path,
        revised_memo.encode("utf-8"), "text/markdown",
        Actor(type="system", ref=f"preset:initial_screening:review:{agent_run_id}"),
    )
    if review_notes.strip():
        await ws.write_file(
            db, entity_id, review_notes_path,
            review_notes.encode("utf-8"), "text/markdown",
            Actor(type="system", ref=f"preset:initial_screening:review:{agent_run_id}"),
        )
    await db.commit()
    if missing:
        warnings.append(
            f"initial_screening: review ran with {len(missing)} missing section(s)"
        )
    return warnings


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence if the model wrapped output."""
    t = text.strip()
    if t.startswith("```"):
        # Drop the opening fence + optional language tag
        first_newline = t.find("\n")
        if first_newline != -1:
            t = t[first_newline + 1:]
        # Drop trailing fence
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
