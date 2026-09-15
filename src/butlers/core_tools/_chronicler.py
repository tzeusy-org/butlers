"""Chronicler-only infrastructure controls."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from butlers.chronicler.day_close_cache import (
    InvalidDayCloseTimezoneError,
    MissingDayCloseTimezoneError,
    day_close_cache_key,
    resolve_day_close_timezone,
)
from butlers.chronicler.day_close_writer import (
    DAY_CLOSE_TASK_NAME,
    build_day_close_prompt_hooks,
    write_day_close_cache,
)
from butlers.core.model_routing import coerce_complexity_tier
from butlers.core.tool_call_capture import (
    MANUAL_DAY_CLOSE_TRIGGER_PREFIX,
    get_current_runtime_session_id,
    get_current_runtime_trigger_source,
)
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

_REFRESH_RATE_LIMIT = timedelta(hours=24)
_REFRESH_OPERATION_TIMEOUT_SECONDS = 100.0


def _error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "error", "code": code, "message": message}
    if details:
        response["details"] = details
    return response


def _manual_run_at(target: date, timezone: ZoneInfo) -> datetime:
    """Anchor the scheduler-owned day window to exactly *target*."""
    return datetime.combine(
        target + timedelta(days=1),
        datetime.min.time().replace(hour=12),
        tzinfo=timezone,
    ).astimezone(UTC)


async def _authoritative_witness(pool: Any, target: date, timezone: str) -> Any:
    return await pool.fetchrow(
        """
        SELECT covered_at
        FROM covered_local_days
        WHERE local_date = $1
          AND timezone = $2
          AND origin <> 'legacy_unverified'
        """,
        target,
        timezone,
    )


async def _run_manual_refresh(
    ctx: ToolContext,
    *,
    target: date,
    timezone_name: str,
    timezone_info: ZoneInfo,
) -> dict[str, Any]:
    pool = ctx.pool
    daemon = ctx.daemon
    cache_key = day_close_cache_key(target, timezone_name)
    now = datetime.now(UTC)
    existing_row = await pool.fetchrow(
        """
        SELECT cache_built_at
        FROM tier2_cache
        WHERE cache_key = $1
          AND superseded_at IS NULL
        """,
        cache_key,
    )
    witness_row = await _authoritative_witness(pool, target, timezone_name)

    successful_at = None
    if witness_row is not None:
        successful_at = witness_row["covered_at"]
        if existing_row is not None:
            successful_at = max(successful_at, existing_row["cache_built_at"])
    if successful_at is not None and now - successful_at < _REFRESH_RATE_LIMIT:
        retry_after = max(1, int((_REFRESH_RATE_LIMIT - (now - successful_at)).total_seconds()))
        return _error(
            "day_close_rate_limited",
            f"A day-close refresh for {cache_key!r} was performed recently.",
            details={"retry_after_seconds": retry_after},
        )

    task_row = await pool.fetchrow(
        "SELECT prompt, complexity FROM scheduled_tasks WHERE name = $1 AND enabled = true",
        DAY_CLOSE_TASK_NAME,
    )
    if task_row is None or not task_row["prompt"]:
        return _error(
            "task_not_found",
            f"Scheduled task {DAY_CLOSE_TASK_NAME!r} was not found or has no prompt.",
        )

    run_at = _manual_run_at(target, timezone_info)
    prompt_hook = build_day_close_prompt_hooks(timezone=timezone_name)[DAY_CLOSE_TASK_NAME]
    prepared_prompt = prompt_hook(
        task_name=DAY_CLOSE_TASK_NAME,
        prompt=task_row["prompt"],
        run_at=run_at,
        timezone=timezone_name,
    )
    prepared_prompt += (
        "\n\nThis is a manual historical regeneration. Return the narration for cache "
        "admission only. Do not call notify, remind, schedule_create, or schedule_update."
    )

    try:
        result = await daemon._dispatch_scheduled_task(
            trigger_source=f"{MANUAL_DAY_CLOSE_TRIGGER_PREFIX}{target.isoformat()}",
            prompt=prepared_prompt,
            complexity=coerce_complexity_tier(task_row.get("complexity"), strict=False),
        )
        write_outcome = await write_day_close_cache(
            pool,
            task_name=DAY_CLOSE_TASK_NAME,
            result=result,
            run_at=run_at,
            tz=timezone_name,
            target_date=target,
        )
    except Exception:
        logger.exception(
            "Manual Chronicler day-close refresh failed for date=%s timezone=%s",
            target,
            timezone_name,
        )
        return _error("dispatch_failed", "Day-close regeneration failed during execution.")

    if write_outcome is None:
        return _error(
            "cache_write_failed",
            "Day-close dispatch completed without an admissible cache outcome.",
        )

    new_row = (
        await pool.fetchrow(
            "SELECT cache_built_at FROM tier2_cache WHERE cache_key = $1 AND superseded_at IS NULL",
            cache_key,
        )
        if not write_outcome.quiet
        else None
    )
    if not write_outcome.quiet and new_row is None:
        return _error(
            "cache_write_failed",
            "Day-close regeneration completed without a cache row.",
        )

    if await _authoritative_witness(pool, target, timezone_name) is None:
        return _error(
            "coverage_witness_write_failed",
            "Day-close regeneration completed without a durable coverage witness.",
        )

    invalid_reason = write_outcome.invalid_reason
    if invalid_reason is not None:
        return {
            "status": "success",
            "cache_key": cache_key,
            "cache_built_at": new_row["cache_built_at"],
            "quiet": False,
            "invalid": True,
            "invalid_reason": invalid_reason,
        }

    if write_outcome.quiet:
        return {
            "status": "success",
            "cache_key": cache_key,
            "quiet": True,
        }

    return {
        "status": "success",
        "cache_key": cache_key,
        "cache_built_at": new_row["cache_built_at"],
        "quiet": False,
        "invalid": False,
        "invalid_reason": None,
    }


def register_chronicler_tools(ctx: ToolContext, mcp: Any, _core_tool: Any) -> None:
    """Register dashboard-only Chronicler controls on the Chronicler daemon."""
    if ctx.butler_name != "chronicler":
        return

    refresh_locks: dict[tuple[date, str], asyncio.Lock] = {}

    @_core_tool("infra", name="chronicler_day_close_refresh")
    async def chronicler_day_close_refresh(date_label: str, timezone: str) -> dict[str, Any]:
        """Regenerate one settled day-close tuple for the dashboard.

        This is an infrastructure RPC, not an LLM-facing action. Runtime
        sessions are refused so an agent cannot recursively spawn another
        day-close session. The dashboard invokes it server-to-server over the
        existing MCP boundary.
        """
        if (
            get_current_runtime_session_id() is not None
            or get_current_runtime_trigger_source() is not None
        ):
            return _error(
                "refresh_context_forbidden",
                "Day-close refresh is available only to the dashboard control plane.",
            )

        try:
            target = date.fromisoformat(date_label)
        except (TypeError, ValueError):
            return _error("invalid_date", "date_label must be a valid YYYY-MM-DD date.")
        if target.isoformat() != date_label:
            return _error("invalid_date", "date_label must be a valid YYYY-MM-DD date.")

        try:
            timezone_name, timezone_info = resolve_day_close_timezone(timezone)
        except MissingDayCloseTimezoneError:
            return _error("missing_parameter", "timezone is required.")
        except InvalidDayCloseTimezoneError:
            return _error("invalid_timezone", f"Unrecognized IANA timezone: {timezone!r}")

        today_local = datetime.now(UTC).astimezone(timezone_info).date()
        if target >= today_local:
            return _error(
                "day_close_not_settled",
                "Day-close refresh targets must be a settled historical local day.",
                details={
                    "date": target.isoformat(),
                    "today": today_local.isoformat(),
                    "tz": timezone_name,
                },
            )

        refresh_lock = refresh_locks.setdefault((target, timezone_name), asyncio.Lock())

        async def _locked_refresh() -> dict[str, Any]:
            async with refresh_lock:
                return await _run_manual_refresh(
                    ctx,
                    target=target,
                    timezone_name=timezone_name,
                    timezone_info=timezone_info,
                )

        try:
            return await asyncio.wait_for(
                _locked_refresh(),
                timeout=_REFRESH_OPERATION_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return _error(
                "dispatch_timeout",
                "Day-close regeneration exceeded its execution deadline.",
            )
