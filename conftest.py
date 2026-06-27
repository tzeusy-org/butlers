"""Root conftest — makes shared test fixtures available to all test trees.

Fixtures defined in ``tests/conftest.py`` are automatically visible to tests
under ``tests/`` (pytest's normal conftest scoping).  This root conftest
re-exports them so they are equally visible from ``roster/*/tests/``.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

import pytest

# Trigger roster module discovery so dynamically-loaded modules
# are available in sys.modules before test collection.
from butlers.modules.registry import default_registry as _default_registry
from butlers.testing.shared_fixtures import MockSpawner, SpawnerResult, mock_spawner

__all__ = ["MockSpawner", "SpawnerResult", "mock_spawner"]

_default_registry()

# Pre-load roster job modules so ``from butlers.jobs._roster.<butler>_jobs``
# imports work in tests without relying on roster/ being a namespace package
# on sys.path (which fails in Docker).
from butlers.jobs._roster_loader import load_roster_jobs as _load_roster_jobs  # noqa: E402

for _butler in ("finance", "health", "relationship", "travel"):
    try:
        _load_roster_jobs(_butler)
    except FileNotFoundError:
        pass

# Pre-load roster API routers so ``from butlers.api._roster.<butler>.router``
# and ``from butlers.api._roster.<butler>.models`` work in tests.
from butlers.api.router_discovery import discover_butler_routers as _discover_routers  # noqa: E402

try:
    _discover_routers()
except Exception:
    pass


@pytest.fixture(autouse=True)
def _mock_s3_startup_check(monkeypatch):
    """Globally skip S3 connectivity checks in daemon tests.

    Patches the daemon's startup to skip the S3 head_bucket call.
    Tests that specifically test S3 (test_blob_storage.py) use moto's
    ThreadedMotoServer and call startup_check() directly on the instance.
    """

    async def _noop_startup_check(self):
        pass

    from butlers.storage.blobs import S3BlobStore

    monkeypatch.setattr(S3BlobStore, "startup_check", _noop_startup_check)


@pytest.fixture(autouse=True)
def _fake_embedding_engine(monkeypatch):
    """Globally replace the real sentence-transformers model with a deterministic fake.

    The real ``EmbeddingEngine`` loads ``all-MiniLM-L6-v2`` from HuggingFace at
    construction time.  In CI this triggers an HTTP 429 rate-limit on fresh
    runners that have no local model cache, causing random test failures.

    This fixture replaces ``EmbeddingEngine`` in the helpers module with a fake
    class that produces 384-dimensional vectors seeded deterministically by the
    hash of the input text — no network access, no model files, reproducible
    across runs.

    Tests that specifically exercise the caching/singleton behaviour of
    ``get_embedding_engine()`` already patch ``EmbeddingEngine`` locally inside a
    ``unittest.mock.patch`` context manager; those local patches take precedence
    over this fixture and are unaffected.

    The ``_embedding_engines`` singleton cache is cleared before each test and
    restored afterwards so that tests cannot accidentally share a stale real
    engine that was constructed before this fixture applied.
    """
    import hashlib

    class _FakeEmbeddingEngine:
        """Deterministic drop-in for EmbeddingEngine — no model/network needed."""

        _DIMENSION = 384

        def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
            self._model_name = model_name
            self._dim = self._DIMENSION

        @property
        def model_name(self) -> str:
            return self._model_name

        @property
        def dimension(self) -> int:
            return self._dim

        def embed(self, text: str) -> list[float]:
            return self._hash_vec(text or " ")

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.embed(t) for t in texts]

        def _hash_vec(self, text: str) -> list[float]:
            """Seed a 384-float vector from the SHA-256 digest of *text*.

            Each dimension is derived from a different 4-byte slice of a
            sequence of SHA-256 digests (re-hashing as needed), normalised to
            the range [-1, 1].  The result is stable across Python versions and
            OS platforms.
            """
            raw = hashlib.sha256(text.encode()).digest()
            # Extend to cover 384 * 4 bytes = 1536 bytes (6 rounds of 256 bits).
            while len(raw) < self._DIMENSION * 4:
                raw += hashlib.sha256(raw).digest()
            import struct

            floats: list[float] = []
            for i in range(self._DIMENSION):
                (uint,) = struct.unpack_from(">I", raw, i * 4)
                floats.append((uint / 0xFFFF_FFFF) * 2.0 - 1.0)
            return floats

    # Patch the class used by get_embedding_engine() to construct new instances.
    from butlers.modules.memory.tools import _helpers

    monkeypatch.setattr(_helpers, "EmbeddingEngine", _FakeEmbeddingEngine)

    # Clear the singleton cache so no test inherits a stale real engine that was
    # constructed before this fixture applied.  Restore the original entries on
    # teardown so other fixtures/tests are not affected by cross-test state.
    saved_cache = dict(_helpers._embedding_engines)
    _helpers._embedding_engines.clear()
    yield
    _helpers._embedding_engines.clear()
    _helpers._embedding_engines.update(saved_cache)


@pytest.fixture(autouse=True)
def _restore_approvals_guard_hooks():
    """Snapshot and restore the process-global approvals guard hooks per test.

    ``butlers.core.approvals_hooks`` holds two module-level hook slots
    (``_email_guard_hook`` and ``_recipient_guard_hook``) that the approvals
    module registers during ``on_startup`` — a process-global that is never torn
    down (a daemon lives forever in production, so unregistering on shutdown is
    pointless there). In the test suite, however, any test that starts a real
    daemon with the approvals module enabled (e.g. the messenger route.execute
    integration tests) leaves these globals registered for the rest of the xdist
    worker process. That flips the otherwise fail-open ``check_recipient`` /
    ``check_email_recipient`` core stubs into fail-closed mode for unrelated
    later tests that mock an empty pool, parking owner/default sends they expect
    to deliver. Snapshot-and-restore here makes every test independent of
    whichever earlier test happened to register a hook.
    """
    import butlers.core.approvals_hooks as _hooks

    saved_email = _hooks._email_guard_hook
    saved_recipient = _hooks._recipient_guard_hook
    try:
        yield
    finally:
        _hooks._email_guard_hook = saved_email
        _hooks._recipient_guard_hook = saved_recipient


if TYPE_CHECKING:
    from asyncpg.pool import Pool
    from testcontainers.postgres import PostgresContainer

docker_available = shutil.which("docker") is not None
logger = logging.getLogger(__name__)
_TESTCONTAINER_START_LOCK_PATH = os.path.join(
    tempfile.gettempdir(), "butlers-testcontainers-start.lock"
)
_DEFAULT_XDIST_AUTO_WORKERS = 3

_TEARDOWN_TRANSIENT_EXIT_EVENT_SNIPPET = "did not receive an exit event"
_TEARDOWN_TRANSIENT_ALREADY_IN_PROGRESS_SNIPPET = "is already in progress"
_TEARDOWN_TRANSIENT_NO_SUCH_CONTAINER_SNIPPET = "no such container"
_TESTCONTAINER_STOP_RETRY_ATTEMPTS = 4
_TESTCONTAINER_STOP_BASE_DELAY_SECONDS = 0.1
_TRANSIENT_DOCKER_STARTUP_ERROR_MARKERS = (
    "error while fetching server api version",
    "read timed out",
)
_TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS = (
    "did not receive an exit event",
    "tried to kill container",
    "no such container",
    "removal of container",
    "is already in progress",
    "is dead or marked for removal",
)


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


def _is_transient_docker_startup_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return all(marker in message for marker in _TRANSIENT_DOCKER_STARTUP_ERROR_MARKERS)


def _initialize_docker_client_with_retry(
    initialize: Callable[[], None],
    *,
    max_attempts: int = 3,
) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            initialize()
            return
        except Exception as exc:
            if not _is_transient_docker_startup_error(exc) or attempt >= max_attempts:
                raise
            time.sleep(0.5 * attempt)


@contextmanager
def _serialize_testcontainer_startup() -> Iterator[None]:
    """Serialize Docker container creation across xdist workers.

    Session-scoped fixtures still instantiate once per worker process under
    pytest-xdist. Locking only the Docker API create/start section avoids the
    `requests.exceptions.ReadTimeout` bursts seen when many workers ask the
    daemon to create `pgvector/pgvector:pg17` containers at the same time.
    """

    fd = os.open(_TESTCONTAINER_START_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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


def _install_resilient_testcontainers_startup() -> None:
    from testcontainers.core.docker_client import DockerClient

    if getattr(DockerClient.__init__, "__butlers_resilient_startup__", False):
        return

    original_init = DockerClient.__init__

    def _resilient_init(self: object, **kwargs: object) -> None:
        _initialize_docker_client_with_retry(lambda: original_init(self, **kwargs))

    _resilient_init.__butlers_resilient_startup__ = True
    _resilient_init.__wrapped__ = original_init
    DockerClient.__init__ = _resilient_init


def _install_serialized_testcontainers_run() -> None:
    from testcontainers.core.docker_client import DockerClient

    if getattr(DockerClient.run, "__butlers_serialized_start__", False):
        return

    original_run = DockerClient.run

    def _serialized_run(self: object, *args: object, **kwargs: object) -> object:
        with _serialize_testcontainer_startup():
            return original_run(self, *args, **kwargs)

    _serialized_run.__butlers_serialized_start__ = True
    _serialized_run.__wrapped__ = original_run
    DockerClient.run = _serialized_run


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


_install_resilient_testcontainers_startup()
_install_serialized_testcontainers_run()
_install_resilient_testcontainers_stop()


def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    """Cap ``-n auto`` to the repo's intended worker count.

    CI integration commands explicitly pass ``-n auto``, which bypasses the
    ``pyproject.toml`` default and can fan out enough workers to overwhelm
    Docker-backed testcontainers startup. Keep auto aligned with the repo's
    documented three-worker contract unless an explicit override is supplied.
    """

    raw = os.environ.get("PYTEST_XDIST_AUTO_WORKERS")
    if raw:
        return max(1, int(raw))
    return _DEFAULT_XDIST_AUTO_WORKERS


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

    with PostgresContainer("pgvector/pgvector:pg17") as pg:
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
