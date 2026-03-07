"""Tests for the ingestion events API endpoints.

Verifies the API contract (status codes, response shapes, pagination) for:
- GET /api/ingestion/events
- GET /api/ingestion/events/{requestId}
- GET /api/ingestion/events/{requestId}/sessions
- GET /api/ingestion/events/{requestId}/rollup

Uses mocked DatabaseManager so no real database is required.

Issue: bu-0b7.7
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_events import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_REQUEST_ID = str(uuid4())


def _make_event_row(
    *,
    event_id: str | None = None,
    received_at: datetime | None = None,
    source_channel: str = "telegram",
    source_provider: str | None = "telegram",
    triage_decision: str = "accepted",
    triage_target: str | None = "atlas",
) -> dict:
    """Create a dict mimicking an asyncpg Record for ingestion_event columns."""
    return {
        "id": event_id or str(uuid4()),
        "received_at": received_at or _NOW,
        "source_channel": source_channel,
        "source_provider": source_provider,
        "source_endpoint_identity": None,
        "source_sender_identity": None,
        "source_thread_identity": None,
        "external_event_id": None,
        "dedupe_key": None,
        "dedupe_strategy": None,
        "ingestion_tier": None,
        "policy_tier": None,
        "triage_decision": triage_decision,
        "triage_target": triage_target,
    }


def _make_session_row(
    *,
    session_id: str | None = None,
    butler_name: str = "atlas",
    trigger_source: str = "ingestion",
    started_at: datetime | None = None,
    input_tokens: int = 100,
    output_tokens: int = 200,
    cost: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    """Create a dict mimicking a session fan-out row with butler_name."""
    return {
        "id": session_id or str(uuid4()),
        "butler_name": butler_name,
        "trigger_source": trigger_source,
        "started_at": started_at or _NOW,
        "completed_at": _NOW,
        "success": True,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost or {"total_usd": 0.005},
        "trace_id": trace_id,
    }


def _app_with_mock_db(
    app: FastAPI,
    *,
    shared_pool: AsyncMock | None = None,
    fan_out_results: dict | None = None,
    shared_pool_error: Exception | None = None,
) -> FastAPI:
    """Wire a FastAPI app with a mocked DatabaseManager.

    Parameters
    ----------
    app:
        The shared module-scoped app fixture.
    shared_pool:
        Mock pool to return from credential_shared_pool(). If None, a
        default mock returning empty results is used.
    fan_out_results:
        Return value for mock_db.fan_out(); defaults to empty dict.
    shared_pool_error:
        If set, credential_shared_pool() raises this exception.
    """
    mock_db = MagicMock(spec=DatabaseManager)

    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = AsyncMock()
            shared_pool.fetchval = AsyncMock(return_value=0)
            shared_pool.fetch = AsyncMock(return_value=[])
        mock_db.credential_shared_pool.return_value = shared_pool

    if fan_out_results is not None:
        mock_db.fan_out = AsyncMock(return_value=fan_out_results)
    else:
        mock_db.fan_out = AsyncMock(return_value={})

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# GET /api/ingestion/events — list
# ---------------------------------------------------------------------------


class TestListIngestionEvents:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with pagination fields."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_returns_empty_list_when_no_events(self, app):
        """Empty shared pool should return empty data and total=0."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_event_summaries(self, app):
        """Events from shared pool should appear in data list."""
        row = _make_event_row(event_id=_REQUEST_ID, source_channel="telegram")

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(return_value=[row])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 1
        assert len(body["data"]) == 1
        event = body["data"][0]
        assert event["id"] == _REQUEST_ID
        assert event["source_channel"] == "telegram"

    async def test_pagination_params_forwarded(self, app):
        """Limit and offset query params should be accepted without error."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"limit": 5, "offset": 10})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["limit"] == 5
        assert body["meta"]["offset"] == 10

    async def test_source_channel_filter_accepted(self, app):
        """source_channel query param should be accepted without error."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"source_channel": "telegram"})

        assert resp.status_code == 200

    async def test_503_when_shared_pool_unavailable(self, app):
        """Returns 503 when credential_shared_pool() raises KeyError."""
        _app_with_mock_db(
            app,
            shared_pool_error=KeyError("Shared credential pool is not configured"),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

        assert resp.status_code == 503

    async def test_event_summary_fields(self, app):
        """Each event summary should include expected fields."""
        row = _make_event_row(
            event_id=_REQUEST_ID,
            source_channel="email",
            triage_decision="accepted",
            triage_target="atlas",
        )

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(return_value=[row])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

        assert resp.status_code == 200
        event = resp.json()["data"][0]
        assert event["id"] == _REQUEST_ID
        assert event["source_channel"] == "email"
        assert event["triage_decision"] == "accepted"
        assert event["triage_target"] == "atlas"
        assert "received_at" in event


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}
# ---------------------------------------------------------------------------


