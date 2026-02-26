"""Unit tests for education butler analytics tools.

All tests mock the asyncpg pool/connection objects — no live database required.

Coverage:
- analytics_compute_snapshot: all 14 metric fields computed correctly
- retention rates only count response_type='review' responses
- velocity averages over last 4 weekly buckets
- upsert on (mind_map_id, snapshot_date) conflict
- struggling_nodes requires 5+ review responses per node, avg quality < 2.5
- analytics_compute_all: active map discovery, feedback loop trigger
- analytics_get_snapshot: latest and specific-date retrieval
- analytics_get_trend: ascending order within date range
- analytics_get_cross_topic: multi-map stats, strongest/weakest, portfolio
- feedback loop: triggers on struggling>=3 or retention_7d<0.60
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers: mock asyncpg pool / connection builder
# ---------------------------------------------------------------------------


class _MockRecord:
    """Minimal asyncpg.Record-like object backed by a dict."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def _make_row(data: dict[str, Any]) -> _MockRecord:
    return _MockRecord(data)


def _make_pool(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg.Pool (direct pool calls)."""
    pool = AsyncMock()

    if fetchrow_returns is not None:
        pool.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        pool.fetchrow = AsyncMock(return_value=None)

    if fetch_returns is not None:
        pool.fetch = AsyncMock(side_effect=list(fetch_returns))
    else:
        pool.fetch = AsyncMock(return_value=[])

    if fetchval_returns is not None:
        pool.fetchval = AsyncMock(side_effect=list(fetchval_returns))
    else:
        pool.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        pool.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        pool.execute = AsyncMock(return_value="UPDATE 1")

    return pool


def _make_conn(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg connection."""
    conn = AsyncMock()

    if fetchrow_returns is not None:
        conn.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        conn.fetchrow = AsyncMock(return_value=None)

    if fetch_returns is not None:
        conn.fetch = AsyncMock(side_effect=list(fetch_returns))
    else:
        conn.fetch = AsyncMock(return_value=[])

    if fetchval_returns is not None:
        conn.fetchval = AsyncMock(side_effect=list(fetchval_returns))
    else:
        conn.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        conn.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        conn.execute = AsyncMock(return_value="UPDATE 1")

    return conn


def _make_pool_with_conn(conn: AsyncMock) -> MagicMock:
    """Build a pool whose acquire() context manager yields the given conn."""
    pool = MagicMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    # Also set direct fetch/fetchrow for functions that use pool directly
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="UPDATE 1")

    return pool


# ---------------------------------------------------------------------------
# Snapshot row builder helpers
# ---------------------------------------------------------------------------


