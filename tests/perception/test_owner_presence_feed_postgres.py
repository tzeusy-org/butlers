"""public.owner_presence_observations: typed, coordinate-free, Home-only (bu-jv1moa.66)."""

from __future__ import annotations

import json
import math
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine

from butlers.jobs.home_presence_reducer import owntracks_observations
from butlers.perception.presence import gps_inside, gps_outside
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
EARTH_M = 6371008.8
RADIUS = 150
PLACE = uuid.UUID(int=1)
SSID = "SentinelNet-9c2e"
COLUMNS = ["observed_at", "accuracy_m", "place_ref", "side", "ssid_match"]
CALL = "SELECT * FROM public.owner_presence_observations($1, $2, $3, $4)"


@pytest.fixture(scope="module")
def dsn(postgres_container):
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    engine = create_engine(migration_bootstrap_db_url(postgres_container, name))
    url = engine.url
    engine.dispose()
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def session(dsn):
    conn = await asyncpg.connect(dsn, command_timeout=30)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def place(conn, *, kind="home", label="Home", ssids=(SSID,), paused=False, deleted=False):
    # The reference place sits at 0,0, so a point at latitude d/R degrees is exactly d metres away.
    return await conn.fetchval(
        "INSERT INTO public.owner_places(label, kind, lat, lon, radius_m, ssids, paused, deleted_at)"
        " VALUES ($1, $2, 0, 0, $3, $4, $5, CASE WHEN $6 THEN now() END) RETURNING id",
        label,
        kind,
        RADIUS,
        list(ssids),
        paused,
        deleted,
    )


async def point(conn, distance_m, accuracy, *, ssid=None, minutes=1, key=None):
    payload = {"SSID": ssid} if ssid else {}
    await conn.execute(
        "INSERT INTO connectors.owntracks_points"
        "(idempotency_key, ts, lat, lon, accuracy, endpoint_identity, raw_payload)"
        " VALUES ($1, $2, $3, 0.00001, $4, 'endpoint', $5::jsonb)",
        key or uuid.uuid4().hex,
        NOW - timedelta(minutes=minutes),
        math.degrees(distance_m / EARTH_M),
        accuracy,
        json.dumps(payload),
    )


async def feed(conn, place_id, role="butler_home_rw", limit=500):
    await conn.execute(f"SET LOCAL ROLE {role}")
    rows = await conn.fetch(CALL, NOW - timedelta(hours=1), NOW, limit, place_id)
    await conn.execute("RESET ROLE")
    return rows


@pytest.mark.parametrize("accuracy", [10, 50, 100, 101])
@pytest.mark.parametrize("delta", [-60, -1, 1, 60])
async def test_sql_and_python_rules_agree_around_every_boundary(dsn, accuracy, delta):
    async with session(dsn) as conn:
        home = await place(conn)
        edges = {
            "inside": RADIUS,
            "outside": RADIUS + max(accuracy, 50),
        }
        for name, edge in edges.items():
            distance = edge + delta
            await point(conn, distance, accuracy, key=name)
            expected = (
                "inside"
                if gps_inside(distance, accuracy, RADIUS)
                else "outside"
                if gps_outside(distance, accuracy, RADIUS)
                else None
            )
            await conn.execute(
                "DELETE FROM connectors.owntracks_points WHERE idempotency_key<>$1", name
            )
            rows = await feed(conn, home)
            got = rows[0]["side"] if rows else None
            assert got == expected, (name, distance, accuracy)
            await conn.execute("DELETE FROM connectors.owntracks_points")


async def test_output_is_typed_and_carries_no_coordinate_or_ssid(dsn):
    async with session(dsn) as conn:
        home = await place(conn)
        await point(conn, 20, 15, minutes=5)  # inside
        await point(conn, 900, 15, minutes=4)  # outside
        await point(conn, 180, 15, minutes=3)  # band: no signal
        await point(conn, 20, 400, minutes=2)  # too inaccurate: no signal
        await point(conn, 5000, 400, ssid=SSID, minutes=1)  # ssid hit: inside whatever GPS says
        rows = await feed(conn, home)
        assert [r["side"] for r in rows] == ["inside", "outside", "inside"]
        assert [r["ssid_match"] for r in rows] == [False, False, True]
        assert {r["place_ref"] for r in rows} == {home}
        assert list(rows[0].keys()) == COLUMNS
        text = json.dumps([dict(r) for r in rows], default=str).lower()
        for leaked in (SSID.lower(), "0.00001", "sentinelnet", "lat", "lon", "endpoint"):
            assert leaked not in text


async def test_ssid_match_is_exact_and_case_sensitive(dsn):
    async with session(dsn) as conn:
        home = await place(conn)
        await point(conn, 5000, 400, ssid=SSID.lower(), minutes=2)
        await point(conn, 5000, 400, ssid=SSID + " ", minutes=1)
        assert await feed(conn, home) == []


