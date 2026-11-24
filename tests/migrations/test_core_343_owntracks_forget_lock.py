"""core_343: a forgotten key's insert waits for the deleter (bu-s11n0s.7 S4b-3b).

core_342's insert trigger checked for a tombstone before ``ON CONFLICT`` waited on the row being
deleted. A webhook replay racing the deleter's uncommitted DELETE and tombstone saw no
tombstone, waited on the unique key, and inserted the fix again once the deleter committed.

core_343 adds one transaction advisory lock: the trigger takes it shared
(``owntracks_forget_guard()``) before reading tombstones and the deleter takes it exclusive
(``owntracks_forget_lock()``) before deleting, so the replay reads the tombstone after the
deleter commits. Inserts do not wait for each other, and a bulk insert holds one lock entry
(a per-row lock exhausted ``max_locks_per_transaction``). The trigger also fires on
``UPDATE OF idempotency_key``: no update can move a row onto a forgotten key.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]
_async = pytest.mark.asyncio(loop_scope="session")

_TS = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
_DIGEST = "v1:" + "a" * 64
_KEY = "owntracks:alice:1:location"
_INSERT_POINT = """
    INSERT INTO connectors.owntracks_points
        (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
    VALUES ($1, $2, 1.3, 103.8, 'owntracks:alice', $2)
    ON CONFLICT DO NOTHING
    RETURNING id
"""


@pytest.fixture
def head_url(fresh_db) -> str:
    return fresh_db("core")


async def _forget_uncommitted(conn, key: str, raw_id) -> None:
    """The deleter's work, left uncommitted in ``conn``'s open transaction."""
    grant = uuid4()
    await conn.execute("SELECT connectors.owntracks_forget_lock()")
    await conn.execute("DELETE FROM connectors.owntracks_points WHERE id = $1", raw_id)
    await conn.execute(
        "INSERT INTO connectors.owntracks_retention_tombstones "
        "(logical_source_key, raw_source_id, content_digest, grant_id) VALUES ($1, $2, $3, $4)",
        key,
        raw_id,
        _DIGEST,
        grant,
    )
    await conn.execute(
        "INSERT INTO connectors.owntracks_retention_batches "
        "(grant_id, item_count, deleted_count, already_forgotten_count) VALUES ($1, 1, 1, 0)",
        grant,
    )


@_async
async def test_a_replay_racing_the_deleter_is_skipped_after_it_commits(db, head_url) -> None:
    pool = await db.open(head_url, max_size=3)
    raw_id = await pool.fetchval(_INSERT_POINT, _KEY, _TS)
    deleter = await pool.acquire()
    try:
        transaction = deleter.transaction()
        await transaction.start()
        await _forget_uncommitted(deleter, _KEY, raw_id)
        replay = asyncio.create_task(pool.fetchval(_INSERT_POINT, _KEY, _TS))
        await asyncio.sleep(0.5)
        assert not replay.done(), "the replay must wait for the deleter"
        await transaction.commit()
        assert await asyncio.wait_for(replay, timeout=10) is None
    finally:
        await pool.release(deleter)
    assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 0


@_async
async def test_another_key_waits_out_the_deletion_and_is_kept(db, head_url) -> None:
    pool = await db.open(head_url, max_size=3)
    raw_id = await pool.fetchval(_INSERT_POINT, _KEY, _TS)
    deleter = await pool.acquire()
    try:
        transaction = deleter.transaction()
        await transaction.start()
        await _forget_uncommitted(deleter, _KEY, raw_id)
        other = asyncio.create_task(pool.fetchval(_INSERT_POINT, "owntracks:alice:2:location", _TS))
        await asyncio.sleep(0.5)
        await transaction.commit()
        assert await asyncio.wait_for(other, timeout=10) is not None
    finally:
        await pool.release(deleter)


@_async
async def test_inserts_do_not_wait_for_each_other(db, head_url) -> None:
    pool = await db.open(head_url, max_size=3)
    first = await pool.acquire()
    try:
        async with first.transaction():
            assert await first.fetchval(_INSERT_POINT, _KEY, _TS) is not None
            other = await asyncio.wait_for(
                pool.fetchval(_INSERT_POINT, "owntracks:alice:2:location", _TS), timeout=5
            )
            assert other is not None
    finally:
        await pool.release(first)


@_async
async def test_a_bulk_insert_holds_one_lock_entry(db, head_url) -> None:
    pool = await db.open(head_url)
    # Twice the rows the server's whole lock table holds, so one lock per row cannot fit.
    rows = await pool.fetchval(
        "SELECT current_setting('max_locks_per_transaction')::int "
        "* (current_setting('max_connections')::int "
        "+ current_setting('max_prepared_transactions')::int) * 2"
    )
    inserted = await pool.execute(
        """
        INSERT INTO connectors.owntracks_points
            (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
        SELECT 'owntracks:alice:' || n || ':location', $1, 1.3, 103.8, 'owntracks:alice', $1
        FROM generate_series(1, $2) AS n
        """,
        _TS,
        rows,
    )
    assert inserted == f"INSERT 0 {rows}"


@_async
async def test_no_update_moves_a_row_onto_a_forgotten_key(db, head_url) -> None:
    pool = await db.open(head_url)
    forgotten = await pool.fetchval(_INSERT_POINT, _KEY, _TS)
    async with pool.acquire() as conn, conn.transaction():
        await _forget_uncommitted(conn, _KEY, forgotten)
    other = await pool.fetchval(_INSERT_POINT, "owntracks:alice:2:location", _TS)
    status = await pool.execute(
        "UPDATE connectors.owntracks_points SET idempotency_key = $1 WHERE id = $2", _KEY, other
    )
    assert status == "UPDATE 0"
    assert (
        await pool.fetchval(
            "SELECT idempotency_key FROM connectors.owntracks_points WHERE id = $1", other
        )
        == "owntracks:alice:2:location"
    )


@_async
async def test_the_connector_role_may_take_the_lock(db, head_url) -> None:
    pool = await db.open(head_url)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE connector_writer")
        await conn.execute("SELECT connectors.owntracks_forget_lock()")
        assert await conn.fetchval(_INSERT_POINT, _KEY, _TS) is not None


_SHARED_CATALOG = """
SELECT
    (SELECT array_agg(proname || ':' || oid || ':' || xmin ORDER BY proname) FROM pg_proc
     WHERE pronamespace = 'connectors'::regnamespace
       AND proname IN ('owntracks_forget_guard', 'owntracks_forget_lock',
                       'owntracks_points_refuse_forgotten')),
    (SELECT array_agg(tgname || ':' || oid || ':' || pg_get_triggerdef(oid) ORDER BY tgname)
     FROM pg_trigger WHERE tgname = 'owntracks_points_refuse_forgotten')
"""


def _catalog(db_url: str):
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return tuple(connection.execute(text(_SHARED_CATALOG)).one())
    finally:
        engine.dispose()


def test_installed_once_and_the_last_downgrade_restores_core_342(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_342")
    command.upgrade(second, "core_342")
    before = _catalog(db_url)
    command.upgrade(first, "core_343")
    installed = _catalog(db_url)
    assert len(installed[0]) == 3
    (trigger,) = installed[1]
    assert "INSERT OR UPDATE OF idempotency_key" in trigger
    command.upgrade(second, "core_343")
    assert _catalog(db_url) == installed, "a later schema's run rewrote the catalog"

    command.downgrade(second, "core_342")
    assert _catalog(db_url) == installed
    command.downgrade(first, "core_342")
    restored = _catalog(db_url)
    assert [entry.split(":")[0] for entry in restored[0]] == ["owntracks_points_refuse_forgotten"]
    (trigger,) = restored[1]
    assert "BEFORE INSERT ON connectors.owntracks_points" in trigger
    assert before[1][0].split(":", 2)[2] == trigger.split(":", 2)[2]
