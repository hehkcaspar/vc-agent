"""Evaluation logic extracted from the academic router.

Handles normalisation of agent-written evaluation JSON, delta computation
between consecutive evaluations, score extraction for ranking, and the
background evaluation/refresh task orchestration.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import Channel, Scholar

from .file_utils import dossier_path, read_json, write_json

logger = logging.getLogger(__name__)

# Module-level dict tracking in-flight agent tasks (for cancellation).
running_agents: dict[str, bool] = {}


# ── Normalisation ─────────────────────────────────────────────


def normalize_evaluation(data: dict, filepath: Path) -> dict:
    """Inject defaults and normalise agent-written evaluation JSON.

    The agent may omit fields or use slightly different structures.
    This ensures the response always matches EvaluationResponse schema.
    """
    if "id" not in data:
        data["id"] = filepath.stem
    if "created_at" not in data:
        parts = filepath.stem.split("_", 1)
        data["created_at"] = parts[0] if parts else ""
    if "type" not in data:
        parts = filepath.stem.split("_", 1)
        data["type"] = parts[1] if len(parts) > 1 else "full"

    dims = data.get("dimensions", {})
    if isinstance(dims, dict):
        for key, val in dims.items():
            if isinstance(val, (int, float)):
                dims[key] = {"score": int(val), "explanation": "", "evidence": []}
            elif isinstance(val, dict):
                val.setdefault("score", 0)
                val.setdefault("explanation", "")
                val.setdefault("evidence", [])
                if not isinstance(val["evidence"], list):
                    val["evidence"] = [str(val["evidence"])]
        data["dimensions"] = dims

    comm = data.get("commercialization_signals")
    if isinstance(comm, list):
        data["commercialization_signals"] = {"items": comm}
    elif not isinstance(comm, dict):
        data["commercialization_signals"] = {}

    data.setdefault("computed_metrics", {})
    data.setdefault("field_context", {})
    data.setdefault("trigger", "manual")
    data.setdefault("model", "")

    return data


# ── Delta ─────────────────────────────────────────────────────


def compute_and_attach_delta(scholar_id: str) -> None:
    """Compute evaluation delta and write it into the newest evaluation file.

    Compares the two most recent evaluations' dimension scores.
    Pure file I/O, no LLM.
    """
    evals_dir = dossier_path(scholar_id) / "evaluations"
    if not evals_dir.exists():
        return

    eval_files = sorted(evals_dir.glob("*.json"), reverse=True)
    if len(eval_files) < 2:
        return

    try:
        newest = json.loads(eval_files[0].read_text(encoding="utf-8"))
        previous = json.loads(eval_files[1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    new_dims = newest.get("dimensions", {})
    old_dims = previous.get("dimensions", {})
    if not new_dims or not old_dims:
        return

    dimension_changes: dict[str, dict] = {}
    for key in new_dims:
        new_score = new_dims[key].get("score") if isinstance(new_dims[key], dict) else new_dims[key]
        old_score = old_dims.get(key, {})
        old_score = old_score.get("score") if isinstance(old_score, dict) else old_score
        if isinstance(new_score, (int, float)) and isinstance(old_score, (int, float)):
            change = int(new_score) - int(old_score)
            if change != 0:
                dimension_changes[key] = {
                    "old": int(old_score),
                    "new": int(new_score),
                    "change": f"+{change}" if change > 0 else str(change),
                }

    prev_id = previous.get("id", eval_files[1].stem)

    delta: dict[str, Any] = {
        "vs_evaluation": prev_id,
        "dimension_changes": dimension_changes,
        "new_papers_since": 0,
        "notable_events": [],
    }

    prev_date = previous.get("created_at", "")[:10]
    if prev_date:
        papers_data = read_json(dossier_path(scholar_id) / "papers.json")
        for p in papers_data.get("papers", []):
            pub_date = p.get("publication_date", "") or ""
            if pub_date > prev_date:
                delta["new_papers_since"] += 1

    newest["delta"] = delta
    eval_files[0].write_text(
        json.dumps(newest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


# ── Score extraction ──────────────────────────────────────────


def get_latest_eval_scores(scholar_id: str) -> tuple[dict[str, int], str | None]:
    """Read the latest evaluation and extract dimension scores."""
    evals_dir = dossier_path(scholar_id) / "evaluations"
    if not evals_dir.exists():
        return {}, None
    files = sorted(evals_dir.glob("*.json"), reverse=True)
    if not files:
        return {}, None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        data = normalize_evaluation(data, files[0])
        dims = data.get("dimensions", {})
        scores: dict[str, int] = {}
        for key, val in dims.items():
            if isinstance(val, dict):
                scores[key] = int(val.get("score", 0))
            elif isinstance(val, (int, float)):
                scores[key] = int(val)
        eval_date = data.get("created_at")
        return scores, eval_date
    except Exception:
        return {}, None


# ── Background tasks ──────────────────────────────────────────


async def auto_create_channels(scholar_id: str) -> None:
    """Auto-create monitoring channels from discovered identity in profile.json."""
    profile = read_json(dossier_path(scholar_id) / "profile.json")
    identity = profile.get("identity", {})
    if not identity:
        return

    channels_path = dossier_path(scholar_id) / "channels.json"
    channels_data = read_json(channels_path) if channels_path.exists() else {}
    channels_list = channels_data.get("channels", [])
    existing_types = {c.get("type") for c in channels_list}

    new_channels: list[dict] = []

    gs = identity.get("google_scholar", {})
    gs_url = gs.get("url") if isinstance(gs, dict) else None
    if gs_url and "google_scholar_profile" not in existing_types:
        ch_id = str(_uuid.uuid4())
        new_channels.append({
            "id": ch_id,
            "type": "google_scholar_profile",
            "url": gs_url,
            "is_active": True,
            "polling_interval_hours": 168,
            "last_snapshot": {},
        })

    ss = identity.get("semantic_scholar", {})
    ss_id = ss.get("id") if isinstance(ss, dict) else None
    if ss_id and "semantic_scholar_profile" not in existing_types:
        ch_id = str(_uuid.uuid4())
        ss_url = f"https://api.semanticscholar.org/graph/v1/author/{ss_id}"
        new_channels.append({
            "id": ch_id,
            "type": "semantic_scholar_profile",
            "url": ss_url,
            "is_active": True,
            "polling_interval_hours": 72,
            "last_snapshot": {},
        })

    if not new_channels:
        return

    channels_list.extend(new_channels)
    channels_data["channels"] = channels_list
    write_json(channels_path, channels_data)

    try:
        async with AcademicAsyncSessionLocal() as db:
            for ch in new_channels:
                db.add(Channel(
                    id=ch["id"],
                    scholar_id=scholar_id,
                    channel_type=ch["type"],
                    url=ch["url"],
                    is_active=True,
                    polling_interval_hours=ch["polling_interval_hours"],
                ))
            await db.commit()
        logger.info("Auto-created %d channels for scholar %s", len(new_channels), scholar_id)
    except Exception:
        logger.exception("Failed to create channels for %s", scholar_id)


async def run_evaluation(scholar_id: str) -> None:
    """Background task: invoke the scholar agent for initial evaluation."""
    from app.services.academic.scholar_agent import invoke_scholar_agent
    from app.services.academic.scholar_prompts import GOAL_INITIAL_EVALUATION

    running_agents[scholar_id] = True
    try:
        result = await invoke_scholar_agent(scholar_id, GOAL_INITIAL_EVALUATION)
        logger.info("Evaluation complete for %s: %s", scholar_id, result.get("run_id"))
        compute_and_attach_delta(scholar_id)
        await auto_create_channels(scholar_id)
    except Exception as e:
        logger.exception("Evaluation failed for %s: %s", scholar_id, e)
    finally:
        running_agents.pop(scholar_id, None)
        try:
            async with AcademicAsyncSessionLocal() as db:
                scholar = await db.get(Scholar, scholar_id)
                if scholar and scholar.status == "evaluating":
                    scholar.status = "active"
                    await db.commit()
        except Exception:
            logger.exception("Could not reset scholar status for %s", scholar_id)


async def run_refresh(scholar_id: str) -> None:
    """Background task: refresh a scholar's dossier."""
    from app.services.academic.scholar_agent import invoke_scholar_agent
    from app.services.academic.scholar_prompts import GOAL_REFRESH

    running_agents[scholar_id] = True
    try:
        await invoke_scholar_agent(scholar_id, GOAL_REFRESH)
        compute_and_attach_delta(scholar_id)
        await auto_create_channels(scholar_id)
    except Exception as e:
        logger.exception("Refresh failed for %s: %s", scholar_id, e)
    finally:
        running_agents.pop(scholar_id, None)
        try:
            async with AcademicAsyncSessionLocal() as db:
                s = await db.get(Scholar, scholar_id)
                if s and s.status == "evaluating":
                    s.status = "active"
                    await db.commit()
        except Exception:
            pass