def _snapshot_row(
    mind_map_id: str | None = None,
    snapshot_date: date | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a minimal analytics_snapshots row dict."""
    return {
        "id": str(uuid.uuid4()),
        "mind_map_id": mind_map_id or str(uuid.uuid4()),
        "snapshot_date": snapshot_date or date.today(),
        "metrics": metrics or {},
        "created_at": "2026-02-26T00:00:00+00:00",
    }


def _node_row(
    node_id: str | None = None,
    mastery_status: str = "unseen",
    ease_factor: float = 2.5,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a minimal mind_map_nodes row dict for analytics tests."""
    return {
        "id": node_id or str(uuid.uuid4()),
        "mastery_status": mastery_status,
        "ease_factor": ease_factor,
        "updated_at": updated_at or datetime.now(tz=UTC),
    }


# ---------------------------------------------------------------------------
# Helpers: build a fully-wired conn for analytics_compute_snapshot
# ---------------------------------------------------------------------------


def _build_snapshot_conn(
    *,
    node_rows: list[dict[str, Any]] | None = None,
    retention_rows_7d: dict[str, int] | None = None,  # {"total_review": N, "passed_review": M}
    retention_rows_30d: dict[str, int] | None = None,
    struggling_rows: list[dict[str, Any]] | None = None,
    strongest_subtree_row: dict[str, Any] | None = None,
    total_quiz_responses: int = 0,
    avg_quality_raw: float | None = None,
    sessions_this_period: int = 0,
    time_hour_rows: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    """Build a mock connection wired for each query in analytics_compute_snapshot.

    Velocity is now computed in-memory from node_rows (no separate DB fetch).

    The function calls these methods in this order:
      1. conn.fetch (node_rows)
      2. conn.fetchrow (retention 7d)
      3. conn.fetchrow (retention 30d)
      4. conn.fetch (struggling_nodes CTE)
      5. conn.fetchrow (strongest_subtree)
      6. conn.fetchval (total_quiz_responses)
      7. conn.fetchval (avg_quality_raw)
      8. conn.fetchval (sessions_this_period)
      9. conn.fetch (time_hour_rows)
      10. conn.execute (upsert)
    """
    conn = AsyncMock()

    # --- fetch calls ---
    fetch_queue = [
        # 1. node_rows
        [_make_row(r) for r in (node_rows or [])],
        # 4. struggling_nodes
        [_make_row(r) for r in (struggling_rows or [])],
        # 9. time_hour_rows
        [_make_row(r) for r in (time_hour_rows or [])],
    ]
    conn.fetch = AsyncMock(side_effect=list(fetch_queue))

    # --- fetchrow calls ---
    def _retention_row(d: dict[str, int] | None) -> _MockRecord | None:
        if d is None:
            return _make_row({"total_review": 0, "passed_review": 0})
        return _make_row(d)

    fetchrow_queue = [
        # 2. retention 7d
        _retention_row(retention_rows_7d),
        # 3. retention 30d
        _retention_row(retention_rows_30d),
        # 5. strongest subtree
        _make_row(strongest_subtree_row) if strongest_subtree_row else None,
    ]
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_queue))

    # --- fetchval calls ---
    fetchval_queue = [
        # 6. total_quiz_responses
        total_quiz_responses,
        # 7. avg_quality_raw
        avg_quality_raw,
        # 8. sessions_this_period
        sessions_this_period,
    ]
    conn.fetchval = AsyncMock(side_effect=list(fetchval_queue))

    # --- execute (upsert) ---
    conn.execute = AsyncMock(return_value="INSERT 1")

    return conn


# ---------------------------------------------------------------------------
# Tests: analytics_compute_snapshot — metric correctness
# ---------------------------------------------------------------------------


class TestAnalyticsComputeSnapshot:
    """Tests for analytics_compute_snapshot metric computation."""

    async def test_basic_metrics_correct(self) -> None:
        """Snapshot with 2 mastered of 4 total nodes computes correct mastery_pct."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [
            _node_row(mastery_status="mastered", ease_factor=2.5),
            _node_row(mastery_status="mastered", ease_factor=3.0),
            _node_row(mastery_status="learning", ease_factor=2.0),
            _node_row(mastery_status="unseen", ease_factor=2.5),
        ]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            total_quiz_responses=10,
            avg_quality_raw=3.5,
            sessions_this_period=5,
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["total_nodes"] == 4
        assert metrics["mastered_nodes"] == 2
        assert metrics["mastery_pct"] == 0.5
        assert metrics["total_quiz_responses"] == 10
        assert metrics["avg_quality_score"] == 3.5
        assert metrics["sessions_this_period"] == 5

    async def test_mastery_pct_zero_when_no_nodes(self) -> None:
        """mastery_pct is 0.0 when mind map has no nodes, and related metrics are sensible."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())

        conn = _build_snapshot_conn(node_rows=[])
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["total_nodes"] == 0
        assert metrics["mastered_nodes"] == 0
        assert metrics["mastery_pct"] == 0.0
        assert metrics["avg_ease_factor"] == 0.0
        assert metrics["estimated_completion_days"] is None

    async def test_avg_ease_factor_computed(self) -> None:
        """avg_ease_factor is mean of all node ease_factors."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [
            _node_row(mastery_status="unseen", ease_factor=2.0),
            _node_row(mastery_status="unseen", ease_factor=3.0),
        ]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["avg_ease_factor"] == 2.5

    async def test_avg_quality_score_rounded_1_decimal(self) -> None:
        """avg_quality_score is rounded to 1 decimal place."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            avg_quality_raw=3.666,
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["avg_quality_score"] == 3.7

    async def test_avg_quality_score_null_when_no_responses(self) -> None:
        """avg_quality_score is None when there are no quiz responses."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            avg_quality_raw=None,
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["avg_quality_score"] is None

    async def test_mastery_pct_rounded_2_decimals(self) -> None:
        """mastery_pct is rounded to 2 decimal places (1/3 → 0.33)."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [
            _node_row(mastery_status="mastered"),
            _node_row(mastery_status="learning"),
            _node_row(mastery_status="unseen"),
        ]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["mastery_pct"] == 0.33

    async def test_upsert_is_called(self) -> None:
        """The upsert INSERT ... ON CONFLICT execute() is called exactly once."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        # execute() called once for the upsert
        assert conn.execute.call_count == 1
        call_sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in call_sql
        assert "DO UPDATE" in call_sql

    async def test_snapshot_date_defaults_to_today(self) -> None:
        """When snapshot_date is None, upsert uses today's date."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        await analytics_compute_snapshot(pool, map_id)

        # execute was called — just verify it ran without error
        assert conn.execute.call_count == 1


