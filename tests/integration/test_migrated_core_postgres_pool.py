"""Regression coverage for the migrated core Postgres fixture."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tests.integration.conftest import migrated_core_postgres_pool


class _FakePool:
    async def fetchval(self, query: str) -> str:
        assert query == "SELECT current_database()"
        return "fixture_db"


class _FakePostgresContainer:
    username = "user@name"
    password = "pa:ss/word?with#percent% and+plus"

    def get_container_host_ip(self) -> str:
        return "127.0.0.1"

    def get_exposed_port(self, port: int) -> str:
        assert port == 5432
        return "54321"


async def test_migrated_core_pool_url_encodes_container_credentials(monkeypatch) -> None:
    """Reserved credential characters must not alter the migration URL structure."""
    migration_urls: list[str] = []

    async def _record_migration(url: str, **_kwargs: object) -> None:
        migration_urls.append(url)

    @asynccontextmanager
    async def _provisioned_postgres_pool() -> AsyncIterator[_FakePool]:
        yield _FakePool()

    monkeypatch.setattr("butlers.migrations.run_migrations", _record_migration)
    provision = migrated_core_postgres_pool.__wrapped__(
        _provisioned_postgres_pool,
        _FakePostgresContainer(),
    )

    async with provision():
        pass

    assert migration_urls == [
        "postgresql://user%40name:pa%3Ass%2Fword%3Fwith%23percent%25%20and%2Bplus"
        "@127.0.0.1:54321/fixture_db"
    ]
