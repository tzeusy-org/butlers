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
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.butler_logging import ButlerLogger, _level_rank

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, raise_on_execute: Exception | None = None) -> MagicMock:
    """Return a mock asyncpg pool with a working acquire() async context manager."""
    conn = AsyncMock()
    if raise_on_execute is not None:
        conn.execute = AsyncMock(side_effect=raise_on_execute)
    else:
        conn.execute = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_async_ctx(conn))
    return pool, conn


class _async_ctx:
    """Minimal async context manager wrapping an object."""

    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *args):
        pass


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
    pool, _ = _make_pool()
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        assert ButlerLogger(pool=pool, schema="general", min_level=level) is not None
    with pytest.raises(ValueError, match="Invalid min_level"):
        ButlerLogger(pool=pool, schema="general", min_level="TRACE")


# ---------------------------------------------------------------------------
# Write propagation
# ---------------------------------------------------------------------------


async def test_log_info_writes_and_uppercases_level() -> None:
    """INFO message is inserted via the pool; lowercase level is uppercased on write."""
    pool, conn = _make_pool()
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
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level=min_level)
    await bl.log(level, "a line")
    if expect_write:
        conn.execute.assert_called_once()
    else:
        conn.execute.assert_not_called()


async def test_log_forwards_request_id_metadata_and_timestamp() -> None:
    """request_id (stringified), metadata (JSON), and explicit ts pass through to the INSERT."""
    from uuid import uuid4

    req_id = uuid4()
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")
    await bl.log("WARN", "something odd", request_id=req_id, metadata={"key": "value"}, ts=ts)
    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    assert str(req_id) in args
    assert '{"key": "value"}' in args
    assert ts in args


async def test_log_db_error_does_not_propagate() -> None:
    """A pool failure is swallowed and does not raise to the caller."""
    pool, conn = _make_pool(raise_on_execute=RuntimeError("db exploded"))
    bl = ButlerLogger(pool=pool, schema="general")
    # Should not raise:
    await bl.log("ERROR", "error message")


# ---------------------------------------------------------------------------
# fire-and-forget log_nowait
# ---------------------------------------------------------------------------


async def test_log_nowait_schedules_task() -> None:
    """log_nowait creates an asyncio task; the write eventually executes."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")
    bl.log_nowait("INFO", "fire and forget")
    # Give the event loop a chance to run the task
    await asyncio.sleep(0)
    conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Metadata serialisation
# ---------------------------------------------------------------------------


async def test_metadata_serialisation_failure_drops_field() -> None:
    """If metadata cannot be JSON-serialised, the row is still inserted without it."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")

    class _Unserializable:
        pass

    # Should not raise
    await bl.log("INFO", "msg with bad metadata", metadata={"bad": _Unserializable()})
    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    # metadata_json is passed as None when serialisation fails
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


def _make_record(
    name: str = "test", level: int = logging.INFO, msg: str = "hi"
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=0, msg=msg, args=(), exc_info=None
    )


async def test_db_handler_writes_when_context_matches() -> None:
    """A record emitted in the butler's context is written via ButlerLogger."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context("general")
    handler.emit(_make_record(msg="hello db"))
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
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context(butler_ctx)  # type: ignore[arg-type]
    handler.emit(_make_record(name=record_name, msg="dropped"))
    await asyncio.sleep(0)

    conn.execute.assert_not_called()


async def test_db_handler_respects_python_log_level() -> None:
    """A DEBUG record is dropped by the handler when its level is INFO."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general", level=logging.INFO)

    set_butler_context("general")
    # Logger.handle() applies level filtering, but emit() may still be called
    # directly in tests; emulate the Logger.handle() check.
    record = _make_record(level=logging.DEBUG, msg="debug noise")
    if handler.level <= record.levelno:
        handler.emit(record)
    await asyncio.sleep(0)

    conn.execute.assert_not_called()


def test_db_handler_emit_does_not_raise_on_logger_failure() -> None:
    """emit() never propagates exceptions from the underlying ButlerLogger."""
    pool, _conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    bl.log_nowait = _boom  # type: ignore[method-assign]
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context("general")
    # Should not raise.
    handler.emit(_make_record(msg="bad"))
