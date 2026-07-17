"""Tests for the ``window`` query param + row cap on GET /api/issues.

JARVIS pursuit move 13 (bu-qvnce.13): the audit-derived-issues CTE was an
unbounded all-time scan with no LIMIT. These tests exercise the new default
7-day window, explicit windows, the ``all`` opt-out, the always-on row cap,
and 422 handling for a malformed window value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

    def fetch(sql: str, *_args: Any) -> list[dict[str, Any]]:
        # Keep the audit-group rows separate from dismissal acknowledgements:
        # realistic group fixtures do not carry the ack table's ``issue_key``.
        return list(fetch_rows or []) if "grouped_errors" in sql else []

    mock_pool.fetch = AsyncMock(side_effect=fetch)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_butler_configs] = lambda: []
    return app, mock_pool


def _audit_group_row(index: int) -> dict[str, Any]:
    """Return one valid grouped-audit row in the query's returned order."""
    seen_at = datetime(2026, 7, 16, tzinfo=UTC)
    return {
        "error_summary": f"test audit error {index}",
        "butlers": ["calendar"],
        "schedule_names": [],
        "has_schedule": False,
        "occurrences": 1,
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
    }


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
        # Fetch one sentinel row beyond the public cap so the response can
        # distinguish an exact 500-group result from a truncated one.
        assert f"LIMIT {_MAX_AUDIT_GROUP_ROWS + 1}" in group_call.args[0]
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
        assert f"LIMIT {_MAX_AUDIT_GROUP_ROWS + 1}" in group_call.args[0]


class TestIssuesAuditGroupCap:
    async def test_overflow_marks_response_truncated_and_keeps_only_the_public_cap(self) -> None:
        app, pool = _build_app(
            [_audit_group_row(index) for index in range(_MAX_AUDIT_GROUP_ROWS + 1)]
        )

        resp = await _call(app, "/api/issues")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["truncated"] is True
        assert len(body["data"]) == _MAX_AUDIT_GROUP_ROWS
        assert body["data"][0]["error_message"] == "test audit error 0"
        assert "sources_degraded" not in body["meta"]
        group_call = next(c for c in pool.fetch.await_args_list if "grouped_errors" in c.args[0])
        assert f"LIMIT {_MAX_AUDIT_GROUP_ROWS + 1}" in group_call.args[0]

    async def test_exact_public_cap_does_not_claim_truncation(self) -> None:
        app, _ = _build_app([_audit_group_row(index) for index in range(_MAX_AUDIT_GROUP_ROWS)])

        resp = await _call(app, "/api/issues")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == _MAX_AUDIT_GROUP_ROWS
        # Preserve the established healthy-envelope shape. ``false`` would
        # make an exact-cap response look like a newly-added state to older
        # consumers; only genuine overflow needs the additive flag.
        assert "truncated" not in body["meta"]


class TestIssuesWindowInvalid:
    async def test_malformed_window_is_422(self) -> None:
        app, _ = _build_app()

        resp = await _call(app, "/api/issues?window=bogus")

        assert resp.status_code == 422

    async def test_negative_style_window_is_422(self) -> None:
        app, _ = _build_app()

        resp = await _call(app, "/api/issues?window=-7d")

        assert resp.status_code == 422