async def test_paused_and_deleted_places_take_no_part(dsn):
    async with session(dsn) as conn:
        paused = await place(conn, kind="work", label="Paused", paused=True)
        gone = await place(conn, kind="other", label="Gone", deleted=True)
        await point(conn, 10, 10)
        assert await feed(conn, paused) == []
        assert await feed(conn, gone) == []


async def test_only_home_may_execute_and_places_stay_fenced(dsn):
    async with session(dsn) as conn:
        home = await place(conn)
        await point(conn, 10, 10)
        assert len(await feed(conn, home)) == 1
        for role in ("butler_travel_rw", "butler_chronicler_rw", "connector_writer"):
            savepoint = conn.transaction()
            await savepoint.start()
            await conn.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(CALL, NOW - timedelta(hours=1), NOW, 10, home)
            await savepoint.rollback()
        await conn.execute("SET LOCAL ROLE butler_home_rw")
        # Home reads neither raw table through the feed's definer: the places stay fenced.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("SELECT count(*) FROM public.owner_places")


async def test_a_hostile_search_path_cannot_shadow_the_places(dsn):
    async with session(dsn) as conn:
        home = await place(conn)
        await point(conn, 10, 10)
        # A session-level shadow of the places table, ahead of public on the search path.
        await conn.execute(
            "CREATE TEMP TABLE owner_places (id uuid, radius_m int, lat float8, lon float8,"
            " ssids text[], paused bool, deleted_at timestamptz)"
        )
        await conn.execute(
            "INSERT INTO pg_temp.owner_places VALUES (gen_random_uuid(), 50, 1, 1, '{}', false, NULL)"
        )
        await conn.execute("SET LOCAL search_path = pg_temp, public")
        rows = await feed(conn, home)
        assert len(rows) == 1 and rows[0]["side"] == "inside"


@pytest.mark.parametrize(
    "args",
    [
        (NOW, NOW, 10, PLACE),
        (NOW - timedelta(hours=25), NOW, 10, PLACE),
        (NOW - timedelta(hours=1), NOW, 0, PLACE),
        (NOW - timedelta(hours=1), NOW, 501, PLACE),
        (None, NOW, 10, PLACE),
        (NOW - timedelta(hours=1), NOW, 10, None),
    ],
)
async def test_the_window_and_limit_are_bounded(dsn, args):
    async with session(dsn) as conn:
        await conn.execute("SET LOCAL ROLE butler_home_rw")
        with pytest.raises(asyncpg.PostgresError) as caught:
            await conn.fetch(CALL, *args)
        assert caught.value.sqlstate == "22023"


async def test_the_adapter_turns_the_feed_into_opaque_typed_observations(dsn):
    async with session(dsn) as conn:
        home = await place(conn)
        await point(conn, 900, 15, minutes=4)
        await point(conn, 5000, 400, ssid=SSID, minutes=1)
        await conn.execute("SET LOCAL ROLE butler_home_rw")
        observed = await owntracks_observations(conn, place_ref=home, now=NOW)
        assert [(o.side, o.ssid_match, o.evidence) for o in observed] == [
            ("outside", False, "owntracks"),
            ("inside", True, "owntracks"),
        ]
        assert all(len(o.key) == 24 and SSID not in repr(o) for o in observed)
        assert await owntracks_observations(conn, place_ref=uuid.uuid4(), now=NOW) == []


async def test_a_refused_or_missing_feed_degrades_to_no_observations(dsn, caplog):
    async with session(dsn) as conn:
        home = await place(conn)
        await point(conn, 900, 15)
        savepoint = conn.transaction()
        await savepoint.start()
        await conn.execute("SET LOCAL ROLE butler_travel_rw")
        assert await owntracks_observations(conn, place_ref=home, now=NOW) == []
        await savepoint.rollback()
    assert "OwnTracks feed off (InsufficientPrivilegeError)" in caplog.text


async def test_a_busy_window_keeps_the_newest_fixes_and_only_the_requested_place(dsn):
    async with session(dsn) as conn:
        home = await place(conn)
        other = await place(conn, kind="work", label="Office")
        for minute in range(30, 0, -1):  # 30 qualifying inside fixes, oldest first
            await point(conn, 10, 10, minutes=minute, key=f"fix{minute:02d}")
        rows = await feed(conn, home, limit=5)
        # The newest five, oldest first; the other place's rows never count against the limit.
        assert [r["observed_at"] for r in rows] == [
            NOW - timedelta(minutes=m) for m in range(5, 0, -1)
        ]
        assert {r["place_ref"] for r in rows} == {home}
        # The office is 0,0 as well here (same fixture coordinates), so it sees fixes of its own.
        assert {r["place_ref"] for r in await feed(conn, other, limit=500)} == {other}