async def run_comparative(scholar_id: str, other_id: str) -> None:
    """Background task: comparative evaluation via scholar agent."""
    from app.services.academic.scholar_agent import invoke_scholar_agent
    from app.services.academic.scholar_prompts import GOAL_COMPARATIVE_EVALUATION

    running_agents[scholar_id] = True
    try:
        profile_a = read_json(dossier_path(scholar_id) / "profile.json")
        profile_b = read_json(dossier_path(other_id) / "profile.json")
        scores_a, _ = get_latest_eval_scores(scholar_id)
        scores_b, _ = get_latest_eval_scores(other_id)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def _fmt_dims(scores: dict) -> str:
            return "\n".join(f"  - {k}: {v}/100" for k, v in scores.items())

        goal = GOAL_COMPARATIVE_EVALUATION.format(
            name_a=profile_a.get("name", "Scholar A"),
            affiliation_a=profile_a.get("affiliation", {}).get("current", "Unknown"),
            h_index_a=profile_a.get("metrics", {}).get("h_index", "N/A"),
            dimensions_a=_fmt_dims(scores_a),
            name_b=profile_b.get("name", "Scholar B"),
            affiliation_b=profile_b.get("affiliation", {}).get("current", "Unknown"),
            h_index_b=profile_b.get("metrics", {}).get("h_index", "N/A"),
            dimensions_b=_fmt_dims(scores_b),
            date=today,
        )

        await invoke_scholar_agent(scholar_id, goal)
    except Exception as e:
        logger.exception("Comparative evaluation failed: %s", e)
    finally:
        running_agents.pop(scholar_id, None)
        try:
            async with AcademicAsyncSessionLocal() as db:
                scholar = await db.get(Scholar, scholar_id)
                if scholar and scholar.status == "evaluating":
                    scholar.status = "active"
                    await db.commit()
        except Exception:
            pass
