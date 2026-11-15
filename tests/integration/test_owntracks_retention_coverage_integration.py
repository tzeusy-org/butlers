"""Real-Postgres proofs for OwnTracks projection coverage (bu-s11n0s.7, slice 2).

``owntracks.points``, ``owntracks.place_cluster`` and ``owntracks.ssid_presence`` write
``chronicler.location_projection_coverage`` and ``chronicler.location_projection_open_members``
(chronicler_032, multi-key carries since chronicler_033) on their projection transactions,
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
from butlers.chronicler.adapters.owntracks_place_cluster import (
    SOURCE_NAME as PLACE_SOURCE_NAME,
)
from butlers.chronicler.adapters.owntracks_place_cluster import (
    STAY_CARRY_PREFIX,
    OwnTracksPlaceClusterAdapter,
)
from butlers.chronicler.adapters.owntracks_ssid import (
    SOURCE_NAME as SSID_SOURCE_NAME,
)
from butlers.chronicler.adapters.owntracks_ssid import (
    OwnTracksSsidPresenceAdapter,
    presence_carry_key,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Layer, PointEvent, Precision, Privacy
from butlers.chronicler.projection_coverage import (
    DISCOVERY_LIMIT,
    UncoveredScan,
    config_revision,
    discover_uncovered,
    raw_row_digest_sql,
)
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
_REVISION = OwnTracksPointAdapter().coverage_revision
_DIGEST = "v1:" + "b" * 64


async def _scan_all(
    conn: asyncpg.pool.PoolConnectionProxy, revision: str, *, limit: int = DISCOVERY_LIMIT
) -> UncoveredScan:
    """Discovery over the fixed 2026-03 fixture rows: no settle window, a lookback reaching them."""
    return await discover_uncovered(
        conn,
        SOURCE_NAME,
        config_revision=revision,
        settle=timedelta(0),
        lookback=timedelta(days=36500),
        limit=limit,
    )


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
    ssid: str | None = None,
) -> UUID:
    payload: dict[str, object] = {"_type": "location"}
    if ssid is not None:
        payload["SSID"] = ssid
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
        payload,
        recorded_at or ts,
    )


async def _run(target: asyncpg.Pool, **kwargs) -> None:
    result = await OwnTracksPointAdapter(**kwargs).run(pool=target, chronicler_pool=target)
    assert result.error is None, result.error


_SSID_PLACES = {"home-wifi": "home"}


async def _run_place(target: asyncpg.Pool) -> None:
    result = await OwnTracksPlaceClusterAdapter().run(pool=target, chronicler_pool=target)
    assert result.error is None, result.error


async def _run_ssid(target: asyncpg.Pool) -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES)
    result = await adapter.run(pool=target, chronicler_pool=target)
    assert result.error is None, result.error


async def _coverage(target: asyncpg.Pool, adapter: str = SOURCE_NAME) -> dict[UUID, asyncpg.Record]:
    rows = await target.fetch(
        "SELECT * FROM location_projection_coverage WHERE adapter_name = $1", adapter
    )
    return {row["raw_source_id"]: row for row in rows}


async def _open(target: asyncpg.Pool, adapter: str = SOURCE_NAME) -> dict[UUID, asyncpg.Record]:
    rows = await target.fetch(
        "SELECT * FROM location_projection_open_members WHERE adapter_name = $1", adapter
    )
    return {row["raw_source_id"]: row for row in rows}


async def _episode_of(target: asyncpg.Pool, adapter: str, start_at: datetime) -> UUID:
    return await target.fetchval(
        "SELECT id FROM episodes WHERE source_name = $1 AND start_at = $2", adapter, start_at
    )


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


async def _stored_digest(target: asyncpg.Pool, raw_id: UUID) -> str:
    return await target.fetchval(
        f"SELECT {raw_row_digest_sql()} FROM connectors.owntracks_points WHERE id = $1", raw_id
    )


async def _attempt(target: asyncpg.Pool, adapter: str = SOURCE_NAME) -> asyncpg.Record:
    return await target.fetchrow(
        "SELECT * FROM location_projection_attempts WHERE adapter_name = $1", adapter
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
    assert {tuple(member["carry_keys"]) for member in members.values()} == {(_CARRY,)}
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
    # The late fix is the trail's pending candidate, re-read and still revisable: held under
    # its trail carry, never covered yet.
    held = await _open(chronicler_db)
    assert late in held
    assert any(key.startswith("trail:") for key in held[late]["carry_keys"])
    assert late not in await _coverage(chronicler_db)
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
        ("location_projection_attempts", "SELECT"): True,
        ("location_projection_attempts", "INSERT"): True,
        ("location_projection_attempts", "UPDATE"): True,
        ("location_projection_attempts", "DELETE"): False,
    }
    for (table, privilege), allowed in expected.items():
        assert (
            await chronicler_db.fetchval(
                f"SELECT {_privilege('butler_chronicler_rw', table, privilege)}"
            )
            is allowed
        ), (table, privilege)
    for role in existing - {"butler_chronicler_rw"}:
        for table in (
            "location_projection_coverage",
            "location_projection_open_members",
            "location_projection_attempts",
        ):
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
                    config_revision, content_digest, logical_source_key, retention_at,
                    disposition, output_event_ids, projection_batch_id)
                VALUES ($1, $2, $4, $5, 'k', $3, 'projected', ARRAY[gen_random_uuid()],
                        gen_random_uuid())
                """,
                SOURCE_NAME,
                raw_id,
                _T0,
                _REVISION,
                _DIGEST,
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
                config_revision, content_digest, logical_source_key, retention_at, disposition,
                projection_batch_id)
            VALUES ($1, gen_random_uuid(), $3, $4, 'k', $2, 'projected', gen_random_uuid())
            """,
            SOURCE_NAME,
            _T0,
            _REVISION,
            _DIGEST,
        )


# -- owntracks.place_cluster and owntracks.ssid_presence (PR-B) -------------------------------

_FAR_LAT = 1.31000  # ~1.1 km north: another stay


async def test_place_stay_is_held_while_open_and_covered_once_it_closes(
    chronicler_db: asyncpg.Pool,
) -> None:
    stay = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (0, 10, 25)]
    await _run_place(chronicler_db)

    members = await _open(chronicler_db, PLACE_SOURCE_NAME)
    assert set(stay) <= set(members)
    episode = await _episode_of(chronicler_db, PLACE_SOURCE_NAME, _T0)
    assert episode is not None
    for raw_id in stay:
        assert f"{STAY_CARRY_PREFIX}{_ENDPOINT}" in members[raw_id]["carry_keys"]
        assert members[raw_id]["output_episode_ids"] == [episode]
    assert not set(stay) & set(await _coverage(chronicler_db, PLACE_SOURCE_NAME))

    for m in (30, 35):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), lat=_FAR_LAT)
    await _run_place(chronicler_db)

    coverage = await _coverage(chronicler_db, PLACE_SOURCE_NAME)
    for raw_id in stay:
        assert coverage[raw_id]["disposition"] == "projected"
        assert coverage[raw_id]["output_episode_ids"] == [episode]
        assert coverage[raw_id]["retention_at"] == await _retention_at(chronicler_db, raw_id)
        assert coverage[raw_id]["content_digest"] == await _stored_digest(chronicler_db, raw_id)
        assert (
            coverage[raw_id]["config_revision"] == OwnTracksPlaceClusterAdapter().coverage_revision
        )
    assert not set(stay) & set(await _open(chronicler_db, PLACE_SOURCE_NAME))


async def test_place_failed_run_rolls_back_coverage(
    chronicler_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import butlers.chronicler.adapters.owntracks_place_cluster as place

    for m in (0, 10, 25):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m))
    seen_inside: list[int] = []

    async def _fail_after_coverage(conn, source_name, carryover):
        seen_inside.append(
            await conn.fetchval("SELECT count(*) FROM location_projection_open_members")
        )
        raise RuntimeError("checkpoint write failed")

    monkeypatch.setattr(place, "save_carryover", _fail_after_coverage)
    result = await OwnTracksPlaceClusterAdapter().run(
        pool=chronicler_db, chronicler_pool=chronicler_db
    )

    assert result.error == "checkpoint write failed"
    assert seen_inside and seen_inside[0] > 0, "coverage was written inside the failing run"
    assert await _coverage(chronicler_db, PLACE_SOURCE_NAME) == {}
    assert await _open(chronicler_db, PLACE_SOURCE_NAME) == {}


async def test_ssid_presence_is_held_while_open_and_unmapped_fixes_are_evaluated(
    chronicler_db: asyncpg.Pool,
) -> None:
    home = [
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), ssid="home-wifi")
        for m in (0, 10)
    ]
    await _run_ssid(chronicler_db)

    members = await _open(chronicler_db, SSID_SOURCE_NAME)
    episode = await _episode_of(chronicler_db, SSID_SOURCE_NAME, _T0)
    assert episode is not None
    assert set(members) == set(home)
    for raw_id in home:
        assert list(members[raw_id]["carry_keys"]) == [presence_carry_key(_ENDPOINT, _T0)]
        assert members[raw_id]["output_episode_ids"] == [episode]

    unmapped = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=15), ssid="cafe-wifi")
    await _run_ssid(chronicler_db)

    coverage = await _coverage(chronicler_db, SSID_SOURCE_NAME)
    for raw_id in home:
        assert coverage[raw_id]["disposition"] == "projected"
        assert coverage[raw_id]["output_episode_ids"] == [episode]
    assert coverage[unmapped]["disposition"] == "evaluated_no_output"
    assert coverage[unmapped]["output_episode_ids"] == []
    assert await _open(chronicler_db, SSID_SOURCE_NAME) == {}


async def test_ssid_failed_run_rolls_back_coverage_and_records_the_failure(
    chronicler_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import butlers.chronicler.adapters.owntracks_ssid as ssid

    for m in (0, 10):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), ssid="home-wifi")
    seen_inside: list[int] = []

    async def _fail_after_coverage(conn, source_name, carryover):
        seen_inside.append(
            await conn.fetchval("SELECT count(*) FROM location_projection_open_members")
        )
        raise RuntimeError("checkpoint write failed")

    monkeypatch.setattr(ssid, "save_carryover", _fail_after_coverage)
    result = await OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES).run(
        pool=chronicler_db, chronicler_pool=chronicler_db
    )

    assert result.error == "checkpoint write failed"
    assert seen_inside == [2], "coverage was written inside the failing transaction"
    assert await _coverage(chronicler_db, SSID_SOURCE_NAME) == {}
    assert await _open(chronicler_db, SSID_SOURCE_NAME) == {}
    assert (
        await chronicler_db.fetchval(
            "SELECT count(*) FROM episodes WHERE source_name = $1", SSID_SOURCE_NAME
        )
        == 0
    )
    # The failure is recorded after the rollback, outside the projection transaction.
    checkpoint = await chronicler_db.fetchrow(
        "SELECT watermark, last_error FROM projection_checkpoints "
        "WHERE source_name = $1 AND subsource = ''",
        SSID_SOURCE_NAME,
    )
    assert checkpoint is not None
    assert checkpoint["last_error"] == "checkpoint write failed"
    assert checkpoint["watermark"] is None
    attempt = await _attempt(chronicler_db, SSID_SOURCE_NAME)
    assert attempt["last_outcome"] == "failed"
    assert attempt["last_error_class"] == "RuntimeError"
    assert attempt["last_success_at"] is None


# -- exact records, attempts and discovery (PR-C) ---------------------------------------------


async def test_records_name_the_stored_content_and_discovery_finds_what_they_miss(
    db, coverage_url: str, chronicler_db: asyncpg.Pool
) -> None:
    async def _as_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_chronicler_rw")

    runtime = await db.open(coverage_url, schema="chronicler", init=_as_runtime_role, max_size=5)
    closed = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (0, 5, 10)]
    held = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=60))
    await _run(runtime)

    coverage = await _coverage(chronicler_db)
    assert set(coverage) == set(closed)
    for raw_id in closed:
        assert coverage[raw_id]["content_digest"] == await _stored_digest(chronicler_db, raw_id)
        assert coverage[raw_id]["config_revision"] == _REVISION
    assert held in await _open(chronicler_db)

    # The run committed its attempt under the runtime role; the fixture rows are older than the
    # discovery window, so the stored count is zero.
    attempt = await _attempt(chronicler_db)
    assert attempt["last_outcome"] == "succeeded" and attempt["last_success_at"] is not None
    assert attempt["config_revision"] == _REVISION
    assert (attempt["uncovered_rows"], attempt["uncovered_capped"]) == (0, False)
    assert attempt["uncovered_checked_at"] is not None

    async with runtime.acquire() as conn:
        scan = await _scan_all(conn, _REVISION)
        assert (scan.count, scan.capped) == (0, False), "covered and held rows are not pending"

        # An upstream re-write changes a covered row's content: its record no longer matches.
        await chronicler_db.execute(
            "UPDATE connectors.owntracks_points SET accuracy = 99.0 WHERE id = $1", closed[1]
        )
        scan = await _scan_all(conn, _REVISION)
        assert scan.count == 1

        # A changed config revision makes every record stale; held rows still wait.
        other = config_revision(SOURCE_NAME, {"case": "changed"})
        scan = await _scan_all(conn, other)
        assert scan.count == len(closed)
        # Bounded: the count stops at the limit and says so.
        scan = await _scan_all(conn, other, limit=2)
        assert (scan.count, scan.capped) == (2, True)

    # A late commit the cursor passed: its arrival time is before the stored cursor, so the
    # adapter never reads it; only discovery's anti-join sees it.
    behind = await _insert_point(
        chronicler_db,
        ts=_T0 + timedelta(minutes=7),
        recorded_at=_T0 + timedelta(minutes=7),
        key="late-commit",
    )
    await _run(runtime)
    assert behind not in await _coverage(chronicler_db)
    async with runtime.acquire() as conn:
        scan = await _scan_all(conn, _REVISION)
        assert scan.count == 2  # the re-written row and the row behind the cursor


async def test_a_failed_run_commits_its_attempt_and_keeps_the_last_success(
    chronicler_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import butlers.chronicler.adapters.owntracks as points

    for m in (0, 5, 60):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m))
    await _run(chronicler_db)
    succeeded = await _attempt(chronicler_db)
    covered = await _coverage(chronicler_db)
    assert succeeded["last_outcome"] == "succeeded" and covered

    await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=120))

    async def _fail(conn, source_name, carryover):
        raise RuntimeError("raw point at lat 1.3 failed")

    monkeypatch.setattr(points, "save_carryover", _fail)
    for expected_failures in (1, 2):
        result = await OwnTracksPointAdapter().run(
            pool=chronicler_db, chronicler_pool=chronicler_db
        )
        assert result.error == "raw point at lat 1.3 failed"
        failed = await _attempt(chronicler_db)
        assert failed["last_outcome"] == "failed"
        assert failed["last_error_class"] == "RuntimeError", "never the message"
        assert failed["consecutive_failures"] == expected_failures
        assert failed["last_success_at"] == succeeded["last_success_at"]
        assert failed["last_failure_at"] is not None
        assert failed["uncovered_checked_at"] == succeeded["uncovered_checked_at"]
    assert await _coverage(chronicler_db) == covered, "the failed run's coverage rolled back"

    monkeypatch.undo()
    await _run(chronicler_db)
    recovered = await _attempt(chronicler_db)
    assert recovered["last_outcome"] == "succeeded"
    assert recovered["consecutive_failures"] == 0
    assert recovered["last_error_class"] == "RuntimeError", "the last failure stays visible"


async def test_an_ssid_mapping_change_re_decides_rows_under_the_new_revision(
    chronicler_db: asyncpg.Pool,
) -> None:
    home = [
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), ssid="home-wifi")
        for m in (0, 10)
    ]
    first = OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES)
    assert (await first.run(pool=chronicler_db, chronicler_pool=chronicler_db)).error is None
    assert set(await _open(chronicler_db, SSID_SOURCE_NAME)) == set(home)

    # The owner removes the network: the replay reads the rows again and evaluates them.
    changed = OwnTracksSsidPresenceAdapter(ssid_places={})
    assert changed.coverage_revision != first.coverage_revision
    result = await changed.run(pool=chronicler_db, chronicler_pool=chronicler_db)
    assert result.error is None, result.error

    coverage = await _coverage(chronicler_db, SSID_SOURCE_NAME)
    for raw_id in home:
        record = coverage[raw_id]
        assert record["config_revision"] == changed.coverage_revision
        assert record["disposition"] == "evaluated_no_output"
        assert record["output_episode_ids"] == []
    assert await _open(chronicler_db, SSID_SOURCE_NAME) == {}
    attempt = await _attempt(chronicler_db, SSID_SOURCE_NAME)
    assert attempt["config_revision"] == changed.coverage_revision
    assert attempt["last_outcome"] == "succeeded"


# -- a config revision change while a carry is open --------------------------------------------
#
# A member held under the old revision gained its outputs from a decision that revision made. A
# later run under a new revision must not close that carry into a current-revision record; the
# member is decided again only if the new revision reads it again. The changed setting
# (``clock_skew_threshold_hours`` 5 instead of 4) is behaviour-neutral for these fixtures, so only
# the revision differs between the runs.

_NEUTRAL_CHANGE = {"clock_skew_threshold_hours": 5}


def _no_old_revision_output(record: asyncpg.Record | None, old_episode: UUID) -> bool:
    return record is None or old_episode not in record["output_episode_ids"]


async def test_a_points_revision_change_voids_open_movement_carries(
    chronicler_db: asyncpg.Pool,
) -> None:
    held = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (0, 5)]
    await _run(chronicler_db)
    assert set(held) <= set(await _open(chronicler_db))
    old_episode = await _episode_starting(chronicler_db, _T0)
    assert old_episode is not None

    await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=120))  # closes the segment
    changed = OwnTracksPointAdapter(**_NEUTRAL_CHANGE)
    assert changed.coverage_revision != _REVISION
    await _run(chronicler_db, **_NEUTRAL_CHANGE)

    coverage = await _coverage(chronicler_db)
    for raw_id in held:
        assert _no_old_revision_output(coverage.get(raw_id), old_episode)
    assert not set(held) & set(await _open(chronicler_db))


async def test_a_place_revision_change_voids_open_stay_carries(
    chronicler_db: asyncpg.Pool,
) -> None:
    stay = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (0, 10, 25)]
    await _run_place(chronicler_db)
    assert set(stay) <= set(await _open(chronicler_db, PLACE_SOURCE_NAME))
    old_episode = await _episode_of(chronicler_db, PLACE_SOURCE_NAME, _T0)
    assert old_episode is not None

    for m in (30, 35):  # leaving the stay closes it under the new revision
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), lat=_FAR_LAT)
    changed = OwnTracksPlaceClusterAdapter(**_NEUTRAL_CHANGE)
    assert changed.coverage_revision != OwnTracksPlaceClusterAdapter().coverage_revision
    result = await changed.run(pool=chronicler_db, chronicler_pool=chronicler_db)
    assert result.error is None, result.error

    coverage = await _coverage(chronicler_db, PLACE_SOURCE_NAME)
    for raw_id in stay:
        assert _no_old_revision_output(coverage.get(raw_id), old_episode)
    assert not set(stay) & set(await _open(chronicler_db, PLACE_SOURCE_NAME))


async def test_an_ssid_settings_change_voids_open_presence_carries(
    chronicler_db: asyncpg.Pool,
) -> None:
    home = [
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), ssid="home-wifi")
        for m in (0, 10)
    ]
    await _run_ssid(chronicler_db)
    assert set(await _open(chronicler_db, SSID_SOURCE_NAME)) == set(home)
    old_episode = await _episode_of(chronicler_db, SSID_SOURCE_NAME, _T0)
    assert old_episode is not None

    # Same mapping, so no replay: only the settings part of the revision moved.
    await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=15), ssid="cafe-wifi")
    changed = OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES, **_NEUTRAL_CHANGE)
    assert (
        changed.coverage_revision
        != OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES).coverage_revision
    )
    result = await changed.run(pool=chronicler_db, chronicler_pool=chronicler_db)
    assert result.error is None, result.error

    coverage = await _coverage(chronicler_db, SSID_SOURCE_NAME)
    for raw_id in home:
        assert _no_old_revision_output(coverage.get(raw_id), old_episode)
    assert not set(home) & set(await _open(chronicler_db, SSID_SOURCE_NAME))


# -- committed readbacks under the runtime role (PR-D) ----------------------------------------


async def _runtime_pool(db, coverage_url: str) -> asyncpg.Pool:
    """A pool whose every connection acts as the production Chronicler runtime role."""

    async def _as_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_chronicler_rw")

    return await db.open(coverage_url, schema="chronicler", init=_as_runtime_role, max_size=5)


async def test_place_coverage_commits_under_the_runtime_role(
    db, coverage_url: str, chronicler_db: asyncpg.Pool
) -> None:
    runtime = await _runtime_pool(db, coverage_url)
    stay = [await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m)) for m in (0, 10, 25)]
    await _run_place(runtime)
    # Read back through a different pool and role: only committed state is visible.
    assert set(stay) <= set(await _open(chronicler_db, PLACE_SOURCE_NAME))

    for m in (30, 35):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), lat=_FAR_LAT)
    await _run_place(runtime)

    coverage = await _coverage(chronicler_db, PLACE_SOURCE_NAME)
    revision = OwnTracksPlaceClusterAdapter().coverage_revision
    for raw_id in stay:
        assert coverage[raw_id]["disposition"] == "projected"
        assert coverage[raw_id]["config_revision"] == revision
        assert coverage[raw_id]["content_digest"] == await _stored_digest(chronicler_db, raw_id)
    assert not set(stay) & set(await _open(chronicler_db, PLACE_SOURCE_NAME))
    attempt = await _attempt(chronicler_db, PLACE_SOURCE_NAME)
    assert attempt["last_outcome"] == "succeeded" and attempt["config_revision"] == revision
    assert attempt["uncovered_checked_at"] is not None


async def test_ssid_coverage_commits_under_the_runtime_role(
    db, coverage_url: str, chronicler_db: asyncpg.Pool
) -> None:
    runtime = await _runtime_pool(db, coverage_url)
    home = [
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), ssid="home-wifi")
        for m in (0, 10)
    ]
    await _run_ssid(runtime)
    assert set(await _open(chronicler_db, SSID_SOURCE_NAME)) == set(home)

    unmapped = await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=15), ssid="cafe-wifi")
    await _run_ssid(runtime)

    coverage = await _coverage(chronicler_db, SSID_SOURCE_NAME)
    revision = OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES).coverage_revision
    for raw_id in home:
        assert coverage[raw_id]["disposition"] == "projected"
        assert coverage[raw_id]["config_revision"] == revision
    assert coverage[unmapped]["disposition"] == "evaluated_no_output"
    assert await _open(chronicler_db, SSID_SOURCE_NAME) == {}
    attempt = await _attempt(chronicler_db, SSID_SOURCE_NAME)
    assert attempt["last_outcome"] == "succeeded" and attempt["config_revision"] == revision


async def test_a_failed_ssid_run_records_its_attempt_under_the_runtime_role(
    db, coverage_url: str, chronicler_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import butlers.chronicler.adapters.owntracks_ssid as ssid

    runtime = await _runtime_pool(db, coverage_url)
    for m in (0, 10):
        await _insert_point(chronicler_db, ts=_T0 + timedelta(minutes=m), ssid="home-wifi")

    async def _fail(*_args, **_kwargs):
        raise RuntimeError("checkpoint write failed")

    monkeypatch.setattr(ssid, "save_carryover", _fail)
    result = await OwnTracksSsidPresenceAdapter(ssid_places=_SSID_PLACES).run(
        pool=runtime, chronicler_pool=runtime
    )

    assert result.error == "checkpoint write failed"
    # The failure path writes after the rollback, on its own connection, as the runtime role.
    attempt = await _attempt(chronicler_db, SSID_SOURCE_NAME)
    assert attempt["last_outcome"] == "failed"
    assert attempt["last_error_class"] == "RuntimeError"
    assert await _coverage(chronicler_db, SSID_SOURCE_NAME) == {}
