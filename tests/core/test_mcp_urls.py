"""Tests for runtime MCP URL and transport selection helpers."""

from __future__ import annotations

import pytest

from butlers.core.mcp_urls import (
    resolve_runtime_mcp_transport,
    runtime_mcp_transport_from_url,
    runtime_mcp_url,
)

pytestmark = pytest.mark.unit


def test_runtime_mcp_url_uses_streamable_http_path():
    assert runtime_mcp_url(40103) == "http://localhost:40103/mcp"


def test_runtime_mcp_transport_from_url_detects_sse():
    assert runtime_mcp_transport_from_url("http://localhost:40103/sse") == "sse"


def test_runtime_mcp_transport_from_url_defaults_to_http():
    assert runtime_mcp_transport_from_url("http://localhost:40103/mcp") == "http"


def test_resolve_runtime_mcp_transport_prefers_explicit_http_alias():
    assert (
        resolve_runtime_mcp_transport(
            {"url": "http://localhost:40103/sse", "transport": "streamable-http"}
        )
        == "http"
    )


def test_resolve_runtime_mcp_transport_prefers_explicit_sse():
    assert (
        resolve_runtime_mcp_transport({"url": "http://localhost:40103/mcp", "transport": "sse"})
        == "sse"
    )


def test_resolve_runtime_mcp_transport_falls_back_to_url_inference():
    assert resolve_runtime_mcp_transport({"url": "http://localhost:40103/sse"}) == "sse"
