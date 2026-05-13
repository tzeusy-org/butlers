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


def test_level_rank_ordering():
    assert _level_rank("DEBUG") < _level_rank("INFO") < _level_rank("WARN") < _level_rank("ERROR")


def test_level_rank_unknown():
    assert _level_rank("TRACE") == -1


# ---------------------------------------------------------------------------
# ButlerLogger __init__ validation
# ---------------------------------------------------------------------------


def test_invalid_min_level_raises() -> None:
    """Passing an invalid min_level to ButlerLogger raises ValueError."""
    pool, _ = _make_pool()
    with pytest.raises(ValueError, match="Invalid min_level"):
        ButlerLogger(pool=pool, schema="general", min_level="TRACE")


def test_valid_min_levels_accepted() -> None:
    """All four documented levels are accepted as min_level."""
    pool, _ = _make_pool()
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        bl = ButlerLogger(pool=pool, schema="general", min_level=level)
        assert bl is not None


# ---------------------------------------------------------------------------
# Write propagation
# ---------------------------------------------------------------------------


async def test_log_info_writes_to_pool() -> None:
    """INFO message is inserted via the pool."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="INFO")
    await bl.log("INFO", "hello world", source="spawner")
    conn.execute.assert_called_once()
    sql, *args = conn.execute.call_args.args
    assert "INSERT INTO butler_logs" in sql
    assert "INFO" in args
    assert "hello world" in args


async def test_log_debug_dropped_at_info_min_level() -> None:
    """DEBUG line is silently dropped when min_level=INFO."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="INFO")
    await bl.log("DEBUG", "noisy debug line")
    conn.execute.assert_not_called()


async def test_log_debug_passes_when_min_level_debug() -> None:
    """DEBUG line is written when min_level=DEBUG."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    await bl.log("DEBUG", "verbose line")
    conn.execute.assert_called_once()


async def test_log_unknown_level_drops_silently() -> None:
    """Lines with unknown level (e.g., TRACE) are discarded without error."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")
    await bl.log("TRACE", "some trace line")
    conn.execute.assert_not_called()


async def test_log_normalises_level_to_uppercase() -> None:
    """Level is uppercased before being written."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="INFO")
    await bl.log("info", "lower-case level input")
    sql, *args = conn.execute.call_args.args
    assert "INFO" in args


async def test_log_with_request_id_and_metadata() -> None:
    """request_id and metadata are passed through to the INSERT."""
    from uuid import uuid4

    req_id = uuid4()
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")
    await bl.log(
        "WARN",
        "something odd",
        request_id=req_id,
        metadata={"key": "value"},
    )
    conn.execute.assert_called_once()
    _, *args = conn.execute.call_args.args
    # request_id is stringified
    assert str(req_id) in args
    # metadata is serialised to JSON string
    assert '{"key": "value"}' in args


async def test_log_db_error_does_not_propagate() -> None:
    """A pool failure is swallowed and does not raise to the caller."""
    pool, conn = _make_pool(raise_on_execute=RuntimeError("db exploded"))
    bl = ButlerLogger(pool=pool, schema="general")
    # Should not raise:
    await bl.log("ERROR", "error message")


async def test_log_with_explicit_timestamp() -> None:
    """When ts= is provided, it is forwarded as the first bind parameter."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general")
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    await bl.log("INFO", "timestamped", ts=ts)
    sql, *args = conn.execute.call_args.args
    assert ts in args


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
    sql, *args = conn.execute.call_args.args
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
    sql, *args = conn.execute.call_args.args
    assert "INSERT INTO butler_logs" in sql
    assert "hello db" in args
    assert "INFO" in args


async def test_db_handler_drops_when_context_mismatch() -> None:
    """A record emitted under a different butler's context is dropped."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context("lifestyle")
    handler.emit(_make_record(msg="should not see"))
    await asyncio.sleep(0)

    conn.execute.assert_not_called()


async def test_db_handler_drops_when_no_butler_context() -> None:
    """A record with no butler context is dropped (e.g. parent CLI logs)."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context(None)  # type: ignore[arg-type]
    handler.emit(_make_record(msg="orphan"))
    await asyncio.sleep(0)

    conn.execute.assert_not_called()


async def test_db_handler_drops_records_from_own_module() -> None:
    """Records from ``butler_logging`` itself are dropped to avoid loops."""
    pool, conn = _make_pool()
    bl = ButlerLogger(pool=pool, schema="general", min_level="DEBUG")
    handler = ButlerDBLogHandler(butler_logger=bl, butler_name="general")

    set_butler_context("general")
    handler.emit(_make_record(name="butlers.core.butler_logging", msg="feedback"))
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
