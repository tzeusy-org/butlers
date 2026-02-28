"""Tests for switchboard connector ingestion API endpoints.

Covers:
- GET /api/switchboard/connectors (list)
- GET /api/switchboard/connectors/summary
- GET /api/switchboard/connectors/{type}/{identity} (detail)
- GET /api/switchboard/connectors/{type}/{identity}/stats
- GET /api/switchboard/connectors/{type}/{identity}/fanout
- GET /api/switchboard/ingestion/overview
- GET /api/switchboard/ingestion/fanout

Test scenarios include empty-state, populated-state, degraded-state (DB errors),
and connector-not-found cases.

Issue: butlers-dsa4.4.1
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

# Dynamically load the switchboard router to get _get_db_manager.
# IMPORTANT: use the SAME module name as router_discovery.py uses
# ("switchboard_api_router") so we share the same module object across
# all test files.  If the module is already in sys.modules (e.g., loaded
# by test_switchboard_views.py), we reuse it rather than re-executing it,
# which would replace the _get_db_manager reference and break dependency
# overrides in already-imported test files.
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"
_MODULE_NAME = "switchboard_api_router"

if _MODULE_NAME in sys.modules:
    switchboard_module = sys.modules[_MODULE_NAME]
else:
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    switchboard_module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = switchboard_module
    spec.loader.exec_module(switchboard_module)

pytestmark = pytest.mark.unit


def _current_get_db_manager():
    """Dynamically fetch _get_db_manager from the current loaded module.

    This avoids stale function references when test files reload the module
    (e.g., test_switchboard_views.py unconditionally re-executes the module).
    We always look up the LIVE function from sys.modules at call time.
    """
    return sys.modules[_MODULE_NAME]._get_db_manager


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_SAMPLE_CONNECTOR_ROW = {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123",
    "instance_id": None,
    "version": "1.0.0",
    "state": "healthy",
    "error_message": None,
    "uptime_s": 3600,
    "last_heartbeat_at": "2026-02-23T10:00:00+00:00",
    "first_seen_at": "2026-02-01T00:00:00+00:00",
    "registered_via": "self",
    "counter_messages_ingested": 42,
    "counter_messages_failed": 1,
    "counter_source_api_calls": 150,
    "counter_checkpoint_saves": 10,
    "counter_dedupe_accepted": 0,
    "today_messages_ingested": 7,
    "today_messages_failed": 0,
    "checkpoint_cursor": "update-12345",
    "checkpoint_updated_at": "2026-02-23T09:55:00+00:00",
}

_SAMPLE_HOURLY_ROW = {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123",
    "hour": "2026-02-23T10:00:00+00:00",
    "messages_ingested": 10,
    "messages_failed": 0,
    "source_api_calls": 5,
    "dedupe_accepted": 1,
    "heartbeat_count": 4,
    "healthy_count": 4,
    "degraded_count": 0,
    "error_count": 0,
}

_SAMPLE_DAILY_ROW = {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123",
    "day": "2026-02-23",
    "messages_ingested": 100,
    "messages_failed": 2,
    "source_api_calls": 50,
    "dedupe_accepted": 5,
    "heartbeat_count": 48,
    "healthy_count": 46,
    "degraded_count": 2,
    "error_count": 0,
    "uptime_pct": 95.83,
}

_SAMPLE_FANOUT_ROW = {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123",
    "target_butler": "health",
    "message_count": 25,
}


def _app_with_mock_db(
    app,
    *,
    fetch_rows: list | None = None,
    fetchval_result: int | None = 0,
    fetchrow_result: dict | None = None,
    pool_available: bool = True,
    fetch_side_effect: Exception | None = None,
    fetchrow_side_effect: Exception | None = None,
    fetchval_side_effect: Exception | None = None,
):
    """Create a FastAPI app with a mocked DatabaseManager."""
    mock_pool = AsyncMock()

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    if fetchval_side_effect is not None:
        mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_pool.fetchval = AsyncMock(return_value=fetchval_result)

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    app.dependency_overrides[_current_get_db_manager()] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/switchboard/connectors
# ---------------------------------------------------------------------------


class TestListConnectors:
    async def test_empty_state_returns_empty_list(self, app):
        """Empty connector registry returns an empty data list."""
        app = _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_populated_state_returns_connector_entries(self, app):
        """With a connector in registry, response includes its fields."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_CONNECTOR_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        entry = body["data"][0]
        assert entry["connector_type"] == "telegram_bot"
        assert entry["endpoint_identity"] == "bot-123"
        assert entry["state"] == "healthy"
        assert entry["counter_messages_ingested"] == 42

    async def test_today_stats_fields_returned(self, app):
        """Today stats from hourly rollup are included in connector entries."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_CONNECTOR_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")

        assert resp.status_code == 200
        entry = resp.json()["data"][0]
        assert entry["today_messages_ingested"] == 7
        assert entry["today_messages_failed"] == 0

    async def test_degraded_db_falls_back_to_empty_list(self, app):
        """When connector_registry table is missing, returns empty list (not 500)."""
        app = _app_with_mock_db(app, fetch_side_effect=Exception("relation does not exist"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_db_unavailable_returns_503(self, app):
        """When the DB pool itself is not available, returns 503."""
        app = _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")

        assert resp.status_code == 503

    async def test_multiple_connectors_returned(self, app):
        """Multiple connectors in registry are all returned."""
        row2 = {
            **_SAMPLE_CONNECTOR_ROW,
            "connector_type": "gmail",
            "endpoint_identity": "user@x.com",
        }
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_CONNECTOR_ROW, row2])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2


# ---------------------------------------------------------------------------
# GET /api/switchboard/connectors/summary
# ---------------------------------------------------------------------------


class TestConnectorsSummary:
    async def test_empty_state_returns_zero_summary(self, app):
        """Empty registry returns zero-value summary without errors."""
        app = _app_with_mock_db(
            app,
            fetchrow_result={
                "total_connectors": 0,
                "online_count": 0,
                "stale_count": 0,
                "offline_count": 0,
                "unknown_count": 0,
                "total_messages_ingested": 0,
                "total_messages_failed": 0,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/summary")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["total_connectors"] == 0
        assert data["error_rate_pct"] == 0.0

    async def test_populated_summary_fields(self, app):
        """Summary includes all required fields for the Connectors tab header."""
        app = _app_with_mock_db(
            app,
            fetchrow_result={
                "total_connectors": 3,
                "online_count": 2,
                "stale_count": 0,
                "offline_count": 1,
                "unknown_count": 0,
                "total_messages_ingested": 200,
                "total_messages_failed": 10,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/summary")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["total_connectors"] == 3
        assert data["online_count"] == 2
        assert data["offline_count"] == 1
        assert data["total_messages_ingested"] == 200
        assert data["total_messages_failed"] == 10
        assert data["error_rate_pct"] > 0

    async def test_error_rate_computed_correctly(self, app):
        """Error rate is (failed / (ingested + failed)) * 100."""
        app = _app_with_mock_db(
            app,
            fetchrow_result={
                "total_connectors": 1,
                "online_count": 1,
                "stale_count": 0,
                "offline_count": 0,
                "unknown_count": 0,
                "total_messages_ingested": 90,
                "total_messages_failed": 10,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/summary")

        body = resp.json()
        # 10 / (90 + 10) * 100 = 10.0
        assert body["data"]["error_rate_pct"] == 10.0

    async def test_degraded_db_returns_zero_summary(self, app):
        """When DB fails, summary falls back to all-zeros (not 500)."""
        app = _app_with_mock_db(app, fetchrow_side_effect=Exception("relation does not exist"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total_connectors"] == 0


# ---------------------------------------------------------------------------
# GET /api/switchboard/connectors/{type}/{identity}
# ---------------------------------------------------------------------------


class TestConnectorDetail:
    async def test_returns_connector_detail_when_found(self, app):
        """When connector exists, detail endpoint returns its data."""
        app = _app_with_mock_db(app, fetchrow_result=_SAMPLE_CONNECTOR_ROW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram_bot/bot-123")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["connector_type"] == "telegram_bot"
        assert body["data"]["endpoint_identity"] == "bot-123"
        assert body["data"]["state"] == "healthy"
        assert body["data"]["today_messages_ingested"] == 7
        assert body["data"]["today_messages_failed"] == 0

    async def test_returns_404_when_not_found(self, app):
        """When connector is not in registry, 404 is returned."""
        app = _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram_bot/nonexistent")

        assert resp.status_code == 404

    async def test_degraded_db_returns_503(self, app):
        """When DB errors on detail lookup, 503 is returned."""
        app = _app_with_mock_db(app, fetchrow_side_effect=Exception("relation does not exist"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram_bot/bot-123")

        assert resp.status_code == 503

    async def test_connector_with_error_state(self, app):
        """Connector in error state is returned correctly."""
        error_row = {
            **_SAMPLE_CONNECTOR_ROW,
            "state": "error",
            "error_message": "Connection refused",
        }
        app = _app_with_mock_db(app, fetchrow_result=error_row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram_bot/bot-123")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["state"] == "error"
        assert body["data"]["error_message"] == "Connection refused"


# ---------------------------------------------------------------------------
# GET /api/switchboard/connectors/{type}/{identity}/stats
# ---------------------------------------------------------------------------


class TestConnectorStats:
    async def test_24h_returns_hourly_stats(self, app):
        """period=24h returns hourly stats data."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_HOURLY_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/stats",
                params={"period": "24h"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        entry = body["data"][0]
        assert "hour" in entry
        assert entry["messages_ingested"] == 10

    async def test_7d_returns_daily_stats(self, app):
        """period=7d returns daily stats data."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_DAILY_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/stats",
                params={"period": "7d"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        entry = body["data"][0]
        assert "day" in entry
        assert entry["uptime_pct"] == 95.83

    async def test_30d_returns_daily_stats(self, app):
        """period=30d returns daily stats data."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_DAILY_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/stats",
                params={"period": "30d"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1

    async def test_empty_state_returns_empty_list(self, app):
        """No stats data returns empty list."""
        app = _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/stats",
                params={"period": "24h"},
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_degraded_db_falls_back_to_empty_list(self, app):
        """When rollup tables are missing, returns empty list (not 500)."""
        app = _app_with_mock_db(app, fetch_side_effect=Exception("relation does not exist"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/stats",
                params={"period": "24h"},
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_default_period_is_24h(self, app):
        """When no period param is provided, defaults to 24h (hourly data)."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_HOURLY_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/stats",
            )

        assert resp.status_code == 200
        body = resp.json()
        # hourly data has 'hour' field
        assert "hour" in body["data"][0]


# ---------------------------------------------------------------------------
# GET /api/switchboard/connectors/{type}/{identity}/fanout
# ---------------------------------------------------------------------------


class TestConnectorFanout:
    async def test_empty_state_returns_empty_list(self, app):
        """No fanout data returns empty list."""
        app = _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/fanout",
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_returns_fanout_rows(self, app):
        """Fanout rows include connector and butler info."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_FANOUT_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/fanout",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["connector_type"] == "telegram_bot"
        assert row["target_butler"] == "health"
        assert row["message_count"] == 25

    async def test_period_param_accepted(self, app):
        """period=7d and period=30d are accepted without errors."""
        app = _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for period in ("24h", "7d", "30d"):
                resp = await client.get(
                    "/api/switchboard/connectors/telegram_bot/bot-123/fanout",
                    params={"period": period},
                )
                assert resp.status_code == 200

    async def test_degraded_db_falls_back_to_empty_list(self, app):
        """Missing rollup tables return empty list (not 500)."""
        app = _app_with_mock_db(app, fetch_side_effect=Exception("relation does not exist"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/telegram_bot/bot-123/fanout",
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# GET /api/switchboard/ingestion/overview
# ---------------------------------------------------------------------------


class TestIngestionOverview:
    async def _make_overview_app(
        self,
        app,
        *,
        active_connectors: int = 2,
        tier_row: dict | None = None,
        fetchval_side_effect: Exception | None = None,
        fetchrow_side_effect: Exception | None = None,
    ):
        """Build a mock app for the overview endpoint.

        overview calls: fetchval (active connectors), fetchrow (tier breakdown
        from message_inbox — also the source for total_ingested).
        total_ingested is derived as tier1+tier2+tier3, so no rollup-table
        fetchrow is needed.
        """
        mock_pool = AsyncMock()

        if fetchval_side_effect is not None:
            mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
        else:
            mock_pool.fetchval = AsyncMock(return_value=active_connectors)

        _tier = tier_row or {"tier1_full": 80, "tier2_metadata": 15, "tier3_skip": 5}

        if fetchrow_side_effect is not None:
            mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        else:
            mock_pool.fetchrow = AsyncMock(return_value=_tier)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app.dependency_overrides[_current_get_db_manager()] = lambda: mock_db
        return app

    async def test_returns_overview_struct(self, app):
        """Overview response includes all required stat fields."""
        app = await self._make_overview_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert "period" in data
        assert "total_ingested" in data
        assert "total_skipped" in data
        assert "total_metadata_only" in data
        assert "llm_calls_saved" in data
        assert "active_connectors" in data
        assert "tier1_full_count" in data
        assert "tier2_metadata_count" in data
        assert "tier3_skip_count" in data

    async def test_total_ingested_is_sum_of_tiers_from_message_inbox(self, app):
        """total_ingested equals tier1+tier2+tier3 from message_inbox (not rollup tables).

        This verifies the fix for the bug where internal-module messages were
        not counted because connector_stats_hourly only covers external connectors.
        """
        app = await self._make_overview_app(
            app, tier_row={"tier1_full": 70, "tier2_metadata": 20, "tier3_skip": 10}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")

        body = resp.json()
        data = body["data"]
        # total_ingested = tier1 + tier2 + tier3 = 70 + 20 + 10 = 100
        assert data["total_ingested"] == 100
        assert data["tier1_full_count"] == 70
        assert data["tier2_metadata_count"] == 20
        assert data["tier3_skip_count"] == 10

    async def test_active_connectors_counted(self, app):
        """active_connectors field reflects healthy connector count."""
        app = await self._make_overview_app(app, active_connectors=3)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")

        body = resp.json()
        assert body["data"]["active_connectors"] == 3

    async def test_llm_calls_saved_is_tier2_plus_tier3(self, app):
        """LLM calls saved = tier2 + tier3 message counts."""
        app = await self._make_overview_app(
            app, tier_row={"tier1_full": 70, "tier2_metadata": 20, "tier3_skip": 10}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")

        body = resp.json()
        # llm_calls_saved = tier2 + tier3 = 20 + 10 = 30
        assert body["data"]["llm_calls_saved"] == 30
        assert body["data"]["total_skipped"] == 10
        assert body["data"]["total_metadata_only"] == 20

    async def test_period_param_accepted(self, app):
        """24h, 7d, and 30d periods are all accepted."""
        for period in ("24h", "7d", "30d"):
            app = await self._make_overview_app(app)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/switchboard/ingestion/overview",
                    params={"period": period},
                )
            assert resp.status_code == 200
            assert resp.json()["data"]["period"] == period

    async def test_degraded_db_returns_zero_overview(self, app):
        """When DB errors, overview falls back to zeros (not 500)."""
        app = await self._make_overview_app(
            app,
            fetchval_side_effect=Exception("relation does not exist"),
            fetchrow_side_effect=Exception("relation does not exist"),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["active_connectors"] == 0
        assert data["total_ingested"] == 0
        assert data["llm_calls_saved"] == 0

    async def test_empty_state_all_zeros(self, app):
        """When no data exists, all numeric fields are zero."""
        app = await self._make_overview_app(
            app,
            active_connectors=0,
            tier_row={"tier1_full": 0, "tier2_metadata": 0, "tier3_skip": 0},
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["total_ingested"] == 0
        assert data["active_connectors"] == 0
        assert data["llm_calls_saved"] == 0


# ---------------------------------------------------------------------------
# GET /api/switchboard/ingestion/fanout
# ---------------------------------------------------------------------------


class TestIngestionFanout:
    async def test_empty_state_returns_empty_list(self, app):
        """Empty fanout data returns an empty list."""
        app = _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/fanout")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_returns_fanout_matrix_rows(self, app):
        """Fanout matrix rows include all required fields."""
        app = _app_with_mock_db(app, fetch_rows=[_SAMPLE_FANOUT_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/fanout")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["connector_type"] == "telegram_bot"
        assert row["endpoint_identity"] == "bot-123"
        assert row["target_butler"] == "health"
        assert row["message_count"] == 25

    async def test_period_param_accepted(self, app):
        """All period values are accepted by fanout endpoint."""
        app = _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for period in ("24h", "7d", "30d"):
                resp = await client.get(
                    "/api/switchboard/ingestion/fanout",
                    params={"period": period},
                )
                assert resp.status_code == 200

    async def test_degraded_db_falls_back_to_empty_list(self, app):
        """Missing rollup tables return empty list (not 500)."""
        app = _app_with_mock_db(app, fetch_side_effect=Exception("relation does not exist"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/fanout")

        assert resp.status_code == 200
        assert resp.json()["data"] == []
