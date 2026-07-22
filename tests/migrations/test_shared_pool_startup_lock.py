"""Regression coverage for shared credential-schema startup during backups.

Plain-format ``pg_dump`` holds an ``ACCESS SHARE`` lock while copying table
contents.  Startup must not re-run schema-evolution DDL against an already
migrated ``butler_secrets`` table, because ``ALTER TABLE`` requires an
incompatible lock and used to make dashboard/daemon startup wait for the dump.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from butlers.credential_store import ensure_secrets_schema

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def test_fresh_shared_pool_schema_includes_test_state_columns(
    provisioned_postgres_pool,
) -> None:
    """Fresh provisioning produces the full migrated shared-pool table shape."""
    async with provisioned_postgres_pool() as pool:
        await ensure_secrets_schema(pool)

        columns = {
            row["column_name"]
            for row in await pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'butler_secrets'
                """
            )
        }
        category_index = await pool.fetchval(
            "SELECT to_regclass('ix_butler_secrets_category') IS NOT NULL"
        )

    assert {
        "last_verified",
        "last_test_ok",
        "last_test_code",
        "last_test_message",
    } <= columns
    assert category_index is True


async def test_existing_shared_pool_startup_recreates_index_without_waiting_for_backup_share_lock(
    provisioned_postgres_pool,
) -> None:
    """An existing table stays startup-ready while a backup reads it."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=2) as pool:
        await ensure_secrets_schema(pool)
        await pool.execute("DROP INDEX ix_butler_secrets_category")

        async with pool.acquire() as backup_conn:
            transaction = backup_conn.transaction()
            await transaction.start()
            try:
                # This is the relation-lock class held by plain-format pg_dump.
                await backup_conn.execute("LOCK TABLE butler_secrets IN ACCESS SHARE MODE")

                await asyncio.wait_for(ensure_secrets_schema(pool), timeout=0.5)
                assert (
                    await pool.fetchval(
                        "SELECT to_regclass('ix_butler_secrets_category') IS NOT NULL"
                    )
                ) is True
            finally:
                await transaction.rollback()
