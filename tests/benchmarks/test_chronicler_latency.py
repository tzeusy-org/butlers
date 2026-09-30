"""Latency benchmark: aggregate endpoints P95 < 200ms (7-day window).

Validates that ``/api/chronicler/aggregate/by-category`` and
``/api/chronicler/aggregate/by-day`` both achieve P95 < 200ms on a
realistic 7-day fixture (~1050 episodes).

Failure threshold: P95 > 250ms (50ms slack vs target) blocks CI.

Per ``about/craft-and-care/performance-discipline.md``:
- Measure before optimising.
- Preserve diagnosability while improving speed.

This test spins up a real PostgreSQL container (testcontainers), runs the
real core and chronicler migration chains, inserts ~1050 synthetic episodes
(the session and listening sources on the counted ``activity`` layer, the
calendar source on ``intent`` as in production), and
drives the handlers via ``httpx.AsyncClient``.  The aggregation path is
almost entirely Python-side (after a single ``pool.fetch()``), so the
relevant bottleneck is Python in-memory aggregation over the fetched rows
— exactly what the P95 target is meant to guard.

The test is marked ``integration`` (requires Docker) and
``asyncio(loop_scope="session")`` so that the session-scoped pool fixture
is shared correctly under pytest-xdist.
"""

from __future__ import annotations

import shutil
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UTC = UTC

# Synthetic fixture dimensions
_DAYS = 7
_EPISODES_PER_DAY_PER_SOURCE = 50
_SOURCES = [
    ("core.sessions", "work"),
    ("spotify.session_summary", "listening_episode"),
    ("google_calendar.completed", "scheduled_block"),
]
_TOTAL_EPISODES = _DAYS * _EPISODES_PER_DAY_PER_SOURCE * len(_SOURCES)  # 1050

# Window: 7 days ending at a fixed UTC anchor
_WINDOW_END = datetime(2026, 1, 8, 0, 0, 0, tzinfo=_UTC)
_WINDOW_START = _WINDOW_END - timedelta(days=_DAYS)

# Benchmark parameters
_WARMUP_ITERS = 20
_MEASURE_ITERS = 200
_P95_THRESHOLD_MS = 250.0  # fail-fast threshold (50ms slack above 200ms target)

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Seed helpers (schema comes from the real core + chronicler migration chains)
# ---------------------------------------------------------------------------

# Production layer per synthetic source: calendar is ``intent`` (never counted
# on its own); the session and listening sources project ``activity`` rows,
# which is the layer the aggregation counts (``lane_for_activity``).
_SOURCE_LAYER = {
    "core.sessions": "activity",
    "spotify.session_summary": "activity",
    "google_calendar.completed": "intent",
}


async def _insert_source_adapters(pool: Any) -> None:
    """Seed source_adapter_state rows required by the episodes FK."""
    for source_name, _ in _SOURCES:
        await pool.execute(
            """
            INSERT INTO source_adapter_state
                (source_name, chronicler_compatibility, active)
            VALUES ($1, 'supported', true)
            ON CONFLICT (source_name) DO NOTHING
            """,
            source_name,
        )


async def _insert_synthetic_episodes(pool: Any) -> int:
    """Insert ~1050 synthetic episodes spread over 7 days × 3 sources.

    Each episode has a realistic duration between 15 min and 3 h.
    Returns the total rows inserted.
    """
    rows: list[tuple[Any, ...]] = []
    day = _WINDOW_START
    for d in range(_DAYS):
        day = _WINDOW_START + timedelta(days=d)
        for source_name, episode_type in _SOURCES:
            for ep in range(_EPISODES_PER_DAY_PER_SOURCE):
                # Spread episodes evenly across the day
                offset_seconds = (ep * 86400) // _EPISODES_PER_DAY_PER_SOURCE
                start = day + timedelta(seconds=offset_seconds)
                # Duration: alternate between 15 min, 30 min, 60 min, and 3h
                durations_minutes = [15, 30, 60, 180]
                duration_min = durations_minutes[ep % len(durations_minutes)]
                end = start + timedelta(minutes=duration_min)
                # Keep episode within window
                if end > _WINDOW_END:
                    end = _WINDOW_END

                rows.append(
                    (
                        source_name,
                        f"{source_name}:{d}:{ep}:{uuid.uuid4().hex[:8]}",
                        episode_type,
                        start,
                        end,
                        "exact",
                        "normal",
                        _SOURCE_LAYER[source_name],
                    )
                )

    # Batch insert via executemany
    await pool.executemany(
        """
        INSERT INTO episodes
            (source_name, source_ref, episode_type, start_at, end_at, precision, privacy, layer)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (source_name, source_ref) DO NOTHING
        """,
        rows,
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Fixture: real PostgreSQL-backed pool with chronicler data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def chronicler_pool(postgres_container):
    """Session-scoped chronicler database with ~1050 synthetic episodes.

    The schema is the real one: a fresh database is bootstrapped and migrated
    through the core chain and then the chronicler chain, so the benchmark
    queries exactly the columns and views production has (a hand-copied DDL
    drifted behind migration 017's ``layer``/``confidence`` and made both
    aggregate endpoints 500).

    Shares the session-scoped postgres_container to avoid repeated Docker
    container startups across test workers.
    """
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "chronicler"],
        schemas={"chronicler": "chronicler"},
        pool_schema="chronicler",
        min_pool_size=2,
        max_pool_size=5,
    )
    try:
        await _insert_source_adapters(pool)
        n = await _insert_synthetic_episodes(pool)
        assert n == _TOTAL_EPISODES, f"Expected {_TOTAL_EPISODES} rows, got {n}"
        yield pool
    finally:
        await pool.close()


