"""Tests for the ingestion events API endpoints.

Verifies the API contract (status codes, response shapes, pagination) for:
- GET /api/ingestion/events
- GET /api/ingestion/events/{requestId}
- GET /api/ingestion/events/{requestId}/sessions
- GET /api/ingestion/events/{requestId}/rollup
- POST /api/ingestion/events/{id}/replay

Uses mocked DatabaseManager so no real database is required.

Issue: bu-0b7.7, bu-6kvk.5
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.pricing import PricingConfig
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
    status: str = "ingested",
    filter_reason: str | None = None,
    error_detail: str | None = None,
) -> dict:
    """Create a dict mimicking an asyncpg Record for the unified ingestion timeline.

    Includes ``status``, ``filter_reason``, and ``error_detail`` fields that are
    added by the unified UNION query.
    """
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
        "status": status,
        "filter_reason": filter_reason,
        "error_detail": error_detail,
    }


def _make_filtered_event_row(
    *,
    event_id: str | None = None,
    received_at: datetime | None = None,
    source_channel: str = "email",
    status: str = "filtered",
    filter_reason: str = "label_exclude:CATEGORY_PROMOTIONS",
    error_detail: str | None = None,
) -> dict:
    """Create a dict mimicking a connectors.filtered_events row in the unified timeline.

    The unified UNION query maps filtered_events columns onto the shared shape,
    so non-present columns are NULL.
    """
    return {
        "id": event_id or str(uuid4()),
        "received_at": received_at or _NOW,
        "source_channel": source_channel,
        "source_provider": None,
        "source_endpoint_identity": None,  # endpoint_identity in filtered_events
        "source_sender_identity": None,  # sender_identity in filtered_events
        "source_thread_identity": None,
        "external_event_id": None,  # external_message_id in filtered_events
        "dedupe_key": None,
        "dedupe_strategy": None,
        "ingestion_tier": None,
        "policy_tier": None,
        "triage_decision": None,
        "triage_target": None,
        "status": status,
        "filter_reason": filter_reason,
        "error_detail": error_detail,
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
    model: str | None = None,
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
        "model": model,
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
    app.dependency_overrides[get_pricing] = lambda: PricingConfig(models={})
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


# ---------------------------------------------------------------------------
# Unified timeline: status and filter_reason fields in list response
# ---------------------------------------------------------------------------


class TestUnifiedTimeline:
    async def test_ingested_event_has_status_and_filter_reason(self, app):
        """Ingested events must include status='ingested' and filter_reason=null."""
        row = _make_event_row(event_id=_REQUEST_ID, status="ingested", filter_reason=None)

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
        assert event["status"] == "ingested"
        assert event["filter_reason"] is None

    async def test_filtered_event_has_status_and_filter_reason(self, app):
        """Filtered events from connectors.filtered_events must include status and filter_reason."""
        row = _make_filtered_event_row(
            event_id=str(uuid4()),
            status="filtered",
            filter_reason="label_exclude:CATEGORY_PROMOTIONS",
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
        assert event["status"] == "filtered"
        assert event["filter_reason"] == "label_exclude:CATEGORY_PROMOTIONS"

    async def test_mixed_events_returned(self, app):
        """Unified list can contain both ingested and filtered events."""
        row_ingested = _make_event_row(status="ingested")
        row_filtered = _make_filtered_event_row(status="filtered")

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=2)
        mock_pool.fetch = AsyncMock(return_value=[row_ingested, row_filtered])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 2
        statuses = {e["status"] for e in body["data"]}
        assert "ingested" in statuses
        assert "filtered" in statuses

    async def test_error_event_exposes_error_detail(self, app):
        """Error events must expose error_detail when present."""
        row = _make_filtered_event_row(
            status="error",
            filter_reason="exception_raised",
            error_detail="ConnectionError: timed out after 30s",
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
        assert event["status"] == "error"
        assert event["filter_reason"] == "exception_raised"
        assert event["error_detail"] == "ConnectionError: timed out after 30s"

    async def test_ingested_event_has_null_error_detail(self, app):
        """Ingested events must always have error_detail=null."""
        row = _make_event_row(status="ingested", error_detail=None)

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
        assert event["error_detail"] is None


# ---------------------------------------------------------------------------
# Status filter parameter
# ---------------------------------------------------------------------------


class TestStatusFilter:
    async def test_status_ingested_filter_accepted(self, app):
        """status=ingested query param should be accepted without error."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "ingested"})

        assert resp.status_code == 200

    async def test_status_filtered_filter_accepted(self, app):
        """status=filtered query param should be accepted without error."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "filtered"})

        assert resp.status_code == 200

    async def test_status_error_filter_accepted(self, app):
        """status=error query param should be accepted without error."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "error"})

        assert resp.status_code == 200

    async def test_status_replay_pending_filter_accepted(self, app):
        """status=replay_pending query param should be accepted without error."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "replay_pending"})

        assert resp.status_code == 200

    async def test_status_filter_returns_correct_events(self, app):
        """status filter should return events matching that status."""
        row = _make_filtered_event_row(status="error", filter_reason="validation_error")

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(return_value=[row])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "error"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 1
        assert body["data"][0]["status"] == "error"
        assert body["data"][0]["filter_reason"] == "validation_error"

    async def test_status_and_channel_combined(self, app):
        """status and source_channel filters can be combined."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/ingestion/events",
                params={"status": "filtered", "source_channel": "email"},
            )

        assert resp.status_code == 200

    async def test_status_invalid_value_returns_422(self, app):
        """Invalid status value should return 422 Unprocessable Entity."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "unknown_status"})

        assert resp.status_code == 422

    async def test_status_invalid_value_detail_message(self, app):
        """422 response for invalid status should include validation error detail."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events", params={"status": "invalid"})

        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body


