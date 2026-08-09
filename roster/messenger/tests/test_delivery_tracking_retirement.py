"""PostgreSQL-backed proof for the safe Messenger tracking retirement."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "roster/messenger/migrations"
_RETIRED_TABLES = (
    "delivery_dead_letter",
    "delivery_receipts",
    "delivery_attempts",
    "delivery_requests",
)
_SKIP_NO_DOCKER = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")


def _migration(filename: str):
    """Load one Messenger migration without mutating Alembic global state."""
    path = MIGRATIONS / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_sql(filename: str, operation: str) -> list[str]:
    """Collect the exact SQL emitted by a migration operation."""
    module = _migration(filename)
    statements: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda statement: statements.append(str(statement))
    with patch.object(module, "op", mock_op):
        getattr(module, operation)()
    return statements


async def _execute_migration_sql(connection, filename: str, operation: str) -> None:
    for statement in _migration_sql(filename, operation):
        await connection.execute(statement)


async def _apply_migration(pool, filename: str, operation: str) -> None:
    """Apply a migration operation in one transaction, like Alembic does."""
    async with pool.acquire() as connection:
        async with connection.transaction():
            await _execute_migration_sql(connection, filename, operation)


async def _prepare_msg_002(pool) -> None:
    """Create the exact pre-retirement schema through its real migrations."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS messenger")
    await pool.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    await _apply_migration(pool, "001_messenger_tables.py", "upgrade")
    await _apply_migration(pool, "002_delivery_requests_priority.py", "upgrade")


async def _existing_tables(pool) -> set[str]:
    rows = await pool.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'messenger'
          AND table_name = ANY($1::text[])
        """,
        list(_RETIRED_TABLES),
    )
    return {str(row["table_name"]) for row in rows}


async def _schema_fingerprint(pool) -> tuple[tuple[object, ...], ...]:
    """Capture catalog facts needed to prove an exact msg_002 compatibility schema."""
    table_names = list(_RETIRED_TABLES)
    columns = await pool.fetch(
        """
        SELECT table_name, ordinal_position, column_name, data_type, udt_name,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'messenger'
          AND table_name = ANY($1::text[])
        ORDER BY table_name, ordinal_position
        """,
        table_names,
    )
    constraints = await pool.fetch(
        """
        SELECT relation.relname AS table_name, constraint_entry.conname,
               constraint_entry.contype,
               pg_get_constraintdef(constraint_entry.oid, true) AS definition
        FROM pg_constraint AS constraint_entry
        JOIN pg_class AS relation ON relation.oid = constraint_entry.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'messenger'
          AND relation.relname = ANY($1::text[])
        ORDER BY relation.relname, constraint_entry.conname
        """,
        table_names,
    )
    indexes = await pool.fetch(
        """
        SELECT relation.relname AS table_name, index_relation.relname AS index_name,
               pg_get_indexdef(index_relation.oid) AS definition
        FROM pg_index AS index
        JOIN pg_class AS relation ON relation.oid = index.indrelid
        JOIN pg_class AS index_relation ON index_relation.oid = index.indexrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'messenger'
          AND relation.relname = ANY($1::text[])
        ORDER BY relation.relname, index_relation.relname
        """,
        table_names,
    )
    return tuple(
        (kind, *tuple(row))
        for kind, records in (
            ("column", columns),
            ("constraint", constraints),
            ("index", indexes),
        )
        for row in records
    )


async def _insert_retained_row(connection, table: str) -> None:
    """Write one valid legacy row to the requested table."""
    request_id = await connection.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent, target_identity,
            message_content, request_envelope
        )
        VALUES ($1, 'health', 'telegram', 'send', 'owner', 'retained row', '{}'::jsonb)
        RETURNING id
        """,
        f"retained-{table}",
    )
    if table == "delivery_requests":
        return
    if table == "delivery_attempts":
        await connection.execute(
            """
            INSERT INTO delivery_attempts (delivery_request_id, attempt_number, outcome)
            VALUES ($1, 1, 'success')
            """,
            request_id,
        )
        return
    if table == "delivery_receipts":
        await connection.execute(
            """
            INSERT INTO delivery_receipts (delivery_request_id, receipt_type)
            VALUES ($1, 'sent')
            """,
            request_id,
        )
        return
    assert table == "delivery_dead_letter"
    await connection.execute(
        """
        INSERT INTO delivery_dead_letter (
            delivery_request_id, quarantine_reason, error_class, error_summary,
            total_attempts, first_attempt_at, last_attempt_at, original_request_envelope
        )
        VALUES ($1, 'retained', 'retained', 'retained row', 1, now(), now(), '{}'::jsonb)
        """,
        request_id,
    )


