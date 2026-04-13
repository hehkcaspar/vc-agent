"""Digest generation service — weekly scholar digest via Gemini."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import Scholar, ScholarEvent
from app.config import settings

from .evaluation_service import get_latest_eval_scores
from .file_utils import dossier_path, read_json
from .llm_client import genai_client
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

DIGESTS_DIR = settings.ACADEMIC_CONFIG_DIR / "digests"


async def run_digest_generation() -> None:
    """Background task: assemble context and generate digest with Gemini."""
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc)
    week_ago = today - timedelta(days=7)

    try:
        async with AcademicAsyncSessionLocal() as db:
            result = await db.execute(select(Scholar).order_by(Scholar.name))
            scholars = result.scalars().all()

            result = await db.execute(
                select(ScholarEvent)
                .where(ScholarEvent.created_at >= week_ago)
                .order_by(ScholarEvent.created_at.desc())
                .limit(100)
            )
            events = result.scalars().all()
    except Exception as e:
        logger.exception("Digest: failed to gather context: %s", e)
        return

    scholar_summaries = []
    for s in scholars:
        scores, eval_date = get_latest_eval_scores(s.id)
        profile = read_json(dossier_path(s.id) / "profile.json")
        h = profile.get("metrics", {}).get("h_index", "N/A")
        aff = profile.get("affiliation", {}).get("current", "Unknown")
        score_str = ", ".join(f"{k}: {v}" for k, v in scores.items()) if scores else "No evaluation yet"
        stale = ""
        if eval_date:
            try:
                ed = datetime.fromisoformat(
                    eval_date.replace("Z", "+00:00") if "Z" in eval_date else eval_date
                )
                if (today - ed).days > 30:
                    stale = " [STALE - last eval >30 days ago]"
            except Exception:
                pass
        scholar_summaries.append(
            f"- {s.name} ({aff}, h={h}, priority={s.tracking_priority}){stale}\n"
            f"  Scores: {score_str}"
        )

    event_summaries = []
    for e in events[:50]:
        event_summaries.append(
            f"- [{e.significance}] {e.event_type}: {e.title or 'No title'} "
            f"(scholar_id={e.scholar_id}, {e.created_at.strftime('%Y-%m-%d') if e.created_at else '?'})"
        )

    prompt = f"""\
Generate a concise weekly scholar digest for a VC firm tracking academic scholars.
Title the digest "Weekly Scholar Digest" (do not use "Portfolio").
Date: {today.strftime('%Y-%m-%d')}

## Tracked Scholars ({len(scholars)} total)
{chr(10).join(scholar_summaries) if scholar_summaries else "No scholars tracked."}

## Events This Week ({len(events)} total)
{chr(10).join(event_summaries) if event_summaries else "No events this week."}

Write a markdown digest with these sections:
1. **Executive Summary** — 2-3 sentence overview
2. **Key Signals** — top events by significance
3. **Top Movers** — scholars with notable score changes or new achievements
4. **Attention Needed** — stale scholars needing refresh, errors, or declining metrics
5. **Recommendations** — suggested actions for the analyst

Be concise and actionable. Focus on investment-relevant insights."""

    try:
        client = genai_client()
        response = await client.aio.models.generate_content(
            model=settings.ACADEMIC_GEMINI_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=genai_types.GenerateContentConfig(),
        )
        digest_content = getattr(response, "text", "") or ""

        digest_path = DIGESTS_DIR / f"{today.strftime('%Y-%m-%d')}_weekly.md"
        digest_path.write_text(digest_content, encoding="utf-8")
        logger.info("Digest generated: %s", digest_path)
    except Exception as e:
        logger.exception("Digest generation failed: %s", e)