# ---------------------------------------------------------------------------
# Tests: retention rate (review-only)
# ---------------------------------------------------------------------------


class TestRetentionRates:
    """Retention rates must use only response_type='review' responses."""

    async def test_retention_7d_computed_correctly(self) -> None:
        """8 review responses, 6 quality>=3 → retention_rate_7d = 0.75."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            retention_rows_7d={"total_review": 8, "passed_review": 6},
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["retention_rate_7d"] == 0.75

    async def test_retention_30d_computed_correctly(self) -> None:
        """10 review responses, 9 quality>=3 → retention_rate_30d = 0.90."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            retention_rows_30d={"total_review": 10, "passed_review": 9},
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["retention_rate_30d"] == 0.9

    async def test_retention_null_when_no_review_responses(self) -> None:
        """retention_rate_7d is None when there are no review responses in the window."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            retention_rows_7d={"total_review": 0, "passed_review": 0},
            retention_rows_30d={"total_review": 0, "passed_review": 0},
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["retention_rate_7d"] is None
        assert metrics["retention_rate_30d"] is None


# ---------------------------------------------------------------------------
# Tests: velocity computation
# ---------------------------------------------------------------------------


class TestVelocity:
    """Velocity averages mastered nodes per week over last 4 weeks."""

    async def test_velocity_zero_when_no_mastered_nodes(self) -> None:
        """velocity_nodes_per_week is 0.0 when no nodes were mastered recently."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        # Velocity is computed in-memory from node_rows; a "learning" node has no mastery date
        nodes = [_node_row(mastery_status="learning")]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["velocity_nodes_per_week"] == 0.0

    async def test_velocity_averages_4_weeks(self) -> None:
        """4 nodes mastered in week 0 (relative to snapshot_date) → velocity = 4/4 = 1.0."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        # snapshot_date = 2026-02-26; reference point = 2026-02-27 00:00 UTC
        # Week 0 = 0-7 days before reference = 2026-02-20 to 2026-02-26
        snap_date = date(2026, 2, 26)
        reference = datetime(2026, 2, 27, tzinfo=UTC)
        map_id = str(uuid.uuid4())
        nodes = [
            # 4 mastered nodes updated 3 days before reference (in week 0)
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=3)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=3)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=3)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=3)),
        ]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, snap_date)

        # 4 in week 0, 0 in weeks 1-3 → average = 4/4 = 1.0
        assert metrics["velocity_nodes_per_week"] == 1.0

    async def test_velocity_spread_across_weeks(self) -> None:
        """Nodes mastered across different weeks are averaged correctly."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        # snapshot_date = 2026-02-26; reference = 2026-02-27 UTC
        snap_date = date(2026, 2, 26)
        reference = datetime(2026, 2, 27, tzinfo=UTC)
        map_id = str(uuid.uuid4())
        nodes = [
            # week 0 (0-7 days before reference): 2 nodes
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=2)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=4)),
            # week 1 (7-14 days before reference): 1 node
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=9)),
            # week 2 (14-21 days before reference): 1 node
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=15)),
            # week 3 (21-28 days before reference): 0 nodes (non-mastered filler)
            _node_row(mastery_status="learning"),
        ]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, snap_date)

        # Buckets: [2, 1, 1, 0] → average = 4/4 = 1.0
        assert metrics["velocity_nodes_per_week"] == 1.0


