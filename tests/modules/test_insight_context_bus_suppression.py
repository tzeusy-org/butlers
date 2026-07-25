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
            assert await get_suppressing_context_signal(object()) == "dnd"
