"""Tests for HAEventReorderBuffer — time_fired-ordered, bounded submission.

Covers the connector-home-assistant spec "Event ordering" requirement and the
HA_EVENT_QUEUE_MAX bound:
- out-of-order events within the reorder window are submitted in time_fired order
- the buffer is bounded by max_size with a drain-earliest (no-drop) overflow policy
- normal in-order flow is unchanged

[bu-6y25g]
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.home_assistant import HAEventReorderBuffer


class _Clock:
    """Deterministic monotonic clock stand-in for the buffer's time_source."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _event(time_fired: str, entity_id: str = "sensor.temp") -> dict[str, Any]:
    return {
        "event_type": "state_changed",
        "time_fired": time_fired,
        "data": {"entity_id": entity_id},
    }


def _make_collector() -> tuple[list[tuple[str, str]], Any]:
    submitted: list[tuple[str, str]] = []

    async def _submit(event_type: str, event: dict[str, Any], transport: str | None = None) -> None:
        submitted.append((event_type, event.get("time_fired", "")))

    return submitted, _submit


def _make_transport_collector() -> tuple[list[tuple[str, str | None]], Any]:
    """Collector that records the transport tag forwarded by the buffer."""
    submitted: list[tuple[str, str | None]] = []

    async def _submit(event_type: str, event: dict[str, Any], transport: str | None = None) -> None:
        submitted.append((event.get("time_fired", ""), transport))

    return submitted, _submit


_T1 = "2026-03-26T10:00:01.000000+00:00"
_T2 = "2026-03-26T10:00:02.000000+00:00"
_T3 = "2026-03-26T10:00:03.000000+00:00"
_T4 = "2026-03-26T10:00:04.000000+00:00"
_T5 = "2026-03-26T10:00:05.000000+00:00"


@pytest.mark.asyncio
async def test_out_of_order_events_submitted_in_time_fired_order() -> None:
    """Events arriving out of order within the window are released in order."""
    clock = _Clock()
    submitted, submit = _make_collector()
    buf = HAEventReorderBuffer(max_size=100, submit=submit, window_s=0.5, time_source=clock)

    # Arrive out of time_fired order (HA internal batching).
    await buf.add("state_changed", _event(_T3))
    await buf.add("state_changed", _event(_T1))
    await buf.add("state_changed", _event(_T2))

    # Nothing is due before the reorder window elapses.
    await buf.flush_due()
    assert submitted == []

    clock.advance(0.5)
    await buf.flush_due()

    assert [t for _, t in submitted] == [_T1, _T2, _T3]


@pytest.mark.asyncio
async def test_in_order_flow_unchanged() -> None:
    """Normal in-order arrivals are submitted in the same order, once each."""
    clock = _Clock()
    submitted, submit = _make_collector()
    buf = HAEventReorderBuffer(max_size=100, submit=submit, window_s=0.5, time_source=clock)

    for tf in (_T1, _T2, _T3):
        await buf.add("state_changed", _event(tf))

    clock.advance(0.5)
    await buf.flush_due()

    assert [t for _, t in submitted] == [_T1, _T2, _T3]


@pytest.mark.asyncio
async def test_window_holds_then_releases() -> None:
    """An event is held for window_s before becoming eligible for submission."""
    clock = _Clock()
    submitted, submit = _make_collector()
    buf = HAEventReorderBuffer(max_size=100, submit=submit, window_s=0.5, time_source=clock)

    await buf.add("state_changed", _event(_T1))

    await buf.flush_due()
    assert submitted == []  # 0.0s elapsed

    clock.advance(0.4)
    await buf.flush_due()
    assert submitted == []  # still inside the window

    clock.advance(0.1)  # total 0.5s == window
    await buf.flush_due()
    assert [t for _, t in submitted] == [_T1]


@pytest.mark.asyncio
async def test_bound_enforced_overflow_drains_earliest_without_dropping() -> None:
    """The buffer never exceeds max_size; overflow drains the earliest event."""
    clock = _Clock()
    submitted, submit = _make_collector()
    # Large window so nothing ages out: only the bound can force a release.
    buf = HAEventReorderBuffer(max_size=3, submit=submit, window_s=10_000, time_source=clock)

    for tf in (_T1, _T2, _T3, _T4, _T5):
        await buf.add("state_changed", _event(tf))
        assert len(buf) <= 3  # bound is honored at all times

    # Two earliest events were force-flushed (drain-earliest), in order.
    assert [t for _, t in submitted] == [_T1, _T2]

    # Remaining events drain in order; nothing is lost and nothing duplicated.
    await buf.flush_all()
    assert [t for _, t in submitted] == [_T1, _T2, _T3, _T4, _T5]


@pytest.mark.asyncio
async def test_event_type_is_preserved() -> None:
    """The event_type passed to add() is propagated to submit()."""
    clock = _Clock()
    submitted, submit = _make_collector()
    buf = HAEventReorderBuffer(max_size=100, submit=submit, window_s=0.0, time_source=clock)

    await buf.add("automation_triggered", _event(_T1, entity_id="automation.morning"))
    await buf.flush_due()

    assert submitted == [("automation_triggered", _T1)]


@pytest.mark.asyncio
async def test_missing_time_fired_sorts_after_valid_events() -> None:
    """Events with no/unparseable time_fired are released after well-formed ones."""
    clock = _Clock()
    submitted, submit = _make_collector()
    buf = HAEventReorderBuffer(max_size=100, submit=submit, window_s=0.0, time_source=clock)

    bad = {"event_type": "state_changed", "data": {"entity_id": "sensor.x"}}  # no time_fired
    await buf.add("state_changed", bad)
    await buf.add("state_changed", _event(_T1))

    await buf.flush_all()

    # Valid event first; the malformed one is still delivered (downstream skips it).
    assert [t for _, t in submitted] == [_T1, ""]


@pytest.mark.asyncio
async def test_transport_tag_is_threaded_through_to_submit() -> None:
    """The transport passed to add() is forwarded to submit (REST tag preserved).

    Both the WS client (transport=None) and the REST fallback
    (transport="rest_fallback") feed the buffer, so the buffer must carry each
    event's transport through to the shared dispatcher rather than dropping it.
    """
    clock = _Clock()
    submitted, submit = _make_transport_collector()
    buf = HAEventReorderBuffer(max_size=100, submit=submit, window_s=0.5, time_source=clock)

    await buf.add("state_changed", _event(_T2), "rest_fallback")
    await buf.add("state_changed", _event(_T1))  # WS event: transport defaults to None

    await buf.flush_all()

    # Released in time_fired order, each retaining its own transport tag.
    assert submitted == [(_T1, None), (_T2, "rest_fallback")]
