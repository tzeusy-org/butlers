"""Timed insight lane rules that need no database (bu-jv1moa.8 PR-A)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.context_bus import ContextEntry
from butlers.core.insight_premise import probe_premise
from butlers.tools.switchboard.insight import timed

_NOW = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)


def _edge(confirmed_at: Any, probe: str = "departure_still_open") -> dict[str, Any]:
    return probe_premise("home", probe, {"confirmed_at": confirmed_at})


@pytest.mark.parametrize(
    ("premise", "fresh"),
    [
        (_edge((_NOW - timedelta(minutes=1)).isoformat()), True),
        (_edge((_NOW - timedelta(minutes=14, seconds=59)).isoformat()), True),
        (_edge((_NOW - timedelta(minutes=15)).isoformat()), False),
        (_edge((_NOW + timedelta(minutes=1)).isoformat()), False),
        (_edge("2026-08-20T17:59:00"), False),
        (_edge("not a time"), False),
        (_edge(1234), False),
        (_edge((_NOW - timedelta(minutes=1)).isoformat(), probe="arrival_open"), False),
        (None, False),
    ],
    ids=[
        "one-minute",
        "just-under-fifteen",
        "fifteen",
        "future",
        "naive",
        "unparseable",
        "not-a-string",
        "not-a-presence-probe",
        "no-premise",
    ],
)
def test_fresh_presence_edge(premise, fresh):
    assert timed.fresh_presence_edge(premise, now=_NOW) is fresh


def _signal(signal_type: str, age: timedelta) -> ContextEntry:
    return ContextEntry(
        signal_type=signal_type,
        value=None,
        set_by_butler="health",
        set_at=_NOW - age,
        expires_at=_NOW + timedelta(hours=1),
        confidence=1.0,
    )


@pytest.mark.parametrize(
    ("signals", "hold"),
    [
        ([("sleeping", timedelta(hours=30))], "sleeping"),
        ([("dnd", timedelta(hours=30))], "dnd"),
        ([("meeting", timedelta(minutes=10))], "meeting"),
        ([("meeting", timedelta(hours=3))], None),
        ([("meeting", timedelta(minutes=10)), ("dnd", timedelta(hours=30))], "dnd"),
        ([], None),
    ],
    ids=[
        "sleeping-uncapped",
        "dnd-uncapped",
        "meeting-in-hold",
        "meeting-lapsed",
        "dnd-wins",
        "none",
    ],
)
def test_context_hold(signals, hold):
    entries = [_signal(name, age) for name, age in signals]
    assert timed._context_hold(entries, now=_NOW) == hold


def test_timed_lane_ships_dark():
    assert timed.TIMED_INSIGHTS_LIVE is False
    assert timed.TIMED_DAILY_CAP == 6
    assert timed.PRESENCE_EDGE_FRESHNESS == timedelta(minutes=15)


async def test_dark_cycle_touches_nothing():
    pool = MagicMock()
    notify = AsyncMock()
    result = await timed.timed_delivery_cycle(pool, notify_fn=notify, now=_NOW)
    assert result["skipped"] is True and result["reason"] == "timed_lane_dark"
    assert pool.method_calls == [] and not notify.called


async def test_timed_subcycle_job_is_registered_and_wires_the_production_notify_fn():
    from butlers.scheduled_jobs import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _run_switchboard_insight_timed_subcycle_job,
    )

    pool = MagicMock()
    builder = MagicMock(return_value=AsyncMock())
    with (
        patch("butlers.scheduled_jobs._build_switchboard_insight_notify_fn", builder),
        patch.object(timed, "timed_delivery_cycle", AsyncMock(return_value={"ok": 1})) as cycle,
    ):
        result = await _run_switchboard_insight_timed_subcycle_job(pool, None)

    registry = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["switchboard"]
    assert registry["insight_timed_subcycle"] is _run_switchboard_insight_timed_subcycle_job
    builder.assert_called_once_with(pool)
    cycle.assert_awaited_once_with(pool, notify_fn=builder.return_value)
    assert result == {"ok": 1}


def test_no_roster_schedules_the_dark_timed_subcycle():
    from pathlib import Path

    roster = Path(__file__).resolve().parents[2] / "roster"
    assert not [
        path
        for path in roster.glob("*/butler.toml")
        if "insight_timed_subcycle" in path.read_text()
    ]
