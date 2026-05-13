"""Chronicler day-close cache writer.

Post-execution hook that persists the prose output of the
``chronicler_day_close`` scheduled prompt to ``chronicler.tier2_cache``.

The hook is registered in the scheduler's ``completion_hooks`` dict for the
``chronicler_day_close`` task name.  It runs after the spawner returns
successfully (non-empty output).  If the spawner returned an empty result or
an error, the hook is a no-op so that stale cache is not replaced with silence.

Window computation
------------------
``chronicler_day_close`` runs at ``05:01 UTC`` for the *previous* day.  The
hook computes the day window from the run timestamp:

    yesterday = run_at.date() - timedelta(days=1)
    start_at  = midnight UTC of yesterday
    end_at    = midnight UTC of today (exclusive upper bound)

``cache_key`` is ``day_close:{YYYY-MM-DD}`` where ``{YYYY-MM-DD}`` is
yesterday's ISO date.

Provenance extraction
---------------------
The SpawnerResult carries ``tool_calls``.  The hook scans tool calls for the
day-close bundle result first, then legacy ``chronicler_list_episodes`` and
``chronicler_list_events`` results, extracting ``source_ref`` values for cache
staleness.  User-facing prose does not need to print these machine refs; when no
tool-call provenance is available the hook falls back to an empty list (the
prose still persists — provenance is best-effort).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.chronicler.storage import upsert_tier2_cache

logger = logging.getLogger(__name__)

# Name of the scheduled task this writer handles.
DAY_CLOSE_TASK_NAME = "chronicler_day_close"


def _extract_provenance_refs(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Extract source_ref strings from chronicler list tool-call results.

    Scans the tool_calls list (from SpawnerResult) for calls to
    ``chronicler_day_close_bundle``, ``chronicler_list_episodes``, or
    ``chronicler_list_events`` and pulls ``source_ref`` values from their
    results.  Deduplicates while preserving order.

    Returns an empty list if no provenance can be extracted.
    """
    refs: list[str] = []
    seen: set[str] = set()
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        tool_name: str = call.get("tool", "") or ""
        if tool_name not in {
            "chronicler_day_close_bundle",
            "chronicler_list_episodes",
            "chronicler_list_events",
        }:
            continue
        result_raw = call.get("result")
        if result_raw is None:
            continue
        if isinstance(result_raw, str):
            try:
                result_raw = json.loads(result_raw)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(result_raw, dict):
            continue
        if tool_name == "chronicler_day_close_bundle":
            for ref in result_raw.get("citations") or []:
                if isinstance(ref, str) and ref and ref not in seen:
                    refs.append(ref)
                    seen.add(ref)
            continue
        items = result_raw.get("data") or result_raw.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("source_ref")
            if isinstance(ref, str) and ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    return refs


def _compute_day_window(run_at: datetime) -> tuple[date, datetime, datetime]:
    """Return (day_date, start_at, end_at) for the day closed by run_at.

    ``chronicler_day_close`` targets *yesterday* relative to its run time.
    The window is [yesterday 00:00 UTC, today 00:00 UTC).
    """
    utc_run = run_at.astimezone(UTC) if run_at.tzinfo else run_at.replace(tzinfo=UTC)
    today_utc = utc_run.date()
    yesterday_utc = today_utc - timedelta(days=1)
    start_at = datetime(yesterday_utc.year, yesterday_utc.month, yesterday_utc.day, tzinfo=UTC)
    end_at = datetime(today_utc.year, today_utc.month, today_utc.day, tzinfo=UTC)
    return yesterday_utc, start_at, end_at


async def write_day_close_cache(
    pool: asyncpg.Pool,
    *,
    task_name: str,
    result: Any,
    run_at: datetime,
) -> None:
    """Post-execution hook: persist day-close prose to tier2_cache.

    Called by the scheduler tick after ``chronicler_day_close`` dispatches.
    No-op when:
    - ``result`` has no non-empty ``output`` (nothing to cache).
    - ``result.success`` is False (error path — do not cache failures).

    Args:
        pool: asyncpg pool for the chronicler DB (scoped to the chronicler schema).
        task_name: Scheduled task name (must be ``DAY_CLOSE_TASK_NAME``).
        result: SpawnerResult (or None) returned by the dispatch.
        run_at: Wall-clock time the tick fired (used to compute the day window).
    """
    if task_name != DAY_CLOSE_TASK_NAME:
        return

    # Defensive: accept either a SpawnerResult dataclass or a plain dict.
    if result is None:
        logger.debug("day_close_writer: result is None, skipping cache write")
        return

    if hasattr(result, "success"):
        success: bool = bool(result.success)
        output: str | None = getattr(result, "output", None)
        tool_calls: list[dict[str, Any]] = list(getattr(result, "tool_calls", None) or [])
    elif isinstance(result, dict):
        success = bool(result.get("success", False))
        output = result.get("output")
        tool_calls = list(result.get("tool_calls") or [])
    else:
        logger.warning("day_close_writer: unrecognised result type %s, skipping", type(result))
        return

    if not success:
        logger.debug("day_close_writer: dispatch was not successful, skipping cache write")
        return

    if not output or not output.strip():
        logger.debug("day_close_writer: output is empty, skipping cache write")
        return

    day_date, start_at, end_at = _compute_day_window(run_at)
    cache_key = f"day_close:{day_date.isoformat()}"
    provenance_refs = _extract_provenance_refs(tool_calls)

    try:
        await upsert_tier2_cache(
            pool,
            cache_key=cache_key,
            start_at=start_at,
            end_at=end_at,
            prose=output.strip(),
            provenance_refs=provenance_refs,
        )
        logger.info(
            "day_close_writer: wrote tier2_cache[%s] (%d provenance refs)",
            cache_key,
            len(provenance_refs),
        )
    except Exception:
        logger.exception(
            "day_close_writer: failed to write tier2_cache[%s] — cache miss will occur",
            cache_key,
        )


def build_day_close_completion_hooks(
    pool: asyncpg.Pool,
) -> dict[str, Callable[..., Any]]:
    """Return the completion_hooks dict for the chronicler scheduler loop.

    The returned dict maps ``chronicler_day_close`` to a partial of
    :func:`write_day_close_cache` with the pool pre-bound.

    Usage::

        hooks = build_day_close_completion_hooks(db.pool)
        await scheduler_loop(..., completion_hooks=hooks)
    """

    async def _hook(*, task_name: str, result: Any, run_at: datetime) -> None:
        await write_day_close_cache(pool, task_name=task_name, result=result, run_at=run_at)

    return {DAY_CLOSE_TASK_NAME: _hook}
