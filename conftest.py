"""Root conftest — makes shared test fixtures available to all test trees.

Fixtures defined in ``tests/conftest.py`` are automatically visible to tests
under ``tests/`` (pytest's normal conftest scoping).  This root conftest
re-exports them so they are equally visible from ``roster/*/tests/``.
"""

from __future__ import annotations

import shutil
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from asyncpg.pool import Pool
    from testcontainers.postgres import PostgresContainer

docker_available = shutil.which("docker") is not None
_TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS = (
    "did not receive an exit event",
    "no such container",
    "removal of container",
    "is already in progress",
)


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


def _is_transient_docker_teardown_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS)


def _remove_container_with_retry(
    container: object,
    *,
    force: bool,
    delete_volume: bool,
    max_attempts: int = 4,
) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            container.remove(force=force, v=delete_volume)
            return
        except Exception as exc:
            if not _is_transient_docker_teardown_error(exc):
                raise
            if attempt < max_attempts:
                time.sleep(0.1 * attempt)
                continue
            warnings.warn(
                "Ignoring transient Docker teardown error after retries: "
                f"{exc}. This can happen under pytest-xdist container shutdown races.",
                RuntimeWarning,
                stacklevel=2,
            )
            return


def _install_resilient_testcontainers_stop() -> None:
    from testcontainers.core.container import DockerContainer

    if getattr(DockerContainer.stop, "__butlers_resilient__", False):
        return

    original_stop = DockerContainer.stop

    def _resilient_stop(self: object, force: bool = True, delete_volume: bool = True) -> None:
        if self._container:
            _remove_container_with_retry(
                self._container,
                force=force,
                delete_volume=delete_volume,
            )
        self.get_docker_client().client.close()

    _resilient_stop.__butlers_resilient__ = True
    _resilient_stop.__wrapped__ = original_stop
    DockerContainer.stop = _resilient_stop


_install_resilient_testcontainers_stop()


def _unique_test_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
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
    postgres_container: PostgresContainer,
) -> Callable[..., AbstractAsyncContextManager[Pool]]:
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
    ) -> AsyncIterator[Pool]:
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
