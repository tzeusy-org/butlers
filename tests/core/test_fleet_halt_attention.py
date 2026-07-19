"""Tests for butlers.core.fleet_halt_attention (bu-7o89u.4).

Covers:
- _current_halt_window / _current_month_start: calendar-month window keying.
- _already_notified_this_window: reads the debounce marker back from
  public.audit_log, fails open (False) on lookup error.
- _count_denied_this_month: counts quota_skip ceiling denials for the
  current month, None on query error.
- _check_suppression: mirrors notify()'s quiet-hours + context-bus gate.
- _compose_message: always includes the denied count and the door URL.
- maybe_push_fleet_halt_attention:
  - debounced: a second call within the same window is a clean no-op (no
    ledger write, no delivery attempt).
  - onset: a genuinely new window delivers, records the ledger row with
    denied_count/door/window metadata, and writes the debounce marker.
  - high priority (the production default) skips the suppression check
    entirely -- mirrors notify()'s own "priority=high always delivers"
    convention.
  - non-high priority IS suppressed by quiet hours / context bus, proving
    the gate is real and wired, not decorative.
  - no recipient configured -> deferred, no debounce marker (retries next
    denial).
  - delivery failure -> deferred, no debounce marker.
  - an unexpected exception anywhere in the body never raises and never
    blocks the caller.
  - pool=None is a clean no-op.

No real database required -- all pool interactions are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.fleet_halt_attention import (
    _already_notified_this_window,
    _check_suppression,
    _compose_message,
    _count_denied_this_month,
    _current_halt_window,
    _current_month_start,
    maybe_push_fleet_halt_attention,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _current_halt_window / _current_month_start
# ---------------------------------------------------------------------------


def test_current_halt_window_formats_as_year_month():
    assert _current_halt_window(datetime(2026, 7, 12, 9, 30, tzinfo=UTC)) == "2026-07"


def test_current_halt_window_pads_single_digit_month():
    assert _current_halt_window(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01"


def test_current_month_start_truncates_to_first_of_month():
    start = _current_month_start(datetime(2026, 7, 12, 9, 30, 45, tzinfo=UTC))
    assert start == datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _already_notified_this_window
# ---------------------------------------------------------------------------


async def test_already_notified_true_when_window_matches():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"note": "2026-07"})
    assert await _already_notified_this_window(pool, "2026-07") is True


async def test_already_notified_false_when_window_differs():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"note": "2026-06"})
    assert await _already_notified_this_window(pool, "2026-07") is False


async def test_already_notified_false_when_no_row():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert await _already_notified_this_window(pool, "2026-07") is False


async def test_already_notified_fails_open_on_error():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _already_notified_this_window(pool, "2026-07") is False


# ---------------------------------------------------------------------------
# _count_denied_this_month
# ---------------------------------------------------------------------------


async def test_count_denied_this_month_returns_int():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=7)
    assert await _count_denied_this_month(pool) == 7


async def test_count_denied_this_month_none_on_error():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _count_denied_this_month(pool) is None


# ---------------------------------------------------------------------------
# _check_suppression
# ---------------------------------------------------------------------------


async def test_check_suppression_quiet_hours():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention.get_approvals_policy_quiet_hours",
            new=AsyncMock(
                return_value={"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}
            ),
        ),
        patch(
            "butlers.core.fleet_halt_attention.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
        patch("butlers.core.fleet_halt_attention.is_policy_quiet_now", return_value=True),
    ):
        reason = await _check_suppression(pool)
    assert reason == "quiet_hours"


async def test_check_suppression_context_bus():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.fleet_halt_attention.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ),
    ):
        reason = await _check_suppression(pool)
    assert reason == "context_bus:dnd"


async def test_check_suppression_none_when_clear():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.fleet_halt_attention.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        reason = await _check_suppression(pool)
    assert reason is None


# ---------------------------------------------------------------------------
# _compose_message
# ---------------------------------------------------------------------------


def test_compose_message_includes_count_and_door():
    message = _compose_message(
        5, "http://localhost:41200", "http://localhost:41200/spend?openDrawer=fleet-halt"
    )
    assert "5 dispatches" in message
    assert "http://localhost:41200/spend?openDrawer=fleet-halt" in message


def test_compose_message_singular_for_one():
    message = _compose_message(
        1, "http://localhost:41200", "http://localhost:41200/spend?openDrawer=fleet-halt"
    )
    assert "1 dispatch" in message
    assert "1 dispatches" not in message


def test_compose_message_unknown_count():
    message = _compose_message(
        None, "http://localhost:41200", "http://localhost:41200/spend?openDrawer=fleet-halt"
    )
    assert "unknown number" in message


# ---------------------------------------------------------------------------
# maybe_push_fleet_halt_attention
# ---------------------------------------------------------------------------


async def test_pool_none_is_clean_noop():
    # Must not raise, and must not attempt any lookup.
    await maybe_push_fleet_halt_attention(None)


async def test_debounced_second_call_is_a_clean_noop():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention._already_notified_this_window",
            new=AsyncMock(return_value=True),
        ),
        patch("butlers.core.fleet_halt_attention._count_denied_this_month") as count_mock,
        patch("butlers.core.fleet_halt_attention.record_attention_event") as ledger_mock,
        patch(
            "butlers.core.fleet_halt_attention.resolve_owner_telegram_recipient"
        ) as recipient_mock,
    ):
        await maybe_push_fleet_halt_attention(pool)

    count_mock.assert_not_called()
    ledger_mock.assert_not_called()
    recipient_mock.assert_not_called()


async def test_onset_delivers_records_ledger_and_writes_debounce_marker():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention._already_notified_this_window",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.fleet_halt_attention._current_halt_window",
            return_value="2026-07",
        ),
        patch(
            "butlers.core.fleet_halt_attention._count_denied_this_month",
            new=AsyncMock(return_value=3),
        ),
        patch(
            "butlers.core.fleet_halt_attention.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent", "notification_id": "n-1"}),
        ) as deliver_mock,
        patch(
            "butlers.core.fleet_halt_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
        patch("butlers.core.fleet_halt_attention._check_suppression") as suppression_mock,
    ):
        await maybe_push_fleet_halt_attention(pool)

    # priority="high" (the default) skips the suppression check entirely.
    suppression_mock.assert_not_called()

    deliver_mock.assert_awaited_once()
    call_kwargs = deliver_mock.await_args.kwargs
    assert call_kwargs["channel"] == "telegram"
    assert call_kwargs["recipient"] == "12345"
    assert "3 dispatches" in call_kwargs["message"]
    assert "openDrawer=fleet-halt" in call_kwargs["message"]

    ledger_mock.assert_awaited_once()
    ledger_kwargs = ledger_mock.await_args.kwargs
    assert ledger_kwargs["outcome"] == "delivered"
    assert ledger_kwargs["priority"] == "high"
    assert ledger_kwargs["dedup_key"] == "ceiling_halt:2026-07"
    assert ledger_kwargs["metadata"]["denied_count"] == 3
    assert "openDrawer=fleet-halt" in ledger_kwargs["metadata"]["door"]
    assert ledger_kwargs["metadata"]["window"] == "2026-07"

    audit_append.assert_awaited_once()
    assert audit_append.await_args.args[2] == "ceiling_halt_notified"
    assert audit_append.await_args.kwargs["target"] == "ceiling_halt"
    assert audit_append.await_args.kwargs["note"] == "2026-07"


async def test_non_high_priority_is_suppressed_by_quiet_hours():
    """Proves the gating hook is real: a lower priority genuinely gets
    suppressed instead of always bypassing (unlike the production default)."""
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention._already_notified_this_window",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.fleet_halt_attention._count_denied_this_month",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "butlers.core.fleet_halt_attention._check_suppression",
            new=AsyncMock(return_value="quiet_hours"),
        ),
        patch(
            "butlers.core.fleet_halt_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch(
            "butlers.core.fleet_halt_attention.resolve_owner_telegram_recipient"
        ) as recipient_mock,
        patch("butlers.api.routers.audit.append") as audit_append,
    ):
        await maybe_push_fleet_halt_attention(pool, priority="medium")

    recipient_mock.assert_not_called()
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "suppressed"
    assert ledger_mock.await_args.kwargs["reason"] == "quiet_hours"
    audit_append.assert_not_called()


async def test_no_recipient_defers_without_debounce_marker():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention._already_notified_this_window",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.fleet_halt_attention._count_denied_this_month",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "butlers.core.fleet_halt_attention.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.fleet_halt_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append") as audit_append,
    ):
        await maybe_push_fleet_halt_attention(pool)

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "no_recipient_configured"
    audit_append.assert_not_called()


async def test_delivery_failure_defers_without_debounce_marker():
    pool = object()
    with (
        patch(
            "butlers.core.fleet_halt_attention._already_notified_this_window",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.fleet_halt_attention._count_denied_this_month",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "butlers.core.fleet_halt_attention.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "failed", "error": "boom"}),
        ),
        patch(
            "butlers.core.fleet_halt_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append") as audit_append,
    ):
        await maybe_push_fleet_halt_attention(pool)

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "delivery_error:boom"
    audit_append.assert_not_called()


async def test_unexpected_error_never_raises():
    pool = object()
    with patch(
        "butlers.core.fleet_halt_attention._already_notified_this_window",
        side_effect=RuntimeError("boom"),
    ):
        # Must not raise.
        await maybe_push_fleet_halt_attention(pool)
