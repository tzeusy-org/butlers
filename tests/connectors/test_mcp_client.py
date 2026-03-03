"""Tests for CachedMCPClient and wait_for_switchboard_ready."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.mcp_client import (
    CachedMCPClient,
    _switchboard_health_url,
    wait_for_switchboard_ready,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def client() -> CachedMCPClient:
    """Create a CachedMCPClient for testing."""
    return CachedMCPClient("http://localhost:40100/sse", client_name="test-connector")


class TestCachedMCPClient:
    """Tests for CachedMCPClient."""

    def test_initial_state(self, client: CachedMCPClient) -> None:
        """Test initial state is disconnected."""
        assert not client.is_connected()

    async def test_call_tool_connects_lazily(self, client: CachedMCPClient) -> None:
        """Test that call_tool connects on first use."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = {"request_id": "abc", "status": "accepted", "duplicate": False}

        mock_mcp_client = AsyncMock()
        mock_mcp_client.call_tool = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_mcp_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.is_connected = MagicMock(return_value=True)

        with patch("butlers.connectors.mcp_client.MCPClient", return_value=mock_ctx):
            result = await client.call_tool("ingest", {"schema_version": "ingest.v1"})

        assert result == {"request_id": "abc", "status": "accepted", "duplicate": False}

    async def test_call_tool_retries_on_failure(self, client: CachedMCPClient) -> None:
        """Test that call_tool retries once on connection failure."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = {"status": "accepted"}

        call_count = 0

        async def failing_then_success(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("first attempt fails")
            return mock_result

        mock_mcp_client = AsyncMock()
        mock_mcp_client.call_tool = AsyncMock(side_effect=failing_then_success)
        mock_mcp_client.is_connected = MagicMock(return_value=True)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_mcp_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.is_connected = MagicMock(return_value=True)

        with patch("butlers.connectors.mcp_client.MCPClient", return_value=mock_ctx):
            result = await client.call_tool("ingest", {})

        assert result == {"status": "accepted"}
        assert call_count == 2

    async def test_call_tool_raises_on_mcp_error(self, client: CachedMCPClient) -> None:
        """Test that MCP error results raise RuntimeError."""
        mock_result = MagicMock()
        mock_result.is_error = True
        mock_result.content = [MagicMock(text="Validation failed")]

        mock_mcp_client = AsyncMock()
        mock_mcp_client.call_tool = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_mcp_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.is_connected = MagicMock(return_value=True)

        with patch("butlers.connectors.mcp_client.MCPClient", return_value=mock_ctx):
            with pytest.raises(RuntimeError, match="Validation failed"):
                await client.call_tool("ingest", {})

    async def test_aclose(self, client: CachedMCPClient) -> None:
        """Test clean shutdown."""
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.is_connected = MagicMock(return_value=True)

        with patch("butlers.connectors.mcp_client.MCPClient", return_value=mock_ctx):
            # Connect
            client._client_ctx = mock_ctx
            client._client = MagicMock()

            await client.aclose()

        assert client._client_ctx is None
        assert client._client is None

    def test_parse_result_json_text_block(self) -> None:
        """Test parsing JSON from text block result."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.data = None
        del mock_result.data  # Remove .data so it falls through to text blocks

        mock_block = MagicMock()
        mock_block.text = '{"request_id": "123", "status": "accepted"}'
        mock_result.__iter__ = MagicMock(return_value=iter([mock_block]))

        result = CachedMCPClient._parse_result(mock_result, "ingest")
        assert result == {"request_id": "123", "status": "accepted"}


# -----------------------------------------------------------------------------
# Switchboard readiness probe tests (butlers-p4qf)
# -----------------------------------------------------------------------------


class TestSwitchboardHealthUrl:
    """Tests for _switchboard_health_url URL derivation."""

    def test_derives_health_from_sse_url(self) -> None:
        """SSE endpoint URL is mapped to /health path on the same host."""
        assert _switchboard_health_url("http://localhost:40100/sse") == (
            "http://localhost:40100/health"
        )

    def test_preserves_host_and_port(self) -> None:
        """Non-default ports are preserved in the derived health URL."""
        assert _switchboard_health_url("http://192.168.1.10:9999/sse") == (
            "http://192.168.1.10:9999/health"
        )

    def test_handles_trailing_path_segments(self) -> None:
        """Only scheme + netloc is used; the path is replaced with /health."""
        assert _switchboard_health_url("http://localhost:40100/mcp/sse") == (
            "http://localhost:40100/health"
        )


