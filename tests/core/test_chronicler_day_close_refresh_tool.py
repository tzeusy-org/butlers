"""Behavior tests for the Chronicler-only manual day-close refresh control tool."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.day_close_writer import DayCloseCacheWriteOutcome
from butlers.config import ButlerType
from butlers.core.model_routing import Complexity
from butlers.core.tool_call_capture import (
    reset_current_runtime_session_id,
    reset_current_runtime_trigger_source,
    set_current_runtime_session_id,
    set_current_runtime_trigger_source,
)
from butlers.core_tools._base import ToolContext
from butlers.core_tools._chronicler import (
    MANUAL_DAY_CLOSE_TRIGGER_PREFIX,
    register_chronicler_tools,
)
from butlers.core_tools._notifications import register_notification_tools
from butlers.mcp_wrappers import _manual_day_close_tool_policy, _ToolCallLoggingMCP

pytestmark = pytest.mark.unit


def _capture_tool(*, butler_name: str = "chronicler") -> tuple[AsyncMock, MagicMock, Any | None]:
    pool = AsyncMock()
    daemon = MagicMock()
    daemon._dispatch_scheduled_task = AsyncMock(return_value=MagicMock())
    registered: dict[str, Any] = {}
    mcp = MagicMock()

    def tool_decorator(*_args, **kwargs):
        def decorator(fn):
            registered[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=MagicMock(),
        butler_name=butler_name,
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=MagicMock(),
    )
    register_chronicler_tools(ctx, mcp, lambda _group, **kwargs: mcp.tool(**kwargs))
    return pool, daemon, registered.get("chronicler_day_close_refresh")


async def test_refresh_tool_dispatches_exact_silent_target_and_returns_metadata_only() -> None:
    pool, daemon, refresh = _capture_tool()
    assert refresh is not None
    built_at = datetime.now(UTC)
    pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
            {"cache_built_at": built_at},
            {"covered_at": built_at},
        ]
    )
    outcome = DayCloseCacheWriteOutcome()

    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=outcome),
    ) as writer:
        response = await refresh(date_label="2026-09-03", timezone="Asia/Singapore")

    assert response == {
        "status": "success",
        "cache_key": "day_close:2026-09-03:tz:Asia/Singapore",
        "cache_built_at": built_at,
        "quiet": False,
        "invalid": False,
        "invalid_reason": None,
    }
    assert set(response) == {
        "status",
        "cache_key",
        "cache_built_at",
        "quiet",
        "invalid",
        "invalid_reason",
    }
    writer.assert_awaited_once()
    assert writer.call_args.kwargs["target_date"].isoformat() == "2026-09-03"
    assert writer.call_args.kwargs["tz"] == "Asia/Singapore"

    daemon._dispatch_scheduled_task.assert_awaited_once()
    dispatch_kwargs = daemon._dispatch_scheduled_task.call_args.kwargs
    assert dispatch_kwargs["trigger_source"] == f"{MANUAL_DAY_CLOSE_TRIGGER_PREFIX}2026-09-03"
    assert dispatch_kwargs["complexity"] is Complexity.REASONING
    assert "date_label=2026-09-03" in dispatch_kwargs["prompt"]
    assert "timezone=Asia/Singapore" in dispatch_kwargs["prompt"]
    assert "do not call notify" in dispatch_kwargs["prompt"].lower()


@pytest.mark.parametrize("runtime_context", ["session", "trigger"])
async def test_refresh_tool_refuses_runtime_originated_calls(runtime_context: str) -> None:
    pool, _daemon, refresh = _capture_tool()
    assert refresh is not None
    session_token = trigger_token = None
    if runtime_context == "session":
        session_token = set_current_runtime_session_id("runtime-session")
    else:
        trigger_token = set_current_runtime_trigger_source("trigger")
    try:
        response = await refresh(date_label="2026-09-03", timezone="Asia/Singapore")
    finally:
        if session_token is not None:
            reset_current_runtime_session_id(session_token)
        if trigger_token is not None:
            reset_current_runtime_trigger_source(trigger_token)

    assert response["status"] == "error"
    assert response["code"] == "refresh_context_forbidden"
    pool.fetchrow.assert_not_awaited()


async def test_refresh_tool_enforces_tuple_rate_limit_before_dispatch() -> None:
    pool, daemon, refresh = _capture_tool()
    assert refresh is not None
    refreshed_at = datetime.now(UTC) - timedelta(hours=1)
    pool.fetchrow = AsyncMock(
        side_effect=[
            {"cache_built_at": refreshed_at},
            {"covered_at": refreshed_at},
        ]
    )

    response = await refresh(date_label="2026-09-03", timezone="Asia/Singapore")

    assert response["status"] == "error"
    assert response["code"] == "day_close_rate_limited"
    assert response["details"]["retry_after_seconds"] > 0
    assert pool.fetchrow.await_args_list[0].args[1] == ("day_close:2026-09-03:tz:Asia/Singapore")
    assert pool.fetchrow.await_args_list[1].args[1:] == (
        date(2026, 9, 3),
        "Asia/Singapore",
    )
    daemon._dispatch_scheduled_task.assert_not_awaited()


async def test_refresh_tool_dispatches_after_the_tuple_rate_limit_expires() -> None:
    pool, daemon, refresh = _capture_tool()
    assert refresh is not None
    old = datetime.now(UTC) - timedelta(hours=25)
    fresh = datetime.now(UTC)
    pool.fetchrow = AsyncMock(
        side_effect=[
            {"cache_built_at": old},
            {"covered_at": old},
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
            {"cache_built_at": fresh},
            {"covered_at": fresh},
        ]
    )

    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=DayCloseCacheWriteOutcome()),
    ):
        response = await refresh(date_label="2026-09-03", timezone="Asia/Singapore")

    assert response["status"] == "success"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_refresh_tool_retries_a_fresh_cache_that_has_no_coverage_witness() -> None:
    pool, daemon, refresh = _capture_tool()
    assert refresh is not None
    built_at = datetime.now(UTC)
    pool.fetchrow = AsyncMock(
        side_effect=[
            {"cache_built_at": built_at},
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
            {"cache_built_at": built_at},
            {"covered_at": built_at},
        ]
    )

    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=DayCloseCacheWriteOutcome()),
    ):
        response = await refresh(date_label="2026-09-03", timezone="Asia/Singapore")

    assert response["status"] == "success"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_refresh_tool_serializes_concurrent_calls_before_dispatch() -> None:
    pool, daemon, refresh = _capture_tool()
    assert refresh is not None
    fresh = datetime.now(UTC)
    cache_row = {"cache_built_at": fresh}
    witness_row = {"covered_at": fresh}
    pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
            cache_row,
            witness_row,
            cache_row,
            witness_row,
        ]
    )
    dispatch_started = asyncio.Event()
    release_dispatch = asyncio.Event()

    async def dispatch(**_kwargs):
        dispatch_started.set()
        await release_dispatch.wait()
        return MagicMock()

    daemon._dispatch_scheduled_task.side_effect = dispatch
    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=DayCloseCacheWriteOutcome()),
    ):
        first = asyncio.create_task(refresh(date_label="2026-09-03", timezone="Asia/Singapore"))
        await dispatch_started.wait()
        second = asyncio.create_task(refresh(date_label="2026-09-03", timezone="Asia/Singapore"))
        await asyncio.sleep(0)
        daemon._dispatch_scheduled_task.assert_awaited_once()
        release_dispatch.set()
        first_result, second_result = await asyncio.gather(first, second)

    assert first_result["status"] == "success"
    assert second_result["code"] == "day_close_rate_limited"
    daemon._dispatch_scheduled_task.assert_awaited_once()


async def test_refresh_tool_deadline_includes_waiting_for_the_tuple_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool, daemon, refresh = _capture_tool()
    assert refresh is not None
    fresh = datetime.now(UTC)
    pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
            {"cache_built_at": fresh},
            {"covered_at": fresh},
        ]
    )
    dispatch_started = asyncio.Event()
    release_dispatch = asyncio.Event()

    async def dispatch(**_kwargs):
        dispatch_started.set()
        await release_dispatch.wait()
        return MagicMock()

    daemon._dispatch_scheduled_task.side_effect = dispatch
    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=DayCloseCacheWriteOutcome()),
    ):
        first = asyncio.create_task(refresh(date_label="2026-09-03", timezone="Asia/Singapore"))
        await dispatch_started.wait()
        monkeypatch.setattr(
            "butlers.core_tools._chronicler._REFRESH_OPERATION_TIMEOUT_SECONDS",
            0.001,
        )
        second_result = await asyncio.wait_for(
            refresh(date_label="2026-09-03", timezone="Asia/Singapore"),
            timeout=0.1,
        )
        assert second_result["code"] == "dispatch_timeout"
        daemon._dispatch_scheduled_task.assert_awaited_once()
        release_dispatch.set()
        first_result = await first

    assert first_result["status"] == "success"
    daemon._dispatch_scheduled_task.assert_awaited_once()


@pytest.mark.parametrize(
    ("outcome", "initial_witness", "final_witness", "expected_status", "expected_code"),
    [
        (DayCloseCacheWriteOutcome(quiet=True), False, True, "success", None),
        (
            DayCloseCacheWriteOutcome(invalid_reason="date_mismatch"),
            True,
            True,
            "success",
            "date_mismatch",
        ),
        (
            DayCloseCacheWriteOutcome(invalid_reason="date_mismatch"),
            False,
            False,
            "error",
            "coverage_witness_write_failed",
        ),
        (
            DayCloseCacheWriteOutcome(),
            False,
            False,
            "error",
            "coverage_witness_write_failed",
        ),
    ],
)
async def test_refresh_tool_preserves_quiet_invalid_and_missing_witness_outcomes(
    outcome: DayCloseCacheWriteOutcome,
    initial_witness: bool,
    final_witness: bool,
    expected_status: str,
    expected_code: str | None,
) -> None:
    pool, _daemon, refresh = _capture_tool()
    assert refresh is not None
    old_witness = {"covered_at": datetime.now(UTC) - timedelta(hours=25)}
    fetchrows: list[Any] = [
        None,
        old_witness if initial_witness else None,
        {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
    ]
    if not outcome.quiet:
        fetchrows.append({"cache_built_at": datetime.now(UTC)})
    fetchrows.append(old_witness if final_witness else None)
    pool.fetchrow = AsyncMock(side_effect=fetchrows)

    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(return_value=outcome),
    ):
        response = await refresh(date_label="2026-09-03", timezone="Asia/Singapore")

    assert response["status"] == expected_status
    if expected_code is None:
        assert response["quiet"] is True
    elif expected_status == "success":
        assert response["invalid_reason"] == expected_code
    else:
        assert response["code"] == expected_code


async def test_refresh_tool_contains_missing_task_dispatch_failure_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_pool, _missing_daemon, missing_refresh = _capture_tool()
    assert missing_refresh is not None
    missing_pool.fetchrow = AsyncMock(side_effect=[None, None, None])
    missing = await missing_refresh(date_label="2026-09-03", timezone="Asia/Singapore")
    assert missing["status"] == "error"
    assert missing["code"] == "task_not_found"

    failing_pool, failing_daemon, failing_refresh = _capture_tool()
    assert failing_refresh is not None
    failing_pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
        ]
    )
    failing_daemon._dispatch_scheduled_task.side_effect = RuntimeError("private failure")
    failed = await failing_refresh(date_label="2026-09-03", timezone="Asia/Singapore")
    assert failed == {
        "status": "error",
        "code": "dispatch_failed",
        "message": "Day-close regeneration failed during execution.",
    }

    timeout_pool, timeout_daemon, timeout_refresh = _capture_tool()
    assert timeout_refresh is not None
    timeout_pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            None,
            {"prompt": "Run the scheduled day close.", "complexity": "reasoning"},
        ]
    )

    async def never_returns(**_kwargs):
        await asyncio.Event().wait()

    timeout_daemon._dispatch_scheduled_task.side_effect = never_returns
    monkeypatch.setattr("butlers.core_tools._chronicler._REFRESH_OPERATION_TIMEOUT_SECONDS", 0.001)
    with patch(
        "butlers.core_tools._chronicler.write_day_close_cache",
        new=AsyncMock(),
    ) as writer:
        timed_out = await timeout_refresh(
            date_label="2026-09-03",
            timezone="Asia/Singapore",
        )

    assert timed_out["status"] == "error"
    assert timed_out["code"] == "dispatch_timeout"
    writer.assert_not_awaited()


@pytest.mark.parametrize(
    ("date_label", "timezone", "code"),
    [
        ("not-a-date", "Asia/Singapore", "invalid_date"),
        ("20260903", "Asia/Singapore", "invalid_date"),
        ("2026-09-03", "Not/A/Timezone", "invalid_timezone"),
        ("2999-01-01", "Asia/Singapore", "day_close_not_settled"),
    ],
)
async def test_refresh_tool_rejects_invalid_or_unsettled_targets(
    date_label: str, timezone: str, code: str
) -> None:
    pool, _daemon, refresh = _capture_tool()
    assert refresh is not None

    response = await refresh(date_label=date_label, timezone=timezone)

    assert response["status"] == "error"
    assert response["code"] == code
    pool.fetchrow.assert_not_awaited()


def test_refresh_tool_registers_only_for_chronicler() -> None:
    _pool, _daemon, refresh = _capture_tool(butler_name="general")
    assert refresh is None


async def test_manual_refresh_suppresses_reminders_before_scheduling() -> None:
    pool = AsyncMock()
    registered: dict[str, Any] = {}
    mcp = MagicMock()

    def tool_decorator(*_args, **kwargs):
        def decorator(fn):
            registered[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    ctx = ToolContext(
        daemon=MagicMock(),
        pool=pool,
        spawner=MagicMock(),
        butler_name="chronicler",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=MagicMock(),
    )
    wrapped_mcp = _ToolCallLoggingMCP(mcp, "chronicler", module_name="core")
    register_notification_tools(
        ctx,
        wrapped_mcp,
        lambda _group, **kwargs: wrapped_mcp.tool(**kwargs),
    )

    trigger_token = set_current_runtime_trigger_source("api:day_close_refresh:2026-09-03")
    try:
        response = await registered["remind"](
            message="Historical summary",
            channel="email",
            delay_minutes=5,
        )
    finally:
        reset_current_runtime_trigger_source(trigger_token)

    assert response == {
        "status": "suppressed",
        "reason": "manual_day_close_refresh_tool_policy",
        "retryable": False,
    }
    pool.fetchrow.assert_not_awaited()
    pool.execute.assert_not_awaited()


def test_manual_refresh_tool_policy_allows_only_the_bounded_bundle_read() -> None:
    trigger_token = set_current_runtime_trigger_source("api:day_close_refresh:2026-09-03")
    try:
        assert (
            _manual_day_close_tool_policy(
                butler_name="chronicler",
                tool_name="chronicler_day_close_bundle",
            )
            is None
        )
        for tool_name in ("notify", "remind", "trigger", "schedule_create", "route.execute"):
            result = _manual_day_close_tool_policy(
                butler_name="chronicler",
                tool_name=tool_name,
            )
            assert result == {
                "status": "suppressed",
                "reason": "manual_day_close_refresh_tool_policy",
                "retryable": False,
            }
    finally:
        reset_current_runtime_trigger_source(trigger_token)
