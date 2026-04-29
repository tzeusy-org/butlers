"""Tests for butlers.core.sessions — session log CRUD operations — condensed."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with sessions table cleared between tests."""
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute("TRUNCATE TABLE sessions CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# session_create / sessions_get
# ---------------------------------------------------------------------------


async def test_session_create_and_get(pool):
    """session_create returns UUID; fields are persisted; request_id=None raises."""
    from butlers.core.sessions import session_create, sessions_get

    req_id = str(uuid.uuid4())
    session_id = await session_create(
        pool,
        prompt="Run daily report",
        trigger_source="tick",
        trace_id="abc-123",
        request_id=req_id,
    )
    assert isinstance(session_id, uuid.UUID)

    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["prompt"] == "Run daily report"
    assert session["trigger_source"] == "tick"
    assert session["request_id"] == req_id
    assert session["result"] is None
    assert session["success"] is None
    assert session["completed_at"] is None

    # Missing key returns None
    assert await sessions_get(pool, uuid.uuid4()) is None

    # request_id=None raises
    with pytest.raises((ValueError, Exception)):
        await session_create(pool, prompt="x", trigger_source="tick", request_id=None)


# ---------------------------------------------------------------------------
# session_complete
# ---------------------------------------------------------------------------


async def test_session_complete_success_and_failure(pool):
    """session_complete sets success/error/result/duration; nonexistent raises."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    req_id = str(uuid.uuid4())
    session_id = await session_create(
        pool, prompt="Do work", trigger_source="schedule:x", request_id=req_id
    )

    # Complete successfully
    await session_complete(
        pool,
        session_id,
        output="All done",
        tool_calls=[{"name": "tool1"}],
        duration_ms=1234,
        success=True,
        input_tokens=100,
        output_tokens=50,
    )
    done = await sessions_get(pool, session_id)
    assert done["success"] is True
    assert done["result"] == "All done"
    assert done["duration_ms"] == 1234
    assert done["error"] is None
    assert done["completed_at"] is not None

    # Create and complete with failure
    s2 = await session_create(
        pool, prompt="Will fail", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await session_complete(
        pool,
        s2,
        output=None,
        tool_calls=[],
        duration_ms=0,
        success=False,
        error="something went wrong",
    )
    failed = await sessions_get(pool, s2)
    assert failed["success"] is False
    assert failed["error"] == "something went wrong"
    assert failed["result"] is None

    # Nonexistent raises
    with pytest.raises((ValueError, Exception)):
        await session_complete(
            pool, uuid.uuid4(), output=None, tool_calls=[], duration_ms=0, success=False
        )


async def test_session_fields_sanitize_untranslatable_unicode(pool):
    """Bad Unicode in TEXT/JSONB payloads should be stripped before persistence."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    session_id = await session_create(
        pool,
        prompt="prompt\x00with\ud83dnoise",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
    )
    await session_complete(
        pool,
        session_id,
        output="done\x00\ud83d",
        tool_calls=[
            {
                "name": "tool\x00\ud83d",
                "arguments": {
                    "value": "bad\x00\ud83dtext",
                    "items": ["ok", "\x00\ud83d"],
                },
            }
        ],
        duration_ms=42,
        success=False,
        error="boom\x00\ud83d",
        cost={"raw": "cost\x00\ud83d"},
    )

    row = await sessions_get(pool, session_id)
    assert row is not None
    assert row["prompt"] == "promptwithnoise"
    assert row["result"] == "done"
    assert row["error"] == "boom"
    assert row["tool_calls"] == [
        {
            "name": "tool",
            "arguments": {
                "value": "badtext",
                "items": ["ok", ""],
            },
        }
    ]
    assert row["cost"] == {"raw": "cost"}


# ---------------------------------------------------------------------------
# sessions_list / sessions_summary
# ---------------------------------------------------------------------------


async def test_sessions_list_and_summary(pool):
    """sessions_list returns sessions in order; sessions_summary aggregates correctly."""
    from butlers.core.sessions import (
        session_complete,
        session_create,
        sessions_list,
        sessions_summary,
    )

    for i in range(3):
        sid = await session_create(
            pool,
            prompt=f"task {i}",
            trigger_source="tick",
            request_id=str(uuid.uuid4()),
            model="claude-3",
        )
        await session_complete(
            pool,
            sid,
            output=f"result {i}",
            tool_calls=[],
            duration_ms=100,
            success=True,
            input_tokens=100,
            output_tokens=50,
        )

    listed = await sessions_list(pool)
    assert len(listed) >= 3
    # Pagination works
    page1 = await sessions_list(pool, limit=2, offset=0)
    assert len(page1) == 2

    summary = await sessions_summary(pool, period="7d")
    assert summary["total_sessions"] >= 3
    assert "by_model" in summary

    # Invalid period raises
    with pytest.raises((ValueError, Exception)):
        await sessions_summary(pool, period="invalid_period")


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------


