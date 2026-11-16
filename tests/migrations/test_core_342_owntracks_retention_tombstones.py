"""core_342: connector-side retention tombstones and batch receipts (bu-s11n0s.7 S4b-3a).

The connector will delete granted raw OwnTracks rows under ``connector_writer`` (a later slice).
This revision only creates where that deletion leaves its evidence, and the floor that keeps a
deleted row deleted:

* ``connectors.owntracks_retention_batches``: one receipt per grant the connector executed.
* ``connectors.owntracks_retention_tombstones``: one row per forgotten logical key.
* Both are append-only by trigger (init-db re-grants schema-wide DML to ``connector_writer``).
* A tombstoned logical key is never inserted into ``connectors.owntracks_points`` again: the
  insert is skipped, as a duplicate would be, so a replayed webhook cannot restore raw evidence.

Nothing writes the new tables yet.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
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
_BATCHES = "connectors.owntracks_retention_batches"
_TOMBSTONES = "connectors.owntracks_retention_tombstones"
_INSERT_POINT = """
    INSERT INTO connectors.owntracks_points
        (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
    VALUES ($1, $2, 1.3, 103.8, 'owntracks:alice', $2)
    ON CONFLICT DO NOTHING
    RETURNING id
"""
_INSERT_BATCH = (
    f"INSERT INTO {_BATCHES} (grant_id, item_count, deleted_count, already_forgotten_count) "
    "VALUES ($1, $2, $3, $4)"
)
_INSERT_TOMBSTONE = (
    f"INSERT INTO {_TOMBSTONES} (logical_source_key, raw_source_id, content_digest, grant_id) "
    "VALUES ($1, $2, $3, $4)"
)


@pytest.fixture
def head_url(fresh_db) -> str:
    return fresh_db("core")


async def _forget(conn: asyncpg.Connection, key: str, raw_id, grant=None) -> object:
    grant = grant or uuid4()
    async with conn.transaction():
        await conn.execute(_INSERT_TOMBSTONE, key, raw_id, _DIGEST, grant)
        await conn.execute(_INSERT_BATCH, grant, 1, 1, 0)
    return grant


@_async
async def test_the_connector_role_may_only_read_and_append_and_chronicler_may_read(
    db, head_url: str
) -> None:
    pool = await db.open(head_url)
    for table in (_BATCHES, _TOMBSTONES):
        for role, expected in (
            ("connector_writer", {"SELECT": True, "INSERT": True}),
            ("butler_chronicler_rw", {"SELECT": True, "INSERT": False}),
        ):
            granted = {
                privilege: await pool.fetchval(
                    "SELECT has_table_privilege($1, $2, $3)", role, table, privilege
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            assert granted == {
                **expected,
                "UPDATE": False,
                "DELETE": False,
                "TRUNCATE": False,
            }, (table, role)


@_async
async def test_receipts_and_tombstones_are_append_only_and_consistent(db, head_url: str) -> None:
    pool = await db.open(head_url)
    async with pool.acquire() as conn:
        grant = await _forget(conn, "owntracks:alice:1:location", uuid4())
        for statement in (
            f"UPDATE {_BATCHES} SET deleted_count = 0",
            f"DELETE FROM {_BATCHES}",
            f"UPDATE {_TOMBSTONES} SET content_digest = 'x'",
            f"DELETE FROM {_TOMBSTONES}",
        ):
            with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="append-only"):
                await conn.execute(statement)
        # A key forgotten twice, a tombstone without its receipt, counts that do not add up,
        # and a malformed digest are all refused.
        with pytest.raises(asyncpg.UniqueViolationError):
            await _forget(conn, "owntracks:alice:1:location", uuid4())
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with conn.transaction():
                await conn.execute(_INSERT_TOMBSTONE, "k2", uuid4(), _DIGEST, uuid4())
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(_INSERT_BATCH, uuid4(), 2, 1, 0)
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(_INSERT_TOMBSTONE, "k3", uuid4(), "legacy", grant)
    assert await pool.fetchval(f"SELECT count(*) FROM {_TOMBSTONES}") == 1


@_async
async def test_a_forgotten_logical_key_is_never_inserted_again(db, head_url: str) -> None:
    pool = await db.open(head_url)
    raw_id = await pool.fetchval(_INSERT_POINT, "owntracks:alice:1:location", _TS)
    async with pool.acquire() as conn:
        await _forget(conn, "owntracks:alice:1:location", raw_id)
        await conn.execute("DELETE FROM connectors.owntracks_points WHERE id = $1", raw_id)
        # The connector's replay: skipped like a duplicate, not an error.
        assert await conn.fetchval(_INSERT_POINT, "owntracks:alice:1:location", _TS) is None
        # As the connector role, too.
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE connector_writer")
            assert await conn.fetchval(_INSERT_POINT, "owntracks:alice:1:location", _TS) is None
            assert await conn.fetchval(_INSERT_POINT, "owntracks:alice:2:location", _TS)
    keys = await pool.fetch("SELECT idempotency_key FROM connectors.owntracks_points")
    assert [row["idempotency_key"] for row in keys] == ["owntracks:alice:2:location"]


_SHARED_CATALOG = """
SELECT
    (SELECT array_agg(relname || ':' || oid || ':' || xmin ORDER BY relname) FROM pg_class
     WHERE relnamespace = 'connectors'::regnamespace AND relkind = 'r'
       AND relname LIKE 'owntracks_retention_%'),
    (SELECT array_agg(tgname || ':' || oid ORDER BY tgname) FROM pg_trigger
     WHERE NOT tgisinternal
       AND (tgname = 'owntracks_points_refuse_forgotten'
            OR tgname LIKE 'owntracks_retention_%'))
"""


def _shared_catalog(db_url: str):
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return tuple(connection.execute(text(_SHARED_CATALOG)).one())
    finally:
        engine.dispose()


def _rows(db_url: str, sql: str):
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return connection.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_installed_once_and_the_downgrade_refuses_while_evidence_exists(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_341")
    command.upgrade(second, "core_341")
    command.upgrade(first, "core@head")
    installed = _shared_catalog(db_url)
    assert len(installed[0]) == 2 and len(installed[1]) == 3
    command.upgrade(second, "core@head")
    assert _shared_catalog(db_url) == installed, "a later schema's run rewrote the catalog"

    # Another schema still depends on the tables: the first downgrade leaves them.
    command.downgrade(second, "core_341")
    assert _shared_catalog(db_url) == installed

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {_BATCHES} "
                    "(grant_id, item_count, deleted_count, already_forgotten_count) "
                    "VALUES (:grant, 0, 0, 0)"
                ),
                {"grant": uuid4()},
            )
    finally:
        engine.dispose()
    with pytest.raises(Exception, match="forgotten"):
        command.downgrade(first, "core_341")
    assert _rows(db_url, f"SELECT count(*) FROM {_BATCHES}") == 1


def test_the_last_downgrade_on_empty_tables_drops_everything(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, "core@head")
    command.downgrade(config, "core_341")
    assert _rows(db_url, f"SELECT to_regclass('{_BATCHES}')") is None
    assert _rows(db_url, f"SELECT to_regclass('{_TOMBSTONES}')") is None
    assert (
        _rows(
            db_url,
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'owntracks_points_refuse_forgotten'",
        )
        == 0
    )
