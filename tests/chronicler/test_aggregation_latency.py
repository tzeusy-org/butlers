"""Latency benchmark: aggregate endpoints P95 < 200ms (7-day window).

Validates that ``/api/chronicler/aggregate/by-category`` and
``/api/chronicler/aggregate/by-day`` both achieve P95 < 200ms on a
realistic 7-day fixture (~1050 episodes).

Failure threshold: P95 > 250ms (50ms slack vs target) blocks CI.

Per ``about/craft-and-care/performance-discipline.md``:
- Measure before optimising.
- Preserve diagnosability while improving speed.

This test spins up a real PostgreSQL container (testcontainers), applies
chronicler schema migrations, inserts ~1050 synthetic episodes, and
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

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

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
# Schema setup helpers — apply chronicler DDL without Alembic runner
# ---------------------------------------------------------------------------

_DDL_SOURCE_ADAPTER_STATE = """
CREATE TABLE IF NOT EXISTS source_adapter_state (
    source_name TEXT PRIMARY KEY,
    chronicler_compatibility TEXT NOT NULL
        CHECK (chronicler_compatibility IN (
            'supported', 'deferred', 'not_time_bearing', 'planned'
        )),
    read_surface TEXT,
    boundary_semantics TEXT,
    optional_schema BOOLEAN NOT NULL DEFAULT false,
    active BOOLEAN NOT NULL DEFAULT false,
    inactive_reason TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_EPISODES = """
CREATE TABLE IF NOT EXISTS episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
    source_ref TEXT NOT NULL,
    episode_type TEXT NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ,
    precision TEXT NOT NULL DEFAULT 'exact'
        CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
    title TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    privacy TEXT NOT NULL DEFAULT 'normal'
        CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
    retention_days INTEGER,
    tombstone_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_name, source_ref),
    CHECK (end_at IS NULL OR end_at >= start_at)
)
"""

_DDL_OVERRIDES = """
CREATE TABLE IF NOT EXISTS overrides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('episode', 'point_event')),
    target_id UUID NOT NULL,
    corrected_start_at TIMESTAMPTZ,
    corrected_end_at TIMESTAMPTZ,
    corrected_title TEXT,
    corrected_privacy TEXT
        CHECK (corrected_privacy IS NULL OR
               corrected_privacy IN ('normal', 'sensitive', 'restricted')),
    corrected_tombstone_at TIMESTAMPTZ,
    note TEXT,
    submitted_by TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        corrected_start_at IS NOT NULL OR
        corrected_end_at IS NOT NULL OR
        corrected_title IS NOT NULL OR
        corrected_privacy IS NOT NULL OR
        corrected_tombstone_at IS NOT NULL OR
        note IS NOT NULL
    )
)
"""

_DDL_V_LATEST_OVERRIDES = """
CREATE OR REPLACE VIEW v_latest_overrides AS
SELECT DISTINCT ON (target_kind, target_id)
    target_kind,
    target_id,
    corrected_start_at,
    corrected_end_at,
    corrected_title,
    corrected_privacy,
    corrected_tombstone_at,
    note,
    created_at AS corrected_at
FROM overrides
ORDER BY target_kind, target_id, created_at DESC
"""

_DDL_V_EPISODES_CORRECTED = """
CREATE OR REPLACE VIEW v_episodes_corrected AS
SELECT
    e.id,
    e.source_name,
    e.source_ref,
    e.episode_type,
    COALESCE(o.corrected_start_at, e.start_at) AS start_at,
    COALESCE(o.corrected_end_at, e.end_at) AS end_at,
    e.precision,
    COALESCE(o.corrected_title, e.title) AS title,
    e.payload,
    COALESCE(o.corrected_privacy, e.privacy) AS privacy,
    e.retention_days,
    COALESCE(o.corrected_tombstone_at, e.tombstone_at) AS tombstone_at,
    e.start_at AS canonical_start_at,
    e.end_at AS canonical_end_at,
    e.title AS canonical_title,
    e.privacy AS canonical_privacy,
    o.corrected_at,
    o.note AS correction_note,
    e.created_at,
    e.updated_at
FROM episodes e
LEFT JOIN v_latest_overrides o
    ON o.target_kind = 'episode' AND o.target_id = e.id
"""


async def _apply_chronicler_schema(pool: Any) -> None:
    """Apply the minimal chronicler DDL needed for aggregate endpoint tests."""
    await pool.execute(_DDL_SOURCE_ADAPTER_STATE)
    await pool.execute(_DDL_EPISODES)
    await pool.execute(_DDL_OVERRIDES)
    await pool.execute(_DDL_V_LATEST_OVERRIDES)
    await pool.execute(_DDL_V_EPISODES_CORRECTED)


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
                    )
                )

    # Batch insert via executemany
    await pool.executemany(
        """
        INSERT INTO episodes
            (source_name, source_ref, episode_type, start_at, end_at, precision, privacy)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
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

    Shares the session-scoped postgres_container to avoid repeated Docker
    container startups across test workers.

    We provision the pool here directly (not via provisioned_postgres_pool which
    is function-scoped) so that the pool outlives individual test functions.
    """
    from butlers.db import Database

    db = Database(
        db_name=f"test_{uuid.uuid4().hex[:12]}",
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=2,
        max_pool_size=5,
    )
    await db.provision()
    pool = await db.connect()
    try:
        await _apply_chronicler_schema(pool)
        await _insert_source_adapters(pool)
        n = await _insert_synthetic_episodes(pool)
        assert n == _TOTAL_EPISODES, f"Expected {_TOTAL_EPISODES} rows, got {n}"
        yield pool
    finally:
        await db.close()


@pytest.fixture(scope="session")
def chronicler_app(chronicler_pool):
    """FastAPI test app wired to the real chronicler pool for latency measurements."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = chronicler_pool

    app = create_app()

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app


# ---------------------------------------------------------------------------
# Latency measurement helper
# ---------------------------------------------------------------------------


def _p95_ms(times_ns: list[int]) -> float:
    """Return P95 latency in milliseconds from a list of nanosecond durations."""
    times_ms = [t / 1_000_000 for t in times_ns]
    # statistics.quantiles(data, n=100) returns 99 cut points; index 94 is P95
    return statistics.quantiles(times_ms, n=100)[94]


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

    p95 = _p95_ms(latencies_ns)
    p50 = statistics.median([t / 1_000_000 for t in latencies_ns])
    p99 = statistics.quantiles([t / 1_000_000 for t in latencies_ns], n=100)[98]

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

    p95 = _p95_ms(latencies_ns)
    p50 = statistics.median([t / 1_000_000 for t in latencies_ns])
    p99 = statistics.quantiles([t / 1_000_000 for t in latencies_ns], n=100)[98]

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
