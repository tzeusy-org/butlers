"""Root conftest — makes shared test fixtures available to all test trees.

Fixtures defined in ``tests/conftest.py`` are automatically visible to tests
under ``tests/`` (pytest's normal conftest scoping).  This root conftest
re-exports them so they are equally visible from ``roster/*/tests/``.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from asyncpg.pool import Pool
    from testcontainers.postgres import PostgresContainer

docker_available = shutil.which("docker") is not None
logger = logging.getLogger(__name__)

_TEARDOWN_TRANSIENT_EXIT_EVENT_SNIPPET = "did not receive an exit event"
_TEARDOWN_TRANSIENT_ALREADY_IN_PROGRESS_SNIPPET = "is already in progress"
_TEARDOWN_TRANSIENT_NO_SUCH_CONTAINER_SNIPPET = "no such container"
_TESTCONTAINER_STOP_RETRY_ATTEMPTS = 4
_TESTCONTAINER_STOP_BASE_DELAY_SECONDS = 0.1
_TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS = (
    "did not receive an exit event",
    "tried to kill container",
    "no such container",
    "removal of container",
    "is already in progress",
    "is dead or marked for removal",
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


def _iter_exception_messages(exc: BaseException) -> Iterator[str]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))

        message = str(current).strip()
        if message:
            yield message.lower()

        explanation = getattr(current, "explanation", None)
        if explanation:
            if isinstance(explanation, bytes):
                explanation_text = explanation.decode("utf-8", errors="replace")
            else:
                explanation_text = str(explanation)
            explanation_text = explanation_text.strip()
            if explanation_text:
                yield explanation_text.lower()

        if current.__cause__ is not None:
            current = current.__cause__
            continue

        if current.__context__ is not None and not current.__suppress_context__:
            current = current.__context__
            continue

        current = None


def _is_transient_docker_teardown_error(exc: Exception) -> bool:
    return any(
        marker in message
        for message in _iter_exception_messages(exc)
        for marker in _TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS
    )


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


def _safe_exception_text(exc: BaseException) -> str:
    explanation_text = str(getattr(exc, "explanation", "") or "")
    try:
        rendered_error = str(exc)
    except Exception:
        rendered_error = ""
    return " ".join(part for part in (explanation_text, rendered_error) if part)


def _is_transient_testcontainer_teardown_error(exc: BaseException) -> bool:
    """True for known transient Docker API teardown races from force-remove."""
    try:
        from requests.exceptions import ReadTimeout
    except Exception:
        ReadTimeout = tuple()  # type: ignore[assignment]

    if isinstance(exc, ReadTimeout):
        return True

    try:
        from docker.errors import APIError
    except Exception:
        return False

    if not isinstance(exc, APIError):
        return False

    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code != 500:
        return False

    error_text = _safe_exception_text(exc).lower()
    return any(
        marker in error_text
        for marker in (
            _TEARDOWN_TRANSIENT_EXIT_EVENT_SNIPPET,
            _TEARDOWN_TRANSIENT_ALREADY_IN_PROGRESS_SNIPPET,
            _TEARDOWN_TRANSIENT_NO_SUCH_CONTAINER_SNIPPET,
        )
    )


def _retry_testcontainer_stop(
    stop_call: Callable[[], None],
    *,
    max_attempts: int = _TESTCONTAINER_STOP_RETRY_ATTEMPTS,
    base_delay_seconds: float = _TESTCONTAINER_STOP_BASE_DELAY_SECONDS,
) -> None:
    """Retry transient Docker teardown races with bounded backoff."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    delay = base_delay_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            stop_call()
            return
        except Exception as exc:
            if attempt >= max_attempts or not _is_transient_testcontainer_teardown_error(exc):
                raise
            logger.warning(
                "Transient Docker API teardown race (attempt %s/%s): %s",
                attempt,
                max_attempts,
                _safe_exception_text(exc),
            )
            time.sleep(delay)
            delay *= 2


def _patch_testcontainers_stop_with_retry() -> None:
    """Patch testcontainers stop() to tolerate transient Docker daemon races."""
    try:
        from testcontainers.core.container import DockerContainer
    except Exception:
        return

    if getattr(DockerContainer.stop, "_butlers_retry_patch", False):
        return

    original_stop = DockerContainer.stop

    def _stop_with_retry(self: Any, force: bool = True, delete_volume: bool = True) -> None:
        _retry_testcontainer_stop(
            lambda: original_stop(self, force=force, delete_volume=delete_volume)
        )

    setattr(_stop_with_retry, "_butlers_retry_patch", True)
    DockerContainer.stop = _stop_with_retry


_patch_testcontainers_stop_with_retry()


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
