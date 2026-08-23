"""Partition provisioning in the ingress-dedupe path must survive a rollback.

``switchboard_message_inbox_ensure_partition()`` performs DDL
(``CREATE TABLE IF NOT EXISTS ... PARTITION OF message_inbox``).  PostgreSQL
allows DDL inside a transaction, but rolling that transaction back also drops
any table created within it.  So the call has to run on an auto-commit pool
connection, outside the advisory-lock dedupe transaction — otherwise a later
failure inside that transaction (missing ``public.ingestion_events``, network
error, unique violation) silently destroys the partition it just created and
every subsequent insert fails in a tight loop.

Regression guard for bu-x0wkl.  The sibling ingest path already gets this right
(``roster/switchboard/tools/ingestion/ingest.py``), as does
``butlers.connectors.filtered_event_buffer``.
"""

from __future__ import annotations

import shutil
from typing import Any

import asyncpg
import pytest

from butlers.modules.pipeline import MessagePipeline

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def switchboard_dsn(postgres_container):
    """Create a trusted core + Switchboard migration database."""
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
    )


class _FailingInsertConnection:
    """Connection proxy that fails the ``message_inbox`` INSERT.

    Everything else — the advisory lock, the dedupe SELECT — is forwarded to the
    real connection, so the transaction rolls back at exactly the point a real
    insert failure would.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO message_inbox" in query:
            raise RuntimeError("simulated message_inbox insert failure")
        return await self._conn.fetchrow(query, *args, **kwargs)


class _FailingInsertAcquire:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __aenter__(self) -> _FailingInsertConnection:
        return _FailingInsertConnection(await self._inner.__aenter__())

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._inner.__aexit__(*exc_info)


class _FailingInsertPool:
    """Pool proxy whose acquired connections fail the ``message_inbox`` INSERT."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)

    def acquire(self, *args: Any, **kwargs: Any) -> _FailingInsertAcquire:
        return _FailingInsertAcquire(self._pool.acquire(*args, **kwargs))


_PARTITION_ATTACHED_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_inherits i
        JOIN pg_class child ON child.oid = i.inhrelid
        WHERE i.inhparent = to_regclass('message_inbox')
          AND child.relname = $1
    )
"""


async def _unused_dispatch(*_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
    raise AssertionError("dispatch_fn must not be called by _accept_ingress")


async def test_ensure_partition_survives_dedupe_transaction_rollback(switchboard_dsn):
    """A rollback of the dedupe transaction must not destroy the new partition."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        # ``_accept_ingress`` stamps received_at with datetime.now(UTC), so the
        # partition under test is the current month's — which the migration
        # already created.  Drop it so the pipeline has to provision it.
        partition_name = await pool.fetchval(
            "SELECT switchboard_message_inbox_ensure_partition(now())"
        )
        quoted_partition = '"' + partition_name.replace('"', '""') + '"'
        await pool.execute(f"DROP TABLE {quoted_partition}")
        assert await pool.fetchval(_PARTITION_ATTACHED_SQL, partition_name) is False

        pipeline = MessagePipeline(
            _FailingInsertPool(pool),
            _unused_dispatch,
            enable_ingress_dedupe=True,
        )

        with pytest.raises(RuntimeError, match="simulated message_inbox insert failure"):
            await pipeline._accept_ingress(
                message_text="partition durability probe",
                args={"chat_id": "chat-x0wkl", "message_id": "msg-x0wkl"},
                source_metadata={"channel": "telegram_bot", "identity": "bot-x0wkl"},
                source="telegram_bot",
                chat_id="chat-x0wkl",
            )

        # Read back on a *separate* pool acquisition: proving durability, not
        # intra-transaction visibility.
        async with pool.acquire() as verify_conn:
            assert await verify_conn.fetchval(_PARTITION_ATTACHED_SQL, partition_name) is True
    finally:
        await pool.close()
