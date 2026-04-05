"""Tests for butlers.core.sessions — session log CRUD operations — condensed."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def pool(postgres_container):
    db_name = _unique_db_name()
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
            ingestion_event_id UUID,
            complexity TEXT DEFAULT 'medium',
            resolution_source TEXT DEFAULT 'toml_fallback',
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """)
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
    await session_complete(pool, s2, output=None, tool_calls=[], duration_ms=0, success=False, error="something went wrong")
    failed = await sessions_get(pool, s2)
    assert failed["success"] is False
    assert failed["error"] == "something went wrong"
    assert failed["result"] is None

    # Nonexistent raises
    with pytest.raises((ValueError, Exception)):
        await session_complete(pool, uuid.uuid4(), output=None, tool_calls=[], duration_ms=0, success=False)


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
        await session_complete(pool, sid, output=f"result {i}", tool_calls=[], duration_ms=100, success=True, input_tokens=100, output_tokens=50)

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
