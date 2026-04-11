"""Tests for runtime MCP URL and transport selection helpers."""

from __future__ import annotations

import pytest

from butlers.core.mcp_urls import (
    prefer_streamable_runtime_mcp_url,
    resolve_runtime_mcp_transport,
    runtime_mcp_transport_from_url,
    runtime_mcp_url,
)

pytestmark = pytest.mark.unit


def test_mcp_url_and_transport():
    """URL uses streamable-http path; transport inferred from URL; explicit transport preferred."""
    # URL format
    assert runtime_mcp_url(41103) == "http://localhost:41103/mcp"
    assert (
        prefer_streamable_runtime_mcp_url("http://localhost:41103/sse")
        == "http://localhost:41103/mcp"
    )
    assert (
        prefer_streamable_runtime_mcp_url("http://localhost:41103/mcp")
        == "http://localhost:41103/mcp"
    )

    # Inferred from URL
    assert runtime_mcp_transport_from_url("http://localhost:41103/sse") == "sse"
    assert runtime_mcp_transport_from_url("http://localhost:41103/mcp") == "http"

    # Explicit transport takes priority; streamable-http alias maps to http
    assert (
        resolve_runtime_mcp_transport(
            {"url": "http://localhost:41103/sse", "transport": "streamable-http"}
        )
        == "http"
    )
    assert (
        resolve_runtime_mcp_transport({"url": "http://localhost:41103/mcp", "transport": "sse"})
        == "sse"
    )

    # Falls back to URL inference
    assert resolve_runtime_mcp_transport({"url": "http://localhost:41103/sse"}) == "sse"
