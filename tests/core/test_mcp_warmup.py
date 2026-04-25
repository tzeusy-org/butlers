"""Tests for the MCP endpoint warmup module.

Covers:
- Kill-switch (BUTLERS_MCP_WARMUP_DISABLED=1) skips warmup and returns [].
- Successful warmup: returns success=True, latency_ms set, tool_count derived.
- Failed warmup: returns success=False, error populated; function does not raise.
- Multiple endpoints: all fired concurrently, all results returned.
- warmup_mcp_endpoints: own URL included, extra_urls appended.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestKillSwitch:
    """BUTLERS_MCP_WARMUP_DISABLED disables warmup without errors."""

    async def test_kill_switch_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Warmup returns [] immediately when kill-switch env var is set."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.setenv("BUTLERS_MCP_WARMUP_DISABLED", "1")
        results = await warmup_mcp_endpoints("test-butler", butler_port=9100)
        assert results == []

    async def test_kill_switch_true_string_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'true' is accepted as truthy kill-switch value."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.setenv("BUTLERS_MCP_WARMUP_DISABLED", "true")
        results = await warmup_mcp_endpoints("test-butler", butler_port=9100)
        assert results == []

    async def test_kill_switch_unset_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Warmup proceeds when kill-switch is not set."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        with patch("butlers.core.mcp_warmup._warmup_endpoint", new_callable=AsyncMock) as mock_ep:
            mock_ep.return_value = {
                "url": "http://localhost:9100/mcp",
                "success": True,
                "latency_ms": 12,
                "tool_count": 5,
                "error": None,
            }
            results = await warmup_mcp_endpoints("test-butler", butler_port=9100)

        assert len(results) == 1
        assert results[0]["success"] is True


class TestWarmupEndpoint:
    """_warmup_endpoint fires initialize + tools/list and returns structured result."""

    async def test_success_result_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Success: url, success=True, latency_ms set, tool_count matches tool list."""
        from butlers.core.mcp_warmup import _warmup_endpoint

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        # Simulate a successful HTTP exchange.
        mock_response_init = AsyncMock()
        mock_response_init.raise_for_status = lambda: None
        mock_response_init.headers = {"mcp-session-id": "sess-abc"}

        tools_body = {"result": {"tools": [{"name": "tool1"}, {"name": "tool2"}]}}
        mock_response_tools = AsyncMock()
        mock_response_tools.raise_for_status = lambda: None
        mock_response_tools.headers = {}
        mock_response_tools.json = lambda: tools_body

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[mock_response_init, mock_response_tools])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.core.mcp_warmup.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await _warmup_endpoint("http://localhost:9100/mcp", butler_name="test-butler")

        assert result["url"] == "http://localhost:9100/mcp"
        assert result["success"] is True
        assert isinstance(result["latency_ms"], int)
        assert result["latency_ms"] >= 0
        assert result["tool_count"] == 2
        assert result["error"] is None

    async def test_failure_does_not_raise(self) -> None:
        """Endpoint failure returns success=False with error message; never raises."""
        from butlers.core.mcp_warmup import _warmup_endpoint

        with patch("butlers.core.mcp_warmup.httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = Exception("connection refused")
            # Must not raise
            result = await _warmup_endpoint("http://localhost:9100/mcp", butler_name="test-butler")

        assert result["success"] is False
        assert "connection refused" in result["error"]
        assert result["latency_ms"] is None
        assert result["tool_count"] is None

    async def test_http_error_captured(self) -> None:
        """HTTP error status is captured as failure; never raises."""
        from unittest.mock import MagicMock

        import httpx

        from butlers.core.mcp_warmup import _warmup_endpoint

        # raise_for_status() is a sync call, so use MagicMock for the response
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.core.mcp_warmup.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await _warmup_endpoint("http://localhost:9100/mcp", butler_name="test-butler")

        assert result["success"] is False
        assert result["error"] is not None


class TestWarmupMcpEndpoints:
    """warmup_mcp_endpoints builds the correct URL set and gathers results."""

    async def test_own_url_always_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Butler's own endpoint URL is always in the set of warmed URLs."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        called_urls: list[str] = []

        async def fake_warmup(url: str, *, butler_name: str) -> dict[str, Any]:
            called_urls.append(url)
            return {"url": url, "success": True, "latency_ms": 5, "tool_count": 0, "error": None}

        with patch("butlers.core.mcp_warmup._warmup_endpoint", side_effect=fake_warmup):
            results = await warmup_mcp_endpoints("my-butler", butler_port=8500)

        assert "http://localhost:8500/mcp" in called_urls
        assert len(results) == 1

    async def test_extra_urls_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """extra_urls are warmed in addition to the butler's own endpoint."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        called_urls: list[str] = []

        async def fake_warmup(url: str, *, butler_name: str) -> dict[str, Any]:
            called_urls.append(url)
            return {"url": url, "success": True, "latency_ms": 5, "tool_count": 0, "error": None}

        extra = ["http://localhost:41200/mcp"]
        with patch("butlers.core.mcp_warmup._warmup_endpoint", side_effect=fake_warmup):
            results = await warmup_mcp_endpoints("my-butler", butler_port=8500, extra_urls=extra)

        assert "http://localhost:8500/mcp" in called_urls
        assert "http://localhost:41200/mcp" in called_urls
        assert len(results) == 2

    async def test_partial_failure_returns_all_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When one endpoint fails, results for all endpoints are still returned."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        call_count = 0

        async def fake_warmup(url: str, *, butler_name: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "url": url,
                    "success": False,
                    "latency_ms": None,
                    "tool_count": None,
                    "error": "timeout",
                }
            return {"url": url, "success": True, "latency_ms": 10, "tool_count": 3, "error": None}

        with patch("butlers.core.mcp_warmup._warmup_endpoint", side_effect=fake_warmup):
            results = await warmup_mcp_endpoints(
                "my-butler",
                butler_port=8500,
                extra_urls=["http://localhost:41200/mcp"],
            )

        assert len(results) == 2
        statuses = {r["success"] for r in results}
        assert statuses == {True, False}, "Expected one success and one failure"

    async def test_no_extra_urls_defaults_to_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """extra_urls defaults to empty; only own endpoint is warmed."""
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        called_urls: list[str] = []

        async def fake_warmup(url: str, *, butler_name: str) -> dict[str, Any]:
            called_urls.append(url)
            return {"url": url, "success": True, "latency_ms": 1, "tool_count": 0, "error": None}

        with patch("butlers.core.mcp_warmup._warmup_endpoint", side_effect=fake_warmup):
            results = await warmup_mcp_endpoints("solo", butler_port=9999)

        assert called_urls == ["http://localhost:9999/mcp"]
        assert len(results) == 1


class TestWarmupMcpUrls:
    """warmup_mcp_urls operates on explicit URL lists."""

    async def test_dedupes_explicit_urls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Duplicate URLs are only warmed once."""
        from butlers.core.mcp_warmup import warmup_mcp_urls

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        called_urls: list[str] = []

        async def fake_warmup(url: str, *, butler_name: str) -> dict[str, Any]:
            called_urls.append(url)
            return {"url": url, "success": True, "latency_ms": 1, "tool_count": 0, "error": None}

        with patch("butlers.core.mcp_warmup._warmup_endpoint", side_effect=fake_warmup):
            results = await warmup_mcp_urls(
                "dedupe-test",
                [
                    "http://localhost:8500/mcp",
                    "http://localhost:8500/mcp",
                    "http://localhost:8600/mcp",
                ],
            )

        assert called_urls == ["http://localhost:8500/mcp", "http://localhost:8600/mcp"]
        assert len(results) == 2
