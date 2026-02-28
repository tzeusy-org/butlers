"""Tests for GET /api/switchboard/registry/{name}/eligibility-history endpoint.

Verifies the eligibility timeline returns correct segments from audit log data.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_current_db_manager_dep() -> object:
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]._get_db_manager
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module._get_db_manager


def _app_with_mocks(
    app,
    *,
    fetchrow_result: dict | None = None,
    fetch_result: list | None = None,
):
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.fetch = AsyncMock(return_value=fetch_result or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    get_db_manager = _get_current_db_manager_dep()
    app.dependency_overrides[get_db_manager] = lambda: mock_db

    return app, mock_pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEligibilityHistory:
    async def test_no_transitions_returns_single_segment(self, app):
        """Full window in current state when no log rows exist."""
        app, _ = _app_with_mocks(
            app,
            fetchrow_result={"eligibility_state": "active"},
            fetch_result=[],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/health/eligibility-history")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["butler_name"] == "health"
        assert len(data["segments"]) == 1
        assert data["segments"][0]["state"] == "active"

    async def test_transitions_produce_correct_segments(self, app):
        """Two log rows produce three segments."""
        now = datetime.datetime.now(datetime.UTC)
        t1 = now - datetime.timedelta(hours=12)
        t2 = now - datetime.timedelta(hours=6)

        app, _ = _app_with_mocks(
            app,
            fetchrow_result={"eligibility_state": "active"},
            fetch_result=[
                {"previous_state": "active", "new_state": "stale", "observed_at": t1},
                {"previous_state": "stale", "new_state": "active", "observed_at": t2},
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/health/eligibility-history")

        assert resp.status_code == 200
        segments = resp.json()["data"]["segments"]
        assert len(segments) == 3
        assert segments[0]["state"] == "active"  # window_start → t1
        assert segments[1]["state"] == "stale"  # t1 → t2
        assert segments[2]["state"] == "active"  # t2 → now

    async def test_unknown_butler_returns_404(self, app):
        """Requesting history for an unknown butler returns 404."""
        app, _ = _app_with_mocks(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/unknown/eligibility-history")

        assert resp.status_code == 404

    async def test_custom_hours_parameter(self, app):
        """Custom hours parameter is accepted and used."""
        app, mock_pool = _app_with_mocks(
            app,
            fetchrow_result={"eligibility_state": "active"},
            fetch_result=[],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/health/eligibility-history?hours=48")

        assert resp.status_code == 200
        # Verify the fetch call used the correct window
        fetch_call = mock_pool.fetch.call_args
        assert fetch_call is not None
        # The second positional arg after SQL is the butler name, third is window_start
        window_start = fetch_call[0][2]
        now = datetime.datetime.now(datetime.UTC)
        # Should be ~48h ago (allow 5s tolerance)
        expected = now - datetime.timedelta(hours=48)
        assert abs((window_start - expected).total_seconds()) < 5

    async def test_segments_cover_full_window(self, app):
        """First segment starts at window_start, last segment ends at window_end."""
        app, _ = _app_with_mocks(
            app,
            fetchrow_result={"eligibility_state": "stale"},
            fetch_result=[],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/health/eligibility-history")

        assert resp.status_code == 200
        data = resp.json()["data"]
        segments = data["segments"]
        assert segments[0]["start_at"] == data["window_start"]
        assert segments[-1]["end_at"] == data["window_end"]
