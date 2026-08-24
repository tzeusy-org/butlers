"""Root conftest — makes shared test fixtures available to all test trees.

Fixtures defined in ``tests/conftest.py`` are automatically visible to tests
under ``tests/`` (pytest's normal conftest scoping).  This root conftest
re-exports them so they are equally visible from ``roster/*/tests/``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import tempfile
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# --- Package-source guard (bu-1redj) -----------------------------------------
#
# A worktree whose ``.venv`` is a *symlink* to another checkout's venv inherits
# that venv's editable-install ``.pth``, which hardcodes the other checkout's
# ``src/``.  ``import butlers`` then resolves outside this tree and the suite
# validates someone else's working copy while looking like it validated the
# branch diff.  CI never sees it (fresh checkout, fresh install); what it
# destroys is local pre-push confidence.
#
# Because the damage *is* false confidence, this refuses to run rather than
# warning into a scrollback nobody reads.  It runs before the first ``butlers``
# import below, so the wrong package is never loaded at all.

PACKAGE_SOURCE_GUARD_BANNER = "the `butlers` package does not resolve inside this checkout"

_ALLOW_EXTERNAL_PACKAGE_ENV = "BUTLERS_ALLOW_EXTERNAL_PACKAGE"
_REPO_ROOT = Path(__file__).resolve().parent


def _package_source_paths(spec: ModuleSpec | None) -> list[Path]:
    """Every filesystem location *spec* would load the package from.

    Both halves matter: ``origin`` is the ``__init__.py`` a regular package
    loads, ``submodule_search_locations`` is all a namespace package has.
    """
    if spec is None:
        return []
    paths = [Path(spec.origin)] if spec.origin and spec.origin != "namespace" else []
    paths.extend(Path(location) for location in spec.submodule_search_locations or [])
    return paths


def _external_package_error(repo_root: Path, source_paths: list[Path]) -> str | None:
    """Describe an out-of-tree ``butlers``, or return ``None`` when it is in tree.

    "In tree" means under ``<repo_root>/src``, not merely under *repo_root*: a
    stale non-editable copy sitting in the worktree's own ``.venv`` is the same
    hazard, since the code under test is a copy rather than the working tree.

    An empty *source_paths* means nothing is installed; that is a plain
    ``ImportError`` a moment later, and it already says so clearly.
    """
    expected_root = (repo_root / "src").resolve()
    for path in source_paths:
        resolved = path.resolve()
        if resolved == expected_root or expected_root in resolved.parents:
            continue
        return (
            f"\n{PACKAGE_SOURCE_GUARD_BANNER}.\n\n"
            f"  this checkout : {repo_root}\n"
            f"  expected under: {expected_root}\n"
            f"  resolved to   : {resolved}\n\n"
            "Running the suite now would validate that other source tree instead of\n"
            "this one (bu-1redj). The usual cause is a `.venv` symlinked to another\n"
            "checkout's venv, whose editable-install .pth hardcodes that checkout's src/.\n\n"
            "  check:  ls -ld .venv\n"
            "  repair: rm .venv && uv sync --dev    # rm on a symlink drops the link only\n\n"
            "Do NOT run `uv sync` while .venv is still a symlink: it mutates the linked\n"
            "venv and can re-point the other checkout's .pth at this worktree.\n\n"
            f"Set {_ALLOW_EXTERNAL_PACKAGE_ENV}=1 to test the external package on purpose.\n"
        )
    return None


def _assert_butlers_resolves_in_tree() -> None:
    if os.environ.get(_ALLOW_EXTERNAL_PACKAGE_ENV):
        return
    message = _external_package_error(
        _REPO_ROOT, _package_source_paths(importlib.util.find_spec("butlers"))
    )
    if message is not None:
        raise RuntimeError(message)


_assert_butlers_resolves_in_tree()

# Trigger roster module discovery so dynamically-loaded modules
# are available in sys.modules before test collection.
from butlers.modules.registry import default_registry as _default_registry  # noqa: E402
from butlers.testing.shared_fixtures import (  # noqa: E402
    MockSpawner,
    SpawnerResult,
    mock_spawner,
)

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
def _restore_approval_hook_runtimes():
    """Keep pool-scoped approvals runtimes hermetic between tests."""
    import butlers.core.approvals_hooks as _hooks

    saved_runtimes = dict(_hooks._approval_hooks_by_pool)
    _hooks._approval_hooks_by_pool.clear()
    try:
        yield
    finally:
        _hooks._approval_hooks_by_pool.clear()
        _hooks._approval_hooks_by_pool.update(saved_runtimes)


if TYPE_CHECKING:
    from asyncpg.pool import Pool
    from testcontainers.postgres import PostgresContainer

docker_available = shutil.which("docker") is not None
logger = logging.getLogger(__name__)
_TESTCONTAINER_START_LOCK_PATH = os.path.join(
    tempfile.gettempdir(), "butlers-testcontainers-start.lock"
)
_DEFAULT_XDIST_AUTO_WORKERS = 3

_TESTCONTAINER_STOP_RETRY_ATTEMPTS = 4
_TESTCONTAINER_STOP_BASE_DELAY_SECONDS = 0.1
_TRANSIENT_DOCKER_STARTUP_ERROR_MARKERS = (
    "error while fetching server api version",
    "read timed out",
)
# The one marker set for teardown. Everything that retries a container removal
# classifies through ``_is_transient_docker_teardown_error``; do not add a second
# list somewhere else (bu-1y1qs). "read timed out" is here as well as in the
# startup set: a busy daemon can time out the remove call it is in fact still
# processing.
_TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS = (
    "did not receive an exit event",
    "tried to kill container",
    "no such container",
    "removal of container",
    "is already in progress",
    "is dead or marked for removal",
    "read timed out",
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
    """True when a teardown failure is a Docker race rather than real breakage.

    Two rules, both deliberate (bu-1y1qs):

    - A ``requests`` read timeout is transient by *type*, independent of message
      text. The daemon has usually completed the removal we stopped waiting for,
      and a contended host produces these in bursts (AGENTS.md, "Do not run two
      full backend gates concurrently").
    - Otherwise match ``_TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS`` across the
      whole ``__cause__``/``__context__`` chain and any docker-py
      ``.explanation``. Deliberately *not* gated on an HTTP 500: docker-py
      raises ``NotFound`` (404) for "No such container" and a 409 for "removal
      of container ... is already in progress", which are exactly the races
      worth tolerating, so a 500-only rule would call them fatal.
    """
    try:
        from requests.exceptions import ReadTimeout
    except Exception:  # pragma: no cover - requests ships as a docker-py dependency
        pass
    else:
        if isinstance(exc, ReadTimeout):
            return True

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


def _container_identity(container: object) -> str:
    """Best-effort ``name (short-id)`` for a docker-py container, for warnings."""
    name = getattr(container, "name", None) or "<unnamed>"
    container_id = getattr(container, "id", None) or "<unknown-id>"
    return f"{name} ({container_id[:12]})"


def _remove_container_with_retry(
    container: object,
    *,
    force: bool,
    delete_volume: bool,
    max_attempts: int = _TESTCONTAINER_STOP_RETRY_ATTEMPTS,
    base_delay_seconds: float = _TESTCONTAINER_STOP_BASE_DELAY_SECONDS,
) -> None:
    """Remove a container, retrying the known transient Docker teardown races.

    A final *transient* failure warns and returns instead of raising, and that
    is the contract, not an oversight (openspec/specs/testing/spec.md, scenario
    "Resilient testcontainer teardown"). Raising would turn a Docker race in a
    session-scoped fixture's teardown into a red ~40-minute gate after every
    test had already passed, while the damage it reports — one leaked container
    — is recoverable: Ryuk reaps the ordinary case and
    ``scripts/reap_orphaned_testcontainers.py`` sweeps the residue
    (docs/testing/orphaned-testcontainers.md). Non-transient errors still raise
    on the first attempt.
    """
    delay = base_delay_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            container.remove(force=force, v=delete_volume)
            return
        except Exception as exc:
            if not _is_transient_docker_teardown_error(exc):
                raise
            if attempt < max_attempts:
                logger.warning(
                    "Transient Docker teardown race removing %s (attempt %s/%s): %s",
                    _container_identity(container),
                    attempt,
                    max_attempts,
                    exc,
                )
                time.sleep(delay)
                delay *= 2
                continue
            # Giving up here leaks a live container (bu-3zu5l): name it, so the
            # orphan is traceable to this run instead of being rediscovered
            # weeks later by `docker ps`. Sweep with
            # `python3 scripts/reap_orphaned_testcontainers.py`.
            warnings.warn(
                f"Leaked test container {_container_identity(container)}: ignoring transient "
                f"Docker teardown error after retries: {exc}. This can happen under "
                "pytest-xdist container shutdown races. Sweep leftovers with "
                "`python3 scripts/reap_orphaned_testcontainers.py`.",
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
    """Install the *only* patch this repo puts on ``DockerContainer.stop``.

    ``_resilient_stop`` reimplements upstream ``stop`` (testcontainers 4.14.2)
    exactly, with ``container.remove`` swapped for the retrying variant. The
    retry deliberately sits around the removal call and not around the whole
    ``stop``: a second layer wrapping ``stop`` would also re-run ``remove`` when
    only ``client.close()`` had failed, and would multiply the attempt budget by
    its own. This file used to carry such a layer; it was removed in bu-1y1qs
    and ``tests/scripts/test_conftest_teardown_patch.py`` fails if one comes
    back.
    """
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

    Pass ``schema=`` to scope the pool's search_path (mirrors one-db/
    multi-schema production topology, e.g. a real "switchboard"-scoped pool
    for tests that need schema-qualified queries to resolve without an
    explicit prefix, or "public" to reproduce a caller whose search_path
    lacks the schema entirely).
    """
    from butlers.db import Database

    @asynccontextmanager
    async def _provision(
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 3,
        schema: str | None = None,
    ) -> AsyncIterator[Pool]:
        db = Database(
            db_name=_unique_test_db_name(),
            schema=schema,
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