# ---------------------------------------------------------------------------
# Tests: estimated_completion_days
# ---------------------------------------------------------------------------


class TestEstimatedCompletion:
    """estimated_completion_days is correct or None."""

    async def test_estimated_completion_null_when_velocity_zero(self) -> None:
        """estimated_completion_days is None when velocity is 0."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        # No mastered nodes → velocity = 0
        nodes = [
            _node_row(mastery_status="learning"),
            _node_row(mastery_status="unseen"),
        ]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["estimated_completion_days"] is None

    async def test_estimated_completion_null_when_all_mastered(self) -> None:
        """estimated_completion_days is None when all nodes are mastered (unmastered=0)."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        # snapshot_date = 2026-02-26; reference = 2026-02-27 UTC
        snap_date = date(2026, 2, 26)
        reference = datetime(2026, 2, 27, tzinfo=UTC)
        map_id = str(uuid.uuid4())
        nodes = [_node_row(mastery_status="mastered", updated_at=reference - timedelta(days=1))]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, snap_date)

        assert metrics["estimated_completion_days"] is None

    async def test_estimated_completion_computed(self) -> None:
        """With 2 unmastered and velocity=1.0, estimated_completion=14 days (ceil(2/1*7))."""

        from butlers.tools.education.analytics import analytics_compute_snapshot

        # snapshot_date = 2026-02-26; reference = 2026-02-27 UTC
        # 4 nodes mastered in week 0 (1-4 days before reference) → velocity = 4/4 = 1.0
        snap_date = date(2026, 2, 26)
        reference = datetime(2026, 2, 27, tzinfo=UTC)
        map_id = str(uuid.uuid4())
        nodes = [
            # 1 mastered (in week 0) + 2 unmastered → velocity=0.25, but we want velocity=1.0
            # Use 4 mastered nodes all in week 0 + 2 unmastered
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=1)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=2)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=3)),
            _node_row(mastery_status="mastered", updated_at=reference - timedelta(days=4)),
            _node_row(mastery_status="learning"),
            _node_row(mastery_status="unseen"),
        ]

        conn = _build_snapshot_conn(node_rows=nodes)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, snap_date)

        # velocity=1.0 (4/4), unmastered=2 → ceil(2/1.0 * 7) = 14
        assert metrics["estimated_completion_days"] == 14


# ---------------------------------------------------------------------------
# Tests: struggling_nodes
# ---------------------------------------------------------------------------


class TestStrugglingNodes:
    """struggling_nodes requires 5+ review responses with avg quality < 2.5."""

    async def test_struggling_nodes_returned(self) -> None:
        """Nodes with >= 5 review responses and avg quality < 2.5 appear in list."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        struggling_node_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(
            node_rows=nodes,
            struggling_rows=[{"node_id": struggling_node_id}],
        )
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert struggling_node_id in metrics["struggling_nodes"]

    async def test_struggling_nodes_empty_when_none(self) -> None:
        """struggling_nodes is empty list when no nodes qualify."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        conn = _build_snapshot_conn(node_rows=nodes, struggling_rows=[])
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        assert metrics["struggling_nodes"] == []


# ---------------------------------------------------------------------------
# Tests: time_of_day_distribution
# ---------------------------------------------------------------------------


