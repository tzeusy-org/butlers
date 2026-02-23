"""Tests for GET /api/butlers/{name}/notifications — butler-scoped notifications.

This endpoint is a narrowed view of /api/notifications with the butler path
parameter acting as the mandatory source_butler filter. Tests here focus on
path-parameter injection and butler-scoped boundary behaviour; general
pagination, validation, and SQL construction are covered by
test_notifications_router.py and test_notification_endpoints.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from tests.api.conftest import build_notifications_app, make_notification_row

pytestmark = pytest.mark.unit


class TestButlerNotificationsDefaults:
    """Test the default pagination behaviour for butler-scoped endpoint."""

    async def test_returns_200_with_paginated_response(self):
        rows = [make_notification_row(source_butler="atlas") for _ in range(3)]
        app, mock_pool, _ = build_notifications_app(rows, total=3)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 3
        assert body["meta"]["total"] == 3
        assert body["meta"]["offset"] == 0
        assert body["meta"]["limit"] == 50

    async def test_empty_results(self):
        app, _, _ = build_notifications_app([], total=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0


class TestButlerNotificationsButlerFilter:
    """Verify the butler name from the path is used as the source_butler filter."""

    async def test_butler_name_injected_as_filter(self):
        rows = [make_notification_row(source_butler="atlas")]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/notifications")

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "atlas" in count_args

        data_sql = mock_pool.fetch.call_args[0][0]
        assert "source_butler = $1" in data_sql

    async def test_different_butler_name(self):
        """The butler name from the URL path should be used, not a hardcoded value."""
        rows = [make_notification_row(source_butler="herald")]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/herald/notifications")

        assert resp.status_code == 200
        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "herald" in count_args


class TestButlerNotificationsPagination:
    """Test custom offset/limit parameters on the butler-scoped endpoint."""

    async def test_custom_offset_and_limit(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"offset": 10, "limit": 5},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["offset"] == 10
        assert body["meta"]["limit"] == 5
        assert body["meta"]["total"] == 100

        data_call_args = mock_pool.fetch.call_args
        assert data_call_args[0][-2] == 10
        assert data_call_args[0][-1] == 5

    async def test_invalid_offset_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"offset": -1},
            )

        assert resp.status_code == 422

    async def test_invalid_limit_zero_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"limit": 0},
            )

        assert resp.status_code == 422


class TestButlerNotificationsFilters:
    """Test additional query filters on butler-scoped endpoint."""

    async def test_channel_filter(self):
        rows = [make_notification_row(channel="email")]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"channel": "email"},
            )

        assert resp.status_code == 200
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "channel = $2" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "atlas" in count_args
        assert "email" in count_args

    async def test_status_filter(self):
        rows = [make_notification_row(status="failed")]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"status": "failed"},
            )

        assert resp.status_code == 200
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "status = $2" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "atlas" in count_args
        assert "failed" in count_args

    async def test_since_filter(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"since": "2026-01-01T00:00:00Z"},
            )

        assert resp.status_code == 200
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "created_at >= $2" in count_sql

    async def test_until_filter(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"until": "2026-12-31T23:59:59Z"},
            )

        assert resp.status_code == 200
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "created_at <= $2" in count_sql

    async def test_combined_filters(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={
                    "channel": "telegram",
                    "status": "sent",
                    "since": "2026-01-01T00:00:00Z",
                    "until": "2026-12-31T23:59:59Z",
                },
            )

        assert resp.status_code == 200
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "channel = $2" in count_sql
        assert "status = $3" in count_sql
        assert "created_at >= $4" in count_sql
        assert "created_at <= $5" in count_sql


class TestButlerNotificationsResponseShape:
    """Test the shape of the response data matches cross-butler endpoint."""

    async def test_notification_fields_present(self):
        now = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
        nid = uuid4()
        sid = uuid4()
        rows = [
            {
                "id": nid,
                "source_butler": "atlas",
                "channel": "telegram",
                "recipient": "12345",
                "message": "Test notification",
                "metadata": {"key": "value"},
                "status": "sent",
                "error": None,
                "session_id": sid,
                "trace_id": "abc123",
                "created_at": now,
            }
        ]
        app, _, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/notifications")

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["id"] == str(nid)
        assert item["source_butler"] == "atlas"
        assert item["channel"] == "telegram"
        assert item["recipient"] == "12345"
        assert item["message"] == "Test notification"
        assert item["metadata"] == {"key": "value"}
        assert item["status"] == "sent"
        assert item["error"] is None
        assert item["session_id"] == str(sid)
        assert item["trace_id"] == "abc123"

    async def test_switchboard_pool_is_used(self):
        """Verify the endpoint queries the switchboard database specifically."""
        app, _, mock_db = build_notifications_app([], total=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/butlers/atlas/notifications")

        mock_db.pool.assert_called_with("switchboard")


class TestButlerNotificationsNoButlerQueryParam:
    """Ensure the butler-scoped endpoint does NOT expose a butler query param."""

    async def test_butler_query_param_ignored(self):
        """Even if someone passes ?butler=other, the path {name} takes precedence."""
        rows = [make_notification_row(source_butler="atlas")]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/butlers/atlas/notifications",
                params={"butler": "other"},
            )

        assert resp.status_code == 200

        # The path name ("atlas") should be used, not the query param "other"
        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "atlas" in count_args
        assert "other" not in count_args
