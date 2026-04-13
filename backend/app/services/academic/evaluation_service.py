"""Thin façade over Layer 2 refreshers + Layer 3 dim runner.

The public surface is:

- ``bootstrap_scholar`` — full identity + Layer 2 refresh + all dim evals
  + phase classification + narrative synth. Used by both the manual
  ``/evaluate`` / ``/refresh`` endpoints and the heartbeat dispatcher.
- ``run_evaluation`` / ``run_refresh`` — router entry points that wrap
  ``bootstrap_scholar`` for background execution.
- ``claim_evaluating`` / ``release_evaluating`` — atomic SQL lock on
  ``scholar.status`` that prevents manual + heartbeat runs from racing
  on the same scholar.
- ``launch_background_run`` / ``cancel_scholar_task`` — asyncio task
  registry so ``/stop`` can actually cancel an in-flight run instead
  of just flipping a status bit.
- ``get_all_latest_evals`` / ``get_latest_eval_scores`` — read helpers
  the router uses to build its responses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from sqlalchemy import update

from app.academic_database import AcademicAsyncSessionLocal
from app.academic_models import Scholar

from .continuous_config import load_continuous_tasks
from .dim_runner import run_dim_eval
from .eval_log import log_eval, log_step
from .fact_store import active_red_flags
from .file_utils import latest_record, read_records
from .identity_resolver import resolve_identity
from .narrative_synthesizer import run_narrative_synthesizer
from .phase_classifier import run_phase_classifier
from .refresh_dispatcher import trigger_refresh

logger = logging.getLogger(__name__)


# ── Cross-process / cross-coroutine lock ─────────────────────────────


async def claim_evaluating(scholar_id: str) -> bool:
    """Atomically transition a scholar from ``active`` → ``evaluating``.

    Only one caller can execute the UPDATE — losers see ``rowcount=0``
    and must back off. Heartbeat and the router both call this so
    manual + continuous work cannot interleave on the same scholar.
    Pair with :func:`release_evaluating` in a ``try/finally``.
    """
    try:
        async with AcademicAsyncSessionLocal() as db:
            result = await db.execute(
                update(Scholar)
                .where(Scholar.id == scholar_id)
                .where(Scholar.status == "active")
                .values(status="evaluating")
            )
            await db.commit()
            return result.rowcount > 0
    except Exception:
        logger.exception("could not claim evaluating lock for %s", scholar_id)
        return False


async def release_evaluating(scholar_id: str) -> None:
    """Transition ``evaluating`` → ``active`` without touching paused/archived."""
    try:
        async with AcademicAsyncSessionLocal() as db:
            await db.execute(
                update(Scholar)
                .where(Scholar.id == scholar_id)
                .where(Scholar.status == "evaluating")
                .values(status="active")
            )
            await db.commit()
    except Exception:
        logger.exception("could not release evaluating lock for %s", scholar_id)


# ── Async task registry (for /stop cancellation) ─────────────────────


_running_tasks: dict[str, asyncio.Task[Any]] = {}


def launch_background_run(
    scholar_id: str,
    coro_factory: Callable[[], Awaitable[Any]],
) -> asyncio.Task[Any]:
    """Spawn a background run and store its Task so ``/stop`` can cancel it.

    The caller is expected to have already claimed the evaluating lock
    (so this is called with ``already_claimed=True`` inside the coro).
    The task self-deregisters in ``bootstrap_scholar``'s ``finally``.
    """
    task = asyncio.create_task(coro_factory())
    _running_tasks[scholar_id] = task
    task.add_done_callback(
        lambda t, sid=scholar_id: _running_tasks.pop(sid, None)
        if _running_tasks.get(sid) is t
        else None
    )
    return task


async def cancel_scholar_task(scholar_id: str) -> bool:
    """Cancel an in-flight evaluation and wait for it to unwind.

    Returns True if a running task was cancelled, False if nothing was
    running. Awaiting the task guarantees ``bootstrap_scholar``'s
    ``finally`` block has already released the SQL lock by the time
    this returns, so an immediate re-``evaluate`` won't race.
    """
    task = _running_tasks.get(scholar_id)
    if not task or task.done():
        return False
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    return True


# ── Bootstrap (the only real pipeline) ───────────────────────────────


async def _load_scholar_name(scholar_id: str) -> str | None:
    try:
        async with AcademicAsyncSessionLocal() as db:
            s = await db.get(Scholar, scholar_id)
            return s.name if s else None
    except Exception:
        return None


async def bootstrap_scholar(
    scholar_id: str,
    *,
    mode: str = "bootstrap",
    already_claimed: bool = False,
) -> dict[str, Any]:
    """Run identity → Layer 2 sources → phase classifier → Layer 3 dims → narrative.

    Step-level failures are logged and swallowed so one broken source or
    one broken dim does not abort the whole run. ``CancelledError``
    propagates naturally — callers who invoke ``cancel_scholar_task``
    will observe the scholar transitioning back to ``active`` once the
    ``finally`` block unwinds.

    All setup work (config load, claim, name lookup) runs INSIDE the
    outer try/finally so the evaluating lock is released even if
    bootstrap crashes before reaching the pipeline steps. A broken
    config file must not leave scholars stuck in ``evaluating``
    forever — the router has already claimed the lock by the time we
    get here.
    """
    t0 = time.monotonic()
    scholar_name: str | None = None
    results: dict[str, Any] = {"sources": {}, "dimensions": {}}

    try:
        cfg = load_continuous_tasks()

        if not already_claimed and not await claim_evaluating(scholar_id):
            logger.info(
                "bootstrap_scholar: %s already being evaluated — skipping",
                scholar_id,
            )
            log_eval(
                scholar_id, "bootstrap", "skipped", detail="already_running"
            )
            return {"skipped": True, "reason": "already_running"}

        scholar_name = await _load_scholar_name(scholar_id)
        log_eval(
            scholar_id, "bootstrap", "start",
            detail={"mode": mode},
            scholar_name=scholar_name,
        )
        # ── Identity resolution (pre-Layer-2) ───────────────────
        try:
            async with log_step(scholar_id, "identity", scholar_name=scholar_name):
                results["identity"] = await resolve_identity(scholar_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("bootstrap: identity failed for %s", scholar_id)
            results["identity"] = {"status": "error", "error": str(e)}

        # ── Layer 2 sources (parallel) ──────────────────────────
        enabled_sources = [sid for sid, sc in cfg.sources.items() if sc.enabled]

        async def _run_source(source_id: str) -> tuple[str, Any]:
            try:
                async with log_step(
                    scholar_id, f"source/{source_id}", scholar_name=scholar_name
                ):
                    return source_id, await trigger_refresh(
                        scholar_id,
                        source_id,
                        mode=mode,
                        reason="bootstrap_scholar",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(
                    "bootstrap: source %s failed for %s", source_id, scholar_id
                )
                return source_id, {"error": str(e)}

        for sid, r in await asyncio.gather(*(_run_source(s) for s in enabled_sources)):
            results["sources"][sid] = r

        # ── Phase classification ────────────────────────────────
        try:
            async with log_step(
                scholar_id, "phase_classifier", scholar_name=scholar_name
            ):
                results["peer_group"] = await run_phase_classifier(scholar_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("bootstrap: phase_classifier failed for %s", scholar_id)
            results["peer_group"] = {"error": str(e)}

        # ── Layer 3 dim evals (parallel) ────────────────────────
        enabled_dims = [dim_id for dim_id, dc in cfg.dimensions.items() if dc.enabled]

        async def _run_dim(dim_id: str) -> tuple[str, Any]:
            try:
                async with log_step(
                    scholar_id, f"dim/{dim_id}", scholar_name=scholar_name
                ) as ctx:
                    r = await run_dim_eval(
                        scholar_id, dim_id, cfg=cfg, force_score=True
                    )
                    ctx.detail = {"score": r.get("score")}
                    return dim_id, r
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(
                    "bootstrap: dim %s failed for %s", dim_id, scholar_id
                )
                return dim_id, {"error": str(e)}

        for dim_id, r in await asyncio.gather(*(_run_dim(d) for d in enabled_dims)):
            results["dimensions"][dim_id] = r

        # ── Narrative synthesizer ───────────────────────────────
        try:
            async with log_step(scholar_id, "narrative", scholar_name=scholar_name):
                results["narrative"] = await run_narrative_synthesizer(scholar_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("bootstrap: narrative failed for %s", scholar_id)
            results["narrative"] = {"error": str(e)}

    except asyncio.CancelledError:
        log_eval(
            scholar_id, "bootstrap", "cancelled",
            duration_s=time.monotonic() - t0,
            scholar_name=scholar_name,
        )
        raise
    else:
        log_eval(
            scholar_id, "bootstrap", "done",
            duration_s=time.monotonic() - t0,
            scholar_name=scholar_name,
        )
    finally:
        await release_evaluating(scholar_id)

    return results


# ── Router entry points ──────────────────────────────────────────────


async def run_evaluation(scholar_id: str) -> None:
    await bootstrap_scholar(scholar_id, mode="bootstrap", already_claimed=True)


async def run_refresh(scholar_id: str) -> None:
    await bootstrap_scholar(scholar_id, mode="incremental", already_claimed=True)


# ── Read helpers for the router ──────────────────────────────────────


def _latest_eval(scholar_id: str, dim_id: str) -> dict[str, Any] | None:
    """Latest eval record — scored or not-scoreable (skips triage-only/error)."""
    for rec in reversed(read_records(scholar_id, f"evaluations/{dim_id}")):
        if isinstance(rec.get("score"), (int, float)) and rec.get("scoreable", True):
            return rec
        if rec.get("scoreable") is False:
            return rec
    return None


def get_all_latest_evals(scholar_id: str) -> dict[str, Any]:
    """Per-dim latest eval + narrative + peer group + active red flags."""
    cfg = load_continuous_tasks()
    dims: dict[str, Any] = {dim_id: _latest_eval(scholar_id, dim_id) for dim_id in cfg.dimensions}
    return {
        "dimensions": dims,
        "narrative": latest_record(scholar_id, "narrative"),
        "peer_group": latest_record(scholar_id, "peer_group"),
        "red_flags": active_red_flags(scholar_id),
    }


def get_latest_eval_scores(scholar_id: str) -> tuple[dict[str, int | None], str | None]:
    """Compact ``{dim_id: score}`` for the ranking table. Not-scoreable → None."""
    scores: dict[str, int | None] = {}
    latest_date: str | None = None
    cfg = load_continuous_tasks()
    for dim_id in cfg.dimensions:
        rec = _latest_eval(scholar_id, dim_id)
        if not rec:
            continue
        rid = rec.get("id")
        if rid and (latest_date is None or rid > latest_date):
            latest_date = rid
        if rec.get("scoreable") is False:
            scores[dim_id] = None
        elif isinstance(rec.get("score"), (int, float)):
            scores[dim_id] = int(rec["score"])
    return scores, latest_date
