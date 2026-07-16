"""Shared fixtures for real-Postgres integration tests."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


@pytest.fixture
def migrated_core_postgres_pool(provisioned_postgres_pool, postgres_container):
    """Provision a fresh database with the production core schema.

    ``provisioned_postgres_pool`` owns isolated database creation and pool
    lifecycle. This wrapper runs the core Alembic chain before yielding that
    same pool, so integration tests exercise migrations rather than local DDL
    copies that can drift from production.
    """
    from butlers.migrations import run_migrations

    @asynccontextmanager
    async def _provision(**kwargs):
        async with provisioned_postgres_pool(**kwargs) as pool:
            db_name = await pool.fetchval("SELECT current_database()")
            db_url = (
                f"postgresql://{postgres_container.username}:{postgres_container.password}"
                f"@{postgres_container.get_container_host_ip()}:"
                f"{postgres_container.get_exposed_port(5432)}/{db_name}"
            )
            await run_migrations(db_url, chain="core")
            yield pool

    return _provision
