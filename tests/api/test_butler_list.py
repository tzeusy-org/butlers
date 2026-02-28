"""Tests for GET /api/butlers — butler list with live status.

Verifies butler discovery, parallel status probing, graceful handling of
unreachable butlers, and the response shape matching ButlerSummary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
)
from butlers.api.models import ButlerSummary
from butlers.api.routers.butlers import _probe_butler

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(*, connected: bool = True) -> MagicMock:
    """Create a mock MCP client with async enter/exit and ping."""
    client = MagicMock()
    client.is_connected = MagicMock(return_value=connected)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.ping = AsyncMock(return_value=True)
    client.list_tools = AsyncMock(return_value=[])
    return client


def _make_configs() -> list[ButlerConnectionInfo]:
    """Return a small set of butler configs for testing."""
    return [
        ButlerConnectionInfo(name="switchboard", port=40100, description="Routes messages"),
        ButlerConnectionInfo(name="general", port=40101, description="Catch-all assistant"),
    ]


def _make_manager(configs: list[ButlerConnectionInfo]) -> MCPClientManager:
    """Create an MCPClientManager pre-registered with the given configs."""
    mgr = MCPClientManager()
    for info in configs:
        mgr.register(info.name, info)
    return mgr


# ---------------------------------------------------------------------------
# _probe_butler unit tests
# ---------------------------------------------------------------------------


class TestProbeButler:
    async def test_probe_ok(self, app):
        """Reachable butler returns status='ok'."""
        info = ButlerConnectionInfo(name="test", port=8000, description="Test butler")
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        result = await _probe_butler(mgr, info)

        assert result.name == "test"
        assert result.status == "ok"
        assert result.port == 8000
        assert result.description == "Test butler"
        mgr.get_client.assert_called_once_with("test")
        mock_client.ping.assert_called_once()

    async def test_probe_unreachable(self, app):
        """Unreachable butler returns status='down'."""
        info = ButlerConnectionInfo(name="ghost", port=9999)

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("ghost", cause=ConnectionRefusedError())
        )

        result = await _probe_butler(mgr, info)

        assert result.name == "ghost"
        assert result.status == "down"
        assert result.port == 9999

    async def test_probe_timeout(self, app):
        """Timed-out butler returns status='down'."""
        info = ButlerConnectionInfo(name="slow", port=40200)

        mgr = MagicMock(spec=MCPClientManager)

        async def _slow_connect(name: str):
            import asyncio

            await asyncio.sleep(60)  # longer than the timeout

        mgr.get_client = _slow_connect

        # Patch the timeout to be very short for the test
        with patch("butlers.api.routers.butlers._STATUS_TIMEOUT_S", 0.01):
            result = await _probe_butler(mgr, info)

        assert result.name == "slow"
        assert result.status == "down"

    async def test_probe_unexpected_error(self, app):
        """Unexpected exceptions still yield status='down'."""
        info = ButlerConnectionInfo(name="broken", port=8300)

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=RuntimeError("something went wrong"))

        result = await _probe_butler(mgr, info)

        assert result.name == "broken"
        assert result.status == "down"

    async def test_probe_ping_failure(self, app):
        """Butler connects but ping() fails -> status='down'."""
        info = ButlerConnectionInfo(name="flaky", port=8400)
        mock_client = _make_mock_client()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("ping failed"))

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        result = await _probe_butler(mgr, info)

        assert result.name == "flaky"
        assert result.status == "down"


# ---------------------------------------------------------------------------
# Full endpoint integration tests (ASGI transport, mocked deps)
# ---------------------------------------------------------------------------


class TestListButlersEndpoint:
    """Test the GET /api/butlers endpoint via httpx ASGI transport."""

    async def test_returns_butler_list(self, app):
        """Happy path: all butlers reachable."""
        configs = _make_configs()
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app.dependency_overrides.update(
            {
                __import__(
                    "butlers.api.deps", fromlist=["get_mcp_manager"]
                ).get_mcp_manager: lambda: mgr,
                __import__(
                    "butlers.api.deps", fromlist=["get_butler_configs"]
                ).get_butler_configs: lambda: configs,
            }
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert len(body["data"]) == 2

        names = {b["name"] for b in body["data"]}
        assert names == {"switchboard", "general"}

        for b in body["data"]:
            assert b["status"] == "ok"
            assert "port" in b
            assert "description" in b

    async def test_unreachable_butler_returns_down(self, app):
        """Unreachable butlers show status='down' instead of failing."""
        configs = _make_configs()

        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)

        async def _get_client_side_effect(name: str):
            if name == "switchboard":
                return mock_client
            raise ButlerUnreachableError(name, cause=ConnectionRefusedError())

        mgr.get_client = _get_client_side_effect

        app.dependency_overrides.update(
            {
                __import__(
                    "butlers.api.deps", fromlist=["get_mcp_manager"]
                ).get_mcp_manager: lambda: mgr,
                __import__(
                    "butlers.api.deps", fromlist=["get_butler_configs"]
                ).get_butler_configs: lambda: configs,
            }
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        body = response.json()
        data = body["data"]

        by_name = {b["name"]: b for b in data}
        assert by_name["switchboard"]["status"] == "ok"
        assert by_name["general"]["status"] == "down"

    async def test_empty_roster(self, app):
        """No butlers discovered yields an empty list."""
        mgr = MagicMock(spec=MCPClientManager)

        app.dependency_overrides.update(
            {
                __import__(
                    "butlers.api.deps", fromlist=["get_mcp_manager"]
                ).get_mcp_manager: lambda: mgr,
                __import__(
                    "butlers.api.deps", fromlist=["get_butler_configs"]
                ).get_butler_configs: lambda: [],
            }
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []

    async def test_response_shape_matches_model(self, app):
        """Verify response data can be parsed as ButlerSummary."""
        configs = [
            ButlerConnectionInfo(name="mybutler", port=8500, description="My test butler"),
        ]
        mock_client = _make_mock_client()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)

        app.dependency_overrides.update(
            {
                __import__(
                    "butlers.api.deps", fromlist=["get_mcp_manager"]
                ).get_mcp_manager: lambda: mgr,
                __import__(
                    "butlers.api.deps", fromlist=["get_butler_configs"]
                ).get_butler_configs: lambda: configs,
            }
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        body = response.json()

        # Verify each item in data can be deserialized as ButlerSummary
        for item in body["data"]:
            summary = ButlerSummary.model_validate(item)
            assert summary.name == "mybutler"
            assert summary.status == "ok"
            assert summary.port == 8500
            assert summary.description == "My test butler"
            assert isinstance(summary.modules, list)

    async def test_all_butlers_down(self, app):
        """When all butlers are unreachable, all show status='down'."""
        configs = _make_configs()

        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("x", cause=ConnectionRefusedError())
        )

        app.dependency_overrides.update(
            {
                __import__(
                    "butlers.api.deps", fromlist=["get_mcp_manager"]
                ).get_mcp_manager: lambda: mgr,
                __import__(
                    "butlers.api.deps", fromlist=["get_butler_configs"]
                ).get_butler_configs: lambda: configs,
            }
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers")

        assert response.status_code == 200
        body = response.json()

        for butler in body["data"]:
            assert butler["status"] == "down"
