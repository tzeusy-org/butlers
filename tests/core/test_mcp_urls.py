"""Tests for runtime MCP URL and transport selection helpers."""

from __future__ import annotations

import pytest

from butlers.core.mcp_urls import (
    canonical_runtime_mcp_url,
    prefer_ipv4_loopback_url,
    resolve_cross_container_mcp_url,
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

    # IPv4 loopback canonicalization for IPv4-only MCP listeners
    assert prefer_ipv4_loopback_url("http://localhost:41103/mcp") == "http://127.0.0.1:41103/mcp"
    assert (
        prefer_ipv4_loopback_url("http://localhost:41103/mcp?runtime_session_id=sess-1")
        == "http://127.0.0.1:41103/mcp?runtime_session_id=sess-1"
    )
    assert prefer_ipv4_loopback_url("http://127.0.0.1:41103/mcp") == "http://127.0.0.1:41103/mcp"
    # Non-localhost remote hosts and explicit IP literals are preserved as-is
    assert prefer_ipv4_loopback_url("http://example.com:8080/mcp") == "http://example.com:8080/mcp"
    assert prefer_ipv4_loopback_url("http://[::1]:41103/mcp") == "http://[::1]:41103/mcp"
    # Userinfo with percent-encoded special chars is preserved verbatim,
    # not double-decoded via parsed.username / parsed.password.
    assert (
        prefer_ipv4_loopback_url("http://us%40er:p%3Ass@localhost:41103/mcp")
        == "http://us%40er:p%3Ass@127.0.0.1:41103/mcp"
    )

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


def test_resolve_cross_container_mcp_url_rewrites_localhost_when_butlers_host_set(monkeypatch):
    """bu-hmdqz.3: dashboard-api sets BUTLERS_HOST=butlers-up so it can reach
    butler MCP servers registered as http://localhost:<port> from
    butlers-up's own point of view."""
    monkeypatch.setenv("BUTLERS_HOST", "butlers-up")
    assert (
        resolve_cross_container_mcp_url("http://localhost:41104/mcp")
        == "http://butlers-up:41104/mcp"
    )
    # Preserves query strings and any userinfo.
    assert (
        resolve_cross_container_mcp_url("http://localhost:41104/mcp?x=1")
        == "http://butlers-up:41104/mcp?x=1"
    )


def test_resolve_cross_container_mcp_url_noop_when_butlers_host_unset(monkeypatch):
    """Inside butlers-up itself (or any test env) BUTLERS_HOST is unset, so
    every daemon-to-daemon call keeps its bare localhost URL unchanged."""
    monkeypatch.delenv("BUTLERS_HOST", raising=False)
    assert (
        resolve_cross_container_mcp_url("http://localhost:41104/mcp")
        == "http://localhost:41104/mcp"
    )


def test_resolve_cross_container_mcp_url_noop_for_non_localhost_host(monkeypatch):
    """Never touches a URL whose host isn't the exact literal 'localhost' --
    e.g. an already-remote or already-rewritten endpoint."""
    monkeypatch.setenv("BUTLERS_HOST", "butlers-up")
    assert (
        resolve_cross_container_mcp_url("http://example.com:8080/mcp")
        == "http://example.com:8080/mcp"
    )
    assert (
        resolve_cross_container_mcp_url("http://127.0.0.1:41104/mcp")
        == "http://127.0.0.1:41104/mcp"
    )


def test_resolve_cross_container_mcp_url_noop_when_butlers_host_is_localhost(monkeypatch):
    """An explicit BUTLERS_HOST=localhost (the ButlerConnectionInfo.sse_url
    default) is treated the same as unset -- never a no-op rewrite to itself."""
    monkeypatch.setenv("BUTLERS_HOST", "localhost")
    assert (
        resolve_cross_container_mcp_url("http://localhost:41104/mcp")
        == "http://localhost:41104/mcp"
    )


def test_prefer_ipv4_loopback_url():
    """Bare localhost loopback is rewritten; explicit addresses are preserved."""
    assert prefer_ipv4_loopback_url("http://localhost:41103/mcp") == "http://127.0.0.1:41103/mcp"
    assert (
        prefer_ipv4_loopback_url("http://localhost:41103/mcp?runtime_session_id=sess-1")
        == "http://127.0.0.1:41103/mcp?runtime_session_id=sess-1"
    )
    assert prefer_ipv4_loopback_url("http://127.0.0.1:41103/mcp") == "http://127.0.0.1:41103/mcp"
    assert prefer_ipv4_loopback_url("http://[::1]:41103/mcp") == "http://[::1]:41103/mcp"
