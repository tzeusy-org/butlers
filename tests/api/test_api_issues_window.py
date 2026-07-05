"""Tests for the ``window`` query param + row cap on GET /api/issues.

JARVIS pursuit move 13 (bu-qvnce.13): the audit-derived-issues CTE was an
unbounded all-time scan with no LIMIT. These tests exercise the new default
7-day window, explicit windows, the ``all`` opt-out, the always-on row cap,
and 422 handling for a malformed window value.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_mcp_manager
from butlers.api.routers.issues import _MAX_AUDIT_GROUP_ROWS, _get_db_manager

pytestmark = pytest.mark.unit


def _build_app(fetch_rows: list[dict[str, Any]] | None = None) -> tuple[Any, MagicMock]:
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=list(fetch_rows or []))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_butler_configs] = lambda: []
    return app, mock_pool


async def _call(app: Any, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


class TestIssuesWindowDefault:
    async def test_default_window_applies_seven_day_bound(self) -> None:
        app, pool = _build_app()

        resp = await _call(app, "/api/issues")

        assert resp.status_code == 200
        # Find the audit-group query call (the other pool.fetch call queries
        # public.dismissed_issues and takes no positional bind param).
        group_call = next(c for c in pool.fetch.await_args_list if "grouped_errors" in c.args[0])
        assert "AND created_at >= $1" in group_call.args[0]
        assert f"LIMIT {_MAX_AUDIT_GROUP_ROWS}" in group_call.args[0]
        since = group_call.args[1]
        assert isinstance(since, datetime)
        # Roughly 7 days ago -- allow generous slack for test execution time.
        expected = datetime.now(since.tzinfo) - timedelta(days=7)
        assert abs((since - expected).total_seconds()) < 60


class TestIssuesWindowExplicit:
    async def test_24h_window_narrows_the_bound(self) -> None:
        app, pool = _build_app()

        resp = await _call(app, "/api/issues?window=24h")

        assert resp.status_code == 200
        group_call = next(c for c in pool.fetch.await_args_list if "grouped_errors" in c.args[0])
        since = group_call.args[1]
        expected = datetime.now(since.tzinfo) - timedelta(hours=24)
        assert abs((since - expected).total_seconds()) < 60

    async def test_30d_window(self) -> None:
        app, pool = _build_app()

        resp = await _call(app, "/api/issues?window=30d")

        assert resp.status_code == 200
        group_call = next(c for c in pool.fetch.await_args_list if "grouped_errors" in c.args[0])
        since = group_call.args[1]
        expected = datetime.now(since.tzinfo) - timedelta(days=30)
        assert abs((since - expected).total_seconds()) < 60


class TestIssuesWindowAll:
    async def test_all_disables_time_bound_but_keeps_row_cap(self) -> None:
        app, pool = _build_app()

        resp = await _call(app, "/api/issues?window=all")

        assert resp.status_code == 200
        group_call = next(c for c in pool.fetch.await_args_list if "grouped_errors" in c.args[0])
        # No time-bound param bound alongside the query -- just the query string.
        assert len(group_call.args) == 1
        assert "AND created_at >= $1" not in group_call.args[0]
        assert f"LIMIT {_MAX_AUDIT_GROUP_ROWS}" in group_call.args[0]


class TestIssuesWindowInvalid:
    async def test_malformed_window_is_422(self) -> None:
        app, _ = _build_app()

        resp = await _call(app, "/api/issues?window=bogus")

        assert resp.status_code == 422

    async def test_negative_style_window_is_422(self) -> None:
        app, _ = _build_app()

        resp = await _call(app, "/api/issues?window=-7d")

        assert resp.status_code == 422