@pytest.fixture(scope="session")
def chronicler_app(chronicler_pool):
    """FastAPI test app wired to the real chronicler pool for latency measurements."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = chronicler_pool

    app = create_app(api_key="")

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app


# ---------------------------------------------------------------------------
# Latency measurement helper
# ---------------------------------------------------------------------------


def _calculate_metrics(latencies_ns: list[int]) -> tuple[float, float, float]:
    """Return (P50, P95, P99) in milliseconds from a list of nanosecond durations.

    statistics.quantiles(data, n=100) returns 99 cut points:
    - index 94 is P95
    - index 98 is P99
    """
    times_ms = [t / 1_000_000 for t in latencies_ns]
    qs = statistics.quantiles(times_ms, n=100)
    return statistics.median(times_ms), qs[94], qs[98]


# ---------------------------------------------------------------------------
# Benchmark: by-category
# ---------------------------------------------------------------------------


async def test_aggregate_by_category_p95_latency(chronicler_app):
    """P95 latency for GET /api/chronicler/aggregate/by-category must be < 250 ms.

    Benchmark protocol:
    - Warmup: {_WARMUP_ITERS} iterations (discarded).
    - Measurement: {_MEASURE_ITERS} iterations (P95 computed over this sample).
    - The endpoint fetches {_TOTAL_EPISODES} rows from a real PostgreSQL
      container and aggregates them in Python — same path as production.
    """
    params = {
        "start_at": _WINDOW_START.isoformat(),
        "end_at": _WINDOW_END.isoformat(),
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chronicler_app), base_url="http://test"
    ) as client:
        # Warmup — not measured
        for _ in range(_WARMUP_ITERS):
            resp = await client.get("/api/chronicler/aggregate/by-category", params=params)
            assert resp.status_code == 200

        # Measurement
        latencies_ns: list[int] = []
        for _ in range(_MEASURE_ITERS):
            t0 = time.perf_counter_ns()
            resp = await client.get("/api/chronicler/aggregate/by-category", params=params)
            elapsed = time.perf_counter_ns() - t0
            assert resp.status_code == 200
            latencies_ns.append(elapsed)

    # The timed path must aggregate real rows, not time an empty result.
    buckets = resp.json()["data"]["buckets"]
    assert sum(b["episode_count"] for b in buckets) > 0, buckets
    assert sum(b["total_seconds"] for b in buckets) > 0, buckets

    p50, p95, p99 = _calculate_metrics(latencies_ns)

    # Record measurements for visibility (printed on failure and captured in CI output)
    print(
        f"\naggregate_by_category latency over {_MEASURE_ITERS} iterations "
        f"({_TOTAL_EPISODES} episodes):"
        f"\n  P50 = {p50:.1f} ms"
        f"\n  P95 = {p95:.1f} ms  (threshold: {_P95_THRESHOLD_MS} ms)"
        f"\n  P99 = {p99:.1f} ms"
    )

    assert p95 < _P95_THRESHOLD_MS, (
        f"aggregate_by_category P95 latency {p95:.1f} ms exceeds "
        f"{_P95_THRESHOLD_MS} ms threshold. "
        f"P50={p50:.1f} ms  P99={p99:.1f} ms  n={_MEASURE_ITERS} iters  "
        f"fixture={_TOTAL_EPISODES} episodes"
    )


# ---------------------------------------------------------------------------
# Benchmark: by-day
# ---------------------------------------------------------------------------


async def test_aggregate_by_day_p95_latency(chronicler_app):
    """P95 latency for GET /api/chronicler/aggregate/by-day must be < 250 ms.

    Same protocol as ``test_aggregate_by_category_p95_latency`` but tests
    the more expensive by-day handler which additionally enumerates day
    buckets and splits cross-midnight episodes.
    """
    params = {
        "start_at": _WINDOW_START.isoformat(),
        "end_at": _WINDOW_END.isoformat(),
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chronicler_app), base_url="http://test"
    ) as client:
        # Warmup — not measured
        for _ in range(_WARMUP_ITERS):
            resp = await client.get("/api/chronicler/aggregate/by-day", params=params)
            assert resp.status_code == 200

        # Measurement
        latencies_ns: list[int] = []
        for _ in range(_MEASURE_ITERS):
            t0 = time.perf_counter_ns()
            resp = await client.get("/api/chronicler/aggregate/by-day", params=params)
            elapsed = time.perf_counter_ns() - t0
            assert resp.status_code == 200
            latencies_ns.append(elapsed)

    # The timed path must aggregate real rows, not time an empty result.
    rows = resp.json()
    assert sum(r["episode_count"] for r in rows) > 0, rows
    assert sum(r["total_seconds"] for r in rows) > 0, rows

    p50, p95, p99 = _calculate_metrics(latencies_ns)

    print(
        f"\naggregate_by_day latency over {_MEASURE_ITERS} iterations "
        f"({_TOTAL_EPISODES} episodes):"
        f"\n  P50 = {p50:.1f} ms"
        f"\n  P95 = {p95:.1f} ms  (threshold: {_P95_THRESHOLD_MS} ms)"
        f"\n  P99 = {p99:.1f} ms"
    )

    assert p95 < _P95_THRESHOLD_MS, (
        f"aggregate_by_day P95 latency {p95:.1f} ms exceeds "
        f"{_P95_THRESHOLD_MS} ms threshold. "
        f"P50={p50:.1f} ms  P99={p99:.1f} ms  n={_MEASURE_ITERS} iters  "
        f"fixture={_TOTAL_EPISODES} episodes"
    )
