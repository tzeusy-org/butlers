"""Real-Postgres integration test for the OwnTracks place-cluster adapter (bu-ac2pg).

Mocked-pool tests exercise the pure clustering algorithm and the orchestration
control flow, but they cannot validate the real write path: a real
``connectors.owntracks_points`` table (migrated via the ``core`` chain) read
by the adapter, and a real ``chronicler.episodes``/``projection_checkpoints``
upsert (migrated via the ``chronicler`` chain) — including the actual
``upsert_episode`` ON CONFLICT semantics and the carryover JSONB round-trip.
See "Mocked-pool vs integration test gap" (PR #2598 class) for why this
matters: SQL that looks correct against a mocked pool can still be wrong
against real Postgres.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import (
    DEFAULT_MAX_GAP_MINUTES,
    EPISODE_TYPE_PLACE,
    PLACE_UNKNOWN_LABEL,
    SOURCE_NAME,
    OwnTracksPlaceClusterAdapter,
    PlaceReference,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"
_HOME_LAT = 1.30000
_HOME_LON = 103.80000


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision both the ``connectors`` (core chain) and ``chronicler``
    schemas real migrations create, since this adapter reads the former and
    writes the latter."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE connectors.owntracks_points CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events, projection_checkpoints CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _insert_point(
    pool: asyncpg.Pool,
    *,
    ts: datetime,
    lat: float,
    lon: float,
    endpoint_identity: str = _ENDPOINT,
    idempotency_key: str | None = None,
) -> None:
    key = idempotency_key or f"owntracks:{endpoint_identity}:{int(ts.timestamp())}:location"
    await pool.execute(
        """
        INSERT INTO connectors.owntracks_points
            (idempotency_key, ts, lat, lon, accuracy, trigger, event, endpoint_identity,
             raw_payload, recorded_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        key,
        ts,
        lat,
        lon,
        10.0,
        "p",
        None,
        endpoint_identity,
        {"_type": "location", "lat": lat, "lon": lon},
        ts,
    )


