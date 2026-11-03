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

import asyncio
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
    assert row["layer"] == "evidence"  # bu-r3fmja: place answers "where"; situation blocks count
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
    async with pool.acquire() as conn:
        outer = conn.transaction()
        await outer.start()
        try:
            await conn.execute("DROP TABLE connectors.owntracks_points")
            adapter = OwnTracksPlaceClusterAdapter()
            result = await adapter.project(conn, chronicler_pool=conn, since=None)

            assert result.skipped is True
            assert "owntracks_points" in (result.skipped_reason or "")
        finally:
            # DROP is transactional: restore the exact migrated table before
            # another case's pool fixture starts, even when projection fails.
            await outer.rollback()
    assert await pool.fetchval("SELECT to_regclass('connectors.owntracks_points')") is not None


async def test_movement_gap_boundary_matches_max_gap_constant() -> None:
    """Sanity: the adapter's default max_gap matches the documented constant
    (regression guard against silent drift between docstring and code)."""
    adapter = OwnTracksPlaceClusterAdapter()
    assert adapter.max_gap == timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)


async def test_bootstrap_consensus_is_durable_atomic_and_serialized(
    pool: asyncpg.Pool,
    migrated_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real run/checkpoint/JSONB/marker transactions, not mocked SQL success.

    Split each endpoint's four fixes 1+1+2; run both adapter orders. A failed
    commit, concurrent duplicate, cancelled writer and retry must converge.
    """
    import butlers.chronicler.adapters._owner_entity as owners
    import butlers.chronicler.adapters.owntracks as points
    import butlers.chronicler.adapters.owntracks_place_cluster as places

    parallel_pool = pool
    # The regular fixture seeds the migrated database; use a fresh one-slot
    # pool outside the explicit parallel-writer check to expose nested acquires.
    async with asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=1, init=register_jsonb_codec
    ) as pool:
        point_adapter = OwnTracksPointAdapter()
        place_adapter = OwnTracksPlaceClusterAdapter()
        real_save = points.save_carryover
        endpoints = ("owntracks:retained", "owntracks:expired", "owntracks:tombstoned")
        for reverse in (False, True):
            await pool.execute(
                "TRUNCATE connectors.owntracks_points, episodes, point_events, projection_checkpoints CASCADE"
            )
            adapters = (place_adapter, point_adapter) if reverse else (point_adapter, place_adapter)

            async def insert(minutes: int) -> None:
                for endpoint in endpoints:
                    await _insert_point(
                        pool,
                        ts=_NOW + timedelta(minutes=minutes),
                        lat=52.12 if minutes == 0 else _HOME_LAT,
                        lon=22.06 if minutes == 0 else _HOME_LON,
                        endpoint_identity=endpoint,
                    )

            async def run_both(adapters=adapters) -> None:
                for adapter in adapters:
                    result = await asyncio.wait_for(
                        adapter.run(pool=pool, chronicler_pool=pool), timeout=10
                    )
                    assert result.error is None

            # Both a genuinely absent optional source and a read-time SQL error
            # must leave the outer transaction usable for inactive-status writes.
            await pool.execute(
                "ALTER TABLE connectors.owntracks_points RENAME TO owntracks_missing_fixture"
            )
            try:
                for adapter in adapters:
                    missing = await asyncio.wait_for(
                        adapter.run(pool=pool, chronicler_pool=pool), timeout=10
                    )
                    assert missing.skipped and missing.error is None
                    assert await pool.fetchval("SELECT 42") == 42
            finally:
                await pool.execute(
                    "ALTER TABLE connectors.owntracks_missing_fixture RENAME TO owntracks_points"
                )
            for module, adapter in ((points, point_adapter), (places, place_adapter)):
                original_table = module._EVIDENCE_TABLE
                monkeypatch.setattr(
                    module, "_EVIDENCE_TABLE", "connectors.owntracks_missing_fixture"
                )
                try:
                    # The ordinary metadata probe is true; the actual SELECT raises
                    # UndefinedTable inside the new savepoint (not a mock exception).
                    unreadable = await asyncio.wait_for(
                        adapter.run(pool=pool, chronicler_pool=pool), timeout=10
                    )
                    assert unreadable.skipped and unreadable.error is None
                    assert await pool.fetchval("SELECT 42") == 42
                    assert (
                        await pool.fetchval(
                            "SELECT watermark FROM projection_checkpoints WHERE source_name=$1",
                            adapter.source_name,
                        )
                        is None
                    )
                finally:
                    monkeypatch.setattr(module, "_EVIDENCE_TABLE", original_table)

            await insert(0)
            real_owner_fetch = owners._fetch_owner_row

            async def unreadable_owner(conn):
                return await conn.fetchrow("SELECT 1 / 0")

            monkeypatch.setattr(owners, "_fetch_owner_row", unreadable_owner)
            try:
                # The optional owner query really fails in PostgreSQL. A one-slot
                # source/target pool must still project and commit its checkpoint.
                await run_both()
                assert await pool.fetchval("SELECT 42") == 42
                for adapter in adapters:
                    assert (
                        await pool.fetchval(
                            "SELECT watermark FROM projection_checkpoints WHERE source_name=$1",
                            adapter.source_name,
                        )
                        == _NOW
                    )
            finally:
                monkeypatch.setattr(owners, "_fetch_owner_row", real_owner_fetch)
            seed = await pool.fetchrow(
                "SELECT * FROM point_events WHERE payload->>'endpoint_identity'=$1", endpoints[0]
            )
            assert seed is not None
            await pool.execute(
                "UPDATE point_events SET title='Preserved title', privacy='restricted', retention_days=91, payload=payload || $2::jsonb WHERE id=$1",
                seed["id"],
                {"preserved": True},
            )
            before = dict(await pool.fetchrow("SELECT * FROM point_events WHERE id=$1", seed["id"]))
            await pool.execute(
                "DELETE FROM point_events WHERE payload->>'endpoint_identity'=$1", endpoints[1]
            )
            await pool.execute(
                "UPDATE point_events SET tombstone_at=now(), tombstone_reason='fixture' WHERE payload->>'endpoint_identity'=$1",
                endpoints[2],
            )
            raw = [
                dict(row)
                for row in await pool.fetch("SELECT * FROM connectors.owntracks_points ORDER BY id")
            ]
            await insert(5)
            await run_both()
            checkpoint = await pool.fetchval(
                "SELECT watermark FROM projection_checkpoints WHERE source_name='owntracks.points'"
            )
            assert checkpoint == _NOW + timedelta(minutes=5)
            await insert(15)
            await insert(25)

            # Replay overlaps BOTH the checkpoint-known seed and pending fix.
            # Expiration must not become fresh evidence creation just because
            # those source UUIDs appear in the current full/bounded fetch.
            # Roll back each control to retain the original later obligations.
            for replay_since in (None, _NOW - timedelta(seconds=1)):
                pending_before = dict(
                    await pool.fetchrow(
                        "SELECT * FROM point_events WHERE payload->>'endpoint_identity'=$1 AND occurred_at=$2",
                        endpoints[1],
                        _NOW + timedelta(minutes=5),
                    )
                )
                async with pool.acquire() as conn:
                    replay_control = conn.transaction()
                    await replay_control.start()
                    try:
                        await conn.execute(
                            "DELETE FROM point_events WHERE id=$1", pending_before["id"]
                        )
                        replayed = await point_adapter.project(
                            conn, chronicler_pool=conn, since=replay_since
                        )
                        assert replayed.error is None
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM point_events WHERE payload->>'endpoint_identity'=$1 AND occurred_at=ANY($2::timestamptz[])",
                                endpoints[1],
                                [_NOW, _NOW + timedelta(minutes=5)],
                            )
                            == 0
                        )
                        retained_after = dict(
                            await conn.fetchrow(
                                "SELECT * FROM point_events WHERE id=$1", seed["id"]
                            )
                        )
                        assert {k: v for k, v in retained_after.items() if k != "payload"} == {
                            k: v for k, v in before.items() if k != "payload"
                        }
                        assert {
                            k: v
                            for k, v in retained_after["payload"].items()
                            if k != "trail_outlier"
                        } == before["payload"]
                        assert (
                            retained_after["payload"]["trail_outlier"]["reason"] == "implied_speed"
                        )
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM point_events WHERE payload->>'endpoint_identity'=$1 AND occurred_at=$2 AND tombstone_at IS NOT NULL AND NOT(payload ? 'trail_outlier')",
                                endpoints[2],
                                _NOW,
                            )
                            == 1
                        )
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM point_events WHERE occurred_at=$1 AND tombstone_at IS NULL",
                                _NOW + timedelta(minutes=25),
                            )
                            == 3
                        )
                    finally:
                        await replay_control.rollback()
                assert (
                    dict(
                        await pool.fetchrow(
                            "SELECT * FROM point_events WHERE id=$1", pending_before["id"]
                        )
                    )
                    == pending_before
                )
                assert (
                    await pool.fetchval(
                        "SELECT watermark FROM projection_checkpoints WHERE source_name='owntracks.points'"
                    )
                    == checkpoint
                )

            async def fail_save(*args, **kwargs):
                raise asyncpg.PostgresError("controlled bootstrap state refusal")

            monkeypatch.setattr(points, "save_carryover", fail_save)
            refused = await point_adapter.run(pool=pool, chronicler_pool=pool)
            assert refused.error is not None
            assert (
                await pool.fetchval(
                    "SELECT watermark FROM projection_checkpoints WHERE source_name='owntracks.points'"
                )
                == checkpoint
            )
            unchanged = await pool.fetchval(
                "SELECT payload FROM point_events WHERE id=$1", seed["id"]
            )
            assert "trail_outlier" not in unchanged
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM episodes WHERE source_name='owntracks.points'"
                )
                == 0
            )
            monkeypatch.setattr(points, "save_carryover", real_save)

            # Use DISTINCT connections here: a one-slot pool alone would hide
            # a missing advisory lock. Hold the first writer at real persistence,
            # then positively observe the second writer waiting in PostgreSQL.
            holding = asyncio.Event()
            release_writer = asyncio.Event()

            async def hold_first_save(
                *args, holding=holding, release_writer=release_writer, **kwargs
            ):
                holding.set()
                await release_writer.wait()
                return await real_save(*args, **kwargs)

            monkeypatch.setattr(points, "save_carryover", hold_first_save)
            first = asyncio.create_task(
                point_adapter.run(pool=parallel_pool, chronicler_pool=parallel_pool)
            )
            second = None
            try:
                await asyncio.wait_for(holding.wait(), timeout=5)
                second = asyncio.create_task(
                    point_adapter.run(pool=parallel_pool, chronicler_pool=parallel_pool)
                )

                async def observe_lock_waiter() -> bool:
                    for _ in range(500):
                        if await parallel_pool.fetchval(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE locktype='advisory' AND NOT granted "
                            "AND database=(SELECT oid FROM pg_database "
                            "WHERE datname=current_database()))"
                        ):
                            return True
                        await asyncio.sleep(0.01)
                    return False

                assert await observe_lock_waiter(), "second source writer never waited on its lock"
            finally:
                release_writer.set()
                tasks = [first] if second is None else [first, second]
                try:
                    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    monkeypatch.setattr(points, "save_carryover", real_save)
            assert all(result.error is None for result in results)
            assert (await place_adapter.run(pool=pool, chronicler_pool=pool)).error is None
            after = dict(await pool.fetchrow("SELECT * FROM point_events WHERE id=$1", seed["id"]))
            assert after["payload"]["trail_outlier"]["reason"] == "implied_speed"
            assert {k: v for k, v in after.items() if k != "payload"} == {
                k: v for k, v in before.items() if k != "payload"
            }
            assert {k: v for k, v in after["payload"].items() if k != "trail_outlier"} == before[
                "payload"
            ]
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM point_events WHERE payload->>'endpoint_identity'=$1 AND occurred_at=$2",
                    endpoints[1],
                    _NOW,
                )
                == 0
            )
            tombstoned = await pool.fetchrow(
                "SELECT tombstone_at,payload FROM point_events WHERE payload->>'endpoint_identity'=$1 AND occurred_at=$2",
                endpoints[2],
                _NOW,
            )
            assert (
                tombstoned["tombstone_at"] is not None
                and "trail_outlier" not in tombstoned["payload"]
            )
            for endpoint in endpoints:
                movement = await pool.fetchrow(
                    "SELECT * FROM episodes WHERE source_name='owntracks.points' AND payload->>'endpoint_identity'=$1",
                    endpoint,
                )
                place = await pool.fetchrow(
                    "SELECT * FROM episodes WHERE source_name=$1 AND payload->>'endpoint_identity'=$2",
                    SOURCE_NAME,
                    endpoint,
                )
                assert movement["start_at"] == place["start_at"] == _NOW + timedelta(minutes=5)
                assert movement["payload"]["point_count"] == place["payload"]["point_count"] == 3
            for row in raw:
                assert (
                    dict(
                        await pool.fetchrow(
                            "SELECT * FROM connectors.owntracks_points WHERE id=$1", row["id"]
                        )
                    )
                    == row
                )

            await insert(35)
            entered = asyncio.Event()

            async def suspend_save(*args, entered=entered, **kwargs):
                entered.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(points, "save_carryover", suspend_save)
            cancelled = asyncio.create_task(point_adapter.run(pool=pool, chronicler_pool=pool))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
            finally:
                cancelled.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cancelled
            assert await pool.fetchval(
                "SELECT watermark FROM projection_checkpoints WHERE source_name='owntracks.points'"
            ) == _NOW + timedelta(minutes=25)
            monkeypatch.setattr(points, "save_carryover", real_save)
            resumed = await asyncio.wait_for(
                point_adapter.run(pool=pool, chronicler_pool=pool), timeout=5
            )
            assert resumed.error is None
            assert await pool.fetchval(
                "SELECT watermark FROM projection_checkpoints WHERE source_name='owntracks.points'"
            ) == _NOW + timedelta(minutes=35)
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM episodes WHERE source_name='owntracks.points'"
                )
                == 3
            )
