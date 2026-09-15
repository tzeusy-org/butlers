"""Unit tests for the insight broker's presence-aware context-bus suppression
(bu-ep4ks.9 slice 2 — "Insight broker v2").

``roster/switchboard/tools/insight/broker.get_suppressing_context_signal`` is a
broker-local extension of the shared dnd/sleeping suppression set in
``butlers.core.attention_ledger``: it also holds routine insight delivery on
``meeting``/``traveling``, each capped by its own max-hold TTL so a
long-running signal (``traveling`` can stay active for up to 30 days per
``butlers.context_bus._TTL_CONFIG``) cannot silently queue insights for a
month. Precedence when multiple signals are active: dnd, then meeting,
sleeping, traveling.

These tests mock ``butlers.context_bus.get_active_context`` directly (the
function is imported inline inside the broker, matching the mocking pattern
used for the shared helper in ``tests/core/test_attention_ledger_suppression.py``)
so they run without Docker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.unit]

_NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _signal(signal_type: str, *, set_at: datetime, expires_at: datetime | None = None):
    from butlers.context_bus import ContextEntry

    return ContextEntry(
        signal_type=signal_type,
        value=None,
        set_by_butler="general",
        set_at=set_at,
        expires_at=expires_at or (set_at + timedelta(days=1)),
        confidence=1.0,
    )


async def _get_signal(signals, *, now=_NOW):
    from butlers.tools.switchboard.insight.broker import get_suppressing_context_signal

    with patch(
        "butlers.context_bus.get_active_context",
        new=AsyncMock(return_value=signals),
    ):
        return await get_suppressing_context_signal(object(), now=now)


class TestSignalCoverage:
    async def test_dnd_suppresses_within_max_hold(self):
        result = await _get_signal([_signal("dnd", set_at=_NOW - timedelta(hours=1))])
        assert result == "dnd"

    async def test_meeting_suppresses_within_max_hold(self):
        result = await _get_signal([_signal("meeting", set_at=_NOW - timedelta(minutes=30))])
        assert result == "meeting"

    async def test_sleeping_suppresses_within_max_hold(self):
        result = await _get_signal([_signal("sleeping", set_at=_NOW - timedelta(hours=5))])
        assert result == "sleeping"

    async def test_traveling_suppresses_within_max_hold(self):
        result = await _get_signal([_signal("traveling", set_at=_NOW - timedelta(hours=2))])
        assert result == "traveling"

    async def test_irrelevant_signal_type_does_not_suppress(self):
        result = await _get_signal([_signal("exercising", set_at=_NOW - timedelta(minutes=5))])
        assert result is None

    async def test_no_active_signals_returns_none(self):
        result = await _get_signal([])
        assert result is None


class TestMaxHoldTTL:
    """A signal that has been continuously active longer than its own
    max-hold cap stops suppressing routine insight delivery, independent of
    the signal's own (possibly much longer) context-bus expiry."""

    async def test_dnd_beyond_max_hold_no_longer_suppresses(self):
        # dnd's max-hold is 4h; set_at 5h ago is past the cap even though the
        # signal's own context-bus expiry (set_at + 1d default here) is still
        # far in the future.
        result = await _get_signal([_signal("dnd", set_at=_NOW - timedelta(hours=5))])
        assert result is None

    async def test_meeting_beyond_max_hold_no_longer_suppresses(self):
        result = await _get_signal([_signal("meeting", set_at=_NOW - timedelta(hours=3))])
        assert result is None

    async def test_sleeping_beyond_max_hold_no_longer_suppresses(self):
        result = await _get_signal([_signal("sleeping", set_at=_NOW - timedelta(hours=11))])
        assert result is None

    async def test_traveling_beyond_max_hold_no_longer_suppresses(self):
        # The motivating case: traveling can stay active for up to 30 days,
        # but must not hold routine insights hostage for anywhere near that
        # long. 7h past set_at is beyond the 6h cap.
        result = await _get_signal([_signal("traveling", set_at=_NOW - timedelta(hours=7))])
        assert result is None

    async def test_traveling_active_for_days_never_suppresses(self):
        result = await _get_signal([_signal("traveling", set_at=_NOW - timedelta(days=10))])
        assert result is None

    async def test_exactly_at_max_hold_boundary_no_longer_suppresses(self):
        """The comparison is strict less-than: a signal exactly at its cap no
        longer suppresses (matches quiet-hours' end-exclusive convention)."""
        result = await _get_signal([_signal("meeting", set_at=_NOW - timedelta(hours=2))])
        assert result is None


