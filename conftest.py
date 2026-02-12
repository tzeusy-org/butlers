"""Root conftest — makes shared test fixtures available to all test trees.

Fixtures defined in ``tests/conftest.py`` are automatically visible to tests
under ``tests/`` (pytest's normal conftest scoping).  This root conftest
re-exports them so they are equally visible from ``roster/*/tests/``.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

docker_available = shutil.which("docker") is not None


@dataclass
class SpawnerResult:
    """Represents the result of a Claude Code spawner invocation."""

    output: str | None = None
    success: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class MockSpawner:
    """A mock CC spawner that returns configurable results and records invocations."""

    def __init__(self, default_result: SpawnerResult | None = None) -> None:
        self.default_result = default_result or SpawnerResult()
        self.invocations: list[dict] = []
        self._results: list[SpawnerResult] = []

    def enqueue_result(self, result: SpawnerResult) -> None:
        """Enqueue a result to be returned on the next invocation."""
        self._results.append(result)

    async def spawn(self, **kwargs) -> SpawnerResult:
        """Simulate spawning a Claude Code instance."""
        self.invocations.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return self.default_result


@pytest.fixture
def mock_spawner() -> MockSpawner:
    """Provide a MockSpawner instance for tests."""
    return MockSpawner()


def _unique_test_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def postgres_container():
    """Shared Postgres testcontainer for all DB-backed tests in this pytest session.

    Isolation contract:
    - Shared: Docker container process and server instance (session scope).
    - Reset per test fixture usage: each helper call provisions a new database with
      a random name, so table rows/schemas never leak between tests.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
def provisioned_postgres_pool(
    postgres_container,
) -> Callable[..., AsyncIterator]:
    """Create a fresh database and asyncpg pool for a single test usage.

    Tests should use this as:
        async with provisioned_postgres_pool() as pool:
            ...
    """
    from butlers.db import Database

    @asynccontextmanager
    async def _provision(
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 3,
    ) -> AsyncIterator:
        db = Database(
            db_name=_unique_test_db_name(),
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
        await db.provision()
        pool = await db.connect()
        try:
            yield pool
        finally:
            await db.close()

    return _provision
