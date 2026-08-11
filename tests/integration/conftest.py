"""Shared fixtures for real-Postgres integration tests."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


@pytest.fixture
def migrated_core_postgres_pool(postgres_container):
    """Provision a fresh database with the production core schema.

    The schema-accurate factory stages ``init-db.sql`` through the disposable
    testcontainer control login before it runs the core Alembic chain as its
    normal NOCREATEDB migration login. This keeps integration fixtures aligned
    with the trusted restore-drill bootstrap contract.
    """
    from butlers.testing.migration import create_migrated_test_pool

    @asynccontextmanager
    async def _provision(
        *, min_pool_size: int = 1, max_pool_size: int = 3, schema: str | None = None
    ):
        pool = await create_migrated_test_pool(
            postgres_container,
            chains=["core"],
            pool_schema=schema,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
        try:
            yield pool
        finally:
            await pool.close()

    return _provision
