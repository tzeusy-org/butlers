"""Tests for butlers.core.butler_logging.ButlerLogger.

Covers:
- Write propagation via a mocked asyncpg pool.
- Level filtering: lines below min_level are dropped.
- Unknown level is silently dropped.
- fire-and-forget log_nowait schedules a task.
- metadata serialisation error is handled gracefully.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.core.butler_logging import ButlerLogger, _level_rank
from butlers.core.logging import _butler_context
from butlers.testing.fake_pool import FakeConnection, FakePool, fake_pool

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_butler_context() -> Iterator[None]:
    """Keep a butler name a synchronous test sets from naming later tests' records."""
    token = _butler_context.set(None)
    try:
        yield
    finally:
        _butler_context.reset(token)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pool_and_conn(*, raise_on_execute: Exception | None = None) -> tuple[FakePool, FakeConnection]:
    """Return a fake pool whose acquire() yields a connection with only execute()."""
    execute = AsyncMock(side_effect=raise_on_execute) if raise_on_execute else None
    pool = fake_pool(execute=execute)
    return pool, pool.connection


# ---------------------------------------------------------------------------
# level rank helper
# ---------------------------------------------------------------------------


def test_level_rank_ordering_and_unknown():
    assert _level_rank("DEBUG") < _level_rank("INFO") < _level_rank("WARN") < _level_rank("ERROR")
    assert _level_rank("TRACE") == -1


# ---------------------------------------------------------------------------
# ButlerLogger __init__ validation
# ---------------------------------------------------------------------------


def test_min_level_validation() -> None:
    """All four documented levels accepted; an invalid min_level raises ValueError."""
    pool, _ = _pool_and_conn()
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        assert ButlerLogger(pool=pool, schema="general", min_level=level) is not None
    with pytest.raises(ValueError, match="Invalid min_level"):
        ButlerLogger(pool=pool, schema="general", min_level="TRACE")


# ---------------------------------------------------------------------------
# Write propagation
# ---------------------------------------------------------------------------


async def test_log_info_writes_and_uppercases_level() -> None:
    """INFO message is inserted via the pool; lowercase level is uppercased on write."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general", min_level="INFO")
    await bl.log("info", "hello world", source="spawner")
    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    assert "INFO" in args  # level normalised to uppercase
    assert "hello world" in args


@pytest.mark.parametrize(
    ("min_level", "level", "expect_write"),
    [
        ("INFO", "DEBUG", False),  # below min_level → dropped
        ("DEBUG", "DEBUG", True),  # at min_level → written
        ("DEBUG", "TRACE", False),  # unknown level → silently dropped
    ],
)
async def test_log_level_gating(min_level: str, level: str, expect_write: bool) -> None:
    """min_level gating: below-min and unknown levels are dropped; at/above is written."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general", min_level=min_level)
    await bl.log(level, "a line")
    if expect_write:
        conn.execute.assert_called_once()
    else:
        conn.execute.assert_not_called()


async def test_log_forwards_request_id_metadata_and_timestamp() -> None:
    """request_id (stringified), metadata (dict), and explicit ts pass through to the INSERT.

    metadata must be bound as a plain dict, not a json.dumps() string: every
    asyncpg pool in this codebase registers a JSONB codec that already calls
    json.dumps() once, so binding a pre-serialized string here would
    double-encode the column (bu-cymc4).
    """
    from uuid import uuid4

    req_id = uuid4()
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general")
    await bl.log("WARN", "something odd", request_id=req_id, metadata={"key": "value"}, ts=ts)
    conn.execute.assert_called_once()
    sql, *args = conn.execute.call_args.args
    assert "::jsonb" not in sql
    assert str(req_id) in args
    assert {"key": "value"} in args
    assert ts in args


async def test_log_db_error_does_not_propagate() -> None:
    """A pool failure is swallowed and does not raise to the caller."""
    pool, conn = _pool_and_conn(raise_on_execute=RuntimeError("db exploded"))
    bl = ButlerLogger(pool=pool, schema="general")
    # Should not raise:
    await bl.log("ERROR", "error message")


