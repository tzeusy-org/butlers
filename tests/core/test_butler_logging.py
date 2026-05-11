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
