"""Tests for butlers.core.sessions — session log CRUD operations."""

from __future__ import annotations

import asyncio
import inspect
import shutil
import uuid

import asyncpg
import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Run tests in the session event loop so the pool (created in the session
    # fixture loop via asyncio_default_fixture_loop_scope=session) is usable.
    pytest.mark.asyncio(loop_scope="session"),
]


def _unique_db_name() -> str:
    """Generate a unique database name for test isolation."""
    return f"test_{uuid.uuid4().hex[:12]}"


# Use the session-scoped postgres_container from root conftest (not a local override)
# so the event loop is shared across the whole session, avoiding asyncpg loop mismatch.


@pytest.fixture
async def pool(postgres_container):
    """Create a fresh database with the sessions table and return a pool.

    WARNING: This fixture duplicates the 'sessions' table schema. If you update
    the schema via migrations, you MUST update it here as well to prevent
    schema drift in tests.
    """
    db_name = _unique_db_name()

    # Create the database via the admin connection
    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    # Connect to the new database and create the sessions table
    p = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    await p.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prompt TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            result TEXT,
            tool_calls JSONB NOT NULL DEFAULT '[]',
            duration_ms INTEGER,
            trace_id TEXT,
            model TEXT,
            cost JSONB,
            success BOOLEAN,
            error TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            parent_session_id UUID,
            request_id TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# session_create
# ---------------------------------------------------------------------------


async def test_create_session_returns_uuid(pool):
    """session_create returns a valid UUID."""
    from butlers.core.sessions import session_create

    session_id = await session_create(pool, prompt="Hello", trigger_source="schedule:test-task")
    assert isinstance(session_id, uuid.UUID)


async def test_create_session_persists_fields(pool):
    """Created session has correct prompt, trigger_source, and defaults."""
    from butlers.core.sessions import session_create, sessions_get

    session_id = await session_create(
        pool,
        prompt="Run daily report",
        trigger_source="tick",
        trace_id="abc-123",
    )
    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["prompt"] == "Run daily report"
    assert session["trigger_source"] == "tick"
    assert session["trace_id"] == "abc-123"
    assert session["result"] is None
    assert session["tool_calls"] == []
    assert session["duration_ms"] is None
    assert session["success"] is None
    assert session["error"] is None
    assert session["input_tokens"] is None
    assert session["output_tokens"] is None
    assert session["completed_at"] is None
    assert session["started_at"] is not None


async def test_create_session_with_request_id(pool):
    """Created session with request_id persists the field correctly."""
    from butlers.core.sessions import session_create, sessions_get

    request_id = "01JH8X5JQRM7FKTZQNP8Z4Y9W6"  # Example UUIDv7
    session_id = await session_create(
        pool,
        prompt="Routed message",
        trigger_source="trigger",
        trace_id="trace-123",
        request_id=request_id,
    )
    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["request_id"] == request_id
    assert session["prompt"] == "Routed message"
    assert session["trigger_source"] == "trigger"


async def test_create_session_without_request_id_defaults_null(pool):
    """Created session without request_id has NULL for that field."""
    from butlers.core.sessions import session_create, sessions_get

    session_id = await session_create(
        pool,
        prompt="Scheduled task",
        trigger_source="schedule:daily-report",
    )
    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["request_id"] is None


# ---------------------------------------------------------------------------
# session_complete
# ---------------------------------------------------------------------------


async def test_complete_session_updates_fields(pool):
    """session_complete sets result, tool_calls, duration_ms, success, and completed_at."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    session_id = await session_create(pool, prompt="Test", trigger_source="schedule:test-task")

    tool_calls = [{"tool": "state_get", "args": {"key": "foo"}}]
    await session_complete(
        pool,
        session_id=session_id,
        output="All done",
        tool_calls=tool_calls,
        duration_ms=1234,
        success=True,
        cost={"input_tokens": 100, "output_tokens": 50},
    )

    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["result"] == "All done"
    assert session["tool_calls"] == tool_calls
    assert session["duration_ms"] == 1234
    assert session["cost"] == {"input_tokens": 100, "output_tokens": 50}
    assert session["success"] is True
    assert session["error"] is None
    assert session["completed_at"] is not None


async def test_complete_with_failure(pool):
    """session_complete stores error information with success=false."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    session_id = await session_create(pool, prompt="Failing task", trigger_source="tick")

    await session_complete(
        pool,
        session_id=session_id,
        output=None,
        tool_calls=[],
        duration_ms=30000,
        success=False,
        error="Connection timed out after 30s",
    )

    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["success"] is False
    assert session["error"] == "Connection timed out after 30s"
    assert session["result"] is None
    assert session["duration_ms"] == 30000
    assert session["cost"] is None
    assert session["completed_at"] is not None


