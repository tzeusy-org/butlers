"""Tests for session detail endpoint — process_log field join.

Verifies that the GET /api/butlers/{name}/sessions/{session_id} endpoint
correctly attaches process log data when available and handles the
best-effort fallback when the session_process_logs table is absent.

Issue: bu-gjb1.2 (openspec/changes/session-process-logs task 6.5)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_SESSION_ID = uuid4()


def _make_detail_record(
    *,
    session_id: UUID | None = None,
    prompt: str = "test prompt",
    trigger_source: str = "schedule",
    success: bool = True,
    started_at: datetime = _NOW,
    completed_at: datetime | None = _NOW,
    duration_ms: int = 1234,
    result: str | None = "ok",
    tool_calls: list | None = None,
    trace_id: str | None = "trace-123",
    request_id: str | None = None,
    cost: dict | None = None,
    error: str | None = None,
    model: str | None = "claude-opus-4-20250514",
    input_tokens: int | None = 100,
    output_tokens: int | None = 200,
    parent_session_id: UUID | None = None,
    complexity: str | None = "medium",
    resolution_source: str | None = "toml_fallback",
) -> dict:
    """Create a dict mimicking an asyncpg Record for detail columns."""
    return {
        "id": session_id or _SESSION_ID,
        "prompt": prompt,
        "trigger_source": trigger_source,
        "result": result,
        "tool_calls": tool_calls or [],
        "duration_ms": duration_ms,
        "trace_id": trace_id,
        "request_id": request_id,
        "cost": cost,
        "started_at": started_at,
        "completed_at": completed_at,
        "success": success,
        "error": error,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "parent_session_id": parent_session_id,
        "complexity": complexity,
        "resolution_source": resolution_source,
    }


def _make_process_log_record(
    *,
    pid: int | None = 12345,
    exit_code: int | None = 0,
    command: str | None = "claude --print",
    stderr: str | None = "",
    runtime_type: str | None = "claude",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict:
    """Create a dict mimicking an asyncpg Record for session_process_logs columns."""
    return {
        "pid": pid,
        "exit_code": exit_code,
        "command": command,
        "stderr": stderr,
        "runtime_type": runtime_type,
        "created_at": created_at or _NOW,
        "expires_at": expires_at or datetime(2030, 1, 1, tzinfo=UTC),
    }


def _app_with_two_fetchrow(
    app: FastAPI,
    *,
    session_row: dict | None,
    process_log_row: dict | None,
) -> FastAPI:
    """Wire a FastAPI app whose pool.fetchrow returns different values per call.

    First call: the session row (for the sessions table lookup).
    Second call: the process log row (for the session_process_logs lookup).
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchval = AsyncMock(return_value=0)
    mock_pool.fetchrow = AsyncMock(side_effect=[session_row, process_log_row])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={})

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _app_with_fetchrow_raising_on_second(
    app: FastAPI,
    *,
    session_row: dict,
) -> FastAPI:
    """Wire app so second fetchrow (process log) raises an exception."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchval = AsyncMock(return_value=0)
    mock_pool.fetchrow = AsyncMock(
        side_effect=[session_row, Exception("relation session_process_logs does not exist")]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={})

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Session detail endpoint — process_log field
# ---------------------------------------------------------------------------


class TestSessionDetailProcessLog:
    async def test_process_log_included_when_available(self, app):
        """Session detail includes process_log when session_process_logs row exists."""
        detail = _make_detail_record()
        plog = _make_process_log_record(pid=9999, exit_code=0, runtime_type="claude")
        _app_with_two_fetchrow(app, session_row=detail, process_log_row=plog)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "process_log" in data
        plog_data = data["process_log"]
        assert plog_data is not None
        assert plog_data["pid"] == 9999
        assert plog_data["exit_code"] == 0
        assert plog_data["runtime_type"] == "claude"

    async def test_process_log_null_when_no_row(self, app):
        """Session detail has process_log=null when no process log row exists."""
        detail = _make_detail_record()
        _app_with_two_fetchrow(app, session_row=detail, process_log_row=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["process_log"] is None

    async def test_process_log_includes_all_fields(self, app):
        """process_log response includes all expected fields from the DB row."""
        detail = _make_detail_record()
        plog = _make_process_log_record(
            pid=42,
            exit_code=1,
            command="codex exec --json -- do work",
            stderr="warning: something",
            runtime_type="codex",
        )
        _app_with_two_fetchrow(app, session_row=detail, process_log_row=plog)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        plog_data = resp.json()["data"]["process_log"]
        assert plog_data["pid"] == 42
        assert plog_data["exit_code"] == 1
        assert plog_data["command"] == "codex exec --json -- do work"
        assert plog_data["stderr"] == "warning: something"
        assert plog_data["runtime_type"] == "codex"

    async def test_process_log_null_fields_are_serialized(self, app):
        """process_log with all-null optional fields serializes to null in JSON."""
        detail = _make_detail_record()
        plog = _make_process_log_record(
            pid=None,
            exit_code=None,
            command=None,
            stderr=None,
            runtime_type=None,
        )
        _app_with_two_fetchrow(app, session_row=detail, process_log_row=plog)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        plog_data = resp.json()["data"]["process_log"]
        # All fields should be present but null
        assert plog_data is not None
        assert plog_data["pid"] is None
        assert plog_data["exit_code"] is None
        assert plog_data["command"] is None
        assert plog_data["stderr"] is None
        assert plog_data["runtime_type"] is None

    async def test_process_log_error_is_best_effort(self, app):
        """Session detail still returns 200 even if process log lookup raises an exception."""
        detail = _make_detail_record()
        _app_with_fetchrow_raising_on_second(app, session_row=detail)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        # Best-effort: session detail succeeds; process_log is just absent (null)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["process_log"] is None

    async def test_session_detail_without_process_log_still_has_core_fields(self, app):
        """Session detail returns all core session fields even when process_log is null."""
        detail = _make_detail_record(
            prompt="morning check",
            trigger_source="schedule",
            model="claude-opus-4-20250514",
        )
        _app_with_two_fetchrow(app, session_row=detail, process_log_row=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["prompt"] == "morning check"
        assert data["trigger_source"] == "schedule"
        assert data["model"] == "claude-opus-4-20250514"
        assert data["process_log"] is None

    async def test_process_log_query_uses_expiry_filter(self, app):
        """process_log query filters out expired rows (expires_at >= now() in SQL)."""
        detail = _make_detail_record()
        # Return None to simulate an expired (or absent) process log
        _app_with_two_fetchrow(app, session_row=detail, process_log_row=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        # Verify the second fetchrow was called (it queries the process log)
        mock_pool = app.dependency_overrides[_get_db_manager]().pool.return_value
        assert mock_pool.fetchrow.call_count == 2
        # Second call SQL should include expires_at filter
        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "expires_at" in second_call_sql
