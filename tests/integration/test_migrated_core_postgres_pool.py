"""Regression coverage for the migrated core Postgres fixture."""

from __future__ import annotations

from unittest.mock import create_autospec

import asyncpg

from tests.integration.conftest import migrated_core_postgres_pool


class _FakePostgresContainer: ...


async def test_migrated_core_pool_uses_schema_accurate_bootstrap(monkeypatch) -> None:
    """The wrapper delegates both bootstrap staging and migration to the shared factory."""
    calls: list[dict[str, object]] = []
    pool = create_autospec(asyncpg.Pool, instance=True)

    async def _create_pool(*args: object, **kwargs: object) -> asyncpg.Pool:
        calls.append({"postgres_container": args[0], **kwargs})
        return pool

    monkeypatch.setattr("butlers.testing.migration.create_migrated_test_pool", _create_pool)
    container = _FakePostgresContainer()
    provision = migrated_core_postgres_pool.__wrapped__(container)

    async with provision(min_pool_size=2, max_pool_size=4, schema="switchboard") as yielded_pool:
        assert yielded_pool is pool
        pool.close.assert_not_awaited()

    assert calls == [
        {
            "postgres_container": container,
            "chains": ["core"],
            "pool_schema": "switchboard",
            "min_pool_size": 2,
            "max_pool_size": 4,
        }
    ]
    pool.close.assert_awaited_once_with()
