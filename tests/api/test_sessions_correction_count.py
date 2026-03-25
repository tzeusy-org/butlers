"""Tests for session detail endpoint — correction_count field.

Verifies that the GET /api/butlers/{name}/sessions/{session_id} endpoint
correctly attaches correction_count when the corrections table exists, returns
0 when no corrections exist, and gracefully falls back to 0 when the
corrections table is absent (pre-migration).

Issue: bu-k2af (openspec/changes/error-recovery-corrections tasks 6.1-6.2)
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


def _app_with_correction_count(
    app: FastAPI,
    *,
    session_row: dict | None,
    correction_count: int | None = 0,
    process_log_row: dict | None = None,
    corrections_raises: Exception | None = None,
) -> FastAPI:
    """Wire a FastAPI app for session detail with a controllable correction count.

    fetchrow side_effect: [session_row, process_log_row]
    fetchval side_effect: [correction_count]  (or raises if corrections_raises is set)
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchrow = AsyncMock(side_effect=[session_row, process_log_row])

    if corrections_raises is not None:
        mock_pool.fetchval = AsyncMock(side_effect=corrections_raises)
    else:
        mock_pool.fetchval = AsyncMock(return_value=correction_count)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={})

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Session detail endpoint — correction_count field
# ---------------------------------------------------------------------------


class TestSessionDetailCorrectionCount:
    async def test_correction_count_zero_when_no_corrections(self, app):
        """correction_count defaults to 0 when the corrections table is empty."""
        detail = _make_detail_record()
        _app_with_correction_count(app, session_row=detail, correction_count=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["correction_count"] == 0

    async def test_correction_count_reflects_db_value(self, app):
        """correction_count should reflect the count returned from the corrections table."""
        detail = _make_detail_record()
        _app_with_correction_count(app, session_row=detail, correction_count=3)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["correction_count"] == 3

    async def test_correction_count_zero_when_table_missing(self, app):
        """correction_count falls back to 0 when corrections table does not exist."""
        detail = _make_detail_record()
        _app_with_correction_count(
            app,
            session_row=detail,
            corrections_raises=Exception("relation corrections does not exist"),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        # Graceful fallback: still 200, correction_count defaults to 0
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["correction_count"] == 0

    async def test_correction_count_zero_when_fetchval_returns_none(self, app):
        """correction_count is 0 when fetchval returns None (empty table edge case)."""
        detail = _make_detail_record()
        _app_with_correction_count(app, session_row=detail, correction_count=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["correction_count"] == 0

    async def test_correction_count_query_uses_target_session_id(self, app):
        """Correction count query must filter by target_session_id = session_id."""
        detail = _make_detail_record()
        _app_with_correction_count(app, session_row=detail, correction_count=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        mock_pool = app.dependency_overrides[_get_db_manager]().pool.return_value
        # fetchval should have been called with the session_id as the parameter
        fetchval_calls = mock_pool.fetchval.call_args_list
        assert len(fetchval_calls) == 1
        call_sql = fetchval_calls[0][0][0]
        assert "corrections" in call_sql
        assert "target_session_id" in call_sql
        # The session_id must be passed as a positional arg
        assert fetchval_calls[0][0][1] == _SESSION_ID

    async def test_session_detail_still_returns_200_when_corrections_raise(self, app):
        """Session detail endpoint returns 200 even when correction count query raises."""
        detail = _make_detail_record()
        _app_with_correction_count(
            app,
            session_row=detail,
            corrections_raises=Exception("DB connection lost"),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200

    async def test_correction_count_present_in_response_model(self, app):
        """correction_count is always present in the SessionDetail response."""
        detail = _make_detail_record()
        _app_with_correction_count(app, session_row=detail, correction_count=7)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "correction_count" in data
        assert data["correction_count"] == 7