async def test_complete_nonexistent_session_raises(pool):
    """session_complete raises ValueError for a missing session ID."""
    from butlers.core.sessions import session_complete

    fake_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await session_complete(
            pool,
            session_id=fake_id,
            output="nope",
            tool_calls=[],
            duration_ms=0,
            success=True,
        )


# ---------------------------------------------------------------------------
# duration_ms
# ---------------------------------------------------------------------------


async def test_duration_ms_stored_correctly(pool):
    """Verify various duration_ms values are stored and retrieved accurately."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    for ms in (0, 1, 999, 60_000, 3_600_000):
        sid = await session_create(pool, prompt=f"dur-{ms}", trigger_source="tick")
        await session_complete(pool, sid, output="ok", tool_calls=[], duration_ms=ms, success=True)
        session = await sessions_get(pool, sid)
        assert session is not None
        assert session["duration_ms"] == ms


# ---------------------------------------------------------------------------
# trigger_source validation
# ---------------------------------------------------------------------------


async def test_trigger_source_valid_values(pool):
    """All canonical trigger_source values are accepted."""
    from butlers.core.sessions import session_create

    for source in ("schedule:daily-task", "trigger", "tick", "external", "route"):
        sid = await session_create(pool, prompt="test", trigger_source=source)
        assert isinstance(sid, uuid.UUID)


async def test_trigger_source_invalid_raises(pool):
    """Invalid trigger_source raises ValueError."""
    from butlers.core.sessions import session_create

    with pytest.raises(ValueError, match="Invalid trigger_source"):
        await session_create(pool, prompt="test", trigger_source="unknown")


# ---------------------------------------------------------------------------
# success and error columns
# ---------------------------------------------------------------------------


async def test_successful_session_has_success_true_error_null(pool):
    """Successful sessions have success=true and error=NULL."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    sid = await session_create(pool, prompt="success test", trigger_source="tick")
    await session_complete(pool, sid, output="done", tool_calls=[], duration_ms=100, success=True)
    session = await sessions_get(pool, sid)
    assert session["success"] is True
    assert session["error"] is None
    assert session["result"] == "done"


