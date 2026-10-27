"""Chronicler-only infrastructure controls."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from butlers.chronicler.day_close_cache import (
    InvalidDayCloseTimezoneError,
    MissingDayCloseTimezoneError,
    resolve_day_close_timezone,
)
from butlers.chronicler.day_close_writer import (
    write_day_close_cache,
)
from butlers.core.tool_call_capture import (
    MANUAL_DAY_CLOSE_TRIGGER_PREFIX as MANUAL_DAY_CLOSE_TRIGGER_PREFIX,
)
from butlers.core.tool_call_capture import (
    get_current_runtime_session_id,
    get_current_runtime_trigger_source,
)
from butlers.core_tools._base import ToolContext

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


async def _run_manual_refresh(
    ctx: ToolContext, *, target: date, timezone_name: str, timezone_info: ZoneInfo
) -> dict[str, Any]:
    from butlers.chronicler.day_close_refresh import held_refresh_service

    return await held_refresh_service(ctx.daemon, ctx.pool).refresh(
        target,
        timezone_name,
        bound_s=_REFRESH_OPERATION_TIMEOUT_SECONDS,
        writer=write_day_close_cache,
    )


def register_chronicler_tools(ctx: ToolContext, mcp: Any, _core_tool: Any) -> None:
    """Register dashboard-only Chronicler controls on the Chronicler daemon."""
    if ctx.butler_name != "chronicler":
        return

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

        return await _run_manual_refresh(
            ctx, target=target, timezone_name=timezone_name, timezone_info=timezone_info
        )