async def test_stationary_run_projects_a_labeled_place_episode(pool: asyncpg.Pool) -> None:
    """A real 25-minute stationary run at 'home' produces one place_episode
    labeled against the owner-declared reference point, upserted for real
    against a migrated Postgres instance."""
    for m in range(0, 30, 5):
        await _insert_point(pool, ts=_NOW + timedelta(minutes=m), lat=_HOME_LAT, lon=_HOME_LON)

    # Also project the sibling point-event adapter so evidence_refs resolves
    # against real point_events rows (exactly like production scheduling,
    # where both adapters run against the same evidence table).
    await OwnTracksPointAdapter().project(pool, chronicler_pool=pool, since=None)

    adapter = OwnTracksPlaceClusterAdapter(
        reference_points=(PlaceReference(label="home", lat=_HOME_LAT, lon=_HOME_LON, radius_m=200),)
    )
    result = await adapter.project(pool, chronicler_pool=pool, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1

    row = await pool.fetchrow(
        "SELECT * FROM episodes WHERE source_name = $1 AND episode_type = $2",
        SOURCE_NAME,
        EPISODE_TYPE_PLACE,
    )
    assert row is not None
    assert row["payload"]["label"] == "home"
    assert row["payload"]["point_count"] == 6
    assert row["layer"] == "activity"
    assert row["confidence"] == "low"
    assert row["evidence_refs"], "expected evidence_refs to cite the location point events"


async def test_unlabeled_recurring_cluster_surfaces_as_place_unknown(pool: asyncpg.Pool) -> None:
    """With no reference points configured, a real stationary run still
    upserts — honestly labeled unknown, never discarded or fabricated."""
    for m in range(0, 30, 5):
        await _insert_point(pool, ts=_NOW + timedelta(minutes=m), lat=_HOME_LAT, lon=_HOME_LON)

    adapter = OwnTracksPlaceClusterAdapter(reference_points=())
    result = await adapter.project(pool, chronicler_pool=pool, since=None)

    assert result.episodes_closed == 1
    row = await pool.fetchrow(
        "SELECT payload FROM episodes WHERE source_name = $1 AND episode_type = $2",
        SOURCE_NAME,
        EPISODE_TYPE_PLACE,
    )
    assert row["payload"]["label"] == PLACE_UNKNOWN_LABEL


async def test_short_dwell_is_not_upserted_then_grows_across_a_real_run(pool: asyncpg.Pool) -> None:
    """First run: dwell below threshold -> no episode row. Second run (after
    more points accumulate past the checkpoint watermark): the SAME cluster
    (via carryover) crosses the dwell threshold and is upserted exactly once
    (idempotent upsert, not a duplicate row)."""
    for m in range(0, 10, 5):  # 5 minutes dwell — below the 20-minute default
        await _insert_point(pool, ts=_NOW + timedelta(minutes=m), lat=_HOME_LAT, lon=_HOME_LON)

    adapter = OwnTracksPlaceClusterAdapter()
    first = await adapter.project(pool, chronicler_pool=pool, since=None)
    assert first.episodes_closed == 0

    count_after_first = await pool.fetchval(
        "SELECT COUNT(*) FROM episodes WHERE source_name = $1", SOURCE_NAME
    )
    assert count_after_first == 0

    # More points arrive, extending the same stationary run past the
    # dwell threshold. Simulate a second scheduled run from the checkpoint.
    for m in range(15, 30, 5):
        await _insert_point(pool, ts=_NOW + timedelta(minutes=m), lat=_HOME_LAT, lon=_HOME_LON)

    second = await adapter.project(pool, chronicler_pool=pool, since=first.watermark)
    assert second.episodes_closed == 1

    rows = await pool.fetch("SELECT * FROM episodes WHERE source_name = $1", SOURCE_NAME)
    assert len(rows) == 1
    assert rows[0]["start_at"] == _NOW
    # 2 points from the first run + 3 from the second, same continuous dwell.
    assert rows[0]["payload"]["point_count"] == 5


async def test_checkpoint_and_carryover_persist_across_runs(pool: asyncpg.Pool) -> None:
    """Exercises the full ``ProjectionAdapter.run()`` orchestration (not just
    ``project()`` directly) so the checkpoint watermark — written by
    ``run()`` after ``project()`` returns — is actually persisted, exactly
    as the real scheduled-job path (``jobs.py``) invokes it."""
    for m in range(0, 10, 5):
        await _insert_point(pool, ts=_NOW + timedelta(minutes=m), lat=_HOME_LAT, lon=_HOME_LON)

    adapter = OwnTracksPlaceClusterAdapter()
    await adapter.run(pool=pool, chronicler_pool=pool)

    checkpoint = await pool.fetchrow(
        "SELECT watermark, carryover FROM projection_checkpoints "
        "WHERE source_name = $1 AND subsource = ''",
        SOURCE_NAME,
    )
    assert checkpoint is not None
    assert checkpoint["watermark"] == _NOW + timedelta(minutes=5)
    assert _ENDPOINT in checkpoint["carryover"]


async def test_missing_evidence_table_degrades_gracefully_against_real_db(
    pool: asyncpg.Pool,
) -> None:
    """When connectors.owntracks_points genuinely does not exist (module not
    enabled on this deployment), the adapter degrades gracefully rather than
    raising — verified against a real DB by dropping the table."""
    await pool.execute("DROP TABLE connectors.owntracks_points")

    adapter = OwnTracksPlaceClusterAdapter()
    result = await adapter.project(pool, chronicler_pool=pool, since=None)

    assert result.skipped is True
    assert "owntracks_points" in (result.skipped_reason or "")


async def test_movement_gap_boundary_matches_max_gap_constant() -> None:
    """Sanity: the adapter's default max_gap matches the documented constant
    (regression guard against silent drift between docstring and code)."""
    adapter = OwnTracksPlaceClusterAdapter()
    assert adapter.max_gap == timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
