"""Shared test fixtures for the butlers test suite.

The canonical definitions live in the root ``conftest.py`` so they are visible
to both ``tests/`` and ``roster/*/tests/``.  This file re-exports them so that
existing imports like ``from tests.conftest import SpawnerResult`` keep working.

Database Fixture Pattern
------------------------
Tests that need a schema-accurate PostgreSQL database should use real Alembic
migrations rather than hand-rolled ``CREATE TABLE`` statements.  Hand-rolled DDL
drifts whenever a migration adds a column, causing silent schema mismatches.

**Preferred pattern** — module-scoped DB provisioned with real migrations::

    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    @pytest.fixture(scope="module")
    def migrated_db_url(postgres_container) -> str:
        return create_migrated_test_db(
            postgres_container,
            migration_db_name(),
            chains=["core", "memory", "relationship"],
            # ``schemas`` maps chain name → target schema for SET search_path.
            # Omit a chain (or omit schemas entirely) to land tables in public.
            schemas={"relationship": "relationship"},  # optional
        )

    @pytest.fixture
    async def pool(postgres_container, migrated_db_url):
        # Open an asyncpg pool to the migrated DB, truncate between tests.
        p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
        for table in [<data tables in dependency order>]:
            await p.execute(f"TRUNCATE TABLE {table} CASCADE")
        yield p
        await p.close()

Adding a new migration column/table requires **no fixture changes** — the next
test run picks it up automatically.

See ``tests/features/test_vcard.py`` for a complete working example.
"""

from __future__ import annotations

from butlers.testing.shared_fixtures import MockSpawner, SpawnerResult, mock_spawner  # noqa: F401

__all__ = ["MockSpawner", "SpawnerResult", "mock_spawner"]
