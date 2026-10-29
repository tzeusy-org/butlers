"""Tests for how the root conftest configures the shared Postgres testcontainer.

Regression guard for bu-29wkwj.6. The nightly faketime legs failed on every
scheduled run from 2026-07-25 for two reasons this configuration owns:

1. libfaketime shifted only the pytest process clock, so every DB test that
   compares a Postgres ``now()`` against a Python ``datetime.now()`` failed by
   the leg offset unless someone remembered the ``pg_clock`` marker. The
   container now runs under the same shifted clock as the process, as the
   ``postgres`` user: started as root, libfaketime creates its semaphore
   ``/faketime_sem_1`` as root, and the entrypoint's drop to ``postgres`` then
   exits 1 on "sem_open failed ... Permission denied" (run 39770283852).
2. Docker's default 64 MB ``/dev/shm`` ran out once one container had served a
   third of the suite ("could not resize shared memory segment ... No space
   left on device"), and Postgres then crashed into recovery mode.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIBRARY = "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1"


def _load_root_conftest() -> Any:
    """Load the root conftest by path (its patches are idempotent, so this is safe)."""
    spec = importlib.util.spec_from_file_location(
        "_butlers_root_conftest_postgres_options_under_test", REPO_ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


root_conftest = _load_root_conftest()


class _RecordingContainer:
    """Records the testcontainers builder calls the conftest makes."""

    def __init__(self) -> None:
        self.volumes: list[tuple[str, str, str]] = []
        self.env: dict[str, str] = {}
        self.kwargs: dict[str, object] = {}

    def with_volume_mapping(self, host: str, container: str, mode: str = "ro") -> Any:
        self.volumes.append((host, container, mode))
        return self

    def with_env(self, key: str, value: str) -> Any:
        self.env[key] = value
        return self

    def with_kwargs(self, **kwargs: object) -> Any:
        # testcontainers replaces, not merges, the kwargs on each call.
        self.kwargs = kwargs
        return self


def _configure(environ: dict[str, str]) -> _RecordingContainer:
    container = _RecordingContainer()
    assert root_conftest._configure_postgres_container(container, environ) is container
    return container


def test_container_gets_shm_beyond_the_docker_default() -> None:
    container = _configure({})

    assert container.kwargs == {"shm_size": root_conftest._POSTGRES_SHM_SIZE}
    assert root_conftest._POSTGRES_SHM_SIZE == "1g"


def test_unshifted_process_leaves_the_container_clock_alone() -> None:
    container = _configure({"LD_PRELOAD": "/usr/lib/libother.so"})

    assert container.volumes == []
    assert container.env == {}


@pytest.mark.parametrize(
    "preload",
    [_LIBRARY, f"/usr/lib/libother.so {_LIBRARY}", f"/usr/lib/libother.so:{_LIBRARY}"],
)
def test_shifted_process_shares_its_offset_with_the_container(preload: str) -> None:
    container = _configure({"LD_PRELOAD": preload, "FAKETIME": "+45d"})

    mounted = root_conftest._CONTAINER_FAKETIME_LIBRARY
    assert container.volumes == [(_LIBRARY, mounted, "ro")]
    assert container.env == {"LD_PRELOAD": mounted, "FAKETIME": "+45d"}
    assert container.kwargs == {"shm_size": root_conftest._POSTGRES_SHM_SIZE, "user": "postgres"}


def test_offset_without_the_library_preloaded_is_not_forwarded() -> None:
    container = _configure({"FAKETIME": "+45d"})

    assert container.volumes == []
    assert container.env == {}


@pytest.mark.parametrize("schema", [None, "public", "health", "switchboard", "chronicler_mem"])
async def test_provisioned_pool_is_administrative(monkeypatch, schema):
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.db import Database, register_jsonb_codec, schema_search_path

    provision = AsyncMock()
    monkeypatch.setattr(Database, "provision", provision)
    calls = []

    class Pool(asyncpg.Pool):
        def __init__(self):
            self.close = AsyncMock()
            self.terminate = AsyncMock()  # Must never be used on the successful path.

        def __await__(self):
            async def ready():
                return self

            return ready().__await__()

    pool = Pool()

    def create_pool(**kwargs):
        calls.append(kwargs)
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(
        asyncpg, "connect", AsyncMock(side_effect=AssertionError("network forbidden"))
    )
    container = _AdminContainer()
    factory = root_conftest.provisioned_postgres_pool.__wrapped__(container)
    async with factory(schema=schema, min_pool_size=2, max_pool_size=4) as actual:
        assert actual is pool
        pool.close.assert_not_awaited()
    pool.close.assert_awaited_once()
    pool.terminate.assert_not_called()
    provision.assert_awaited_once()
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 5433
    assert kwargs["user"] == "synthetic_admin"
    assert kwargs["password"] == "synthetic_only"
    assert kwargs["database"].startswith("test_")
    assert kwargs["min_size"] == 2
    assert kwargs["max_size"] == 4
    assert kwargs["init"] is register_jsonb_codec
    assert "setup" not in kwargs  # This fixture does not establish a runtime role.
    expected = schema_search_path(schema)
    assert kwargs.get("server_settings") == ({"search_path": expected} if expected else None)
    codec_connection = AsyncMock()
    await kwargs["init"](codec_connection)
    codec = codec_connection.set_type_codec.await_args.kwargs
    assert codec["schema"] == "pg_catalog" and codec["format"] == "binary"
    value = {"nested": [True, None, 2]}
    assert codec["decoder"](codec["encoder"](value)) == value


class _AdminContainer:
    username = "synthetic_admin"
    password = "synthetic_only"

    def get_container_host_ip(self):
        return "127.0.0.1"

    def get_exposed_port(self, port):
        assert port == 5432
        return "5433"


async def test_provisioned_pool_validates_schema_before_effects(monkeypatch):
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.db import Database

    provision = AsyncMock()
    driver = AsyncMock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr(Database, "provision", provision)
    monkeypatch.setattr(asyncpg, "create_pool", driver)
    factory = root_conftest.provisioned_postgres_pool.__wrapped__(_AdminContainer())
    with pytest.raises(ValueError, match="Invalid schema"):
        async with factory(schema="invalid;schema"):
            pytest.fail("invalid schema was accepted")
    provision.assert_not_awaited()
    driver.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["body", "cancel", "initialization", "initialization_cancel", "close", "close_cancel"],
)
async def test_provisioned_pool_owns_failure_cleanup(monkeypatch, failure):
    import asyncio
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.db import Database

    monkeypatch.setattr(Database, "provision", AsyncMock())
    entered = asyncio.Event()
    terminated = []
    closed = []

    class Pool(asyncpg.Pool):
        def __init__(self):
            pass

        def __await__(self):
            async def initialize():
                if failure == "initialization":
                    raise ValueError("synthetic initialization failure")
                if failure == "initialization_cancel":
                    entered.set()
                    await asyncio.Event().wait()
                return self

            return initialize().__await__()

        async def close(self):
            closed.append(self)
            if failure == "close":
                raise ValueError("synthetic close failure")
            if failure == "close_cancel":
                entered.set()
                await asyncio.Event().wait()

        def terminate(self):
            terminated.append(self)

    pool = Pool()
    monkeypatch.setattr(asyncpg, "create_pool", lambda **kwargs: pool)
    monkeypatch.setattr(
        asyncpg, "connect", AsyncMock(side_effect=AssertionError("network forbidden"))
    )
    factory = root_conftest.provisioned_postgres_pool.__wrapped__(_AdminContainer())

    async def consume():
        async with factory(schema="health"):
            if failure == "body":
                raise ValueError("synthetic body failure")
            if failure == "cancel":
                entered.set()
                await asyncio.Event().wait()

    if "cancel" in failure:
        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ValueError, match="synthetic"):
            await consume()
    assert closed == ([] if failure.startswith("initialization") else [pool])
    assert terminated == (
        [pool]
        if failure.startswith("initialization") or failure in {"close", "close_cancel"}
        else []
    )


@pytest.mark.parametrize("retryable", [True, False, "exhausted"])
async def test_provisioned_pool_ssl_retry_is_bounded(monkeypatch, retryable):
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.db import Database

    monkeypatch.setattr(Database, "provision", AsyncMock())
    calls = []
    terminated = []
    closed = []
    error = (
        ConnectionError("unexpected connection_lost() call") if retryable else ValueError("other")
    )

    class Pool(asyncpg.Pool):
        def __init__(self, fail):
            self.fail = fail

        def __await__(self):
            async def initialize():
                if self.fail:
                    raise error
                return self

            return initialize().__await__()

        async def close(self):
            closed.append(self)

        def terminate(self):
            terminated.append(self)

    first, second = Pool(True), Pool(retryable == "exhausted")

    def create_pool(**kwargs):
        calls.append(kwargs)
        assert len(calls) <= 2
        return first if len(calls) == 1 else second

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    factory = root_conftest.provisioned_postgres_pool.__wrapped__(_AdminContainer())
    if retryable == "exhausted":
        with pytest.raises(ConnectionError):
            async with factory(schema="public"):
                pytest.fail("SSL retry unexpectedly succeeded")
        assert closed == []
        assert calls[1] == {**calls[0], "ssl": "disable"}
    elif retryable:
        async with factory(schema="public") as pool:
            assert pool is second
        assert closed == [second]
        assert calls[1] == {**calls[0], "ssl": "disable"}
    else:
        with pytest.raises(ValueError, match="other"):
            async with factory():
                pytest.fail("unexpected retry")
        assert closed == []
    assert terminated == ([first, second] if retryable == "exhausted" else [first])
    assert len(calls) == (2 if retryable else 1)


@pytest.mark.parametrize("cancel", [False, True])
async def test_provisioned_pool_terminates_native_initialization(monkeypatch, cancel):
    import asyncio
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.db import Database

    monkeypatch.setattr(Database, "provision", AsyncMock())
    entered = asyncio.Event()
    pools = []
    create_pool = asyncpg.create_pool

    async def connection_boundary(*args, **kwargs):
        if cancel:
            entered.set()
            await asyncio.Event().wait()
        raise ValueError("synthetic native initialization failure")

    def native_pool(**kwargs):
        pool = create_pool(**kwargs, connect=connection_boundary)
        pools.append(pool)
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", native_pool)
    factory = root_conftest.provisioned_postgres_pool.__wrapped__(_AdminContainer())

    async def consume():
        async with factory(schema="health", min_pool_size=1, max_pool_size=1):
            pytest.fail("failed native pool was yielded")

    if cancel:
        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ValueError, match="synthetic native"):
            await consume()
    assert len(pools) == 1
    assert type(pools[0]) is asyncpg.Pool
    assert pools[0].is_closing()
    with pytest.raises(asyncpg.InterfaceError, match="closed"):
        await pools[0].acquire()
