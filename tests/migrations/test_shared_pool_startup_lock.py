"""Regression coverage for shared credential-schema startup during backups.

Plain-format ``pg_dump`` holds an ``ACCESS SHARE`` lock while copying table
contents.  Startup must not re-run schema-evolution DDL against an already
migrated ``butler_secrets`` table, because ``ALTER TABLE`` requires an
incompatible lock and used to make dashboard/daemon startup wait for the dump.
"""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest

from alembic import command
from butlers.credential_store import ensure_secrets_schema
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.daemon import write_butler_config
from butlers.testing.db import open_pool
from butlers.testing.migration import create_migrated_test_db, migration_db_name

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


async def test_scoped_daemon_boot_uses_shared_credentials_as_runtime_role(
    booted_daemon, tmp_path
) -> None:
    booted = await booted_daemon(write_butler_config(tmp_path))
    daemon = booted.daemon
    store = daemon._credential_store
    assert store is not None
    assert await daemon.db.pool.fetchval("SELECT current_user") == "butler_general_rw"
    assert store.has_system_global_authority
    assert store.shared_pool is store.require_system_global_pool()
    assert await store.shared_pool.fetchval("SELECT current_user") == "butler_general_rw"
    assert (
        await store.shared_pool.fetchval(
            "SELECT pg_get_indexdef('public.ix_butler_secrets_category'::regclass)"
        )
        == "CREATE INDEX ix_butler_secrets_category ON public.butler_secrets USING btree (category)"
    )
    await ensure_secrets_schema(store.shared_pool)
    # A successful boot must not come from granting the runtime role owner powers.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await store.shared_pool.execute(
            "CREATE INDEX ix_pa06wh_probe ON public.butler_secrets (category)"
        )


@pytest.mark.parametrize("public_first", [False, True], ids=["scoped-upgrade", "public-first"])
async def test_shared_index_upgrade_replay_and_downgrade(postgres_container, public_first) -> None:
    schema = None if public_first else "general"
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core"],
        {"core": schema} if schema else {},
        {"core": "core_374"},
    )
    pool = await open_pool(url)
    try:

        async def index_oid():
            return await pool.fetchval(
                "SELECT to_regclass('public.ix_butler_secrets_category')::oid"
            )

        before = await index_oid()
        assert (before is not None) == public_first
        await run_migrations(url, chain="core", schema=schema)
        installed = await index_oid()
        assert installed is not None
        if public_first:
            assert installed == before
        await run_migrations(url, chain="core", schema="health")
        assert await index_oid() == installed

        config = _build_alembic_config(url, ["core"], target_schema=schema)
        await asyncio.to_thread(command.downgrade, config, "core_374")
        assert await index_oid() == installed
        await run_migrations(url, chain="core", schema=schema)
        assert await index_oid() == installed
    finally:
        await pool.close()
