"""Tests for GET /api/issues — active issues aggregation.

Verifies issue detection for unreachable butlers, sorting by severity,
and graceful handling of edge cases (empty roster, mixed reachability).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import Issue
from butlers.api.routers.issues import _check_butler_reachability, _get_db_manager_optional

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(*, connected: bool = True) -> MagicMock:
    """Create a mock MCP client with async ping."""
    client = MagicMock()
    client.is_connected = MagicMock(return_value=connected)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.ping = AsyncMock(return_value=True)
    return client


def _make_configs() -> list[ButlerConnectionInfo]:
    """Return a small set of butler configs for testing."""
    return [
        ButlerConnectionInfo(name="switchboard", port=40100, description="Routes messages"),
        ButlerConnectionInfo(name="general", port=40101, description="Catch-all assistant"),
    ]


def _override_deps(app, mgr, configs, db=None):
    """Apply dependency overrides for mcp_manager and butler_configs."""
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[get_butler_configs] = lambda: configs
    if db is not None:
        app.dependency_overrides[_get_db_manager_optional] = lambda: db


# ---------------------------------------------------------------------------
# _check_butler_reachability unit tests
# ---------------------------------------------------------------------------


class TestCheckButlerReachability:
    async def test_reachable_returns_none(self):
        """Reachable butler produces no issue."""
        info = ButlerConnectionInfo(name="test", port=8000)
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        result = await _check_butler_reachability(mgr, info)

        assert result is None
        mgr.get_client.assert_called_once_with("test")
        mock_client.ping.assert_called_once()

    async def test_unreachable_returns_critical_issue(self):
        """Unreachable butler generates a critical issue."""
        info = ButlerConnectionInfo(name="ghost", port=9999)

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("ghost", cause=ConnectionRefusedError())
        )

        result = await _check_butler_reachability(mgr, info)

        assert result is not None
        assert result.severity == "critical"
        assert result.type == "unreachable"
        assert result.butler == "ghost"
        assert "ghost" in result.description
        assert result.link == "/butlers/ghost"

    async def test_timeout_returns_critical_issue(self):
        """Timed-out butler generates a critical issue."""
        info = ButlerConnectionInfo(name="slow", port=40200)

        mgr = MagicMock(spec=MCPClientManager)

        async def _slow_connect(name: str):
            import asyncio

            await asyncio.sleep(60)

        mgr.get_client = _slow_connect

        with patch("butlers.api.routers.issues._STATUS_TIMEOUT_S", 0.01):
            result = await _check_butler_reachability(mgr, info)

        assert result is not None
        assert result.severity == "critical"
        assert result.type == "unreachable"
        assert result.butler == "slow"

    async def test_unexpected_error_returns_critical_issue(self):
        """Unexpected exceptions still yield a critical issue."""
        info = ButlerConnectionInfo(name="broken", port=8300)

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=RuntimeError("boom"))

        result = await _check_butler_reachability(mgr, info)

        assert result is not None
        assert result.severity == "critical"
        assert result.type == "unreachable"
        assert result.butler == "broken"
        assert "unexpectedly" in result.description

    async def test_ping_failure_returns_critical_issue(self):
        """Butler connects but ping fails -> critical issue."""
        info = ButlerConnectionInfo(name="flaky", port=8400)
        mock_client = _make_mock_client()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("ping failed"))

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        result = await _check_butler_reachability(mgr, info)

        assert result is not None
        assert result.severity == "critical"
        assert result.butler == "flaky"


# ---------------------------------------------------------------------------
# Full endpoint integration tests (ASGI transport, mocked deps)
# ---------------------------------------------------------------------------


class TestListIssuesEndpoint:
    """Test the GET /api/issues endpoint via httpx ASGI transport."""

    async def test_no_issues_when_all_reachable(self, app):
        """Happy path: all butlers reachable yields empty issues list."""
        configs = _make_configs()
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        _override_deps(app, mgr, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert body["data"] == []

    async def test_unreachable_butler_generates_critical_issue(self, app):
        """Unreachable butler appears as a critical issue."""
        configs = _make_configs()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("x", cause=ConnectionRefusedError())
        )

        _override_deps(app, mgr, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()
        issues = body["data"]
        assert len(issues) == 2

        for issue in issues:
            assert issue["severity"] == "critical"
            assert issue["type"] == "unreachable"

    async def test_timeout_generates_critical_issue(self, app):
        """Timed-out butler generates a critical issue in the endpoint."""
        configs = [ButlerConnectionInfo(name="slow", port=40200)]

        mgr = MagicMock(spec=MCPClientManager)

        async def _slow_connect(name: str):
            import asyncio

            await asyncio.sleep(60)

        mgr.get_client = _slow_connect

        _override_deps(app, mgr, configs)

        with patch("butlers.api.routers.issues._STATUS_TIMEOUT_S", 0.01):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()
        issues = body["data"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "critical"
        assert issues[0]["type"] == "unreachable"
        assert issues[0]["butler"] == "slow"

    async def test_mixed_reachability(self, app):
        """Mix of reachable and unreachable butlers."""
        configs = _make_configs()
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)

        async def _get_client_side_effect(name: str):
            if name == "switchboard":
                return mock_client
            raise ButlerUnreachableError(name, cause=ConnectionRefusedError())

        mgr.get_client = _get_client_side_effect

        _override_deps(app, mgr, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()
        issues = body["data"]
        assert len(issues) == 1
        assert issues[0]["butler"] == "general"
        assert issues[0]["severity"] == "critical"

    async def test_sorting_critical_before_warning(self, app):
        """Issues are sorted: critical first, then by butler name."""
        # We test sorting by injecting issues with different severities.
        # Since only reachability checks exist (all critical), we verify
        # alphabetical sub-sort within the same severity.
        configs = [
            ButlerConnectionInfo(name="zeta", port=40100),
            ButlerConnectionInfo(name="alpha", port=40101),
            ButlerConnectionInfo(name="mid", port=40102),
        ]

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("x", cause=ConnectionRefusedError())
        )

        _override_deps(app, mgr, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()
        issues = body["data"]
        assert len(issues) == 3

        butler_names = [i["butler"] for i in issues]
        assert butler_names == ["alpha", "mid", "zeta"]

    async def test_empty_roster_returns_empty_list(self, app):
        """No butlers discovered yields an empty issues list."""
        mgr = MagicMock(spec=MCPClientManager)

        _override_deps(app, mgr, [])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []

    async def test_response_shape_matches_model(self, app):
        """Verify response data can be parsed as Issue model."""
        configs = [ButlerConnectionInfo(name="mybutler", port=8500)]

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("mybutler", cause=ConnectionRefusedError())
        )

        _override_deps(app, mgr, configs)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        body = response.json()

        for item in body["data"]:
            issue = Issue.model_validate(item)
            assert issue.severity == "critical"
            assert issue.type == "unreachable"
            assert issue.butler == "mybutler"
            assert issue.link == "/butlers/mybutler"

    async def test_audit_schedule_error_surfaces_as_issue(self, app):
        """Latest schedule-triggered audit error should appear as a critical issue."""
        configs = [ButlerConnectionInfo(name="switchboard", port=40100)]
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                {
                    "error_summary": "RuntimeError: sweep failed",
                    "first_seen_at": datetime(2026, 2, 19, 23, 55, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 2, 20, 0, 0, tzinfo=UTC),
                    "occurrences": 3,
                    "butlers": ["switchboard"],
                    "has_schedule": True,
                    "schedule_names": ["eligibility-sweep"],
                }
            ]
        )
        mock_db = MagicMock()
        mock_db.pool = MagicMock(return_value=mock_pool)

        _override_deps(app, mgr, configs, db=mock_db)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        issues = response.json()["data"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "critical"
        assert issues[0]["type"] == "scheduled_task_failure:eligibility-sweep"
        assert issues[0]["butler"] == "switchboard"
        assert issues[0]["occurrences"] == 3
        assert issues[0]["first_seen_at"] == "2026-02-19T23:55:00Z"
        assert issues[0]["last_seen_at"] == "2026-02-20T00:00:00Z"
        assert "eligibility-sweep" in issues[0]["description"]

    async def test_audit_non_schedule_error_surfaces_as_warning(self, app):
        """Non-schedule audit failures should surface as warning issues."""
        configs: list[ButlerConnectionInfo] = []
        mgr = MagicMock(spec=MCPClientManager)

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                {
                    "error_summary": "ValueError: invalid payload",
                    "first_seen_at": datetime(2026, 2, 19, 23, 55, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 2, 20, 0, 0, tzinfo=UTC),
                    "occurrences": 2,
                    "butlers": ["general"],
                    "has_schedule": False,
                    "schedule_names": [],
                }
            ]
        )
        mock_db = MagicMock()
        mock_db.pool = MagicMock(return_value=mock_pool)

        _override_deps(app, mgr, configs, db=mock_db)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        issues = response.json()["data"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "warning"
        assert issues[0]["type"] == "audit_error_group:valueerror-invalid-payload"
        assert issues[0]["butler"] == "general"
        assert issues[0]["occurrences"] == 2
        assert "invalid payload" in issues[0]["description"]

    async def test_grouped_errors_sorted_by_most_recent_last_seen(self, app):
        """Grouped issues should be ordered by last_seen_at descending."""
        configs: list[ButlerConnectionInfo] = []
        mgr = MagicMock(spec=MCPClientManager)

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                {
                    "error_summary": "TimeoutError: upstream slow",
                    "first_seen_at": datetime(2026, 2, 19, 8, 0, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 2, 19, 8, 30, tzinfo=UTC),
                    "occurrences": 7,
                    "butlers": ["general", "switchboard"],
                    "has_schedule": False,
                    "schedule_names": [],
                },
                {
                    "error_summary": "ConnectionError: broker down",
                    "first_seen_at": datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 2, 20, 9, 45, tzinfo=UTC),
                    "occurrences": 4,
                    "butlers": ["switchboard"],
                    "has_schedule": False,
                    "schedule_names": [],
                },
            ]
        )
        mock_db = MagicMock()
        mock_db.pool = MagicMock(return_value=mock_pool)

        _override_deps(app, mgr, configs, db=mock_db)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/issues")

        assert response.status_code == 200
        issues = response.json()["data"]
        assert len(issues) == 2
        assert issues[0]["error_message"] == "ConnectionError: broker down"
        assert issues[0]["last_seen_at"] == "2026-02-20T09:45:00Z"
        assert issues[1]["error_message"] == "TimeoutError: upstream slow"
        assert issues[1]["last_seen_at"] == "2026-02-19T08:30:00Z"