class TestTimeOfDayDistribution:
    """time_of_day_distribution buckets responses into morning/afternoon/evening."""

    async def test_time_distribution_bucketed_correctly(self) -> None:
        """Hours 7, 14, 20 map to morning, afternoon, evening respectively."""
        from butlers.tools.education.analytics import analytics_compute_snapshot

        map_id = str(uuid.uuid4())
        nodes = [_node_row()]

        # hour=7 → morning, hour=14 → afternoon, hour=20 → evening
        time_rows = [{"hour": 7}, {"hour": 14}, {"hour": 20}, {"hour": 9}]

        conn = _build_snapshot_conn(node_rows=nodes, time_hour_rows=time_rows)
        pool = _make_pool_with_conn(conn)

        metrics = await analytics_compute_snapshot(pool, map_id, date(2026, 2, 26))

        tod = metrics["time_of_day_distribution"]
        assert tod["morning"] == 2  # hours 7 and 9
        assert tod["afternoon"] == 1  # hour 14
        assert tod["evening"] == 1  # hour 20

    async def test_time_distribution_evening_includes_late_night(self) -> None:
        """Hours 0-5 are classified as evening (wraps around midnight)."""
        from butlers.tools.education.analytics import _bucket_hour

        for hour in [0, 1, 2, 3, 4, 5, 18, 19, 20, 21, 22, 23]:
            assert _bucket_hour(hour) == "evening", f"hour {hour} should be evening"

    def test_bucket_hour_morning(self) -> None:
        from butlers.tools.education.analytics import _bucket_hour

        for hour in range(6, 12):
            assert _bucket_hour(hour) == "morning"

    def test_bucket_hour_afternoon(self) -> None:
        from butlers.tools.education.analytics import _bucket_hour

        for hour in range(12, 18):
            assert _bucket_hour(hour) == "afternoon"


# ---------------------------------------------------------------------------
# Tests: analytics_compute_all
# ---------------------------------------------------------------------------