# ---------------------------------------------------------------------------
# POST /api/ingestion/events/{id}/replay
# ---------------------------------------------------------------------------

_FILTERED_ID = str(uuid4())


class TestReplayEndpoint:
    async def test_replay_returns_200_and_replay_pending(self, app):
        """Replay of a filtered event returns 200 and sets status to replay_pending."""
        mock_pool = AsyncMock()
        # Atomic UPDATE RETURNING succeeds — row was in a replayable state.
        mock_pool.fetchrow = AsyncMock(return_value={"id": uuid4()})

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "replay_pending"
        assert "id" in body

    async def test_replay_of_error_event_returns_200(self, app):
        """Replay of an error-status event returns 200."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={"id": uuid4()})

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 200
        assert resp.json()["status"] == "replay_pending"

    async def test_replay_of_replay_failed_returns_200(self, app):
        """Re-replay of replay_failed event is allowed and returns 200."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={"id": uuid4()})

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 200
        assert resp.json()["status"] == "replay_pending"

    async def test_replay_returns_404_for_unknown_id(self, app):
        """Replay of unknown event returns 404."""
        unknown_id = str(uuid4())

        mock_pool = AsyncMock()
        # fetchrow returns None (UPDATE matched nothing) and fetchval also None (no row).
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value=None)

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{unknown_id}/replay")

        assert resp.status_code == 404

    async def test_replay_returns_409_for_replay_pending(self, app):
        """Replay of replay_pending event returns 409 Conflict."""
        mock_pool = AsyncMock()
        # fetchrow returns None (UPDATE missed — status is not replayable).
        # fetchval returns the current non-replayable status for the conflict response.
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value="replay_pending")

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 409
        body = resp.json()
        # FastAPI wraps HTTPException detail in {"detail": ...}
        detail = body.get("detail", body)
        assert detail["current_status"] == "replay_pending"

    async def test_replay_returns_409_for_replay_complete(self, app):
        """Replay of replay_complete event returns 409 Conflict."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value="replay_complete")

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 409
        detail = resp.json().get("detail", resp.json())
        assert detail["current_status"] == "replay_complete"

    async def test_replay_409_includes_error_message(self, app):
        """409 response must include 'error' key with human-readable message."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value="replay_pending")

        _app_with_mock_db(app, shared_pool=mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 409
        detail = resp.json().get("detail", resp.json())
        assert "error" in detail
        assert "not replayable" in detail["error"].lower()

    async def test_replay_503_when_shared_pool_unavailable(self, app):
        """Replay returns 503 when credential_shared_pool() raises KeyError."""
        _app_with_mock_db(
            app,
            shared_pool_error=KeyError("Shared credential pool is not configured"),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{_FILTERED_ID}/replay")

        assert resp.status_code == 503
