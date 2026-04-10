"""Tests for connector MCP client URL normalization."""

from __future__ import annotations

from butlers.connectors.mcp_client import CachedMCPClient


def test_cached_mcp_client_prefers_canonical_runtime_url() -> None:
    """Legacy runtime /sse URLs should be normalized to the canonical /mcp path."""
    client = CachedMCPClient("http://localhost:41100/sse", client_name="test-connector")
    assert client._endpoint_url == "http://localhost:41100/mcp"

