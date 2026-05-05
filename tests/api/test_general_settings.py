"""Tests for general settings endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.general_settings import _get_db_manager

pytestmark = pytest.mark.unit


def _app_with_pool(app, *, fetchval_side_effect=None, pool_raises=None):
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_raises is not None:
        mock_db.credential_shared_pool.side_effect = pool_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


class TestGeneralSettingsApi:
    async def test_get_returns_stored_defaults_with_labels(self, app):
        # The asyncpg JSONB codec returns Python dicts directly (no json.dumps needed).
        _app_with_pool(
            app,
            fetchval_side_effect=[
                {
                    "timezone": "Asia/Singapore",
                    "language": "en-US",
                    "date_format": "YYYY-mm-dd",
                    "time_format": "HH:MM",
                    "week_starts_on": "Monday",
                    "currency": "USD",
                }
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/general")

        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["timezone"] == "Asia/Singapore"
        assert payload["timezone_label"] == "Asia/Singapore (GMT+08:00)"
        assert payload["language"] == "en-US"
        assert payload["date_format"] == "YYYY-mm-dd"
        assert payload["time_format"] == "HH:MM"
        assert payload["week_starts_on"] == "Monday"
        assert payload["currency"] == "USD"
        assert payload["measurement_system"] == "metric"

    async def test_put_persists_full_settings_and_returns_updated_labels(self, app):
        # The asyncpg JSONB codec returns Python dicts directly (no json.dumps needed).
        _app_with_pool(
            app,
            fetchval_side_effect=[
                1,
                {
                    "timezone": "America/New_York",
                    "language": "en-SG",
                    "date_format": "YYYY-mm-dd",
                    "time_format": "HH:MM",
                    "week_starts_on": "Monday",
                    "currency": "USD",
                },
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/settings/general",
                json={
                    "timezone": "America/New_York",
                    "language": "en-SG",
                    "date_format": "YYYY-mm-dd",
                    "time_format": "HH:MM",
                    "week_starts_on": "Monday",
                    "currency": "USD",
                },
            )

        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["timezone"] == "America/New_York"
        assert payload["timezone_label"].startswith("America/New_York (GMT")
        assert payload["language"] == "en-SG"
        assert payload["date_format"] == "YYYY-mm-dd"
        assert payload["time_format"] == "HH:MM"
        assert payload["week_starts_on"] == "Monday"
        assert payload["currency"] == "USD"
        assert payload["measurement_system"] == "metric"

    async def test_get_uses_implicit_bootstrap_defaults_when_unset(self, app):
        _app_with_pool(app, fetchval_side_effect=[None])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/general")

        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["timezone"] == "UTC"
        assert payload["language"] == "en-US"
        assert payload["date_format"] == "YYYY-mm-dd"
        assert payload["time_format"] == "HH:MM"
        assert payload["week_starts_on"] == "Monday"
        assert payload["currency"] == "USD"
        assert payload["measurement_system"] == "metric"

    async def test_put_rejects_invalid_timezone(self, app):
        _app_with_pool(app, fetchval_side_effect=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/settings/general",
                json={"timezone": "Mars/Olympus"},
            )

        assert resp.status_code == 422

    async def test_put_rejects_invalid_currency(self, app):
        _app_with_pool(app, fetchval_side_effect=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/settings/general",
                json={
                    "timezone": "UTC",
                    "language": "en-US",
                    "date_format": "YYYY-mm-dd",
                    "time_format": "HH:MM",
                    "week_starts_on": "Monday",
                    "currency": "usdollars",
                },
            )

        assert resp.status_code == 422