class TestGetIngestionEvent:
    async def test_returns_event_detail(self, app):
        """Existing event should return 200 with data field."""
        row = _make_event_row(event_id=_REQUEST_ID)

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=row)

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["id"] == _REQUEST_ID

    async def test_404_when_event_not_found(self, app):
        """Non-existent event should return 404."""
        missing_id = str(uuid4())

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{missing_id}")

        assert resp.status_code == 404

    async def test_422_for_invalid_uuid(self, app):
        """Invalid UUID should return 422."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=ValueError("invalid UUID"))

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events/not-a-uuid")

        assert resp.status_code == 422

    async def test_503_when_shared_pool_unavailable(self, app):
        """Returns 503 when credential_shared_pool() raises KeyError."""
        _app_with_mock_db(
            app,
            shared_pool_error=KeyError("Shared credential pool is not configured"),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}")

        assert resp.status_code == 503

    async def test_detail_fields(self, app):
        """Event detail should include all expected fields."""
        row = _make_event_row(
            event_id=_REQUEST_ID,
            source_channel="telegram",
            triage_decision="rejected",
            triage_target=None,
        )

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=row)

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == _REQUEST_ID
        assert data["source_channel"] == "telegram"
        assert data["triage_decision"] == "rejected"
        assert data["triage_target"] is None


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}/sessions
# ---------------------------------------------------------------------------


class TestGetIngestionEventSessions:
    async def test_returns_sessions_list(self, app):
        """Sessions endpoint should return list wrapped in ApiResponse."""
        session = _make_session_row(butler_name="atlas")

        _app_with_mock_db(app, fan_out_results={"atlas": [session]})

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        assert body["data"][0]["butler_name"] == "atlas"

    async def test_returns_empty_list_when_no_sessions(self, app):
        """No matching sessions should return empty list."""
        _app_with_mock_db(app, fan_out_results={})

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/sessions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_cross_butler_sessions_merged(self, app):
        """Sessions from multiple butlers should all appear in response."""
        session_atlas = _make_session_row(butler_name="atlas")
        session_sb = _make_session_row(butler_name="switchboard")

        _app_with_mock_db(
            app,
            fan_out_results={"atlas": [session_atlas], "switchboard": [session_sb]},
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/sessions")

        assert resp.status_code == 200
        sessions = resp.json()["data"]
        assert len(sessions) == 2
        butler_names = {s["butler_name"] for s in sessions}
        assert butler_names == {"atlas", "switchboard"}

    async def test_session_fields(self, app):
        """Each session should have the expected fields."""
        session = _make_session_row(
            butler_name="atlas",
            trigger_source="ingestion",
            input_tokens=150,
            output_tokens=300,
        )

        _app_with_mock_db(app, fan_out_results={"atlas": [session]})

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/sessions")

        assert resp.status_code == 200
        s = resp.json()["data"][0]
        assert s["butler_name"] == "atlas"
        assert s["trigger_source"] == "ingestion"
        assert s["input_tokens"] == 150
        assert s["output_tokens"] == 300
        assert s["success"] is True


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{requestId}/rollup
# ---------------------------------------------------------------------------


class TestGetIngestionEventRollup:
    async def test_returns_rollup_structure(self, app):
        """Rollup should return aggregated totals wrapped in ApiResponse."""
        session = _make_session_row(
            butler_name="atlas",
            input_tokens=100,
            output_tokens=200,
            cost={"total_usd": 0.01},
        )

        _app_with_mock_db(app, fan_out_results={"atlas": [session]})

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/rollup")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert data["request_id"] == _REQUEST_ID
        assert "total_sessions" in data
        assert "total_input_tokens" in data
        assert "total_output_tokens" in data
        assert "total_cost" in data
        assert "by_butler" in data

    async def test_rollup_aggregates_tokens_and_cost(self, app):
        """Rollup should sum tokens and costs across all sessions."""
        s1 = _make_session_row(
            butler_name="atlas", input_tokens=100, output_tokens=200, cost={"total_usd": 0.01}
        )
        s2 = _make_session_row(
            butler_name="atlas", input_tokens=50, output_tokens=100, cost={"total_usd": 0.005}
        )

        _app_with_mock_db(app, fan_out_results={"atlas": [s1, s2]})

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/rollup")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_sessions"] == 2
        assert data["total_input_tokens"] == 150
        assert data["total_output_tokens"] == 300
        assert abs(data["total_cost"] - 0.015) < 1e-9

    async def test_rollup_by_butler_breakdown(self, app):
        """by_butler should break down per-butler stats."""
        s_atlas = _make_session_row(
            butler_name="atlas", input_tokens=100, output_tokens=200, cost={"total_usd": 0.01}
        )
        s_sb = _make_session_row(
            butler_name="switchboard", input_tokens=50, output_tokens=100, cost={"total_usd": 0.005}
        )

        _app_with_mock_db(
            app,
            fan_out_results={"atlas": [s_atlas], "switchboard": [s_sb]},
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/rollup")

        assert resp.status_code == 200
        by_butler = resp.json()["data"]["by_butler"]
        assert "atlas" in by_butler
        assert "switchboard" in by_butler
        assert by_butler["atlas"]["sessions"] == 1
        assert by_butler["atlas"]["input_tokens"] == 100
        assert by_butler["switchboard"]["sessions"] == 1
        assert by_butler["switchboard"]["input_tokens"] == 50

    async def test_rollup_empty_when_no_sessions(self, app):
        """Rollup with no sessions should return zeros."""
        _app_with_mock_db(app, fan_out_results={})

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{_REQUEST_ID}/rollup")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_sessions"] == 0
        assert data["total_input_tokens"] == 0
        assert data["total_output_tokens"] == 0
        assert data["total_cost"] == 0.0
        assert data["by_butler"] == {}


# ---------------------------------------------------------------------------
# Route absence tests: /api/traces must no longer exist
# ---------------------------------------------------------------------------


class TestTracesRouteRemoved:
    async def test_traces_list_returns_404(self, app):
        """/api/traces should not exist; expect 404 or 405."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces")

        assert resp.status_code in (404, 405)

    async def test_traces_detail_returns_404(self, app):
        """/api/traces/{id} should not exist; expect 404 or 405."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces/some-trace-id")

        assert resp.status_code in (404, 405)
