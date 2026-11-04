"""Unit controls for finite historical quantitative repair."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.history_repair import (
    FactWriteToken,
    RepairDayVersion,
    _require_fact_write_ready,
    capture_fact_write_token,
    make_fact_write_guard,
    replay_epoch,
    run_rebuild,
)
from butlers.chronicler.rollups import _local_day_bounds_utc

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_repair_local_day_uses_both_midnights_across_dst(day, hours):
    start, end = _local_day_bounds_utc(day, ZoneInfo("America/New_York"))
    assert (end - start).total_seconds() == hours * 3600
    assert start.tzinfo == end.tzinfo == UTC


async def test_unacknowledged_replay_epoch_survives_advanced_watermark():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"replay_epoch": 3, "replay_ack": 2}
    assert await replay_epoch(conn) == 3
    conn.fetchrow.return_value = {"replay_epoch": 3, "replay_ack": 3}
    assert await replay_epoch(conn) is None
    conn.fetchrow.return_value = None
    with pytest.raises(RuntimeError, match="state missing"):
        await replay_epoch(conn)


async def test_zone_mismatch_blocks_before_any_connection_or_writes():
    pool = AsyncMock()
    result = await run_rebuild(
        pool,
        timezone="Asia/Singapore",
        situation_timezone="UTC",
        now=datetime(2026, 11, 4, tzinfo=UTC),
    )
    assert result["status"] == "blocked"
    pool.acquire.assert_not_called()


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _RepairStateConnection:
    def __init__(self, control, days):
        self.control = control
        self.days = days
        self.shared_lock_calls = 0

    def transaction(self):
        return _AsyncContext(self)

    async def fetchval(self, query, *_args):
        if "pg_try_advisory_xact_lock_shared" in query:
            self.shared_lock_calls += 1
            return True
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def fetchrow(self, query, *_args):
        if "daily_repair_control" in query:
            return self.control
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query, *_args):
        if "daily_repair_days" in query:
            return self.days
        if "daily_repair_retention_spans" in query or "source.retention_days" in query:
            return []
        raise AssertionError(f"unexpected fetch query: {query}")


class _RepairStatePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


def _repair_control(**overrides):
    return {
        "replay_epoch": 7,
        "replay_ack": 7,
        "timezone": "UTC",
        "closed_through": date(2026, 7, 20),
        "blocked_reason": None,
        **overrides,
    }


async def test_fact_write_token_captures_the_full_window_including_absent_rows():
    target = date(2026, 7, 20)
    day = target - timedelta(days=1)
    connection = _RepairStateConnection(
        _repair_control(),
        [
            {
                "local_date": day,
                "dirty_generation": 4,
                "built_generation": 4,
                "totals_pending": False,
                "outlier_pending": False,
                "facts_pending": False,
            }
        ],
    )

    token = await capture_fact_write_token(
        _RepairStatePool(connection),
        day_date=target,
        timezone="UTC",
        baseline_lookback_days=28,
    )

    assert token.start_date == target - timedelta(days=28)
    assert token.end_date == target
    assert len(token.days) == 29
    assert token.days[0] == (target - timedelta(days=28), None)
    assert token.days[27][0] == day
    assert token.days[27][1].dirty_generation == 4
    assert token.days[-1] == (target, None)
    assert connection.shared_lock_calls == 1


async def test_fact_write_guard_rejects_generation_change_after_embedding():
    target = date(2026, 7, 20)
    day = target - timedelta(days=1)
    original = {
        "local_date": day,
        "dirty_generation": 4,
        "built_generation": 4,
        "totals_pending": False,
        "outlier_pending": False,
        "facts_pending": False,
    }
    connection = _RepairStateConnection(
        _repair_control(),
        [
            {
                **original,
                "dirty_generation": 5,
                "totals_pending": True,
                "facts_pending": True,
            }
        ],
    )
    token = await capture_fact_write_token(
        _RepairStatePool(_RepairStateConnection(_repair_control(), [original])),
        day_date=target,
        timezone="UTC",
        baseline_lookback_days=28,
    )

    with pytest.raises(RuntimeError, match="repair state changed"):
        await make_fact_write_guard(token)(connection)
    assert connection.shared_lock_calls == 1


async def test_fact_write_token_rejects_a_window_beyond_invalidation_horizon():
    with pytest.raises(ValueError, match="28-day invalidation horizon"):
        await capture_fact_write_token(
            _RepairStatePool(_RepairStateConnection(_repair_control(), [])),
            day_date=date(2026, 7, 20),
            timezone="UTC",
            baseline_lookback_days=29,
        )


def test_fact_write_ready_allows_fact_only_generation_after_inventory_reset():
    day = date(2026, 7, 20)
    token = FactWriteToken(
        day_date=day,
        timezone="UTC",
        baseline_lookback_days=28,
        start_date=day - timedelta(days=28),
        end_date=day,
        replay_epoch=7,
        replay_ack=7,
        control_timezone="UTC",
        closed_through=day,
        blocked_reason=None,
        days=(
            (
                day - timedelta(days=1),
                RepairDayVersion(
                    dirty_generation=5,
                    built_generation=4,
                    totals_pending=False,
                    outlier_pending=False,
                    facts_pending=False,
                ),
            ),
        ),
    )

    _require_fact_write_ready(token)


def test_fact_write_ready_rejects_overlapping_retention_cursor():
    day = date(2026, 7, 20)
    token = FactWriteToken(
        day_date=day,
        timezone="UTC",
        baseline_lookback_days=28,
        start_date=day - timedelta(days=28),
        end_date=day,
        replay_epoch=7,
        replay_ack=7,
        control_timezone="UTC",
        closed_through=day,
        blocked_reason=None,
        days=(),
        pending_retention=(("00000000-0000-0000-0000-000000000001", day - timedelta(days=2), day),),
    )

    with pytest.raises(RuntimeError, match="retention repair is pending"):
        _require_fact_write_ready(token)


def test_fact_write_ready_still_rejects_unbuilt_numeric_generation():
    day = date(2026, 7, 20)
    token = FactWriteToken(
        day_date=day,
        timezone="UTC",
        baseline_lookback_days=28,
        start_date=day - timedelta(days=28),
        end_date=day,
        replay_epoch=7,
        replay_ack=7,
        control_timezone="UTC",
        closed_through=day,
        blocked_reason=None,
        days=(
            (
                day - timedelta(days=1),
                RepairDayVersion(
                    dirty_generation=5,
                    built_generation=4,
                    totals_pending=True,
                    outlier_pending=False,
                    facts_pending=False,
                ),
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="generation is pending"):
        _require_fact_write_ready(token)