# ---------------------------------------------------------------------------
# fire-and-forget log_nowait
# ---------------------------------------------------------------------------


async def test_log_nowait_schedules_task() -> None:
    """log_nowait creates an asyncio task; the write eventually executes."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general")
    bl.log_nowait("INFO", "fire and forget")
    # Give the event loop a chance to run the task
    await asyncio.sleep(0)
    conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Metadata serialisation
# ---------------------------------------------------------------------------


async def test_metadata_with_non_json_native_value_is_stringified_not_dropped() -> None:
    """A metadata value with no native JSON type is coerced via str(), not dropped.

    The sanitizing round-trip is ``json.dumps(metadata, default=str)`` (matching
    the pattern used by audit.append(), gate.py, events.py, journal.py -- see
    bu-cymc4): arbitrary objects are stringified rather than silently dropping
    the whole metadata dict, while still guaranteeing the value bound to the
    JSONB parameter is a plain dict (never a pre-serialized string).
    """
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general")

    class _Stringifiable:
        def __str__(self) -> str:
            return "stringifiable-repr"

    await bl.log("INFO", "msg with odd metadata", metadata={"bad": _Stringifiable()})
    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    assert {"bad": "stringifiable-repr"} in args


async def test_metadata_serialisation_failure_drops_field() -> None:
    """A genuinely unserialisable value (circular reference) drops the whole field."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general")

    circular: dict[str, object] = {}
    circular["self"] = circular

    # Should not raise
    await bl.log("INFO", "msg with circular metadata", metadata=circular)
    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    # safe_metadata is passed as None when the round-trip itself raises
    assert None in args


# ---------------------------------------------------------------------------
# ButlerDBLogHandler — stdlib logging bridge
# ---------------------------------------------------------------------------


import logging  # noqa: E402

from butlers.core.butler_logging import ButlerDBLogHandler, _map_pylog_level  # noqa: E402
from butlers.core.logging import set_butler_context  # noqa: E402


def test_map_pylog_level_buckets():
    assert _map_pylog_level(logging.DEBUG) == "DEBUG"
    assert _map_pylog_level(logging.INFO) == "INFO"
    assert _map_pylog_level(logging.WARNING) == "WARN"
    assert _map_pylog_level(logging.ERROR) == "ERROR"
    assert _map_pylog_level(logging.CRITICAL) == "ERROR"
    assert _map_pylog_level(logging.NOTSET) is None


def _log_record(
    name: str = "test", level: int = logging.INFO, msg: str = "hi"
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=0, msg=msg, args=(), exc_info=None
    )


async def test_db_handler_writes_when_context_matches() -> None:
    """A record emitted in the butler's context is written via ButlerLogger."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context("general")
    handler.emit(_log_record(msg="hello db"))
    await asyncio.sleep(0)

    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    assert "hello db" in args
    assert "INFO" in args


@pytest.mark.parametrize(
    ("butler_ctx", "record_name", "reason"),
    [
        ("lifestyle", "test", "context mismatch (different butler)"),
        (None, "test", "no butler context (e.g. parent CLI logs)"),
        ("general", "butlers.core.butler_logging", "record from own module (loop guard)"),
    ],
)
async def test_db_handler_drops_record(butler_ctx, record_name, reason) -> None:
    """The DB handler drops records that must not be persisted, for each drop reason."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context(butler_ctx)  # type: ignore[arg-type]
    handler.emit(_log_record(name=record_name, msg="dropped"))
    await asyncio.sleep(0)

    conn.execute.assert_not_called()


async def test_db_handler_respects_python_log_level() -> None:
    """A DEBUG record is dropped by the handler when its level is INFO."""
    pool, conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general", level=logging.INFO)

    set_butler_context("general")
    # Logger.handle() applies level filtering, but emit() may still be called
    # directly in tests; emulate the Logger.handle() check.
    record = _log_record(level=logging.DEBUG, msg="debug noise")
    if handler.level <= record.levelno:
        handler.emit(record)
    await asyncio.sleep(0)

    conn.execute.assert_not_called()


