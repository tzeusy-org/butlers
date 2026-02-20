"""Tests for GET /api/notifications — paginated notification history."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notification_row(
    *,
    source_butler: str = "atlas",
    channel: str = "telegram",
    recipient: str = "12345",
    message: str = "Hello!",
    metadata: dict | None = None,
    status: str = "sent",
    error: str | None = None,
    session_id=None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
):
    """Build a dict mimicking an asyncpg Record for the notifications table."""
    return {
        "id": uuid4(),
        "source_butler": source_butler,
        "channel": channel,
        "recipient": recipient,
        "message": message,
        "metadata": metadata or {},
        "status": status,
        "error": error,
        "session_id": session_id,
        "trace_id": trace_id,
        "created_at": created_at or datetime.now(tz=UTC),
    }


def _build_app_with_mock_db(
    rows: list[dict],
    total: int | None = None,
) -> tuple:
    """Create a FastAPI app with mocked DatabaseManager.

    Returns (app, mock_pool) so tests can inspect call args.
    """
    if total is None:
        total = len(rows)

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(
        return_value=[
            MagicMock(
                **{
                    "__getitem__": lambda self, key, row=row: row[key],
                }
            )
            for row in rows
        ]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListNotificationsDefaults:
    """Test the default pagination behaviour (offset=0, limit=50)."""

    async def test_returns_200_with_paginated_response(self):
        rows = [_make_notification_row() for _ in range(3)]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=3)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 3
        assert body["meta"]["total"] == 3
        assert body["meta"]["offset"] == 0
        assert body["meta"]["limit"] == 50

    async def test_empty_results(self):
        app, _, _ = _build_app_with_mock_db([], total=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_has_more_is_present_in_response(self):
        """Regression: PaginationMeta.has_more must be serialized in the JSON response.

        Previously the field was a plain @property (not @computed_field), which
        Pydantic v2 silently omits from serialization.  The frontend relies on
        has_more to enable the Next-page button.
        """
        rows = [_make_notification_row() for _ in range(3)]
        app, _, _ = _build_app_with_mock_db(rows, total=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 3})

        assert resp.status_code == 200
        meta = resp.json()["meta"]
        # has_more must be present as a JSON key, not undefined
        assert "has_more" in meta
        # With total=100, offset=0, limit=3, has_more must be True
        assert meta["has_more"] is True

    async def test_has_more_false_when_on_last_page(self):
        """has_more is False when all records fit on the first page."""
        rows = [_make_notification_row() for _ in range(3)]
        app, _, _ = _build_app_with_mock_db(rows, total=3)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 50})

        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert "has_more" in meta
        assert meta["has_more"] is False


class TestListNotificationsPagination:
    """Test custom offset/limit parameters."""

    async def test_custom_offset_and_limit(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 10, "limit": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["offset"] == 10
        assert body["meta"]["limit"] == 5
        assert body["meta"]["total"] == 100

        # Verify OFFSET and LIMIT were passed to the data query
        data_call_args = mock_pool.fetch.call_args
        # The last two positional args should be offset=10, limit=5
        assert data_call_args[0][-2] == 10
        assert data_call_args[0][-1] == 5

    async def test_invalid_offset_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": -1})

        assert resp.status_code == 422

    async def test_invalid_limit_zero_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 0})

        assert resp.status_code == 422


class TestListNotificationsFilterByButler:
    """Test filtering by source butler."""

    async def test_butler_filter_passed_to_query(self):
        rows = [_make_notification_row(source_butler="atlas")]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"butler": "atlas"})

        assert resp.status_code == 200

        # Verify the count query includes butler filter
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql

        # Verify the data query includes butler filter
        data_sql = mock_pool.fetch.call_args[0][0]
        assert "source_butler = $1" in data_sql

        # Verify "atlas" was passed as argument
        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "atlas" in count_args


class TestListNotificationsFilterByChannel:
    """Test filtering by delivery channel."""

    async def test_channel_filter_passed_to_query(self):
        rows = [_make_notification_row(channel="email")]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"channel": "email"})

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "channel = $1" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "email" in count_args


class TestListNotificationsFilterByStatus:
    """Test filtering by notification status."""

    async def test_status_filter_passed_to_query(self):
        rows = [_make_notification_row(status="failed")]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"status": "failed"})

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "status = $1" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert "failed" in count_args


class TestListNotificationsFilterByDateRange:
    """Test filtering by date range (since/until)."""

    async def test_since_filter(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={"since": "2026-01-01T00:00:00Z"},
            )

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "created_at >= $1" in count_sql

    async def test_until_filter(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={"until": "2026-12-31T23:59:59Z"},
            )

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "created_at <= $1" in count_sql

    async def test_combined_since_and_until(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={
                    "since": "2026-01-01T00:00:00Z",
                    "until": "2026-12-31T23:59:59Z",
                },
            )

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "created_at >= $1" in count_sql
        assert "created_at <= $2" in count_sql


class TestListNotificationsCombinedFilters:
    """Test applying multiple filters simultaneously."""

    async def test_butler_and_status_combined(self):
        rows = [_make_notification_row(source_butler="atlas", status="sent")]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={"butler": "atlas", "status": "sent"},
            )

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "status = $2" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert count_args == ("atlas", "sent")


class TestListNotificationsResponseShape:
    """Test the shape of the response data."""

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
        app, _, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications")

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

    async def test_non_mapping_metadata_is_normalized_to_null(self):
        """Legacy non-object metadata values must not fail list serialization."""
        rows = [
            _make_notification_row(message="object", metadata={"key": "value"}),
            {**_make_notification_row(message="null"), "metadata": None},
            _make_notification_row(message="array", metadata=["x", "y"]),
            _make_notification_row(message="string", metadata="legacy"),
            _make_notification_row(message="scalar", metadata=42),
        ]
        app, _, _ = _build_app_with_mock_db(rows, total=5)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 20})

        assert resp.status_code == 200
        body = resp.json()
        metadata_by_message = {item["message"]: item["metadata"] for item in body["data"]}
        assert metadata_by_message["object"] == {"key": "value"}
        assert metadata_by_message["null"] is None
        assert metadata_by_message["array"] is None
        assert metadata_by_message["string"] is None
        assert metadata_by_message["scalar"] is None

    async def test_switchboard_pool_is_used(self):
        """Verify the endpoint queries the switchboard database specifically."""
        app, _, mock_db = _build_app_with_mock_db([], total=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications")

        mock_db.pool.assert_called_with("switchboard")
