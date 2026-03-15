"""Unit tests for butlers.core.session_process_logs — no database required.

Covers:
- write(): INSERT, upsert on conflict, stderr cap at 32KiB, custom TTL
- get(): found, expired (returns None), missing (returns None)
- cleanup(): deletes expired rows, skips non-expired rows

A fake asyncpg pool is used so no Docker or live database is needed.

Issue: bu-gjb1.2 (openspec/changes/session-process-logs tasks 6.1-6.3)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake asyncpg pool helpers
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal fake asyncpg pool for unit-testing session_process_logs.

    Tracks calls to execute(), fetchrow(), and returns configurable results.
    """

    def __init__(
        self,
        *,
        fetchrow_result: dict[str, Any] | None = None,
        execute_result: str = "INSERT 0 1",
    ) -> None:
        self._fetchrow_result = fetchrow_result
        self._execute_result = execute_result
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return self._execute_result

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((sql, args))
        return self._fetchrow_result


# ---------------------------------------------------------------------------
# write() — INSERT behaviour
# ---------------------------------------------------------------------------


async def test_write_executes_insert():
    """write() calls pool.execute() with an INSERT statement."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(pool, session_id, pid=1234, exit_code=0, command="claude --print", stderr="")

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert "INSERT INTO session_process_logs" in sql
    assert session_id in args


async def test_write_passes_all_fields():
    """write() passes pid, exit_code, command, stderr, runtime_type as SQL args."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(
        pool,
        session_id,
        pid=42,
        exit_code=1,
        command="codex run",
        stderr="some error",
        runtime_type="codex",
    )

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert 42 in args  # pid
    assert 1 in args  # exit_code
    assert "codex run" in args  # command
    assert "some error" in args  # stderr
    assert "codex" in args  # runtime_type


async def test_write_upsert_on_conflict():
    """write() SQL includes ON CONFLICT (session_id) DO UPDATE — upsert semantics."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(pool, session_id)

    sql, _ = pool.execute_calls[0]
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql


async def test_write_none_fields_are_passed():
    """write() passes None for optional fields that are not provided."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(pool, session_id)

    sql, args = pool.execute_calls[0]
    # With all defaults, pid, exit_code, command, stderr, runtime_type are None
    # session_id is arg[0], then pid, exit_code, command, stderr, runtime_type, ttl_days
    assert args[0] == session_id
    assert args[1] is None  # pid
    assert args[2] is None  # exit_code
    assert args[3] is None  # command
    assert args[4] is None  # stderr
    assert args[5] is None  # runtime_type
    assert isinstance(args[6], int)  # ttl_days is default integer


async def test_write_default_ttl_is_14_days():
    """write() uses 14 days as the default TTL."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(pool, session_id)

    _, args = pool.execute_calls[0]
    # ttl_days is the last arg
    ttl_days = args[-1]
    assert ttl_days == 14


async def test_write_custom_ttl():
    """write() accepts a custom ttl_days and passes it to the INSERT."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(pool, session_id, ttl_days=30)

    _, args = pool.execute_calls[0]
    ttl_days = args[-1]
    assert ttl_days == 30


# ---------------------------------------------------------------------------
# write() — stderr cap at 32 KiB
# ---------------------------------------------------------------------------


async def test_write_stderr_cap_at_32kib():
    """write() trims stderr longer than 32 KiB and appends a [trimmed] marker."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    # Build a stderr string that exceeds 32 KiB
    max_stderr = 32 * 1024
    long_stderr = "x" * (max_stderr + 500)

    await write(pool, session_id, stderr=long_stderr)

    _, args = pool.execute_calls[0]
    stored_stderr = args[4]  # stderr is arg index 4

    assert stored_stderr is not None
    assert len(stored_stderr) <= max_stderr + len("\n... [trimmed]")
    assert stored_stderr.endswith("... [trimmed]")


async def test_write_stderr_exactly_at_limit_not_trimmed():
    """write() does not trim stderr that is exactly 32 KiB."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    max_stderr = 32 * 1024
    exact_stderr = "y" * max_stderr

    await write(pool, session_id, stderr=exact_stderr)

    _, args = pool.execute_calls[0]
    stored_stderr = args[4]

    assert stored_stderr == exact_stderr
    assert "[trimmed]" not in stored_stderr


