"""Tests for GET /api/notifications/stats — summary statistics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager
from tests.api.conftest import build_stats_app

pytestmark = pytest.mark.unit


class TestNotificationStatsBasic:
    """Test the basic /stats endpoint behaviour."""

    async def test_returns_200_with_stats(self):
        app, _, _ = build_stats_app(
            total=100,
            sent=90,
            failed=10,
            channel_rows=[
                {"channel": "telegram", "cnt": 60},
                {"channel": "email", "cnt": 40},
            ],
            butler_rows=[
                {"source_butler": "atlas", "cnt": 70},
                {"source_butler": "hermes", "cnt": 30},
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 100
        assert body["data"]["sent"] == 90
        assert body["data"]["failed"] == 10
        assert body["data"]["by_channel"] == {"telegram": 60, "email": 40}
        assert body["data"]["by_butler"] == {"atlas": 70, "hermes": 30}

    async def test_empty_database_returns_zeros(self):
        app, _, _ = build_stats_app(total=0, sent=0, failed=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 0
        assert body["data"]["sent"] == 0
        assert body["data"]["failed"] == 0
        assert body["data"]["by_channel"] == {}
        assert body["data"]["by_butler"] == {}


class TestNotificationStatsResponseShape:
    """Verify the response conforms to ApiResponse[NotificationStats]."""

    async def test_has_data_and_meta_keys(self):
        app, _, _ = build_stats_app(total=5, sent=3, failed=2)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        body = resp.json()
        assert "data" in body
        assert "meta" in body

    async def test_data_has_all_required_fields(self):
        app, _, _ = build_stats_app(
            total=10,
            sent=7,
            failed=3,
            channel_rows=[{"channel": "telegram", "cnt": 10}],
            butler_rows=[{"source_butler": "atlas", "cnt": 10}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        data = resp.json()["data"]
        assert set(data.keys()) == {"total", "sent", "failed", "by_channel", "by_butler"}


class TestNotificationStatsDBQueries:
    """Verify the correct SQL queries are executed."""

    async def test_uses_switchboard_pool(self):
        app, _, mock_db = build_stats_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications/stats")

        mock_db.pool.assert_called_with("switchboard")

    async def test_executes_count_queries(self):
        app, mock_pool, _ = build_stats_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications/stats")

        # Should have 3 fetchval calls: total, sent, failed
        assert mock_pool.fetchval.call_count == 3

        calls = [c[0][0] for c in mock_pool.fetchval.call_args_list]
        assert any(
            "SELECT count(*) FROM notifications" in sql and "status" not in sql for sql in calls
        ), "Missing total count query"
        assert any("status = 'sent'" in sql for sql in calls), "Missing sent count query"
        assert any("status = 'failed'" in sql for sql in calls), "Missing failed count query"

    async def test_executes_group_by_queries(self):
        app, mock_pool, _ = build_stats_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/notifications/stats")

        # Should have 2 fetch calls: by_channel, by_butler
        assert mock_pool.fetch.call_count == 2

        calls = [c[0][0] for c in mock_pool.fetch.call_args_list]
        assert any("GROUP BY channel" in sql for sql in calls), "Missing channel grouping query"
        assert any("GROUP BY source_butler" in sql for sql in calls), (
            "Missing butler grouping query"
        )


class TestNotificationStatsNullHandling:
    """Test handling of NULL/None values from the database."""

    async def test_none_fetchval_treated_as_zero(self):
        """When fetchval returns None (empty table), counts should be 0."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["sent"] == 0
        assert data["failed"] == 0


class TestNotificationStatsMultipleChannelsAndButlers:
    """Test with multiple channels and butlers."""

    async def test_many_channels_and_butlers(self):
        app, _, _ = build_stats_app(
            total=200,
            sent=180,
            failed=20,
            channel_rows=[
                {"channel": "telegram", "cnt": 80},
                {"channel": "email", "cnt": 70},
                {"channel": "slack", "cnt": 30},
                {"channel": "sms", "cnt": 20},
            ],
            butler_rows=[
                {"source_butler": "atlas", "cnt": 100},
                {"source_butler": "hermes", "cnt": 50},
                {"source_butler": "kronos", "cnt": 50},
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/notifications/stats")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 200
        assert len(data["by_channel"]) == 4
        assert len(data["by_butler"]) == 3
        assert data["by_channel"]["telegram"] == 80
        assert data["by_channel"]["slack"] == 30
        assert data["by_butler"]["atlas"] == 100
        assert data["by_butler"]["kronos"] == 50
