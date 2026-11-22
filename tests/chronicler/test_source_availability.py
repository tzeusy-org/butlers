"""Retired sources and frozen per-day availability (bu-uhbhzq.2); no database IO."""

from __future__ import annotations

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

    async def fetchval(self, _sql: str, *_args: Any) -> int:
        return self.existing

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.lstrip().startswith("INSERT INTO chronicler.day_source_availability"):
            self.inserts.append(args)
        else:
            self.updates.append(sql)
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
        )
    )

    assert await correct_retired_feeder_dark(conn) == {"corrected": 1, "deleted": 1}
    assert deletes == [date(2026, 9, 1)]
    assert upserts[0]["detail"]["dark_sources"] == ["steam.play_history"]
    assert upserts[0]["detail"]["corrected_by"] == "retired_source"


async def test_the_correction_with_nothing_to_correct_changes_nothing(monkeypatch) -> None:
    conn = SimpleNamespace(
        fetch=AsyncMock(side_effect=[[{"source_name": "x", "retired_at": _RETIRED_AT}], []])
    )
    assert await correct_retired_feeder_dark(conn) == {"corrected": 0, "deleted": 0}
