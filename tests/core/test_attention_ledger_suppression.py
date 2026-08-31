"""Unit tests for legacy destructive owner-notify suppression (bu-gts7r).

``check_owner_notify_suppression`` was extracted from the near-verbatim copies in
``butlers.jobs.secrets_lifecycle._check_suppression`` and
``butlers.jobs.home._check_owner_notify_suppression``. It returns a terminal
suppression reason for those out-of-process callers to record and drop, rather than
mirroring direct ``notify()`` owner-default parking. The helper checks quiet hours
first, then the context-bus dnd/sleeping signal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.attention_ledger import check_owner_notify_suppression

pytestmark = pytest.mark.unit

_QUIET_POLICY = {"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}


async def test_quiet_hours_active_returns_quiet_hours() -> None:
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_QUIET_POLICY),
        ),
        patch("butlers.core.attention_ledger.is_policy_quiet_now", return_value=True),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") == "quiet_hours"


async def test_context_bus_active_returns_context_bus_signal() -> None:
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") == "context_bus:dnd"


async def test_neither_returns_none() -> None:
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") is None


async def test_both_active_quiet_hours_takes_precedence() -> None:
    """Quiet hours is checked first, so it wins over a concurrent context signal
    and the context-bus lookup is never consulted."""
    context_lookup = AsyncMock(return_value="dnd")
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_QUIET_POLICY),
        ),
        patch("butlers.core.attention_ledger.is_policy_quiet_now", return_value=True),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=context_lookup,
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") == "quiet_hours"
    context_lookup.assert_not_awaited()


async def test_quiet_hours_lookup_failure_is_non_fatal_and_logs_context(caplog) -> None:
    """A quiet-hours lookup that raises is swallowed (falls through to the
    context-bus check) and logs a debug line prefixed with the caller's context."""
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(side_effect=RuntimeError("policy DB down")),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
        caplog.at_level(logging.DEBUG, logger="butlers.core.attention_ledger"),
    ):
        result = await check_owner_notify_suppression(
            object(), log_context="secrets_lifecycle_check"
        )

    assert result is None
    assert any(
        "secrets_lifecycle_check: quiet-hours policy lookup failed" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# The instant is the input (bu-8y575)
#
# Every test above patches ``is_policy_quiet_now`` to a constant, so none of
# them ever exercises the hour comparison -- the helper would answer the same
# way with no clock at all. The tests below feed the real
# :func:`butlers.core.approvals_policy.is_policy_quiet_now` a real policy and
# pin instants an hour either side of that policy's own edge. Both sides are
# required: a single pinned instant that comes back green proves nothing,
# because a branch that never fires is also green.
#
# The policy is the production Owner Attention Policy core_160 seeds for this
# single-owner deployment (23:00-08:00 Asia/Singapore = 15:00-24:00 UTC).
# ---------------------------------------------------------------------------

_OWNER_POLICY = {"quiet_start_hour": 23, "quiet_end_hour": 8, "timezone": "Asia/Singapore"}

# The window opens at 23:00 SGT (15:00 UTC). These two straddle that edge by
# thirty minutes each way, so shifting the instant by a single hour in either
# direction crosses it.
_INSTANT_INSIDE_QUIET_HOURS = datetime(2026, 1, 15, 15, 30, tzinfo=UTC)  # 23:30 SGT
_INSTANT_OUTSIDE_QUIET_HOURS = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)  # 22:30 SGT


def _owner_policy_and_clear_context_bus():
    return (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_OWNER_POLICY),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    )


async def test_instant_inside_the_owner_quiet_window_suppresses() -> None:
    """23:30 SGT, half an hour after the window opens -> dropped."""
    policy_patch, context_patch = _owner_policy_and_clear_context_bus()
    with policy_patch, context_patch:
        assert (
            await check_owner_notify_suppression(
                object(), log_context="t", now=_INSTANT_INSIDE_QUIET_HOURS
            )
            == "quiet_hours"
        )


async def test_instant_outside_the_owner_quiet_window_allows_delivery() -> None:
    """22:30 SGT, half an hour before the same window opens: the same policy
    and the same clear context bus return no reason at all, so it delivers."""
    policy_patch, context_patch = _owner_policy_and_clear_context_bus()
    with policy_patch, context_patch:
        assert (
            await check_owner_notify_suppression(
                object(), log_context="t", now=_INSTANT_OUTSIDE_QUIET_HOURS
            )
            is None
        )


async def test_injected_instant_is_forwarded_to_the_context_bus_read() -> None:
    """The context-bus consult is evaluated at the caller's instant too, so a
    hold that has already expired at that instant cannot suppress."""
    context_lookup = AsyncMock(return_value=None)
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_OWNER_POLICY),
        ),
        patch("butlers.core.attention_ledger.get_suppressing_context_signal", new=context_lookup),
    ):
        await check_owner_notify_suppression(
            object(), log_context="t", now=_INSTANT_OUTSIDE_QUIET_HOURS
        )
    context_lookup.assert_awaited_once()
    assert context_lookup.await_args.kwargs["now"] == _INSTANT_OUTSIDE_QUIET_HOURS


async def test_omitted_instant_still_reads_the_wall_clock() -> None:
    """The default path, which is what every production caller uses.

    Injecting an instant is only safe if omitting it still asks the real
    clock. Both gates are handed an instant captured from inside the call and
    checked against a window bracketing it, so a default that had frozen to a
    constant -- or been dropped altogether -- would fall outside the bracket.
    """
    seen_by_quiet_hours: list[datetime] = []

    def _capture_quiet_hours(policy, *, now):
        seen_by_quiet_hours.append(now)
        return False

    context_lookup = AsyncMock(return_value=None)
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_OWNER_POLICY),
        ),
        patch("butlers.core.attention_ledger.is_policy_quiet_now", new=_capture_quiet_hours),
        patch("butlers.core.attention_ledger.get_suppressing_context_signal", new=context_lookup),
    ):
        before = datetime.now(UTC)
        await check_owner_notify_suppression(object(), log_context="t")
        after = datetime.now(UTC)

    assert len(seen_by_quiet_hours) == 1
    assert before <= seen_by_quiet_hours[0] <= after
    assert before <= context_lookup.await_args.kwargs["now"] <= after
