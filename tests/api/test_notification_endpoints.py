"""Comprehensive integration-style tests for notification endpoints.

Covers edge cases NOT tested in test_notifications_router.py:
  - Advanced multi-filter combos (3–5 simultaneous filters)
  - Limit boundary capping (le=200 constraint)
  - Empty DB with active filters
  - Missing switchboard pool graceful degradation
  - SQL ordering and query construction correctness
  - Pagination boundary semantics (has_more, offset beyond total, page 2)
  - Full validation error matrix

Issue: butlers-26h.9.5
"""

from __future__ import annotations

import httpx
import pytest

from tests.api.conftest import (
    build_app_missing_switchboard,
    build_notifications_app,
    make_notification_row,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. Combined filters — advanced (3-5 simultaneous)
# ---------------------------------------------------------------------------


class TestListNotificationsCombinedFiltersAdvanced:
    """Test applying all filters simultaneously."""

    async def test_all_filters_combined(self):
        """butler + channel + status + since + until all applied at once."""
        rows = [
            make_notification_row(
                source_butler="atlas",
                channel="email",
                status="sent",
            )
        ]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

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
        assert resp.json()["meta"]["total"] == 1

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "source_butler = $1" in count_sql
        assert "channel = $2" in count_sql
        assert "status = $3" in count_sql
        assert "created_at >= $4" in count_sql
        assert "created_at <= $5" in count_sql

        count_args = mock_pool.fetchval.call_args[0][1:]
        assert count_args[0] == "atlas"
        assert count_args[1] == "email"
        assert count_args[2] == "sent"

    async def test_butler_channel_and_status_combined(self):
        """Three filters simultaneously: butler + channel + status."""
        rows = [
            make_notification_row(
                source_butler="health",
                channel="telegram",
                status="failed",
            )
        ]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

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
        rows = [make_notification_row(channel="email")]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

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
# 2. Limit capping (le=200 constraint)
# ---------------------------------------------------------------------------


class TestListNotificationsLimitCapping:
    """Test that limit > 200 is rejected (FastAPI Query constraint le=200)."""

    async def test_limit_exceeding_200_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 201})

        assert resp.status_code == 422

    async def test_limit_at_200_is_accepted(self):
        rows = [make_notification_row()]
        app, _, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 200})

        assert resp.status_code == 200
        assert resp.json()["meta"]["limit"] == 200

    async def test_limit_at_1_is_accepted(self):
        rows = [make_notification_row()]
        app, _, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 1})

        assert resp.status_code == 200
        assert resp.json()["meta"]["limit"] == 1


# ---------------------------------------------------------------------------
# 3. Empty database with active filters
# ---------------------------------------------------------------------------


class TestListNotificationsEmptyDatabase:
    """Test correct structure when no notifications exist."""

    async def test_empty_db_with_filters_returns_correct_structure(self):
        """Filters on an empty DB still return a valid envelope."""
        app, _, _ = build_notifications_app([], total=0)

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
# 4. Missing switchboard pool — graceful degradation
# ---------------------------------------------------------------------------


class TestNotificationsWithoutSwitchboardPool:
    """Notifications endpoints should degrade gracefully when switchboard DB is absent."""

    async def test_list_returns_empty_payload(self):
        app = build_app_missing_switchboard()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 5, "status": "failed"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
        assert body["meta"]["offset"] == 0
        assert body["meta"]["limit"] == 5

    async def test_stats_returns_zero_payload(self):
        app = build_app_missing_switchboard()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["sent"] == 0
        assert data["failed"] == 0
        assert data["by_channel"] == {}
        assert data["by_butler"] == {}


# ---------------------------------------------------------------------------
# 5. SQL ordering
# ---------------------------------------------------------------------------


class TestListNotificationsOrdering:
    """Verify that the SQL query orders by created_at DESC (newest first)."""

    async def test_query_orders_by_created_at_desc(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications")

        data_sql = mock_pool.fetch.call_args[0][0]
        assert "ORDER BY created_at DESC" in data_sql

    async def test_ordering_preserved_with_filters(self):
        """ORDER BY should appear even when filters are applied."""
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

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
# 6. SQL query construction correctness
# ---------------------------------------------------------------------------


class TestListNotificationsQueryConstruction:
    """Verify SQL query construction details."""

    async def test_no_filters_produces_no_where_clause(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications")

        count_sql = mock_pool.fetchval.call_args[0][0]
        assert "WHERE" not in count_sql

        data_sql = mock_pool.fetch.call_args[0][0]
        assert "WHERE" not in data_sql

    async def test_data_query_selects_expected_columns(self):
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

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
        rows = [make_notification_row()]
        app, mock_pool, _ = build_notifications_app(rows, total=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get(
                "/api/notifications",
                params={"butler": "atlas", "offset": 20, "limit": 10},
            )

        data_call_args = mock_pool.fetch.call_args[0]
        assert data_call_args[-2] == 20
        assert data_call_args[-1] == 10


# ---------------------------------------------------------------------------
# 7. Pagination boundary semantics
# ---------------------------------------------------------------------------


class TestPaginationHasMore:
    """Test pagination has_more logic via the PaginationMeta model."""

    async def test_has_more_false_exact_boundary(self):
        """total=10, offset=0, limit=10 => exactly at boundary, no more."""
        rows = [make_notification_row() for _ in range(10)]
        app, _, _ = build_notifications_app(rows, total=10)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 0, "limit": 10})

        meta = resp.json()["meta"]
        assert meta["total"] == 10
        assert not (meta["offset"] + meta["limit"] < meta["total"])

    async def test_offset_beyond_total_returns_empty_data(self):
        """When offset >= total, data should be empty."""
        app, _, _ = build_notifications_app([], total=5)

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
        rows = [make_notification_row() for _ in range(5)]
        app, mock_pool, _ = build_notifications_app(rows, total=15)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": 5, "limit": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["offset"] == 5
        assert body["meta"]["limit"] == 5
        assert body["meta"]["total"] == 15

        data_call_args = mock_pool.fetch.call_args[0]
        assert data_call_args[-2] == 5  # offset
        assert data_call_args[-1] == 5  # limit


# ---------------------------------------------------------------------------
# 8. Validation error matrix
# ---------------------------------------------------------------------------


class TestNotificationEndpointValidationErrors:
    """Test that invalid query parameters return 422 Unprocessable Entity."""

    async def test_negative_offset_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": -1})

        assert resp.status_code == 422

    async def test_negative_limit_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": -5})

        assert resp.status_code == 422

    async def test_zero_limit_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 0})

        assert resp.status_code == 422

    async def test_limit_over_max_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": 999})

        assert resp.status_code == 422

    async def test_invalid_since_date_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"since": "not-a-date"})

        assert resp.status_code == 422

    async def test_invalid_until_date_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"until": "not-a-date"})

        assert resp.status_code == 422

    async def test_non_integer_offset_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"offset": "abc"})

        assert resp.status_code == 422

    async def test_non_integer_limit_returns_422(self):
        app, _, _ = build_notifications_app([])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications", params={"limit": "xyz"})

        assert resp.status_code == 422