class TestAnalyticsComputeAll:
    """analytics_compute_all discovers active maps and triggers feedback loop."""

    async def test_returns_count_of_processed_maps(self) -> None:
        """Returns the number of mind maps processed."""
        from butlers.tools.education.analytics import analytics_compute_all

        map_id_1 = str(uuid.uuid4())
        map_id_2 = str(uuid.uuid4())

        # Each map_id needs its own conn wired with responses
        # We'll use a simpler approach: mock analytics_compute_snapshot
        import unittest.mock

        with unittest.mock.patch(
            "butlers.tools.education.analytics.analytics_compute_snapshot",
            new=AsyncMock(
                return_value={
                    "struggling_nodes": [],
                    "retention_rate_7d": 0.8,
                    "total_nodes": 5,
                    "mastered_nodes": 2,
                }
            ),
        ):
            pool = _make_pool(
                fetch_returns=[
                    [
                        _make_row({"id": map_id_1}),
                        _make_row({"id": map_id_2}),
                    ]
                ]
            )

            count = await analytics_compute_all(pool, date(2026, 2, 26))

        assert count == 2

    async def test_feedback_loop_triggered_on_struggling(self) -> None:
        """curriculum_replan is called when struggling_nodes >= 3."""
        import unittest.mock

        from butlers.tools.education.analytics import analytics_compute_all

        map_id = str(uuid.uuid4())
        struggling_metrics = {
            "struggling_nodes": [str(uuid.uuid4()) for _ in range(3)],
            "retention_rate_7d": 0.9,  # good retention, but struggling
            "total_nodes": 10,
            "mastered_nodes": 5,
        }

        replan_calls: list[tuple[str, dict[str, Any]]] = []

        async def mock_replan(mid: str, metrics: dict[str, Any]) -> None:
            replan_calls.append((mid, metrics))

        with unittest.mock.patch(
            "butlers.tools.education.analytics.analytics_compute_snapshot",
            new=AsyncMock(return_value=struggling_metrics),
        ):
            pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

            await analytics_compute_all(pool, date(2026, 2, 26), curriculum_replan=mock_replan)

        assert len(replan_calls) == 1
        assert replan_calls[0][0] == map_id

    async def test_feedback_loop_triggered_on_low_retention(self) -> None:
        """curriculum_replan is called when retention_rate_7d < 0.60."""
        import unittest.mock

        from butlers.tools.education.analytics import analytics_compute_all

        map_id = str(uuid.uuid4())
        low_retention_metrics = {
            "struggling_nodes": [],  # no struggling nodes
            "retention_rate_7d": 0.55,  # below threshold
            "total_nodes": 10,
            "mastered_nodes": 5,
        }

        replan_calls: list[str] = []

        async def mock_replan(mid: str, metrics: dict[str, Any]) -> None:
            replan_calls.append(mid)

        with unittest.mock.patch(
            "butlers.tools.education.analytics.analytics_compute_snapshot",
            new=AsyncMock(return_value=low_retention_metrics),
        ):
            pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

            await analytics_compute_all(pool, date(2026, 2, 26), curriculum_replan=mock_replan)

        assert map_id in replan_calls

    async def test_feedback_loop_not_triggered_when_healthy(self) -> None:
        """curriculum_replan is NOT called when map is healthy."""
        import unittest.mock

        from butlers.tools.education.analytics import analytics_compute_all

        map_id = str(uuid.uuid4())
        healthy_metrics = {
            "struggling_nodes": [str(uuid.uuid4()), str(uuid.uuid4())],  # only 2 (<3)
            "retention_rate_7d": 0.75,  # above 0.60
            "total_nodes": 10,
            "mastered_nodes": 5,
        }

        replan_calls: list[str] = []

        async def mock_replan(mid: str, metrics: dict[str, Any]) -> None:
            replan_calls.append(mid)

        with unittest.mock.patch(
            "butlers.tools.education.analytics.analytics_compute_snapshot",
            new=AsyncMock(return_value=healthy_metrics),
        ):
            pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

            await analytics_compute_all(pool, date(2026, 2, 26), curriculum_replan=mock_replan)

        assert replan_calls == []

    async def test_feedback_loop_null_retention_not_triggered(self) -> None:
        """curriculum_replan is NOT called when retention_rate_7d is None (no review data)."""
        import unittest.mock

        from butlers.tools.education.analytics import analytics_compute_all

        map_id = str(uuid.uuid4())
        no_reviews_metrics = {
            "struggling_nodes": [],
            "retention_rate_7d": None,
            "total_nodes": 5,
            "mastered_nodes": 0,
        }

        replan_calls: list[str] = []

        async def mock_replan(mid: str, metrics: dict[str, Any]) -> None:
            replan_calls.append(mid)

        with unittest.mock.patch(
            "butlers.tools.education.analytics.analytics_compute_snapshot",
            new=AsyncMock(return_value=no_reviews_metrics),
        ):
            pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

            await analytics_compute_all(pool, date(2026, 2, 26), curriculum_replan=mock_replan)

        assert replan_calls == []

    async def test_no_maps_returns_zero(self) -> None:
        """When no active maps exist, returns 0."""
        from butlers.tools.education.analytics import analytics_compute_all

        pool = _make_pool(fetch_returns=[[]])

        count = await analytics_compute_all(pool, date(2026, 2, 26))

        assert count == 0


# ---------------------------------------------------------------------------
# Tests: analytics_get_snapshot
# ---------------------------------------------------------------------------


class TestAnalyticsGetSnapshot:
    """analytics_get_snapshot returns latest or specific-date snapshot."""

    async def test_returns_latest_when_no_date(self) -> None:
        """Returns most recent snapshot when date is not specified."""
        from butlers.tools.education.analytics import analytics_get_snapshot

        map_id = str(uuid.uuid4())
        snap = _snapshot_row(
            mind_map_id=map_id,
            snapshot_date=date(2026, 2, 26),
            metrics={"mastery_pct": 0.5},
        )

        pool = _make_pool(fetchrow_returns=[_make_row(snap)])

        result = await analytics_get_snapshot(pool, map_id)

        assert result is not None
        assert result["mind_map_id"] == snap["mind_map_id"]

    async def test_returns_specific_date_snapshot(self) -> None:
        """Returns snapshot for the given date."""
        from butlers.tools.education.analytics import analytics_get_snapshot

        map_id = str(uuid.uuid4())
        target_date = date(2026, 2, 20)
        snap = _snapshot_row(mind_map_id=map_id, snapshot_date=target_date)

        pool = _make_pool(fetchrow_returns=[_make_row(snap)])

        result = await analytics_get_snapshot(pool, map_id, date=target_date)

        assert result is not None

    async def test_returns_none_when_not_found(self) -> None:
        """Returns None when no snapshot exists."""
        from butlers.tools.education.analytics import analytics_get_snapshot

        pool = _make_pool(fetchrow_returns=[None])

        result = await analytics_get_snapshot(pool, str(uuid.uuid4()))

        assert result is None