def test_msg_003_locks_every_existing_table_before_the_empty_check_and_drop() -> None:
    """The migration must not leave a check-to-drop window for concurrent writers."""
    statements = _migration_sql("003_retire_unwired_delivery_tracking.py", "upgrade")
    assert len(statements) == 1, "the retirement guard and destructive DDL must be atomic"
    sql = statements[0]
    assert "LOCK TABLE" in sql
    assert "ACCESS EXCLUSIVE MODE" in sql
    assert sql.index("LOCK TABLE") < sql.index("SELECT EXISTS") < sql.index("DROP TABLE")
    for table in _RETIRED_TABLES:
        assert table in sql


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_SKIP_NO_DOCKER
async def test_empty_msg_002_schema_advances_to_msg_003(provisioned_postgres_pool) -> None:
    """An empty legacy schema is the only schema the retirement may drop."""
    async with provisioned_postgres_pool(schema="messenger", max_pool_size=4) as pool:
        await _prepare_msg_002(pool)
        assert await _existing_tables(pool) == set(_RETIRED_TABLES)

        await _apply_migration(pool, "003_retire_unwired_delivery_tracking.py", "upgrade")

        assert await _existing_tables(pool) == set()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_SKIP_NO_DOCKER
@pytest.mark.parametrize("table", _RETIRED_TABLES)
async def test_nonempty_legacy_table_aborts_before_destructive_ddl(
    provisioned_postgres_pool, table: str
) -> None:
    """Every retired table is independently retention-protected."""
    async with provisioned_postgres_pool(schema="messenger", max_pool_size=4) as pool:
        await _prepare_msg_002(pool)
        await _insert_retained_row(pool, table)

        with pytest.raises(
            asyncpg.PostgresError, match=rf"Cannot retire messenger tracking table {table}"
        ):
            await _apply_migration(pool, "003_retire_unwired_delivery_tracking.py", "upgrade")

        assert await _existing_tables(pool) == set(_RETIRED_TABLES)
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 1


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_SKIP_NO_DOCKER
async def test_concurrent_writer_cannot_commit_rows_into_a_dropped_table(
    provisioned_postgres_pool,
) -> None:
    """An uncommitted writer forces the lock-before-check guard to fail closed."""
    async with provisioned_postgres_pool(schema="messenger", max_pool_size=4) as pool:
        await _prepare_msg_002(pool)
        writer = await pool.acquire()
        upgrade_task: asyncio.Task[None] | None = None
        transaction = writer.transaction()
        await transaction.start()
        try:
            await _insert_retained_row(writer, "delivery_requests")
            upgrade_task = asyncio.create_task(
                _apply_migration(pool, "003_retire_unwired_delivery_tracking.py", "upgrade")
            )

            # The writer's row lock must block msg_003 before it checks rows or drops tables.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(upgrade_task), timeout=0.1)
        finally:
            await transaction.commit()
            await pool.release(writer)

        assert upgrade_task is not None
        with pytest.raises(asyncpg.PostgresError, match="delivery_requests"):
            await upgrade_task
        assert await _existing_tables(pool) == set(_RETIRED_TABLES)
        assert await pool.fetchval("SELECT count(*) FROM delivery_requests") == 1


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_SKIP_NO_DOCKER
async def test_downgrade_recreates_exact_empty_msg_002_schema_then_reupgrades(
    provisioned_postgres_pool,
) -> None:
    """Downgrade is compatibility-only yet must reconstruct every prior schema object."""
    async with provisioned_postgres_pool(schema="messenger", max_pool_size=4) as pool:
        await _prepare_msg_002(pool)
        msg_002_schema = await _schema_fingerprint(pool)

        await _apply_migration(pool, "003_retire_unwired_delivery_tracking.py", "upgrade")
        await _apply_migration(pool, "003_retire_unwired_delivery_tracking.py", "downgrade")

        assert await _schema_fingerprint(pool) == msg_002_schema

        await _apply_migration(pool, "003_retire_unwired_delivery_tracking.py", "upgrade")
        assert await _existing_tables(pool) == set()
