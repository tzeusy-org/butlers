"""Real-Postgres proofs for OwnTracks projection coverage (bu-s11n0s.7, slice 2).

``owntracks.points`` writes ``chronicler.location_projection_coverage`` and
``chronicler.location_projection_open_members`` on its projection transaction (chronicler_032),
reading each raw row's immutable ``retention_at`` (core_340). Every assertion reads committed
state through a fresh query after the adapter run returned.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import (
    EPISODE_TYPE_MOVEMENT,
    EVENT_TYPE_LOCATION,
    MOVEMENT_CARRY_PREFIX,
    SOURCE_NAME,
    OwnTracksPointAdapter,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Layer, PointEvent, Precision, Privacy
from butlers.chronicler.storage import upsert_point_event

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_T0 = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"
_CARRY = f"{MOVEMENT_CARRY_PREFIX}{_ENDPOINT}"
_LAT, _LON = 1.30000, 103.80000


@pytest.fixture
def coverage_url(fresh_db) -> str:
    """Core (raw evidence + core_340) and Chronicler at head (chronicler_032)."""
    from tests.chronicler._private_memory_setup import prepare_chronicler_current_head_sync

    url = fresh_db(
        "core",
        "chronicler",
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    prepare_chronicler_current_head_sync(url)
    return url


@pytest.fixture
async def chronicler_db(db, coverage_url: str) -> asyncpg.Pool:
    conn_pool = await db.open(coverage_url, schema="chronicler", max_size=5)
    await seed_source_registry(conn_pool, sources=INITIAL_SOURCES)
    return conn_pool


async def _insert_point(
    target: asyncpg.Pool,
    *,
    ts: datetime,
    key: str | None = None,
    recorded_at: datetime | None = None,
    lat: float = _LAT,
) -> UUID:
    return await target.fetchval(
        """
        INSERT INTO connectors.owntracks_points
            (idempotency_key, ts, lat, lon, accuracy, trigger, endpoint_identity, raw_payload,
             recorded_at)
        VALUES ($1, $2, $3, $4, 10.0, 'p', $5, $6, $7)
        RETURNING id
        """,
        key or f"owntracks:{_ENDPOINT}:{int(ts.timestamp())}:location",
        ts,
        lat,
        _LON,
        _ENDPOINT,
        {"_type": "location"},
        recorded_at or ts,
    )


async def _run(target: asyncpg.Pool, **kwargs) -> None:
    result = await OwnTracksPointAdapter(**kwargs).run(pool=target, chronicler_pool=target)
    assert result.error is None, result.error


async def _coverage(target: asyncpg.Pool) -> dict[UUID, asyncpg.Record]:
    rows = await target.fetch(
        "SELECT * FROM location_projection_coverage WHERE adapter_name = $1", SOURCE_NAME
    )
    return {row["raw_source_id"]: row for row in rows}


async def _open(target: asyncpg.Pool) -> dict[UUID, asyncpg.Record]:
    rows = await target.fetch(
        "SELECT * FROM location_projection_open_members WHERE adapter_name = $1", SOURCE_NAME
    )
    return {row["raw_source_id"]: row for row in rows}


async def _event_id(target: asyncpg.Pool, raw_id: UUID) -> UUID:
    return await target.fetchval(
        """
        SELECT e.id FROM point_events e
        JOIN connectors.owntracks_points p
          ON e.source_ref = 'connectors.owntracks_points:' || p.idempotency_key
        WHERE p.id = $1 AND e.source_name = $2 AND e.event_type = $3
        """,
        raw_id,
        SOURCE_NAME,
        EVENT_TYPE_LOCATION,
    )


async def _episode_starting(target: asyncpg.Pool, start_at: datetime) -> UUID:
    return await target.fetchval(
        "SELECT id FROM episodes WHERE source_name = $1 AND episode_type = $2 AND start_at = $3",
        SOURCE_NAME,
        EPISODE_TYPE_MOVEMENT,
        start_at,
    )


async def _retention_at(target: asyncpg.Pool, raw_id: UUID) -> datetime:
    return await target.fetchval(
        "SELECT retention_at FROM connectors.owntracks_points WHERE id = $1", raw_id
    )


async def test_closed_segment_is_covered_and_open_segment_waits_until_it_closes(
    db, coverage_url: str, chronicler_db: asyncpg.Pool
) -> None:
    async def _as_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_chronicler_rw")

    # The adapter runs as the production runtime role, so its grants are part of the proof.
    runtime = await db.open(coverage_url, schema="chronicler", init=_as_runtime_role, max_size=5)
    closed = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (0, 5, 10)]
    held = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (60, 65)]

    await _run(runtime)

    coverage, members = await _coverage(chronicler_db), await _open(chronicler_db)
    first_episode = await _episode_starting(chronicler_db, _T0)
    assert first_episode is not None
    assert set(coverage) == set(closed)
    for raw_id in closed:
        record = coverage[raw_id]
        assert record["disposition"] == "projected"
        assert record["output_event_ids"] == [await _event_id(chronicler_db, raw_id)]
        assert record["output_episode_ids"] == [first_episode]
        assert record["retention_at"] == await _retention_at(chronicler_db, raw_id)
        assert record["logical_source_key"].startswith(f"owntracks:{_ENDPOINT}:")
    assert set(members) == set(held)
    assert {member["carry_key"] for member in members.values()} == {_CARRY}
    # The open segment is already upserted as an episode, which is not terminal.
    open_episode = await _episode_starting(chronicler_db, _T0 + timedelta(minutes=60))
    assert open_episode is not None
    assert all(member["output_episode_ids"] == [open_episode] for member in members.values())

    batch = coverage[closed[0]]["projection_batch_id"]
    assert {record["projection_batch_id"] for record in coverage.values()} == {batch}

    closer = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=120))
    await _run(runtime)

    coverage, members = await _coverage(chronicler_db), await _open(chronicler_db)
    assert set(coverage) == {*closed, *held}
    for raw_id in held:
        assert coverage[raw_id]["output_event_ids"] == [await _event_id(chronicler_db, raw_id)]
        assert coverage[raw_id]["output_episode_ids"] == [open_episode]
        assert coverage[raw_id]["projection_batch_id"] != batch
    assert set(members) == {closer}


async def test_identical_arrival_and_device_times_cover_each_row_once_across_batches(
    chronicler_db: asyncpg.Pool,
) -> None:
    tied = [
        await _insert_point(chronicler_db, ts=_T0, key=f"tie:{suffix}") for suffix in ("a", "b")
    ]
    follower = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=5))
    closer = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=60))
    for _ in range(4):  # one row per run: the (recorded_at, id) cursor splits the tie
        await _run(chronicler_db, batch_limit=1)

    coverage = await _coverage(chronicler_db)
    assert set(coverage) == {*tied, follower}
    assert closer in await _open(chronicler_db)
    for raw_id in (*tied, follower):
        assert coverage[raw_id]["output_event_ids"] == [await _event_id(chronicler_db, raw_id)]
    assert (
        await chronicler_db.fetchval(
            "SELECT count(*) FROM point_events WHERE source_name = $1", SOURCE_NAME
        )
        == 4
    )
    episode = await _episode_starting(chronicler_db, _T0)
    first_read, second_read = sorted(tied, key=lambda raw_id: raw_id.int)
    # The first tied row starts the movement; the second arrives at the open carry's
    # horizon, so it is a late fix with a point event only (the adapter's late rule).
    assert coverage[first_read]["output_episode_ids"] == [episode]
    assert coverage[follower]["output_episode_ids"] == [episode]
    assert coverage[second_read]["output_episode_ids"] == []


async def test_late_offline_fix_is_covered_by_device_age_on_a_later_run(
    chronicler_db: asyncpg.Pool,
) -> None:
    for m in (0, 5, 60, 65):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m))
    await _run(chronicler_db)

    late = await _insert_point(
        chronicler_db, ts=_T0 + timedelta(minutes=2), recorded_at=_T0 + timedelta(hours=3)
    )
    assert await _retention_at(chronicler_db, late) == _T0 + timedelta(minutes=2)
    await _run(chronicler_db)
    assert await _event_id(chronicler_db, late) is not None
    # A later fix (arriving after the late one) resolves the trail and closes the old carry.
    await _insert_point(
        chronicler_db,
        ts=_T0 + timedelta(hours=3, minutes=5),
        recorded_at=_T0 + timedelta(hours=3, minutes=6),
    )
    await _run(chronicler_db)

    record = (await _coverage(chronicler_db))[late]
    assert record["output_event_ids"] == [await _event_id(chronicler_db, late)]
    assert record["output_episode_ids"] == []
    assert record["retention_at"] == _T0 + timedelta(minutes=2)
    assert late not in await _open(chronicler_db)


async def test_failed_run_rolls_back_coverage_with_outputs_and_cursor(
    chronicler_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import butlers.chronicler.adapters.owntracks as points

    for m in (0, 5, 10, 60, 65):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m))
    seen_inside: list[tuple[int, int]] = []

    async def _fail_after_coverage(conn, source_name, carryover):
        seen_inside.append(
            (
                await conn.fetchval("SELECT count(*) FROM location_projection_coverage"),
                await conn.fetchval("SELECT count(*) FROM location_projection_open_members"),
            )
        )
        raise RuntimeError("checkpoint write failed")

    monkeypatch.setattr(points, "save_carryover", _fail_after_coverage)
    result = await OwnTracksPointAdapter().run(pool=chronicler_db, chronicler_pool=chronicler_db)

    assert result.error == "checkpoint write failed"
    assert seen_inside == [(3, 2)], "coverage was written inside the failing savepoint"
    assert await _coverage(chronicler_db) == {}
    assert await _open(chronicler_db) == {}
    assert await chronicler_db.fetchval("SELECT count(*) FROM point_events") == 0
    assert (
        await chronicler_db.fetchval(
            "SELECT carryover -> '_ingest_cursor' FROM projection_checkpoints "
            "WHERE source_name = $1 AND subsource = ''",
            SOURCE_NAME,
        )
        is None
    )


async def test_coverage_is_never_derived_from_outputs_the_run_did_not_write(
    chronicler_db: asyncpg.Pool,
) -> None:
    first = await _insert_point(chronicler_db, ts=_T0)
    owner_deleted = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=5))
    await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=10))
    malformed = await _insert_point(
        chronicler_db, ts=_T0 + timedelta(minutes=12), lat=float("nan"), key="malformed"
    )
    await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=60))

    # The owner already tombstoned the projection of one row: an existing, unrelated-to-this-run
    # point event must not make that row look projected.
    key = await chronicler_db.fetchval(
        "SELECT idempotency_key FROM connectors.owntracks_points WHERE id = $1", owner_deleted
    )
    await upsert_point_event(
        chronicler_db,
        PointEvent(
            source_name=SOURCE_NAME,
            source_ref=f"connectors.owntracks_points:{key}",
            event_type=EVENT_TYPE_LOCATION,
            occurred_at=_T0 + timedelta(minutes=5),
            precision=Precision.EXACT,
            title="Location",
            payload={},
            privacy=Privacy.NORMAL,
            layer=Layer.EVIDENCE,
        ),
    )
    await chronicler_db.execute(
        "UPDATE point_events SET tombstone_at = now() WHERE source_ref = $1",
        f"connectors.owntracks_points:{key}",
    )

    await _run(chronicler_db)

    coverage, members = await _coverage(chronicler_db), await _open(chronicler_db)
    assert first in coverage, "positive companion: an ordinary projected row is covered"
    assert owner_deleted not in coverage and owner_deleted not in members
    assert malformed not in coverage and malformed not in members


async def test_only_the_chronicler_runtime_role_reaches_coverage_and_it_is_append_only(
    chronicler_db: asyncpg.Pool,
) -> None:
    existing = {
        row["rolname"]
        for row in await chronicler_db.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])",
            ["butler_chronicler_rw", "connector_writer", "butler_switchboard_rw"],
        )
    }
    assert "butler_chronicler_rw" in existing

    def _privilege(role: str, table: str, privilege: str) -> str:
        return f"has_table_privilege('{role}', 'chronicler.{table}', '{privilege}')"

    expected = {
        ("location_projection_coverage", "SELECT"): True,
        ("location_projection_coverage", "INSERT"): True,
        ("location_projection_coverage", "UPDATE"): False,
        ("location_projection_coverage", "DELETE"): False,
        ("location_projection_open_members", "SELECT"): True,
        ("location_projection_open_members", "INSERT"): True,
        ("location_projection_open_members", "DELETE"): True,
        ("location_projection_open_members", "UPDATE"): False,
    }
    for (table, privilege), allowed in expected.items():
        assert (
            await chronicler_db.fetchval(
                f"SELECT {_privilege('butler_chronicler_rw', table, privilege)}"
            )
            is allowed
        ), (table, privilege)
    for role in existing - {"butler_chronicler_rw"}:
        for table in ("location_projection_coverage", "location_projection_open_members"):
            assert not await chronicler_db.fetchval(
                f"SELECT {_privilege(role, table, 'SELECT')}"
            ), (role, table)

    raw_id = await _insert_point(chronicler_db, ts=_T0)
    async with chronicler_db.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_chronicler_rw")
            await conn.execute(
                """
                INSERT INTO location_projection_coverage (adapter_name, raw_source_id,
                    logical_source_key, retention_at, disposition, output_event_ids,
                    projection_batch_id)
                VALUES ($1, $2, 'k', $3, 'projected', ARRAY[gen_random_uuid()],
                        gen_random_uuid())
                """,
                SOURCE_NAME,
                raw_id,
                _T0,
            )
            assert await conn.fetchval(
                "SELECT count(*) FROM location_projection_coverage WHERE raw_source_id = $1",
                raw_id,
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE location_projection_coverage SET disposition = 'projected'"
                    )

    # Even a role that holds UPDATE (here the migrating owner) cannot weaken a record.
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="append-only"):
        await chronicler_db.execute(
            "DELETE FROM location_projection_coverage WHERE raw_source_id = $1", raw_id
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await chronicler_db.execute(
            """
            INSERT INTO location_projection_coverage (adapter_name, raw_source_id,
                logical_source_key, retention_at, disposition, projection_batch_id)
            VALUES ($1, gen_random_uuid(), 'k', $2, 'projected', gen_random_uuid())
            """,
            SOURCE_NAME,
            _T0,
        )