class TestPrecedence:
    async def test_dnd_wins_over_meeting_sleeping_traveling(self):
        result = await _get_signal(
            [
                _signal("traveling", set_at=_NOW - timedelta(hours=1)),
                _signal("sleeping", set_at=_NOW - timedelta(hours=1)),
                _signal("meeting", set_at=_NOW - timedelta(minutes=10)),
                _signal("dnd", set_at=_NOW - timedelta(minutes=5)),
            ]
        )
        assert result == "dnd"

    async def test_meeting_wins_over_sleeping_and_traveling_when_dnd_absent(self):
        result = await _get_signal(
            [
                _signal("traveling", set_at=_NOW - timedelta(hours=1)),
                _signal("sleeping", set_at=_NOW - timedelta(hours=1)),
                _signal("meeting", set_at=_NOW - timedelta(minutes=10)),
            ]
        )
        assert result == "meeting"

    async def test_sleeping_wins_over_traveling_when_dnd_and_meeting_absent(self):
        result = await _get_signal(
            [
                _signal("traveling", set_at=_NOW - timedelta(hours=1)),
                _signal("sleeping", set_at=_NOW - timedelta(hours=1)),
            ]
        )
        assert result == "sleeping"

    async def test_expired_higher_precedence_signal_falls_through_to_lower(self):
        """dnd is active but past its max-hold cap; meeting is active and
        within its cap — meeting must win rather than the whole cycle
        reporting 'no suppression'."""
        result = await _get_signal(
            [
                _signal("dnd", set_at=_NOW - timedelta(hours=5)),  # beyond dnd's 4h cap
                _signal("meeting", set_at=_NOW - timedelta(minutes=10)),
            ]
        )
        assert result == "meeting"


class TestFailOpen:
    async def test_pool_none_returns_none(self):
        from butlers.tools.switchboard.insight.broker import get_suppressing_context_signal

        assert await get_suppressing_context_signal(None, now=_NOW) is None

    async def test_context_bus_error_fails_open(self):
        from butlers.tools.switchboard.insight.broker import get_suppressing_context_signal

        with patch(
            "butlers.context_bus.get_active_context",
            new=AsyncMock(side_effect=RuntimeError("context bus down")),
        ):
            assert await get_suppressing_context_signal(object(), now=_NOW) is None

    async def test_now_defaults_to_current_time_when_omitted(self):
        """Callers that don't pass `now` still get a working suppression
        check (defaults to datetime.now(UTC)) rather than raising."""
        from butlers.tools.switchboard.insight.broker import get_suppressing_context_signal

        recent = datetime.now(UTC) - timedelta(minutes=1)
        with patch(
            "butlers.context_bus.get_active_context",
            new=AsyncMock(return_value=[_signal("dnd", set_at=recent)]),
        ):
            # live-clock: this verifies the documented default-time path against a
            # context signal anchored to the same live clock.
            assert await get_suppressing_context_signal(object()) == "dnd"