# ---------------------------------------------------------------------------
# Tests: analytics_get_trend
# ---------------------------------------------------------------------------


class TestAnalyticsGetTrend:
    """analytics_get_trend returns snapshots in ascending date order."""

    async def test_returns_snapshots_asc_order(self) -> None:
        """Returned snapshots are in ascending snapshot_date order."""
        from butlers.tools.education.analytics import analytics_get_trend

        map_id = str(uuid.uuid4())
        snaps = [
            _snapshot_row(mind_map_id=map_id, snapshot_date=date(2026, 2, 20)),
            _snapshot_row(mind_map_id=map_id, snapshot_date=date(2026, 2, 23)),
            _snapshot_row(mind_map_id=map_id, snapshot_date=date(2026, 2, 26)),
        ]

        pool = _make_pool(fetch_returns=[[_make_row(s) for s in snaps]])

        results = await analytics_get_trend(pool, map_id, days=30)

        assert len(results) == 3
        # Dates should be in ascending order (as returned by the query)
        # Since mock returns them in order, verify the content
        assert results[0]["snapshot_date"] == snaps[0]["snapshot_date"]

    async def test_returns_empty_list_when_no_snapshots(self) -> None:
        """Returns empty list when no snapshots exist in the date range."""
        from butlers.tools.education.analytics import analytics_get_trend

        pool = _make_pool(fetch_returns=[[]])

        results = await analytics_get_trend(pool, str(uuid.uuid4()), days=30)

        assert results == []

    async def test_default_days_is_30(self) -> None:
        """Default days parameter is 30."""
        from butlers.tools.education.analytics import analytics_get_trend

        pool = _make_pool(fetch_returns=[[]])

        # Should not raise and uses 30 as default
        results = await analytics_get_trend(pool, str(uuid.uuid4()))

        assert results == []
        # Verify the SQL call was made with "30"
        call_args = pool.fetch.call_args[0]
        assert "30" in call_args


# ---------------------------------------------------------------------------
# Tests: analytics_get_cross_topic
# ---------------------------------------------------------------------------


