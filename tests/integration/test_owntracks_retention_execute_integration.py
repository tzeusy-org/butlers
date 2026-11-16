"""The connector executes one Chronicler retention grant in one transaction (S4b-3b).

``execute_grant`` deletes a granted raw row only when it is still exactly the row Chronicler
granted (same logical key, same v1 content digest, retention clock before the cutoff). Any
mismatch refuses the whole grant: nothing is deleted and no receipt is written. A row already
gone counts as already forgotten and is still tombstoned by its logical key, so it cannot be
inserted again. A grant executed twice returns its first receipt and changes nothing.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from butlers.chronicler.projection_coverage import raw_row_digest_sql
from butlers.connectors.owntracks_retention import (
    GrantMismatch,
    execute_grant,
    parse_grant,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECEIVED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_CUTOFF = _RECEIVED + timedelta(days=1)
_INSERT = """
    INSERT INTO connectors.owntracks_points
        (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
    VALUES ($1, $2, 1.3, 103.8, 'owntracks:alice', $2)
    ON CONFLICT DO NOTHING
    RETURNING id
"""
_DIGEST = f"SELECT {raw_row_digest_sql('p')} FROM connectors.owntracks_points p WHERE p.id = $1"


@pytest.fixture
def head_url(fresh_db) -> str:
    return fresh_db("core")


async def _point(pool, key: str, ts: datetime = _RECEIVED) -> dict:
    raw_id = await pool.fetchval(_INSERT, key, ts)
    digest = await pool.fetchval(_DIGEST, raw_id)
    return {"raw_source_id": str(raw_id), "logical_source_key": key, "content_digest": digest}


def _grant(*items: dict, cutoff: datetime = _CUTOFF):
    return parse_grant(
        {"grant_id": str(uuid4()), "cutoff": cutoff.isoformat(), "items": list(items)}
    )


async def _keys(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT idempotency_key FROM connectors.owntracks_points ORDER BY idempotency_key"
    )
    return [row["idempotency_key"] for row in rows]


async def test_a_grant_deletes_its_rows_and_leaves_tombstones_and_a_receipt(db, head_url) -> None:
    pool = await db.open(head_url)
    first, second = await _point(pool, "k1"), await _point(pool, "k2")
    await _point(pool, "kept")
    grant = _grant(first, second)
    async with pool.acquire() as conn:
        receipt = await execute_grant(conn, grant)
    assert (receipt.item_count, receipt.deleted_count, receipt.already_forgotten_count) == (2, 2, 0)
    assert not receipt.replayed
    assert await _keys(pool) == ["kept"]
    tombstones = await pool.fetch(
        "SELECT logical_source_key, raw_source_id::text AS raw, content_digest, grant_id "
        "FROM connectors.owntracks_retention_tombstones ORDER BY logical_source_key"
    )
    assert [(t["logical_source_key"], t["raw"], t["content_digest"]) for t in tombstones] == [
        ("k1", first["raw_source_id"], first["content_digest"]),
        ("k2", second["raw_source_id"], second["content_digest"]),
    ]
    assert {t["grant_id"] for t in tombstones} == {grant.grant_id}
    # The webhook replays the forgotten fix: skipped.
    assert await pool.fetchval(_INSERT, "k1", _RECEIVED) is None


async def test_a_replay_racing_the_deleter_does_not_restore_the_row(db, head_url) -> None:
    """The deleter's uncommitted delete and tombstone hold the key: the replay waits, then skips."""
    pool = await db.open(head_url, max_size=3)
    item = await _point(pool, "k1")
    deleter = await pool.acquire()
    try:
        outer = deleter.transaction()
        await outer.start()
        await execute_grant(deleter, _grant(item))
        replay = asyncio.create_task(pool.fetchval(_INSERT, "k1", _RECEIVED))
        await asyncio.sleep(0.5)
        assert not replay.done()
        await outer.commit()
        assert await asyncio.wait_for(replay, timeout=10) is None
    finally:
        await pool.release(deleter)
    assert await _keys(pool) == []


async def test_a_row_already_gone_is_counted_and_still_tombstoned(db, head_url) -> None:
    pool = await db.open(head_url)
    gone = await _point(pool, "gone")
    await pool.execute(
        "DELETE FROM connectors.owntracks_points WHERE id = $1::uuid", gone["raw_source_id"]
    )
    async with pool.acquire() as conn:
        receipt = await execute_grant(conn, _grant(gone))
    assert (receipt.deleted_count, receipt.already_forgotten_count) == (0, 1)
    assert await pool.fetchval(_INSERT, "gone", _RECEIVED) is None


async def test_a_grant_executed_twice_returns_its_first_receipt(db, head_url) -> None:
    pool = await db.open(head_url)
    grant = _grant(await _point(pool, "k1"))
    async with pool.acquire() as conn:
        first = await execute_grant(conn, grant)
        again = await execute_grant(conn, grant)
    assert again.replayed
    assert (again.deleted_count, again.already_forgotten_count) == (first.deleted_count, 0)
    assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_retention_batches") == 1


@pytest.mark.parametrize(
    "tamper",
    ["digest", "key", "after-cutoff", "at-cutoff"],
)
async def test_a_mismatched_row_refuses_the_whole_grant(db, head_url, tamper) -> None:
    pool = await db.open(head_url)
    good = await _point(pool, "good")
    ts = {"after-cutoff": _CUTOFF + timedelta(hours=1), "at-cutoff": _CUTOFF}.get(tamper, _RECEIVED)
    bad = await _point(pool, "bad", ts)
    if tamper == "digest":
        bad["content_digest"] = "v1:" + "0" * 64
    elif tamper == "key":
        bad["logical_source_key"] = "other"
    async with pool.acquire() as conn:
        with pytest.raises(GrantMismatch):
            await execute_grant(conn, _grant(good, bad))
    assert await _keys(pool) == ["bad", "good"]
    for table in ("owntracks_retention_batches", "owntracks_retention_tombstones"):
        assert await pool.fetchval(f"SELECT count(*) FROM connectors.{table}") == 0


async def test_the_connector_role_can_execute_a_grant(db, head_url) -> None:
    pool = await db.open(head_url)
    grant = _grant(await _point(pool, "k1"))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE connector_writer")
            receipt = await execute_grant(conn, grant)
    assert receipt.deleted_count == 1
    assert await _keys(pool) == []


async def test_a_tombstoned_key_under_another_grant_refuses(db, head_url) -> None:
    """A raw row is granted at most once; a second grant naming it is not executed."""
    pool = await db.open(head_url)
    item = await _point(pool, "k1")
    async with pool.acquire() as conn:
        await execute_grant(conn, _grant(item))
        with pytest.raises(GrantMismatch):
            await execute_grant(conn, _grant(item))
    assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_retention_batches") == 1
