"""Chronicler hands a ready grant to the connector, which executes it (bu-s11n0s.7 S4b-3b).

``ready_grant_payloads`` is the only thing Chronicler offers the connector: grant id, cutoff and
each item's raw id, logical key and digest, for grants with a ``ready`` state and no terminal
one. ``ready`` is unreachable until holder copies land (S4c), so here the test writes it directly.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.chronicler.location_retention_grant import prepare_retention_grant
from butlers.chronicler.location_retention_handoff import ready_grant_payloads
from butlers.connectors.owntracks_retention import execute_grant, parse_grant
from tests.integration import test_owntracks_retention_grant_integration as _grant_suite

# The grant suite's database and helpers: core and Chronicler at head, adapters' runs recorded.
grant_url = _grant_suite.grant_url
chronicler_db = _grant_suite.chronicler_db
runtime_db = _grant_suite.runtime_db
_REVISIONS = _grant_suite._REVISIONS
_cover = _grant_suite._cover
_insert_point = _grant_suite._insert_point

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_STATE = "INSERT INTO location_retention_grant_states (grant_id, state) VALUES ($1, $2)"


async def _prepared(chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, *minutes: int):
    for minute in minutes:
        await _cover(chronicler_db, await _insert_point(chronicler_db, minute))
    async with runtime_db.acquire() as conn:
        return await prepare_retention_grant(conn, config_revisions=_REVISIONS)


async def test_only_a_ready_unsettled_grant_is_offered(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    withheld = await _prepared(chronicler_db, runtime_db, 0)
    reconciled = await _prepared(chronicler_db, runtime_db, 5)
    ready = await _prepared(chronicler_db, runtime_db, 10, 15)
    for grant_id, state in (
        (reconciled.id, "ready"),
        (reconciled.id, "reconciled"),
        (ready.id, "ready"),
        (ready.id, "unknown"),
    ):
        await chronicler_db.execute(_STATE, grant_id, state)

    async with runtime_db.acquire() as conn:
        payloads = await ready_grant_payloads(conn)

    assert [p["grant_id"] for p in payloads] == [str(ready.id)]
    assert withheld.id not in {p["grant_id"] for p in payloads}
    (payload,) = payloads
    assert set(payload) == {"grant_id", "cutoff", "items"}
    assert all(
        set(item) == {"raw_source_id", "logical_source_key", "content_digest"}
        for item in payload["items"]
    )
    header = await chronicler_db.fetchrow(
        "SELECT cutoff FROM location_retention_grants WHERE id = $1", ready.id
    )
    grant = parse_grant(payload)
    assert grant.cutoff == header["cutoff"]
    items = await chronicler_db.fetch(
        "SELECT raw_source_id, logical_source_key, content_digest "
        "FROM location_retention_grant_items WHERE grant_id = $1 ORDER BY raw_source_id",
        ready.id,
    )
    assert [(i.raw_source_id, i.logical_source_key, i.content_digest) for i in grant.items] == [
        tuple(row.values()) for row in items
    ]


async def test_the_connector_executes_what_chronicler_offers(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    ready = await _prepared(chronicler_db, runtime_db, 0, 5)
    await chronicler_db.execute(_STATE, ready.id, "ready")
    async with runtime_db.acquire() as conn:
        (payload,) = await ready_grant_payloads(conn)

    async with chronicler_db.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE connector_writer")
            receipt = await execute_grant(conn, parse_grant(payload))

    assert (receipt.item_count, receipt.deleted_count) == (2, 2)
    assert await chronicler_db.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 0


async def test_a_limit_bounds_the_offer(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    for minute in (0, 5):
        grant = await _prepared(chronicler_db, runtime_db, minute)
        await chronicler_db.execute(_STATE, grant.id, "ready")
    async with runtime_db.acquire() as conn:
        assert len(await ready_grant_payloads(conn, limit=1)) == 1
        assert len(await ready_grant_payloads(conn)) == 2
        with pytest.raises(ValueError):
            await ready_grant_payloads(conn, limit=0)


async def test_nothing_is_offered_while_the_owner_has_expiry_off(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    """bu-s11n0s.75: turning expiry off withdraws even a ready grant within one connector tick.

    A missing policy row offers nothing either: the offer fails closed.
    """
    ready = await _prepared(chronicler_db, runtime_db, 0)
    await chronicler_db.execute(_STATE, ready.id, "ready")
    await chronicler_db.execute(_grant_suite.ENFORCE, False)
    async with runtime_db.acquire() as conn:
        assert await ready_grant_payloads(conn) == []

    await chronicler_db.execute(_grant_suite.ENFORCE, True)
    async with runtime_db.acquire() as conn:
        assert [p["grant_id"] for p in await ready_grant_payloads(conn)] == [str(ready.id)]

    await chronicler_db.execute("DELETE FROM location_retention_policy")
    async with runtime_db.acquire() as conn:
        assert await ready_grant_payloads(conn) == []