class TestAnalyticsGetCrossTopic:
    """analytics_get_cross_topic returns comparative stats across active maps."""

    async def test_returns_topics_list(self) -> None:
        """topics list contains one entry per active map."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        map_id_1 = str(uuid.uuid4())
        map_id_2 = str(uuid.uuid4())

        rows = [
            _make_row(
                {
                    "mind_map_id": map_id_1,
                    "title": "Python",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.8,
                            "retention_rate_7d": 0.9,
                            "velocity_nodes_per_week": 2.0,
                            "mastered_nodes": 8,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
            _make_row(
                {
                    "mind_map_id": map_id_2,
                    "title": "Calculus",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.4,
                            "retention_rate_7d": 0.5,
                            "velocity_nodes_per_week": 1.0,
                            "mastered_nodes": 4,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
        ]

        pool = _make_pool(fetch_returns=[rows])

        result = await analytics_get_cross_topic(pool)

        assert len(result["topics"]) == 2
        topic_ids = [t["mind_map_id"] for t in result["topics"]]
        assert map_id_1 in topic_ids
        assert map_id_2 in topic_ids

    async def test_strongest_topic_highest_mastery(self) -> None:
        """strongest_topic is the map with highest mastery_pct."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        map_id_strong = str(uuid.uuid4())
        map_id_weak = str(uuid.uuid4())

        rows = [
            _make_row(
                {
                    "mind_map_id": map_id_strong,
                    "title": "Python",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.9,
                            "retention_rate_7d": 0.85,
                            "velocity_nodes_per_week": 2.0,
                            "mastered_nodes": 9,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
            _make_row(
                {
                    "mind_map_id": map_id_weak,
                    "title": "Calculus",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.3,
                            "retention_rate_7d": 0.4,
                            "velocity_nodes_per_week": 0.5,
                            "mastered_nodes": 3,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
        ]

        pool = _make_pool(fetch_returns=[rows])

        result = await analytics_get_cross_topic(pool)

        assert result["strongest_topic"] == map_id_strong

    async def test_weakest_topic_lowest_retention(self) -> None:
        """weakest_topic is the map with lowest retention_rate_7d."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        map_id_high_ret = str(uuid.uuid4())
        map_id_low_ret = str(uuid.uuid4())

        rows = [
            _make_row(
                {
                    "mind_map_id": map_id_high_ret,
                    "title": "Python",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.5,
                            "retention_rate_7d": 0.9,
                            "velocity_nodes_per_week": 1.0,
                            "mastered_nodes": 5,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
            _make_row(
                {
                    "mind_map_id": map_id_low_ret,
                    "title": "Calculus",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.5,
                            "retention_rate_7d": 0.4,
                            "velocity_nodes_per_week": 1.0,
                            "mastered_nodes": 5,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
        ]

        pool = _make_pool(fetch_returns=[rows])

        result = await analytics_get_cross_topic(pool)

        assert result["weakest_topic"] == map_id_low_ret

    async def test_portfolio_mastery_computed(self) -> None:
        """portfolio_mastery = sum(mastered) / sum(total) across all maps."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        rows = [
            _make_row(
                {
                    "mind_map_id": str(uuid.uuid4()),
                    "title": "Python",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.8,
                            "retention_rate_7d": 0.85,
                            "velocity_nodes_per_week": 2.0,
                            "mastered_nodes": 8,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
            _make_row(
                {
                    "mind_map_id": str(uuid.uuid4()),
                    "title": "Calculus",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.2,
                            "retention_rate_7d": 0.6,
                            "velocity_nodes_per_week": 0.5,
                            "mastered_nodes": 2,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
        ]

        pool = _make_pool(fetch_returns=[rows])

        result = await analytics_get_cross_topic(pool)

        # 8+2 mastered / 10+10 total = 10/20 = 0.5
        assert result["portfolio_mastery"] == 0.5

    async def test_empty_maps_returns_defaults(self) -> None:
        """Returns empty topics list and sensible defaults when no active maps."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        pool = _make_pool(fetch_returns=[[]])

        result = await analytics_get_cross_topic(pool)

        assert result["topics"] == []
        assert result["strongest_topic"] is None
        assert result["weakest_topic"] is None
        assert result["portfolio_mastery"] == 0.0

    async def test_weakest_topic_none_when_all_retention_null(self) -> None:
        """weakest_topic is None when all maps have NULL retention_rate_7d."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        rows = [
            _make_row(
                {
                    "mind_map_id": str(uuid.uuid4()),
                    "title": "Python",
                    "metrics": json.dumps(
                        {
                            "mastery_pct": 0.5,
                            "retention_rate_7d": None,
                            "velocity_nodes_per_week": 1.0,
                            "mastered_nodes": 5,
                            "total_nodes": 10,
                        }
                    ),
                }
            ),
        ]

        pool = _make_pool(fetch_returns=[rows])

        result = await analytics_get_cross_topic(pool)

        assert result["weakest_topic"] is None

    async def test_cross_topic_handles_dict_metrics(self) -> None:
        """analytics_get_cross_topic handles metrics as dict (not JSON string)."""
        from butlers.tools.education.analytics import analytics_get_cross_topic

        rows = [
            _make_row(
                {
                    "mind_map_id": str(uuid.uuid4()),
                    "title": "Python",
                    "metrics": {
                        "mastery_pct": 0.6,
                        "retention_rate_7d": 0.75,
                        "velocity_nodes_per_week": 1.5,
                        "mastered_nodes": 6,
                        "total_nodes": 10,
                    },
                }
            ),
        ]

        pool = _make_pool(fetch_returns=[rows])

        result = await analytics_get_cross_topic(pool)

        assert len(result["topics"]) == 1
        assert result["topics"][0]["mastery_pct"] == 0.6
