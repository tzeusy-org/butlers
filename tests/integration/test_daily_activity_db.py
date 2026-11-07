"""Real-Postgres coverage for GET /api/butlers/{name}/analytics/daily-activity (bu-s11n0s.5.1).

tests/api/test_daily_activity.py stubs ``pool.fetch``, so it proves the route's
mapping but not the SQL window. The dashboard keys daily bars by UTC
``YYYY-MM-DD`` and draws any day the response omits as "not reported", so the
query must return exactly ``window_days`` dense UTC days, a quiet day being a
measured 0 row, whatever the connection's TimeZone.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import uuid4

import pytest

from butlers.api.routers.sessions import get_butler_daily_activity
from butlers.testing.db_manager import db_manager

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


class _NonUtcPool:
    """A real pool whose queries run under a non-UTC session TimeZone (UTC+8)."""

    def __init__(self, pool: Any):
        self._pool = pool

    async def fetch(self, sql: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            await conn.execute("SET TIME ZONE 'Asia/Singapore'")
            try:
                return await conn.fetch(sql, *args)
            finally:
                await conn.execute("RESET TIME ZONE")


async def _seed(pool: Any, started_at: datetime) -> None:
    session_id = uuid4()
    await pool.execute(
        "INSERT INTO sessions (id, prompt, trigger_source, request_id, started_at)"
        " VALUES ($1, 'p', 'schedule', $2, $3)",
        session_id,
        f"req-{session_id}",
        started_at,
    )


async def test_daily_activity_is_a_dense_utc_window(db) -> None:
    pool = await db("core", schema="general")
    route_db = db_manager({"general": _NonUtcPool(pool)})

    # Empty table: still one measured-zero row per day of the window.
    empty = await get_butler_daily_activity("general", window_days=7, db=route_db)
    today = (await pool.fetchval("SELECT now()")).astimezone(UTC).date()
    assert [b.date for b in empty.data.buckets] == [today - timedelta(days=6 - i) for i in range(7)]
    assert [b.sessions_count for b in empty.data.buckets] == [0] * 7

    midnight = datetime.combine(today, time(), tzinfo=UTC)
    await _seed(pool, midnight - timedelta(microseconds=1))  # yesterday 23:59:59.999999 UTC
    await _seed(pool, midnight)  # today 00:00 UTC
    await _seed(pool, midnight - timedelta(days=2, hours=1))  # 3 days ago, 23:00 UTC
    await _seed(pool, midnight - timedelta(days=6, seconds=1))  # one day before the window

    seeded = await get_butler_daily_activity("general", window_days=7, db=route_db)
    # UTC day boundaries, not the session's UTC+8 ones; the day between them
    # is a quiet 0 row, and the pre-window session is dropped.
    assert [b.sessions_count for b in seeded.data.buckets] == [0, 0, 0, 1, 0, 1, 1]

    month = await get_butler_daily_activity("general", window_days=30, db=route_db)
    assert len(month.data.buckets) == 30
    assert month.data.buckets[-1].date == today
    assert sum(b.sessions_count for b in month.data.buckets) == 4
