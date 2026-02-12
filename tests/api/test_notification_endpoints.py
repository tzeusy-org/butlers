"""Comprehensive integration-style tests for notification endpoints.

Covers edge cases for the list endpoint (combined filters, limit capping,
empty DB, ordering), the stats endpoint (stub response shape), pagination
behaviour (has_more semantics), and validation error cases.

Issue: butlers-26h.9.5
"""

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
) -> dict:
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

    Returns (app, mock_pool, mock_db) so tests can inspect call args.
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
# 1. List endpoint — combined filters
# ---------------------------------------------------------------------------


class TestListNotificationsCombinedFiltersAdvanced:
    """Test applying all filters simultaneously."""

    async def test_all_filters_combined(self):
        """butler + channel + status + since + until all applied at once."""
        rows = [
            _make_notification_row(
                source_butler="atlas",
                channel="email",
                status="sent",
            )
        ]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={
                    "butler": "atlas",
                    "channel": "email",
                    "status": "sent",
                    "since": "2026-01-01T00:00:00Z",
                    "until": "2026-12-31T23:59:59Z",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 1

        # Verify all five WHERE conditions appear in the count query
        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "channel = $2" in count_sql
        assert "status = $3" in count_sql
        assert "created_at >= $4" in count_sql
        assert "created_at <= $5" in count_sql

        # Verify all five args were passed
        count_args = mock_pool.fetchval.call_args[0][1:]
        assert count_args[0] == "atlas"
        assert count_args[1] == "email"
        assert count_args[2] == "sent"

    async def test_butler_channel_and_status_combined(self):
        """Three filters simultaneously: butler + channel + status."""
        rows = [
            _make_notification_row(
                source_butler="health",
                channel="telegram",
                status="failed",
            )
        ]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={
                    "butler": "health",
                    "channel": "telegram",
                    "status": "failed",
                },
            )

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "channel = $2" in count_sql
        assert "status = $3" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert count_args == ("health", "telegram", "failed")

    async def test_channel_and_date_range_combined(self):
        """Two filters: channel + date range."""
        rows = [_make_notification_row(channel="email")]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={
                    "channel": "email",
                    "since": "2026-06-01T00:00:00Z",
                    "until": "2026-06-30T23:59:59Z",
                },
            )

        assert resp.status_code == 200

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "channel = $1" in count_sql
        assert "created_at >= $2" in count_sql
        assert "created_at <= $3" in count_sql


# ---------------------------------------------------------------------------
# 2. Limit capping
# ---------------------------------------------------------------------------


class TestListNotificationsLimitCapping:
    """Test that limit > 200 is rejected (FastAPI Query constraint le=200)."""

    async def test_limit_exceeding_200_returns_422(self):
        """Limit is capped at 200 via Query(le=200); values above should 422."""
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 201})

        assert resp.status_code == 422

    async def test_limit_at_200_is_accepted(self):
        """Limit exactly at 200 should be accepted."""
        rows = [_make_notification_row()]
        app, _, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 200})

        assert resp.status_code == 200
        assert resp.json()["meta"]["limit"] == 200

    async def test_limit_at_1_is_accepted(self):
        """Limit exactly at 1 (minimum) should be accepted."""
        rows = [_make_notification_row()]
        app, _, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 1})

        assert resp.status_code == 200
        assert resp.json()["meta"]["limit"] == 1


# ---------------------------------------------------------------------------
# 3. Empty database
# ---------------------------------------------------------------------------


