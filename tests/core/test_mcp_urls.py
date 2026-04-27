"""Tests for runtime MCP URL and transport selection helpers."""

from __future__ import annotations

import pytest

from butlers.core.mcp_urls import (
    canonical_runtime_mcp_url,
    prefer_ipv4_loopback_url,
    resolve_runtime_mcp_transport,
    runtime_mcp_transport_from_url,
    runtime_mcp_url,
)

pytestmark = pytest.mark.unit


def test_mcp_url_and_transport():
    """URL uses streamable-http path; transport inferred from URL; explicit transport preferred."""
    # URL format
    assert runtime_mcp_url(41103) == "http://localhost:41103/mcp"
    assert canonical_runtime_mcp_url("http://localhost:41103/sse") == "http://localhost:41103/mcp"
    assert canonical_runtime_mcp_url("http://localhost:41103/sse/") == "http://localhost:41103/mcp"
    assert (
        canonical_runtime_mcp_url("http://localhost:41103/mcp/sse")
        == "http://localhost:41103/mcp/sse"
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


def test_prefer_ipv4_loopback_url():
    """Bare localhost loopback is rewritten; explicit addresses are preserved."""
    assert prefer_ipv4_loopback_url("http://localhost:41103/mcp") == "http://127.0.0.1:41103/mcp"
    assert (
        prefer_ipv4_loopback_url("http://localhost:41103/mcp?runtime_session_id=sess-1")
        == "http://127.0.0.1:41103/mcp?runtime_session_id=sess-1"
    )
    assert prefer_ipv4_loopback_url("http://127.0.0.1:41103/mcp") == "http://127.0.0.1:41103/mcp"
    assert prefer_ipv4_loopback_url("http://[::1]:41103/mcp") == "http://[::1]:41103/mcp"
