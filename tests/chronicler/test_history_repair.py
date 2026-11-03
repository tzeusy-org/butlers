"""Unit controls for finite historical quantitative repair."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.history_repair import replay_epoch, run_rebuild
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