class TestListNotificationsEmptyDatabase:
    """Test correct structure when no notifications exist."""

    async def test_empty_db_returns_correct_structure(self):
        app, _, _ = _build_app_with_mock_db([], total=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
        assert body["meta"]["offset"] == 0
        assert body["meta"]["limit"] == 50

    async def test_empty_db_with_filters_returns_correct_structure(self):
        """Filters on an empty DB still return a valid envelope."""
        app, _, _ = _build_app_with_mock_db([], total=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/notifications",
                params={"butler": "nonexistent", "status": "sent"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# 4. Result ordering verification
# ---------------------------------------------------------------------------


class TestListNotificationsOrdering:
    """Verify that the SQL query orders by created_at DESC (newest first)."""

    async def test_query_orders_by_created_at_desc(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications")

        data_sql = mock_pool.fetch.call_args[0][0]
        assert "ORDER BY created_at DESC" in data_sql

    async def test_ordering_preserved_with_filters(self):
        """ORDER BY should appear even when filters are applied."""
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get(
                "/api/notifications",
                params={"butler": "atlas", "status": "sent"},
            )

        data_sql = mock_pool.fetch.call_args[0][0]
        assert "ORDER BY created_at DESC" in data_sql


# ---------------------------------------------------------------------------
# 5. Stats endpoint
# ---------------------------------------------------------------------------


class TestNotificationStatsEndpoint:
    """Test GET /api/notifications/stats — DB-backed implementation."""

    def _build_stats_app(self):
        """Create app with mocked DB returning zeros."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        return app

    async def test_stats_returns_200(self):
        app = self._build_stats_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        assert resp.status_code == 200

    async def test_stats_returns_zero_counts(self):
        app = self._build_stats_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        body = resp.json()
        data = body["data"]
        assert data["total"] == 0
        assert data["sent"] == 0
        assert data["failed"] == 0
        assert data["by_channel"] == {}
        assert data["by_butler"] == {}

    async def test_stats_response_envelope_shape(self):
        """Verify the ApiResponse[NotificationStats] envelope shape."""
        app = self._build_stats_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        body = resp.json()
        assert "data" in body
        assert "meta" in body

        data = body["data"]
        assert set(data.keys()) == {"total", "sent", "failed", "by_channel", "by_butler"}

    async def test_stats_queries_switchboard_pool(self):
        """Stats endpoint should query the switchboard database."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications/stats")

        mock_db.pool.assert_called_with("switchboard")


# ---------------------------------------------------------------------------
# 6. Pagination behaviour — has_more semantics
# ---------------------------------------------------------------------------


class TestPaginationHasMore:
    """Test pagination has_more logic via the PaginationMeta model.

    Note: has_more is a @property on PaginationMeta, which may or may not
    appear in the serialized JSON depending on Pydantic config. We verify
    the logic via the meta values (total, offset, limit).
    """

    async def test_has_more_true_when_more_items_exist(self):
        """total=100, offset=0, limit=10 => more items exist."""
        rows = [_make_notification_row() for _ in range(10)]
        app, _, _ = _build_app_with_mock_db(rows, total=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 10})

        body = resp.json()
        meta = body["meta"]
        assert meta["total"] == 100
        assert meta["offset"] == 0
        assert meta["limit"] == 10
        # offset + limit (10) < total (100) => has_more is true
        assert meta["offset"] + meta["limit"] < meta["total"]

    async def test_has_more_false_at_end(self):
        """total=5, offset=0, limit=50 => no more items."""
        rows = [_make_notification_row() for _ in range(5)]
        app, _, _ = _build_app_with_mock_db(rows, total=5)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 50})

        body = resp.json()
        meta = body["meta"]
        assert meta["total"] == 5
        # offset + limit (50) >= total (5) => has_more is false
        assert not (meta["offset"] + meta["limit"] < meta["total"])

    async def test_has_more_false_exact_boundary(self):
        """total=10, offset=0, limit=10 => exactly at boundary, no more."""
        rows = [_make_notification_row() for _ in range(10)]
        app, _, _ = _build_app_with_mock_db(rows, total=10)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 10})

        body = resp.json()
        meta = body["meta"]
        assert meta["total"] == 10
        # offset + limit (10) == total (10) => has_more is false
        assert not (meta["offset"] + meta["limit"] < meta["total"])

    async def test_offset_beyond_total_returns_empty_data(self):
        """When offset >= total, data should be empty."""
        app, _, _ = _build_app_with_mock_db([], total=5)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 100, "limit": 10})

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 5
        assert body["meta"]["offset"] == 100

    async def test_second_page_pagination(self):
        """Fetching page 2 with correct offset and limit."""
        rows = [_make_notification_row() for _ in range(5)]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=15)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 5, "limit": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["offset"] == 5
        assert body["meta"]["limit"] == 5
        assert body["meta"]["total"] == 15

        # Verify correct offset/limit passed to the DB query
        data_call_args = mock_pool.fetch.call_args[0]
        assert data_call_args[-2] == 5  # offset
        assert data_call_args[-1] == 5  # limit


# ---------------------------------------------------------------------------
# 7. Validation error cases
# ---------------------------------------------------------------------------


class TestNotificationEndpointValidationErrors:
    """Test that invalid query parameters return 422 Unprocessable Entity."""

    async def test_negative_offset_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": -1})

        assert resp.status_code == 422

    async def test_negative_limit_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": -5})

        assert resp.status_code == 422

    async def test_zero_limit_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 0})

        assert resp.status_code == 422

    async def test_limit_over_max_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 999})

        assert resp.status_code == 422

    async def test_invalid_since_date_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"since": "not-a-date"})

        assert resp.status_code == 422

    async def test_invalid_until_date_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"until": "not-a-date"})

        assert resp.status_code == 422

    async def test_non_integer_offset_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": "abc"})

        assert resp.status_code == 422

    async def test_non_integer_limit_returns_422(self):
        app, _, _ = _build_app_with_mock_db([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": "xyz"})

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 8. Data query correctness
# ---------------------------------------------------------------------------


class TestListNotificationsQueryConstruction:
    """Verify SQL query construction details."""

    async def test_no_filters_produces_no_where_clause(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications")

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "WHERE" not in count_sql

        data_sql = mock_pool.fetch.call_args[0][0]
        # The data query should have SELECT ... FROM notifications ORDER BY ...
        # but no WHERE clause
        assert "WHERE" not in data_sql

    async def test_data_query_selects_expected_columns(self):
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications")

        data_sql = mock_pool.fetch.call_args[0][0]
        for col in [
            "id",
            "source_butler",
            "channel",
            "recipient",
            "message",
            "metadata",
            "status",
            "error",
            "session_id",
            "trace_id",
            "created_at",
        ]:
            assert col in data_sql

    async def test_offset_limit_are_last_args_in_data_query(self):
        """offset and limit should always be the last two positional args."""
        rows = [_make_notification_row()]
        app, mock_pool, _ = _build_app_with_mock_db(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get(
                "/api/notifications",
                params={"butler": "atlas", "offset": 20, "limit": 10},
            )

        data_call_args = mock_pool.fetch.call_args[0]
        # Last two args are offset=20 and limit=10
        assert data_call_args[-2] == 20
        assert data_call_args[-1] == 10
