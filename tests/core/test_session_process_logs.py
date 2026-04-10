"""Unit tests for butlers.core.session_process_logs — condensed.

Covers:
- write(): INSERT, upsert, stderr cap at 32KiB, custom TTL
- get(): found, missing
- cleanup(): count return
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _FakePool:
    """Minimal fake asyncpg pool."""

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
# write()
# ---------------------------------------------------------------------------


async def test_write_and_stderr_cap() -> None:
    """write() inserts with correct args; stderr trimmed at 32KiB; custom ttl overrides default."""
    from butlers.core.session_process_logs import write

    max_stderr = 32 * 1024

    # Over limit: trimmed with marker
    pool = _FakePool()
    await write(pool, uuid.uuid4(), stderr="x" * (max_stderr + 500))
    stored = pool.execute_calls[0][1][4]
    assert stored.endswith("... [trimmed]")
    assert len(stored) == max_stderr + len("\n... [trimmed]")

    # Exactly at limit: not trimmed
    pool2 = _FakePool()
    exact = "y" * max_stderr
    await write(pool2, uuid.uuid4(), stderr=exact)
    assert pool2.execute_calls[0][1][4] == exact

    # None: unchanged
    pool3 = _FakePool()
    await write(pool3, uuid.uuid4(), stderr=None)
    assert pool3.execute_calls[0][1][4] is None

    # Default ttl=14; custom ttl overrides
    pool4 = _FakePool()
    await write(pool4, uuid.uuid4())
    assert pool4.execute_calls[0][1][-1] == 14

    pool5 = _FakePool()
    await write(pool5, uuid.uuid4(), ttl_days=30)
    assert pool5.execute_calls[0][1][-1] == 30


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


async def test_get_behavior() -> None:
    """get() returns dict when row exists; None when missing."""
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
    result = await get(pool, uuid.uuid4())
    assert result is not None
    assert result["pid"] == 9999
    assert result["command"] == "claude --print"

    pool2 = _FakePool(fetchrow_result=None)
    assert await get(pool2, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------


class _CleanupFakePool:
    def __init__(self, *, delete_count: int = 0) -> None:
        self._delete_count = delete_count
        self.execute_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return f"DELETE {self._delete_count}"


@pytest.mark.parametrize("count", [0, 5, 42])
async def test_cleanup_count(count: int) -> None:
    """cleanup() returns the number of deleted rows as int."""
    from butlers.core.session_process_logs import cleanup

    pool = _CleanupFakePool(delete_count=count)
    deleted = await cleanup(pool)
    assert isinstance(deleted, int)
    assert deleted == count


# ---------------------------------------------------------------------------
# write() — retry provenance fields
# ---------------------------------------------------------------------------


async def test_write_retry_provenance_fields() -> None:
    """write() passes retry_attempted, retry_succeeded, result_source, attempt_count to DB."""
    from butlers.core.session_process_logs import write

    # Retry attempted and succeeded
    pool = _FakePool()
    await write(
        pool,
        uuid.uuid4(),
        retry_attempted=True,
        retry_succeeded=True,
        result_source="retry",
        attempt_count=2,
    )
    args = pool.execute_calls[0][1]
    # retry_attempted=$7, retry_succeeded=$8, result_source=$9, attempt_count=$10
    assert args[6] is True  # retry_attempted
    assert args[7] is True  # retry_succeeded
    assert args[8] == "retry"  # result_source
    assert args[9] == 2  # attempt_count

    # Retry attempted but failed, fell back to first result
    pool2 = _FakePool()
    await write(
        pool2,
        uuid.uuid4(),
        retry_attempted=True,
        retry_succeeded=False,
        result_source="first",
        attempt_count=2,
    )
    args2 = pool2.execute_calls[0][1]
    assert args2[6] is True
    assert args2[7] is False
    assert args2[8] == "first"
    assert args2[9] == 2

    # No retry (single attempt, MCP not configured)
    pool3 = _FakePool()
    await write(pool3, uuid.uuid4(), attempt_count=1)
    args3 = pool3.execute_calls[0][1]
    assert args3[6] is None  # retry_attempted
    assert args3[7] is None  # retry_succeeded
    assert args3[8] is None  # result_source
    assert args3[9] == 1  # attempt_count

    # All defaults None when not specified
    pool4 = _FakePool()
    await write(pool4, uuid.uuid4())
    args4 = pool4.execute_calls[0][1]
    assert args4[6] is None
    assert args4[7] is None
    assert args4[8] is None
    assert args4[9] is None


async def test_get_includes_retry_provenance_fields() -> None:
    """get() returns retry provenance fields from the DB row."""
    from butlers.core.session_process_logs import get

    now = datetime.now(tz=UTC)
    row = {
        "pid": 1234,
        "exit_code": 0,
        "command": "codex exec ...",
        "stderr": "",
        "runtime_type": "codex",
        "retry_attempted": True,
        "retry_succeeded": False,
        "result_source": "first",
        "attempt_count": 2,
        "created_at": now,
        "expires_at": now + timedelta(days=14),
    }
    pool = _FakePool(fetchrow_result=row)
    result = await get(pool, uuid.uuid4())
    assert result is not None
    assert result["retry_attempted"] is True
    assert result["retry_succeeded"] is False
    assert result["result_source"] == "first"
    assert result["attempt_count"] == 2