class TestSuppressingContextSignalDetail:
    """bu-kqnum.3 slice 3: the broker catch-up cycle needs the winning
    signal's ``set_at`` (not just its type) to compute the suppression's
    max-hold end instant."""

    async def _get_detail(self, signals, *, now=_NOW):
        from butlers.tools.switchboard.insight.broker import (
            _get_suppressing_context_signal_detail,
        )

        with patch(
            "butlers.context_bus.get_active_context",
            new=AsyncMock(return_value=signals),
        ):
            return await _get_suppressing_context_signal_detail(object(), now=now)

    async def test_returns_the_winning_signal_type_and_set_at(self):
        set_at = _NOW - timedelta(hours=1)
        result = await self._get_detail([_signal("dnd", set_at=set_at)])
        assert result == ("dnd", set_at)

    async def test_precedence_still_applies_and_returns_the_winner_set_at(self):
        dnd_set_at = _NOW - timedelta(minutes=5)
        result = await self._get_detail(
            [
                _signal("traveling", set_at=_NOW - timedelta(hours=1)),
                _signal("dnd", set_at=dnd_set_at),
            ]
        )
        assert result == ("dnd", dnd_set_at)

    async def test_no_active_signal_returns_none(self):
        assert await self._get_detail([]) is None

    async def test_beyond_max_hold_returns_none(self):
        result = await self._get_detail([_signal("meeting", set_at=_NOW - timedelta(hours=3))])
        assert result is None

    async def test_pool_none_returns_none(self):
        from butlers.tools.switchboard.insight.broker import (
            _get_suppressing_context_signal_detail,
        )

        assert await _get_suppressing_context_signal_detail(None, now=_NOW) is None

    async def test_get_suppressing_context_signal_delegates_to_the_detail_helper(self):
        """The public str-only function must keep returning exactly the type,
        even though it's now a thin wrapper over the detail helper."""
        set_at = _NOW - timedelta(minutes=10)
        result = await self._get_signal_via_public_fn([_signal("sleeping", set_at=set_at)])
        assert result == "sleeping"

    async def _get_signal_via_public_fn(self, signals, *, now=_NOW):
        from butlers.tools.switchboard.insight.broker import get_suppressing_context_signal

        with patch(
            "butlers.context_bus.get_active_context",
            new=AsyncMock(return_value=signals),
        ):
            return await get_suppressing_context_signal(object(), now=now)


class TestComputeCatchupDeliverAt:
    """Pure-function coverage for the broker catch-up cycle's boundary
    computation (bu-kqnum.3 slice 3) — the deliver_at wiring into
    delivery_cycle itself is covered by
    tests/modules/test_insight_attention_ledger.py::TestBrokerCatchupCycle."""

    def test_quiet_hours_uses_the_policy_end_boundary(self):
        from butlers.core.approvals_policy import policy_quiet_hours_deliver_at
        from butlers.tools.switchboard.insight.broker import _compute_catchup_deliver_at

        policy = {"quiet_start_hour": 22, "quiet_end_hour": 8, "timezone": "UTC"}
        now = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)

        result = _compute_catchup_deliver_at(
            suppression_signal="quiet_hours",
            policy=policy,
            context_signal_set_at=None,
            now=now,
        )

        assert result == policy_quiet_hours_deliver_at(policy, now=now)
        assert result == datetime(2026, 1, 16, 8, 0, tzinfo=UTC)

    def test_quiet_hours_with_no_usable_policy_fails_open_to_none(self):
        from butlers.tools.switchboard.insight.broker import _compute_catchup_deliver_at

        result = _compute_catchup_deliver_at(
            suppression_signal="quiet_hours",
            policy=None,
            context_signal_set_at=None,
            now=_NOW,
        )

        assert result is None

    @pytest.mark.parametrize(
        "signal,hours",
        [("dnd", 4), ("meeting", 2), ("sleeping", 10), ("traveling", 6)],
    )
    def test_context_signal_uses_set_at_plus_its_own_max_hold(self, signal, hours):
        from butlers.tools.switchboard.insight.broker import _compute_catchup_deliver_at

        set_at = _NOW - timedelta(minutes=30)

        result = _compute_catchup_deliver_at(
            suppression_signal=signal,
            policy=None,
            context_signal_set_at=set_at,
            now=_NOW,
        )

        assert result == set_at + timedelta(hours=hours)

    def test_context_signal_without_a_set_at_fails_open_to_none(self):
        """The catch-up's own re-resolution of the context signal (see
        broker._schedule_insight_catchup) can come back empty if the signal
        cleared between the suppression consult and this second read —
        no catch-up is scheduled rather than guessing a boundary."""
        from butlers.tools.switchboard.insight.broker import _compute_catchup_deliver_at

        result = _compute_catchup_deliver_at(
            suppression_signal="dnd",
            policy=None,
            context_signal_set_at=None,
            now=_NOW,
        )

        assert result is None

    def test_unknown_suppression_signal_fails_open_to_none(self):
        from butlers.tools.switchboard.insight.broker import _compute_catchup_deliver_at

        result = _compute_catchup_deliver_at(
            suppression_signal=None,
            policy=None,
            context_signal_set_at=None,
            now=_NOW,
        )

        assert result is None
