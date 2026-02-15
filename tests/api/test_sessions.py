"""Tests for session history API endpoints.

Verifies the API contract (status codes, response shapes) for session
endpoints.  Uses a mocked DatabaseManager so no real database is required.

Issues: butlers-26h.4.1, 4.2, 4.3, 4.4
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_SESSION_ID = uuid4()


def _make_summary_record(
    *,
    session_id: UUID | None = None,
    prompt: str = "test prompt",
    trigger_source: str = "schedule",
    request_id: str | None = None,
    success: bool = True,
    started_at: datetime = _NOW,
    completed_at: datetime | None = _NOW,
    duration_ms: int = 1234,
) -> dict:
    """Create a dict mimicking an asyncpg Record for summary columns."""
    return {
        "id": session_id or _SESSION_ID,
        "prompt": prompt,
        "trigger_source": trigger_source,
        "request_id": request_id,
        "success": success,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
    }


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
    }


def _app_with_mock_db(
    *,
    fan_out_result: dict[str, list] | None = None,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
    fetchrow_result: dict | None = None,
):
    """Create a FastAPI app with a mocked DatabaseManager.

    The mock supports both fan_out (cross-butler) and pool (single-butler) modes.
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas", "switchboard"]

    # fan_out returns a dict of butler_name -> rows
    if fan_out_result is not None:
        mock_db.fan_out = AsyncMock(return_value=fan_out_result)
    else:
        mock_db.fan_out = AsyncMock(return_value={})

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/sessions — cross-butler paginated sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination fields."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_returns_sessions_from_multiple_butlers(self):
        """Sessions from fan_out should be merged with butler names attached."""
        s1 = _make_summary_record(session_id=uuid4(), prompt="atlas job")
        s2 = _make_summary_record(session_id=uuid4(), prompt="switchboard job")

        # fan_out is called twice: once for counts, once for data
        count_atlas = MagicMock(__getitem__=lambda self, i: 1)
        count_sw = MagicMock(__getitem__=lambda self, i: 1)
        fan_out_calls = [
            # First call: count query — returns [[count]] per butler
            {"atlas": [count_atlas], "switchboard": [count_sw]},
            # Second call: data query — returns rows
            {"atlas": [s1], "switchboard": [s2]},
        ]
        app = _app_with_mock_db()
        # Override fan_out to return different results for count vs data calls
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["atlas", "switchboard"]
        mock_db.fan_out = AsyncMock(side_effect=fan_out_calls)
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 2
        assert len(body["data"]) == 2
        # Each session should have a butler field
        butlers_in_results = {s["butler"] for s in body["data"]}
        assert "atlas" in butlers_in_results
        assert "switchboard" in butlers_in_results

    async def test_filter_params_accepted(self):
        """All query filter parameters should be accepted without error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/sessions",
                params={
                    "butler": "atlas",
                    "trigger_source": "schedule",
                    "success": "true",
                    "from_date": "2025-01-01T00:00:00Z",
                    "to_date": "2025-12-31T23:59:59Z",
                    "limit": 10,
                    "offset": 0,
                },
            )

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no sessions exist, return empty data with total 0."""
        app = _app_with_mock_db(fan_out_result={})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/sessions — single-butler sessions
# ---------------------------------------------------------------------------


class TestListButlerSessions:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination fields."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_sessions_have_butler_name(self):
        """Each session in the response should have the butler name set."""
        row = _make_summary_record()
        app = _app_with_mock_db(fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["butler"] == "atlas"

    async def test_filter_params_accepted(self):
        """All query filter parameters should be accepted without error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/sessions",
                params={
                    "trigger_source": "schedule",
                    "success": "false",
                    "from_date": "2025-01-01T00:00:00Z",
                    "to_date": "2025-12-31T23:59:59Z",
                    "limit": 25,
                    "offset": 5,
                },
            )

        assert resp.status_code == 200

    async def test_butler_db_unavailable_returns_503(self):
        """When the butler's DB pool doesn't exist, return 503."""
        app = _app_with_mock_db()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("no pool")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/nonexistent/sessions")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/sessions/{session_id} — session detail
# ---------------------------------------------------------------------------


class TestGetButlerSession:
    async def test_returns_session_detail(self):
        """Response should wrap a SessionDetail in ApiResponse envelope."""
        detail = _make_detail_record()
        app = _app_with_mock_db(fetchrow_result=detail)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert data["id"] == str(_SESSION_ID)
        assert data["butler"] == "atlas"
        assert data["prompt"] == "test prompt"
        assert data["trigger_source"] == "schedule"
        assert data["model"] == "claude-opus-4-20250514"
        assert data["input_tokens"] == 100
        assert data["output_tokens"] == 200

    async def test_missing_session_returns_404(self):
        """A non-existent session should return 404."""
        app = _app_with_mock_db(fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/sessions/00000000-0000-0000-0000-000000000000"
            )

        assert resp.status_code == 404

    async def test_butler_db_unavailable_returns_503(self):
        """When the butler's DB pool doesn't exist, return 503."""
        app = _app_with_mock_db()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("no pool")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/nonexistent/sessions/{uuid4()}")

        assert resp.status_code == 503

    async def test_detail_includes_all_fields(self):
        """SessionDetail should include all database columns."""
        parent_id = uuid4()
        detail = _make_detail_record(
            result="completed successfully",
            tool_calls=[{"name": "read_state", "args": {}}],
            cost={"usd": 0.05},
            error=None,
            parent_session_id=parent_id,
        )
        app = _app_with_mock_db(fetchrow_result=detail)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{_SESSION_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["result"] == "completed successfully"
        assert data["tool_calls"] == [{"name": "read_state", "args": {}}]
        assert data["cost"] == {"usd": 0.05}
        assert data["trace_id"] == "trace-123"
        assert data["error"] is None
        assert data["parent_session_id"] == str(parent_id)
