"""Scheduled-prompt preparation, continuity block and completion hooks."""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import Any

import asyncpg

logger = logging.getLogger("butlers.core.scheduler")


def _accepts_keyword(callback: Any, keyword: str) -> bool:
    """Return whether *callback* accepts a named keyword or arbitrary kwargs."""
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    return keyword in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _prepare_scheduled_prompt(
    prompt_hooks: dict[str, Any] | None,
    *,
    task_name: str,
    prompt: str,
    run_at: datetime,
    timezone: str,
) -> str:
    """Apply one task-owned deterministic prompt hook, failing closed on bad output."""
    hook = prompt_hooks.get(task_name) if prompt_hooks else None
    if hook is None:
        return prompt
    prepared_prompt = hook(
        task_name=task_name,
        prompt=prompt,
        run_at=run_at,
        timezone=timezone,
    )
    if not isinstance(prepared_prompt, str) or not prepared_prompt.strip():
        raise RuntimeError(
            f"Prompt hook for scheduled task {task_name!r} returned an empty or non-string prompt"
        )
    return prepared_prompt


async def _continuity_block_for_task(
    pool: asyncpg.Pool,
    *,
    butler_name: str | None,
    task_name: str,
    now: datetime,
) -> str | None:
    """Return the task-continuity injection block for one opted-in recurring task.

    Reads the single "live" row from ``public.task_continuity`` for
    ``(butler_name, task_name)`` -- see ``core_229_task_continuity_ledger.py``.
    Never raises: continuity is an additive opt-in convenience, not a
    fail-closed honesty layer like the blind-spot preamble, so a query error
    here degrades to ``None`` (today's behavior) rather than blocking dispatch.

    Returns a block naming the previous run's carry-forward content and its
    age when a live row exists, or an honest "no carry-forward" block when the
    task has run before under continuity but never called ``carry_forward`` --
    the gap must be named, never silently treated as if nothing changed.
    """
    if not butler_name:
        return None
    try:
        from butlers.core.task_continuity import fetch_live_carry_forward

        row = await fetch_live_carry_forward(pool, butler_name=butler_name, task_name=task_name)
    except Exception:
        logger.warning(
            "Task-continuity lookup failed for butler=%s task=%s; dispatching without it",
            butler_name,
            task_name,
            exc_info=True,
        )
        return None

    if row is None:
        return f"## Task Continuity — {task_name}\n\nThe last run recorded no carry-forward."

    recorded_at = row["recorded_at"]
    age_seconds = max(0, int((now - recorded_at).total_seconds()))
    return (
        f"## Task Continuity — {task_name}\n\n"
        f"The previous run (session {row['session_id']}, recorded {recorded_at.isoformat()}, "
        f"{age_seconds}s ago) concluded:\n\n{row['carry_forward']}"
    )


async def _run_completion_hook(
    completion_hooks: dict[str, Any] | None,
    *,
    task_name: str,
    result: Any,
    run_at: datetime,
    timezone: str,
) -> None:
    """Run one completion hook while preserving the original callback contract."""
    hook = completion_hooks.get(task_name) if completion_hooks else None
    if hook is None:
        return
    try:
        hook_kwargs = {
            "task_name": task_name,
            "result": result,
            "run_at": run_at,
        }
        if _accepts_keyword(hook, "timezone"):
            hook_kwargs["timezone"] = timezone
        await hook(**hook_kwargs)
    except Exception:
        logger.exception("Completion hook for scheduled task %r raised; continuing", task_name)


def _prepend_seasonal_context(prompt: str, active_seasons: list[dict[str, Any]] | None) -> str:
    """Prepend a seasonal context prefix to *prompt* when active seasons exist.

    Returns the original prompt unchanged when *active_seasons* is empty/None.
    Used by both the deadline dispatch pass and the cron dispatch pass so that
    the prefix format stays consistent across all prompt-mode dispatches.
    """
    if not active_seasons:
        return prompt
    season_names = ", ".join(s["name"] for s in active_seasons)
    seasonal_prefix = f"[Seasonal context: active periods: {season_names}]"
    return f"{seasonal_prefix}\n\n{prompt}"