async def test_failed_session_has_success_false_error_set_result_null(pool):
    """Failed sessions have success=false, error=<message>, result=NULL."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    sid = await session_create(pool, prompt="fail test", trigger_source="schedule:test-task")
    await session_complete(
        pool,
        sid,
        output=None,
        tool_calls=[],
        duration_ms=200,
        success=False,
        error="RuntimeError: something broke",
    )
    session = await sessions_get(pool, sid)
    assert session["success"] is False
    assert session["error"] == "RuntimeError: something broke"
    assert session["result"] is None


# ---------------------------------------------------------------------------
# sessions_list pagination
# ---------------------------------------------------------------------------


async def test_sessions_list_default(pool):
    """sessions_list returns sessions ordered by started_at DESC."""
    from butlers.core.sessions import session_create, sessions_list

    ids = []
    for i in range(3):
        sid = await session_create(pool, prompt=f"list-{i}", trigger_source="schedule:test-task")
        ids.append(sid)
        # Insert a small delay so started_at ordering is deterministic
        await asyncio.sleep(0.01)

    result = await sessions_list(pool)
    assert len(result) >= 3
    # Most recent first — the last created should appear first
    result_ids = [r["id"] for r in result]
    # ids[2] was created last so should appear before ids[1] and ids[0]
    assert result_ids.index(ids[2]) < result_ids.index(ids[1])
    assert result_ids.index(ids[1]) < result_ids.index(ids[0])


async def test_sessions_list_pagination(pool):
    """sessions_list respects limit and offset."""
    from butlers.core.sessions import session_create, sessions_list

    created = []
    for i in range(5):
        sid = await session_create(pool, prompt=f"page-{i}", trigger_source="tick")
        created.append(sid)
        await asyncio.sleep(0.01)

    # Get first page of 2
    page1 = await sessions_list(pool, limit=2, offset=0)
    assert len(page1) == 2

    # Get second page of 2
    page2 = await sessions_list(pool, limit=2, offset=2)
    assert len(page2) == 2

    # Pages should not overlap
    page1_ids = {r["id"] for r in page1}
    page2_ids = {r["id"] for r in page2}
    assert page1_ids.isdisjoint(page2_ids)


async def test_sessions_list_includes_success_and_error(pool):
    """sessions_list returns success and error columns."""
    from butlers.core.sessions import session_complete, session_create, sessions_list

    sid = await session_create(pool, prompt="list-check", trigger_source="tick")
    await session_complete(pool, sid, output="ok", tool_calls=[], duration_ms=10, success=True)
    sessions = await sessions_list(pool, limit=100)
    match = next(s for s in sessions if s["id"] == sid)
    assert "success" in match
    assert "error" in match
    assert match["success"] is True
    assert match["error"] is None


# ---------------------------------------------------------------------------
# sessions_get
# ---------------------------------------------------------------------------


async def test_sessions_get_returns_full_record(pool):
    """sessions_get returns all columns for an existing session."""
    from butlers.core.sessions import session_create, sessions_get

    sid = await session_create(pool, prompt="full", trigger_source="trigger", trace_id="t-1")
    session = await sessions_get(pool, sid)
    assert session is not None

    expected_keys = {
        "id",
        "prompt",
        "trigger_source",
        "result",
        "tool_calls",
        "duration_ms",
        "trace_id",
        "model",
        "cost",
        "success",
        "error",
        "input_tokens",
        "output_tokens",
        "request_id",
        "started_at",
        "completed_at",
    }
    assert set(session.keys()) == expected_keys
    assert session["id"] == sid


async def test_sessions_get_missing_returns_none(pool):
    """sessions_get returns None for a non-existent session ID."""
    from butlers.core.sessions import sessions_get

    result = await sessions_get(pool, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# sessions_summary
# ---------------------------------------------------------------------------


async def test_sessions_summary_aggregates_totals_and_by_model(pool):
    """sessions_summary should return period totals and per-model token stats."""
    from butlers.core.sessions import session_complete, session_create, sessions_summary

    sid_a = await session_create(
        pool,
        prompt="sum-a",
        trigger_source="trigger",
        model="claude-sonnet-4-20250514",
    )
    await session_complete(
        pool,
        sid_a,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=1200,
        output_tokens=300,
    )

    sid_b = await session_create(
        pool,
        prompt="sum-b",
        trigger_source="tick",
        model="claude-haiku-35-20241022",
    )
    await session_complete(
        pool,
        sid_b,
        output="ok",
        tool_calls=[],
        duration_ms=20,
        success=True,
        input_tokens=800,
        output_tokens=100,
    )

    summary = await sessions_summary(pool, period="today")
    assert summary["period"] == "today"
    assert summary["total_sessions"] == 2
    assert summary["total_input_tokens"] == 2000
    assert summary["total_output_tokens"] == 400
    assert summary["by_model"] == {
        "claude-haiku-35-20241022": {"input_tokens": 800, "output_tokens": 100},
        "claude-sonnet-4-20250514": {"input_tokens": 1200, "output_tokens": 300},
    }


async def test_sessions_summary_respects_period_filter(pool):
    """sessions_summary should filter rows based on the requested period window."""
    from butlers.core.sessions import session_complete, session_create, sessions_summary

    sid_old = await session_create(
        pool,
        prompt="old",
        trigger_source="trigger",
        model="claude-sonnet-4-20250514",
    )
    await session_complete(
        pool,
        sid_old,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=700,
        output_tokens=200,
    )
    await pool.execute(
        "UPDATE sessions SET started_at = now() - INTERVAL '10 days' WHERE id = $1",
        sid_old,
    )

    sid_recent = await session_create(
        pool,
        prompt="recent",
        trigger_source="trigger",
        model="claude-sonnet-4-20250514",
    )
    await session_complete(
        pool,
        sid_recent,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=300,
        output_tokens=100,
    )

    summary_7d = await sessions_summary(pool, period="7d")
    assert summary_7d["total_sessions"] == 1
    assert summary_7d["total_input_tokens"] == 300
    assert summary_7d["total_output_tokens"] == 100

    summary_30d = await sessions_summary(pool, period="30d")
    assert summary_30d["total_sessions"] == 2
    assert summary_30d["total_input_tokens"] == 1000
    assert summary_30d["total_output_tokens"] == 300


async def test_sessions_summary_rejects_invalid_period(pool):
    """sessions_summary raises ValueError for unsupported periods."""
    from butlers.core.sessions import sessions_summary

    with pytest.raises(ValueError, match="Invalid period"):
        await sessions_summary(pool, period="90d")


# ---------------------------------------------------------------------------
# append-only contract
# ---------------------------------------------------------------------------


def test_no_delete_function_exists():
    """The sessions module exposes no delete capability (append-only)."""
    import butlers.core.sessions as mod

    public_names = [
        name for name in dir(mod) if not name.startswith("_") and callable(getattr(mod, name))
    ]
    for name in public_names:
        assert "delete" not in name.lower(), f"Found delete-like function: {name}"
        assert "remove" not in name.lower(), f"Found remove-like function: {name}"
        assert "purge" not in name.lower(), f"Found purge-like function: {name}"


def test_module_has_no_drop_or_truncate():
    """The sessions module source contains no DROP or TRUNCATE statements."""
    import butlers.core.sessions as mod

    source = inspect.getsource(mod)
    source_upper = source.upper()
    assert "DROP TABLE" not in source_upper
    assert "TRUNCATE" not in source_upper
    assert "DELETE FROM" not in source_upper