def test_db_handler_emit_does_not_raise_on_logger_failure() -> None:
    """emit() never propagates exceptions from the underlying ButlerLogger."""
    pool, _conn = _pool_and_conn()
    bl = ButlerLogger(pool=pool, schema="general")

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    bl.log_nowait = _boom  # type: ignore[method-assign]
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context("general")
    # Should not raise.
    handler.emit(_log_record(msg="bad"))


# ---------------------------------------------------------------------------
# Loop ownership and shutdown drain (bu-rvgzmf.7, bu-rvgzmf.12)
# ---------------------------------------------------------------------------


class _RecordingPool:
    """A pool that records the loop each acquire runs on and can hold writes open.

    An asyncpg connection belongs to the loop that opened it; using it from any
    other loop corrupts its protocol state ('got result for unknown protocol
    state'). Writes fail once ``close()`` has begun, as asyncpg's do.
    """

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.acquire_loops: list[asyncio.AbstractEventLoop] = []
        self.writes: list[str] = []
        self.gate = gate
        self.closed = False

    def acquire(self) -> _RecordingPool:
        return self

    async def __aenter__(self) -> _RecordingPool:
        self.acquire_loops.append(asyncio.get_running_loop())
        if self.gate is not None:
            await self.gate.wait()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, _sql: str, _level: str, msg: str, *_args: object) -> None:
        if self.closed:
            raise RuntimeError("pool is closed")
        self.writes.append(msg)


async def test_a_line_logged_on_another_loop_is_written_on_the_pools_loop() -> None:
    # A worker thread that inherits the butler context (asyncio.to_thread copies
    # contextvars) and runs its own loop must not drive the pool from that loop.
    owner = asyncio.get_running_loop()
    pool = _RecordingPool()
    bl = ButlerLogger(pool=pool, schema="general")

    async def log_on_a_foreign_loop() -> None:
        bl.log_nowait("INFO", "from a worker loop")
        await asyncio.sleep(0)

    await asyncio.to_thread(asyncio.run, log_on_a_foreign_loop())
    for _ in range(3):
        await asyncio.sleep(0)

    assert pool.acquire_loops == [owner]
    assert pool.writes == ["from a worker loop"]


async def test_shutdown_lets_in_flight_log_writes_finish_before_the_pool_closes() -> None:
    from types import SimpleNamespace

    from butlers.lifecycle import _shutdown_chronicler_dependents

    pool = _RecordingPool(gate=asyncio.Event())
    handler = ButlerDBLogHandler(
        butler_logger=ButlerLogger(pool=pool, schema="general"), butler_name="general"
    )
    closed_with: list[list[str]] = []

    class _Db:
        async def close(self) -> None:
            pool.closed = True
            closed_with.append(list(pool.writes))

    daemon = SimpleNamespace(
        _active_modules=[],
        _modules=[],
        blob_store=None,
        _db_log_handler=handler,
        _shared_credentials_db=None,
        db=_Db(),
    )
    before = set(asyncio.all_tasks())
    set_butler_context("general")
    handler.emit(_log_record(msg="in flight at shutdown"))

    shutdown = asyncio.create_task(_shutdown_chronicler_dependents(daemon))
    for _ in range(5):
        await asyncio.sleep(0)
    assert closed_with == [], "pool closed under an in-flight log write"

    pool.gate.set()
    await asyncio.wait_for(shutdown, 5)

    assert closed_with == [["in flight at shutdown"]]
    assert daemon._db_log_handler is None
    leaked = {t for t in asyncio.all_tasks() - before if not t.done()} - {asyncio.current_task()}
    assert not leaked


async def test_a_closed_logger_drops_new_lines_instead_of_racing_the_pool() -> None:
    pool = _RecordingPool()
    bl = ButlerLogger(pool=pool, schema="general")

    await bl.aclose()
    bl.log_nowait("INFO", "after close")
    await asyncio.sleep(0)

    assert pool.acquire_loops == []
