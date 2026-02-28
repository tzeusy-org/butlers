"""Tests for CachedMCPClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.mcp_client import CachedMCPClient

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
