"""Typed situation reads against the migrated perception SQL and its real reader roles.

Reuses the perception suite's prepared database (the privileged bootstrap it needs to
configure source profiles). No runtime role is granted anything to make a test pass.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.perception import EventRequest, PerceptionStore
from butlers.perception.situation import Audience, situation_read, timeline_query
from tests.perception.factories import AT, OWNER, request
from tests.perception.test_perception_postgres import prepared_database

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def database(postgres_container):
    with prepared_database(postgres_container) as data:
        yield data


@pytest.fixture
async def conn(database):
    connection = await asyncpg.connect(
        database[1].url.set(drivername="postgresql").render_as_string(hide_password=False),
        command_timeout=30,
    )
    await register_jsonb_codec(connection)
    tx = connection.transaction()
    await tx.start()
    try:
        yield connection
    finally:
        await tx.rollback()
        await connection.close()


async def _as(conn, role):
    await conn.execute("RESET ROLE")
    await conn.execute(f"SET LOCAL ROLE {role}")


async def _seed_place(conn):
    await _as(conn, "perception_dots_writer")
    return await PerceptionStore(conn).write(EventRequest(request()))


async def test_the_owner_reads_the_place_and_the_exposed_caller_only_availability(conn):
    await _seed_place(conn)
    await _as(conn, "perception_owner_reader")
    owner = await situation_read(
        conn, audience=Audience.OWNER, kind="owner.place", subject=OWNER, as_of=AT
    )
    assert owner.state == "known" and owner.value["presence"] == "inside"
    assert owner.source == "home.dots" and owner.labels["label_sens"] == ["location"]

    await _as(conn, "perception_availability_reader")
    exposed = await situation_read(
        conn, audience=Audience.AVAILABILITY, kind="owner.place", subject=OWNER, as_of=AT
    )
    shown = asdict(exposed)
    assert set(shown) == {"state", "busy_until", "availability_reason"}
    assert shown["state"] == "unknown" and shown["busy_until"] is None
    assert "inside" not in json.dumps(shown, default=str)


async def test_past_freshness_the_owner_reads_unknown_with_the_stale_value(conn):
    await _seed_place(conn)
    await _as(conn, "perception_owner_reader")
    later = AT + timedelta(hours=2)
    result = await situation_read(
        conn, audience=Audience.OWNER, kind="owner.place", subject=OWNER, as_of=later
    )
    assert result.state == "unknown" and result.value is None
    assert result.stale_last_value["presence"] == "inside"
    assert result.availability_reason == "stale"


async def test_an_unwritten_kind_reads_as_configured_empty_not_all_clear(conn):
    await _as(conn, "perception_owner_reader")
    result = await situation_read(
        conn, audience=Audience.OWNER, kind="owner.clock", subject=OWNER, as_of=AT
    )
    assert result.state == "unknown" and result.availability_reason == "configured_empty"


async def test_an_owner_connection_cannot_serve_an_exposed_caller(conn):
    await _as(conn, "perception_owner_reader")
    with pytest.raises(PermissionError):
        await situation_read(
            conn, audience=Audience.AVAILABILITY, kind="owner.place", subject=OWNER, as_of=AT
        )


async def test_the_owner_timeline_returns_the_record(conn):
    written = await _seed_place(conn)
    await _as(conn, "perception_owner_reader")
    entries = await timeline_query(
        conn,
        audience=Audience.OWNER,
        since=AT - timedelta(seconds=1),
        until=AT + timedelta(minutes=1),
        kind="owner.place",
        subject=OWNER,
        live=False,
    )
    assert [str(e.id) for e in entries] == [str(written["event_id"])]
    assert entries[0].retracted is False and entries[0].late is False