class TestWaitForSwitchboardReady:
    """Tests for wait_for_switchboard_ready exponential-backoff probe."""

    async def test_returns_immediately_when_healthy_on_first_attempt(self) -> None:
        """Probe returns without sleeping when Switchboard responds 200 OK immediately."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        sleep_calls: list[float] = []

        with (
            patch("butlers.connectors.mcp_client.httpx.AsyncClient") as mock_client_cls,
            patch(
                "butlers.connectors.mcp_client.asyncio.sleep",
                side_effect=lambda s: sleep_calls.append(s),
            ),
        ):
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_resp))
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await wait_for_switchboard_ready(
                "http://localhost:40100/sse",
                max_attempts=5,
                initial_delay_s=1.0,
                max_delay_s=30.0,
            )

        assert sleep_calls == [], "No sleep should occur when healthy on the first probe"

    async def test_retries_until_healthy(self) -> None:
        """Probe retries with backoff and returns once the health check succeeds."""
        responses = [
            MagicMock(status_code=503),
            MagicMock(status_code=503),
            MagicMock(status_code=200),
        ]
        response_iter = iter(responses)

        sleep_calls: list[float] = []

        async def fake_get(url: str) -> MagicMock:
            return next(response_iter)

        async def fake_sleep(s: float) -> None:
            sleep_calls.append(s)

        with (
            patch("butlers.connectors.mcp_client.httpx.AsyncClient") as mock_client_cls,
            patch("butlers.connectors.mcp_client.asyncio.sleep", side_effect=fake_sleep),
            patch("butlers.connectors.mcp_client.random.random", return_value=0.5),
        ):
            mock_http = MagicMock()
            mock_http.get = AsyncMock(side_effect=fake_get)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await wait_for_switchboard_ready(
                "http://localhost:40100/sse",
                max_attempts=10,
                initial_delay_s=1.0,
                max_delay_s=30.0,
            )

        # Two failed attempts → two sleeps before the third attempt succeeds
        assert len(sleep_calls) == 2
        # With random=0.5 the jitter term is 0, so sleep_s == capped_delay
        assert sleep_calls[0] == pytest.approx(1.0, rel=1e-9)  # 1.0 * 2^0
        assert sleep_calls[1] == pytest.approx(2.0, rel=1e-9)  # 1.0 * 2^1

    async def test_retries_on_connection_error(self) -> None:
        """Probe handles network errors (connection refused) and retries."""
        call_count = 0

        async def fake_get(url: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("Connection refused")
            return MagicMock(status_code=200)

        with (
            patch("butlers.connectors.mcp_client.httpx.AsyncClient") as mock_client_cls,
            patch(
                "butlers.connectors.mcp_client.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            patch("butlers.connectors.mcp_client.random.random", return_value=0.5),
        ):
            mock_http = MagicMock()
            mock_http.get = AsyncMock(side_effect=fake_get)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await wait_for_switchboard_ready(
                "http://localhost:40100/sse",
                max_attempts=5,
                initial_delay_s=0.1,
                max_delay_s=10.0,
            )

        assert call_count == 3

    async def test_raises_timeout_error_after_max_attempts(self) -> None:
        """Probe raises TimeoutError if Switchboard never becomes healthy."""

        async def always_fail(url: str) -> MagicMock:
            return MagicMock(status_code=503)

        with (
            patch("butlers.connectors.mcp_client.httpx.AsyncClient") as mock_client_cls,
            patch(
                "butlers.connectors.mcp_client.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            patch("butlers.connectors.mcp_client.random.random", return_value=0.5),
        ):
            mock_http = MagicMock()
            mock_http.get = AsyncMock(side_effect=always_fail)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(TimeoutError, match="did not become healthy"):
                await wait_for_switchboard_ready(
                    "http://localhost:40100/sse",
                    max_attempts=3,
                    initial_delay_s=0.1,
                    max_delay_s=10.0,
                )
