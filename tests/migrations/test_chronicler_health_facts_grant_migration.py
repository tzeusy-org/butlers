"""PostgreSQL-backed coverage for the Health memory evidence-surface grant."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.migrations import run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "modules"
    / "memory"
    / "migrations"
    / "011_grant_chronicler_health_facts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_011", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _apply(conn: asyncpg.Connection, operation: str, *, chronicler_role: str) -> None:
    module = _load_migration()
    module._CHRONICLER_ROLE = chronicler_role
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, operation)()
    for statement in statements:
        await conn.execute(statement)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_health_memory_migration_grants_only_select_and_scopes_to_health(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        async with pool.acquire() as conn:
            await conn.execute("CREATE SCHEMA health")
            await conn.execute("CREATE SCHEMA general")
            chronicler_role = f"mem_011_chronicler_{uuid.uuid4().hex[:12]}"
            await conn.execute(f'CREATE ROLE "{chronicler_role}"')
            await conn.execute(f'GRANT USAGE ON SCHEMA health TO "{chronicler_role}"')
            # schema-standin-exempt: this is a GRANT target, not a query stand-in.
            await conn.execute("CREATE TABLE health.facts (id UUID PRIMARY KEY)")

            await conn.execute("SET search_path TO general")
            await _apply(conn, "upgrade", chronicler_role=chronicler_role)
            assert (
                await conn.fetchval(
                    "SELECT has_table_privilege($1, 'health.facts', 'SELECT')",
                    chronicler_role,
                )
                is False
            )

            await conn.execute("SET search_path TO health")
            await _apply(conn, "upgrade", chronicler_role=chronicler_role)
            await _apply(conn, "upgrade", chronicler_role=chronicler_role)

            privileges = await conn.fetchrow(
                """
                SELECT
                    has_table_privilege($1, 'health.facts', 'SELECT') AS can_select,
                    has_table_privilege($1, 'health.facts', 'INSERT') AS can_insert,
                    has_table_privilege($1, 'health.facts', 'UPDATE') AS can_update,
                    has_table_privilege($1, 'health.facts', 'DELETE') AS can_delete
                """,
                chronicler_role,
            )
            assert privileges is not None
            assert dict(privileges) == {
                "can_select": True,
                "can_insert": False,
                "can_update": False,
                "can_delete": False,
            }

            await conn.execute(f'SET ROLE "{chronicler_role}"')
            try:
                assert await conn.fetchval("SELECT COUNT(*) FROM health.facts") == 0
            finally:
                await conn.execute("RESET ROLE")

            await _apply(conn, "downgrade", chronicler_role=chronicler_role)
            assert (
                await conn.fetchval(
                    "SELECT has_table_privilege($1, 'health.facts', 'SELECT')",
                    chronicler_role,
                )
                is False
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_health_memory_migration_tolerates_missing_role_or_facts(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        async with pool.acquire() as conn:
            await conn.execute("CREATE SCHEMA health")
            await conn.execute("SET search_path TO health")

            await _apply(
                conn,
                "upgrade",
                chronicler_role=f"mem_011_missing_role_{uuid.uuid4().hex[:12]}",
            )

            chronicler_role = f"mem_011_missing_table_{uuid.uuid4().hex[:12]}"
            await conn.execute(f'CREATE ROLE "{chronicler_role}"')
            await _apply(conn, "upgrade", chronicler_role=chronicler_role)


@pytest.fixture(scope="module")
def fresh_health_memory_db_url(postgres_container) -> str:
    """Reproduce production ordering: core first, then Health memory migrations."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))

    async def _provision_chronicler_role() -> None:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            await pool.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = 'butler_chronicler_rw'
                    ) THEN
                        CREATE ROLE butler_chronicler_rw;
                    END IF;
                END;
                $$
                """
            )
            await pool.execute("GRANT USAGE ON SCHEMA health TO butler_chronicler_rw")
        finally:
            await pool.close()

    asyncio.run(_provision_chronicler_role())
    asyncio.run(run_migrations(db_url, chain="memory", schema="health"))
    return db_url


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_fresh_health_memory_chain_grants_chronicler_select(
    fresh_health_memory_db_url: str,
) -> None:
    pool = await asyncpg.create_pool(fresh_health_memory_db_url, min_size=1, max_size=2)
    try:
        privileges = await pool.fetchrow(
            """
            SELECT
                has_table_privilege('butler_chronicler_rw', 'health.facts', 'SELECT')
                    AS can_select,
                has_table_privilege('butler_chronicler_rw', 'health.facts', 'INSERT')
                    AS can_insert
            """
        )
        assert privileges is not None
        assert privileges["can_select"] is True
        assert privileges["can_insert"] is False

        async with pool.acquire() as conn:
            await conn.execute("SET ROLE butler_chronicler_rw")
            try:
                assert await conn.fetchval("SELECT COUNT(*) FROM health.facts") == 0
            finally:
                await conn.execute("RESET ROLE")
    finally:
        await pool.close()