async def test_write_stderr_none_not_modified():
    """write() passes stderr=None through unchanged (no trimming)."""
    from butlers.core.session_process_logs import write

    pool = _FakePool()
    session_id = uuid.uuid4()

    await write(pool, session_id, stderr=None)

    _, args = pool.execute_calls[0]
    assert args[4] is None


# ---------------------------------------------------------------------------
# get() — found / missing / expired
# ---------------------------------------------------------------------------


async def test_get_returns_dict_when_row_found():
    """get() returns a dict when the session has a valid, non-expired process log."""
    from butlers.core.session_process_logs import get

    now = datetime.now(tz=UTC)
    row = {
        "pid": 9999,
        "exit_code": 0,
        "command": "claude --print",
        "stderr": "",
        "runtime_type": "claude",
        "created_at": now,
        "expires_at": now + timedelta(days=14),
    }
    pool = _FakePool(fetchrow_result=row)
    session_id = uuid.uuid4()

    result = await get(pool, session_id)

    assert result is not None
    assert result["pid"] == 9999
    assert result["exit_code"] == 0
    assert result["command"] == "claude --print"
    assert result["runtime_type"] == "claude"


async def test_get_passes_session_id_and_expiry_filter():
    """get() queries with session_id and expires_at >= now() filter."""
    from butlers.core.session_process_logs import get

    pool = _FakePool(fetchrow_result=None)
    session_id = uuid.uuid4()

    await get(pool, session_id)

    assert len(pool.fetchrow_calls) == 1
    sql, args = pool.fetchrow_calls[0]
    assert "session_id" in sql
    assert "expires_at" in sql
    assert args[0] == session_id


async def test_get_returns_none_when_no_row():
    """get() returns None when no matching (non-expired) row exists."""
    from butlers.core.session_process_logs import get

    pool = _FakePool(fetchrow_result=None)
    session_id = uuid.uuid4()

    result = await get(pool, session_id)

    assert result is None


async def test_get_returns_none_for_missing_session():
    """get() returns None for a session_id that has no process log at all."""
    from butlers.core.session_process_logs import get

    pool = _FakePool(fetchrow_result=None)

    result = await get(pool, uuid.uuid4())

    assert result is None


# ---------------------------------------------------------------------------
# cleanup() — deletes expired, skips non-expired
# ---------------------------------------------------------------------------


class _CleanupFakePool:
    """Fake pool that returns configurable DELETE N result strings."""

    def __init__(self, *, delete_count: int = 0) -> None:
        self._delete_count = delete_count
        self.execute_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return f"DELETE {self._delete_count}"


async def test_cleanup_executes_delete_expired():
    """cleanup() runs a DELETE on session_process_logs filtering by expires_at < now()."""
    from butlers.core.session_process_logs import cleanup

    pool = _CleanupFakePool(delete_count=0)

    await cleanup(pool)

    assert len(pool.execute_calls) == 1
    sql, _ = pool.execute_calls[0]
    assert "DELETE" in sql.upper()
    assert "session_process_logs" in sql
    assert "expires_at" in sql


async def test_cleanup_returns_zero_when_nothing_expired():
    """cleanup() returns 0 when no rows have expired."""
    from butlers.core.session_process_logs import cleanup

    pool = _CleanupFakePool(delete_count=0)

    deleted = await cleanup(pool)

    assert deleted == 0


async def test_cleanup_returns_count_of_deleted_rows():
    """cleanup() returns the number of rows actually deleted."""
    from butlers.core.session_process_logs import cleanup

    pool = _CleanupFakePool(delete_count=5)

    deleted = await cleanup(pool)

    assert deleted == 5


async def test_cleanup_delete_count_is_int():
    """cleanup() always returns an int regardless of the DELETE N result format."""
    from butlers.core.session_process_logs import cleanup

    pool = _CleanupFakePool(delete_count=42)

    deleted = await cleanup(pool)

    assert isinstance(deleted, int)
    assert deleted == 42
