"""The owner.clock record against the migrated perception SQL and its real roles.

The ``context.clock`` source accepts ``clock_event`` as ``perception_context_writer``;
the owner reader resolves it back to the same clock and finds it on the timeline.
Reuses the perception suite's prepared database.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.perception import EventRequest, PerceptionStore
from butlers.perception.clock import ClockValue, clock_event, resolve_owner_clock
from tests.perception.factories import AT, EVIDENCE, OWNER
from tests.perception.test_perception_postgres import prepared_database

# Async fixtures run on the session loop (asyncio_default_fixture_loop_scope), so the
# tests must too, or the ``conn`` fixture's connection is driven from another loop.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]


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


def _tokyo(generation=0):
    return EventRequest(
        clock_event(
            ClockValue("Asia/Tokyo", "phone"),
            subject=OWNER,
            evidence_ref=EVIDENCE,
            generation=generation,
            at=AT,
            observed_at=AT,
            evidence_source="fixture.evidence",
        )
    )


async def test_the_context_writer_records_the_clock_and_the_owner_reads_it_back(conn):
    await _as(conn, "perception_context_writer")
    written = await PerceptionStore(conn).write(_tokyo())
    assert written["status"] == "committed"

    await _as(conn, "perception_owner_reader")
    row = await PerceptionStore(conn).read("owner.clock", OWNER, as_of=AT + timedelta(minutes=1))
    assert row["state"] == "known"
    resolved = resolve_owner_clock(row["state"], row["value"], "Asia/Singapore")
    assert resolved == ClockValue("Asia/Tokyo", "phone")


async def test_the_clock_switch_is_an_owner_timeline_record(conn):
    await _as(conn, "perception_context_writer")
    written = await PerceptionStore(conn).write(_tokyo())
    await _as(conn, "perception_owner_reader")
    rows = await PerceptionStore(conn).timeline(
        since=AT - timedelta(seconds=1),
        until=AT + timedelta(minutes=1),
        kind="owner.clock",
        subject=OWNER,
    )
    assert [str(row["id"]) for row in rows] == [str(written["event_id"])]
    assert rows[0]["typed_value"]["zone"] == "Asia/Tokyo"
    assert rows[0]["typed_value"]["basis"] == "phone"


async def test_the_dots_writer_cannot_record_the_clock(conn):
    await _as(conn, "perception_dots_writer")
    with pytest.raises(asyncpg.PostgresError):
        await PerceptionStore(conn).write(_tokyo())