async def test_recover_orphaned_sessions_closes_open_rows(pool):
    """Open sessions are closed and marked failed; completed rows untouched."""
    from datetime import UTC, datetime, timedelta

    from butlers.core.sessions import (
        recover_orphaned_sessions,
        session_complete,
        session_create,
        sessions_get,
    )

    # Two open sessions (one back-dated to verify duration_ms is populated).
    open_recent = await session_create(
        pool, prompt="recent open", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    open_old = await session_create(
        pool, prompt="old open", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    backdated = datetime.now(UTC) - timedelta(days=3)
    await pool.execute("UPDATE sessions SET started_at = $2 WHERE id = $1", open_old, backdated)

    # One already-completed session — must not be touched.
    completed = await session_create(
        pool, prompt="done", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await session_complete(
        pool, completed, output="ok", tool_calls=[], duration_ms=42, success=True
    )

    n = await recover_orphaned_sessions(pool)
    assert n == 2

    recent_row = await sessions_get(pool, open_recent)
    old_row = await sessions_get(pool, open_old)
    completed_row = await sessions_get(pool, completed)

    # Both orphans closed and marked failed.
    for row in (recent_row, old_row):
        assert row["completed_at"] is not None
        assert row["success"] is False
        assert row["error"] == "orphaned: daemon restart"
        assert row["duration_ms"] is not None and row["duration_ms"] >= 0

    # Old orphan got a non-trivial duration backfilled (~3 days).
    assert old_row["duration_ms"] >= 2 * 24 * 3600 * 1000

    # Completed session is untouched.
    assert completed_row["success"] is True
    assert completed_row["duration_ms"] == 42
    assert completed_row["error"] is None


async def test_recover_orphaned_sessions_clamps_duration_for_very_old_rows(pool):
    """30-day-old orphans must not overflow the INTEGER duration_ms column."""
    from datetime import UTC, datetime, timedelta

    from butlers.core.sessions import recover_orphaned_sessions, session_create, sessions_get

    sid = await session_create(
        pool, prompt="ancient", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await pool.execute(
        "UPDATE sessions SET started_at = $2 WHERE id = $1",
        sid,
        datetime.now(UTC) - timedelta(days=30),
    )
    n = await recover_orphaned_sessions(pool)
    assert n == 1
    row = await sessions_get(pool, sid)
    assert row["duration_ms"] == 2147483647


async def test_recover_orphaned_sessions_idempotent_and_no_open(pool):
    """Returns 0 when no open rows; second call after recovery also returns 0."""
    from butlers.core.sessions import (
        recover_orphaned_sessions,
        session_complete,
        session_create,
    )

    # Empty table → 0
    assert await recover_orphaned_sessions(pool) == 0

    # All-completed table → 0
    sid = await session_create(
        pool, prompt="x", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await session_complete(pool, sid, output="ok", tool_calls=[], duration_ms=1, success=True)
    assert await recover_orphaned_sessions(pool) == 0

    # One orphan → 1, then 0 on second pass
    await session_create(pool, prompt="orphan", trigger_source="tick", request_id=str(uuid.uuid4()))
    assert await recover_orphaned_sessions(pool) == 1
    assert await recover_orphaned_sessions(pool) == 0


async def test_recover_orphaned_sessions_preserves_existing_error(pool):
    """If error is already set (e.g. budget overrun), do not overwrite it."""
    from butlers.core.sessions import recover_orphaned_sessions, session_create, sessions_get

    sid = await session_create(
        pool, prompt="x", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await pool.execute("UPDATE sessions SET error = 'budget overrun' WHERE id = $1", sid)
    n = await recover_orphaned_sessions(pool)
    assert n == 1
    row = await sessions_get(pool, sid)
    assert row["error"] == "budget overrun"
    assert row["success"] is False
    assert row["completed_at"] is not None


# ---------------------------------------------------------------------------
# Immutability contract
# ---------------------------------------------------------------------------


def test_no_delete_or_truncate_in_sessions_module():
    """sessions module must not expose delete/truncate/drop functions."""
    import inspect

    import butlers.core.sessions as mod

    source = inspect.getsource(mod)
    assert "DROP TABLE" not in source.upper()
    assert "TRUNCATE" not in source.upper()
    members = dir(mod)
    assert not any(
        "delete" in m.lower() or "drop" in m.lower() or "truncate" in m.lower() for m in members
    )
