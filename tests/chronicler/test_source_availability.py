"""Retired sources and frozen per-day availability (bu-uhbhzq.2); no database IO."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler import flags
from butlers.chronicler.contracts import INITIAL_SOURCES
from butlers.chronicler.flags import (
    AVAILABLE,
    CONFIGURED_ABSENT,
    DARK,
    FLAG_FEEDER_DARK,
    RETIRED,
    classify_source_availability,
    correct_retired_feeder_dark,
    evaluate_and_write_daily_flags,
    freeze_day_availability,
    load_day_availability,
)
from butlers.chronicler.models import Compatibility, ProjectionCheckpoint, SourceAdapterState

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
_RETIRED_AT = date(2026, 8, 13)


def _state(**overrides: Any) -> SourceAdapterState:
    values: dict[str, Any] = {
        "source_name": "health.meals",
        "chronicler_compatibility": Compatibility.SUPPORTED,
        "optional_schema": True,
        "active": False,
        "inactive_reason": "surface absent",
        "retired_at": _RETIRED_AT,
    }
    values.update(overrides)
    return SourceAdapterState(**values)


def _projected_once() -> ProjectionCheckpoint:
    return ProjectionCheckpoint(
        source_name="health.meals", last_success_at=datetime(2026, 8, 13, tzinfo=UTC)
    )


# -- classify_source_availability ------------------------------------------------------------


def test_a_retired_source_is_retired_on_and_after_its_date() -> None:
    for on_date in (_RETIRED_AT, _RETIRED_AT + timedelta(days=100)):
        assert (
            classify_source_availability(
                _state(), _projected_once(), on_date=on_date, cron_minutes=30, now=_NOW
            )
            == RETIRED
        )


def test_the_same_source_is_dark_before_it_retired() -> None:
    assert (
        classify_source_availability(
            _state(),
            _projected_once(),
            on_date=_RETIRED_AT - timedelta(days=1),
            cron_minutes=30,
            now=_NOW,
        )
        == DARK
    )


def test_a_source_that_projected_then_went_inactive_is_dark_without_a_retirement() -> None:
    assert (
        classify_source_availability(
            _state(retired_at=None),
            _projected_once(),
            on_date=date(2026, 11, 1),
            cron_minutes=30,
            now=_NOW,
        )
        == DARK
    )


def test_a_never_projected_optional_source_is_configured_absent() -> None:
    assert (
        classify_source_availability(
            _state(retired_at=None),
            None,
            on_date=date(2026, 11, 1),
            cron_minutes=30,
            now=_NOW,
        )
        == CONFIGURED_ABSENT
    )


def test_a_healthy_source_is_available() -> None:
    fresh = ProjectionCheckpoint(source_name="x", last_success_at=_NOW - timedelta(minutes=1))
    assert (
        classify_source_availability(
            _state(active=True, retired_at=None, inactive_reason=None),
            fresh,
            on_date=date(2026, 11, 19),
            cron_minutes=30,
            now=_NOW,
        )
        == AVAILABLE
    )


def test_health_meals_is_declared_retired_in_git() -> None:
    meals = next(s for s in INITIAL_SOURCES if s.source_name == "health.meals")
    assert meals.retired_at == _RETIRED_AT
    assert meals.retired_reason


# -- evaluate_and_write_daily_flags from a frozen record -------------------------------------


class _Writes:
    def __init__(self) -> None:
        self.upserted: list[dict[str, Any]] = []
        self.deleted: list[str] = []


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> _Writes:
    recorded = _Writes()

    async def upsert(_pool: Any, **kwargs: Any) -> None:
        recorded.upserted.append(kwargs)

    async def delete(_pool: Any, **kwargs: Any) -> None:
        recorded.deleted.append(kwargs["flag_type"])

    monkeypatch.setattr(flags, "list_daily_rollups", AsyncMock(return_value=[]))
    monkeypatch.setattr(flags, "list_routines", AsyncMock(return_value=[]))
    monkeypatch.setattr(flags, "upsert_daily_rollup_flag", upsert)
    monkeypatch.setattr(flags, "delete_daily_rollup_flag", delete)
    return recorded


def _all(state: str, **overrides: str) -> dict[str, str]:
    return {name: overrides.get(name, state) for name in flags.SOURCE_CRON_MINUTES}


async def test_a_retired_source_raises_no_feeder_dark_and_is_listed(writes: _Writes) -> None:
    result = await evaluate_and_write_daily_flags(
        AsyncMock(),
        local_date=date(2026, 11, 19),
        timezone="UTC",
        now=_NOW,
        availability=_all(AVAILABLE, **{"health.meals": RETIRED}),
    )
    assert result["flags"][FLAG_FEEDER_DARK] is False
    assert result["retired_sources"] == ["health.meals"]
    assert FLAG_FEEDER_DARK in writes.deleted
    assert FLAG_FEEDER_DARK not in {u["flag_type"] for u in writes.upserted}


async def test_the_frozen_record_wins_over_current_state(writes: _Writes, monkeypatch) -> None:
    """A source dark on that day stays dark even though it is healthy now."""
    observe = AsyncMock(side_effect=AssertionError("must not read current state"))
    monkeypatch.setattr(flags, "observe_source_availability", observe)

    result = await evaluate_and_write_daily_flags(
        AsyncMock(),
        local_date=date(2026, 11, 12),
        timezone="UTC",
        now=_NOW,
        availability=_all(AVAILABLE, **{"steam.play_history": DARK}),
    )

    assert result["flags"][FLAG_FEEDER_DARK] is True
    upserted = next(u for u in writes.upserted if u["flag_type"] == FLAG_FEEDER_DARK)
    assert upserted["detail"]["dark_sources"] == ["steam.play_history"]


async def test_a_source_dark_only_now_does_not_touch_a_past_day(writes: _Writes) -> None:
    result = await evaluate_and_write_daily_flags(
        AsyncMock(),
        local_date=date(2026, 11, 12),
        timezone="UTC",
        now=_NOW,
        availability=_all(AVAILABLE),
    )
    assert result["flags"][FLAG_FEEDER_DARK] is False


# -- freeze_day_availability / load_day_availability -----------------------------------------


class _Conn:
    def __init__(self, existing: int = 0) -> None:
        self.existing = existing
        self.inserts: list[tuple[Any, ...]] = []
        self.updates: list[str] = []
        self.update_args: list[tuple[Any, ...]] = []

    async def fetchval(self, _sql: str, *_args: Any) -> int:
        return self.existing

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.lstrip().startswith("INSERT INTO chronicler.day_source_availability"):
            self.inserts.append(args)
        else:
            self.updates.append(sql)
            self.update_args.append(args)
        return "OK"


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "source_name": name,
            "state": AVAILABLE,
            "last_success_at": None,
            "watermark": None,
            "retired_at": None,
        }
        for name in flags.SOURCE_CRON_MINUTES
    ]
    monkeypatch.setattr(flags, "observe_source_availability", AsyncMock(return_value=rows))


async def test_the_first_run_after_the_day_closes_writes_one_row_per_source(observed) -> None:
    conn = _Conn()
    status = await freeze_day_availability(
        conn,  # type: ignore[arg-type]
        local_date=date(2026, 11, 19),
        timezone="UTC",
        now=datetime(2026, 11, 20, 0, 30, tzinfo=UTC),
    )
    assert status == "frozen"
    assert len(conn.inserts) == len(flags.SOURCE_CRON_MINUTES)
    assert {row[2] for row in conn.inserts} == set(flags.SOURCE_CRON_MINUTES)
    assert any("'frozen'" in sql for sql in conn.updates)


async def test_freezing_one_timezone_never_marks_another_timezones_repair_day(observed) -> None:
    conn = _Conn()
    await freeze_day_availability(
        conn,  # type: ignore[arg-type]
        local_date=date(2026, 11, 19),
        timezone="Asia/Singapore",
        now=datetime(2026, 11, 20, 12, 30, tzinfo=UTC),
    )
    (sql,) = [q for q in conn.updates if "daily_repair_days" in q]
    assert "timezone = $2" in sql
    assert conn.update_args[-1] == (date(2026, 11, 19), "Asia/Singapore")


async def test_a_day_with_a_snapshot_is_never_written_again(observed) -> None:
    conn = _Conn(existing=len(flags.SOURCE_CRON_MINUTES))
    status = await freeze_day_availability(
        conn,  # type: ignore[arg-type]
        local_date=date(2026, 11, 19),
        timezone="UTC",
        now=datetime(2026, 11, 20, 1, tzinfo=UTC),
    )
    assert status == "exists"
    assert conn.inserts == []


async def test_a_day_past_the_settle_horizon_is_never_snapshotted(observed) -> None:
    conn = _Conn()
    status = await freeze_day_availability(
        conn,  # type: ignore[arg-type]
        local_date=date(2026, 11, 10),
        timezone="UTC",
        now=datetime(2026, 11, 20, 1, tzinfo=UTC),
    )
    assert status == "past_horizon"
    assert conn.inserts == []


async def test_a_day_that_has_not_closed_is_not_snapshotted(observed) -> None:
    conn = _Conn()
    status = await freeze_day_availability(
        conn,  # type: ignore[arg-type]
        local_date=date(2026, 11, 20),
        timezone="UTC",
        now=datetime(2026, 11, 20, 12, tzinfo=UTC),
    )
    assert status == "not_closed"
    assert conn.inserts == []


async def test_load_returns_none_without_a_snapshot_and_the_mapping_with_one() -> None:
    conn = SimpleNamespace(fetch=AsyncMock(return_value=[]))
    assert await load_day_availability(conn, local_date=date(2026, 11, 1), timezone="UTC") is None
    conn.fetch.return_value = [{"source_name": "a", "state": DARK}]
    assert await load_day_availability(conn, local_date=date(2026, 11, 1), timezone="UTC") == {
        "a": DARK
    }


# -- the one-shot correction -------------------------------------------------------------------


async def test_the_correction_removes_only_the_retired_source(monkeypatch) -> None:
    upserts: list[dict[str, Any]] = []
    deletes: list[date] = []

    async def upsert(_conn: Any, **kwargs: Any) -> None:
        upserts.append(kwargs)

    async def delete(_conn: Any, **kwargs: Any) -> None:
        deletes.append(kwargs["local_date"])

    monkeypatch.setattr(flags, "upsert_daily_rollup_flag", upsert)
    monkeypatch.setattr(flags, "delete_daily_rollup_flag", delete)

    only_meals = {
        "local_date": date(2026, 9, 1),
        "severity": "warning",
        "detail": {"dark_sources": ["health.meals"]},
    }
    mixed = {
        "local_date": date(2026, 9, 2),
        "severity": "warning",
        "detail": '{"dark_sources": ["health.meals", "steam.play_history"]}',
    }
    conn = SimpleNamespace(
        fetch=AsyncMock(
            side_effect=[
                [{"source_name": "health.meals", "retired_at": _RETIRED_AT}],
                [only_meals, mixed],
            ]
        ),
        execute=AsyncMock(),
    )

    assert await correct_retired_feeder_dark(conn) == {"corrected": 1, "deleted": 1}
    assert deletes == [date(2026, 9, 1)]
    assert upserts[0]["detail"]["dark_sources"] == ["steam.play_history"]
    assert upserts[0]["detail"]["corrected_by"] == "retired_source"
    # Every correction, including the deleted flag, is logged first with the original detail.
    logged = [
        (c.args[1], c.args[2], c.args[3], c.args[4], json.loads(c.args[6]))
        for c in conn.execute.await_args_list
        if "retired_flag_corrections" in c.args[0]
    ]
    assert logged == [
        (
            date(2026, 9, 1),
            FLAG_FEEDER_DARK,
            "health.meals",
            "flag_deleted",
            {"dark_sources": ["health.meals"]},
        ),
        (
            date(2026, 9, 2),
            FLAG_FEEDER_DARK,
            "health.meals",
            "source_removed",
            {"dark_sources": ["health.meals", "steam.play_history"]},
        ),
    ]


async def test_the_correction_with_nothing_to_correct_changes_nothing(monkeypatch) -> None:
    conn = SimpleNamespace(
        execute=AsyncMock(),
        fetch=AsyncMock(side_effect=[[{"source_name": "x", "retired_at": _RETIRED_AT}], []]),
    )
    assert await correct_retired_feeder_dark(conn) == {"corrected": 0, "deleted": 0}


# ---------------------------------------------------------------------------
# Uncaptured lanes and streaks (slices 4-5)
# ---------------------------------------------------------------------------


def _all(state: str, **overrides: str) -> dict[str, str]:
    base = {name: state for name in flags.SOURCE_CRON_MINUTES}
    base.update(overrides)
    return base


def test_uncaptured_lanes_requires_every_observed_source_unavailable() -> None:
    from butlers.chronicler.flags import uncaptured_lanes

    availability = _all(AVAILABLE, **{"health.meals": RETIRED})
    result = uncaptured_lanes(availability, retired_since={"health.meals": date(2026, 8, 13)})
    assert result == [{"lane": "eat", "state": "not_captured", "since": "2026-08-13"}]


def test_uncaptured_lanes_marks_dark_over_retired() -> None:
    from butlers.chronicler.flags import uncaptured_lanes

    availability = _all(AVAILABLE, **{"health.meals": DARK})
    assert uncaptured_lanes(availability)[0]["state"] == "dark"
    assert uncaptured_lanes(_all(AVAILABLE)) == []


class _AvailabilityConn:
    """Fake conn: ``days`` maps local_date -> {source: state}; a missing day was never frozen."""

    def __init__(self, days: dict[date, dict[str, str]]) -> None:
        self.days = days

    async def fetch(self, query: str, local_date: date, _tz: str) -> list[dict[str, Any]]:
        if "retired_at" in query:
            return []
        return [{"source_name": k, "state": v} for k, v in self.days.get(local_date, {}).items()]


async def test_streak_counts_consecutive_uncaptured_days_and_stops_at_a_gap() -> None:
    from butlers.chronicler.flags import load_uncaptured_with_streaks

    today = date(2026, 11, 20)
    dark = _all(AVAILABLE, **{"health.meals": DARK})
    days = {
        today: dark,
        today - timedelta(days=1): dark,
        today - timedelta(days=2): dark,
        today - timedelta(days=3): _all(AVAILABLE),
        today - timedelta(days=4): dark,
    }
    lanes, streaks = await load_uncaptured_with_streaks(
        _AvailabilityConn(days), local_date=today, timezone="Asia/Singapore"
    )
    assert [e["lane"] for e in lanes] == ["eat"]
    assert streaks == {"eat": 3}


async def test_unsnapshotted_day_has_no_uncaptured_lanes() -> None:
    from butlers.chronicler.flags import load_uncaptured_with_streaks

    assert await load_uncaptured_with_streaks(
        _AvailabilityConn({}), local_date=date(2026, 11, 20), timezone="Asia/Singapore"
    ) == ([], {})


async def test_a_retired_lane_stops_at_its_second_day_without_walking_back() -> None:
    from butlers.chronicler.flags import load_uncaptured_with_streaks

    today = date(2026, 11, 20)
    retired = _all(AVAILABLE, **{"health.meals": RETIRED})
    days = {today - timedelta(days=n): retired for n in range(0, 50)}
    conn = _AvailabilityConn(days)
    reads: list[date] = []
    original = conn.fetch

    async def counting(query: str, local_date: date, tz: str) -> list[dict[str, Any]]:
        if "retired_at" not in query:
            reads.append(local_date)
        return await original(query, local_date, tz)

    conn.fetch = counting  # type: ignore[method-assign]
    _lanes, streaks = await load_uncaptured_with_streaks(
        conn, local_date=today, timezone="Asia/Singapore"
    )
    assert streaks == {"eat": 2}
    assert len(reads) == 2


async def test_a_dark_lane_longer_than_the_lookback_reads_as_unknown_phase(monkeypatch) -> None:
    from butlers.chronicler import flags as flags_module

    monkeypatch.setattr(flags_module, "MAX_STREAK_LOOKBACK_DAYS", 5)
    today = date(2026, 11, 20)
    dark = _all(AVAILABLE, **{"health.meals": DARK})
    days = {today - timedelta(days=n): dark for n in range(0, 30)}
    _lanes, streaks = await flags_module.load_uncaptured_with_streaks(
        _AvailabilityConn(days), local_date=today, timezone="Asia/Singapore"
    )
    assert streaks == {"eat": 0}
