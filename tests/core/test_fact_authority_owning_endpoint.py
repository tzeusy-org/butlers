"""Owning-butler endpoint selection for Switchboard's identity seam (bu-n7zmbp.20).

Under the receiver-derived route cutover the legacy ``butler_registry`` liveness
projection is no longer refreshed, so a quiet Relationship butler reads as
stale there. Identity resolution must then admit the owner exactly as route()
does, through the receiver resolver, not through the legacy projection.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from butlers.core import fact_authority
from butlers.tools.switchboard.registry import registry as registry_mod

route_mod = importlib.import_module("butlers.tools.switchboard.routing.route")

_RECEIVER_URL = "http://receiver.test:41101/mcp"
_LEGACY_URL = "http://legacy.test:41101/mcp"


@pytest.fixture
def resolvers(monkeypatch):
    calls: list[str] = []

    async def receiver(pool, target, *, required_capability, route_contract_version):
        calls.append(f"receiver:{target}")
        return SimpleNamespace(state="ready", endpoint_url=_RECEIVER_URL), "ready", False

    async def legacy(pool, target, **kwargs):
        calls.append(f"legacy:{target}")
        return {"endpoint_url": _LEGACY_URL}, None

    monkeypatch.setattr(route_mod, "resolve_receiver_route_target", receiver)
    monkeypatch.setattr(registry_mod, "resolve_routing_target", legacy)
    return calls


async def test_cutover_uses_receiver_resolver(monkeypatch, resolvers):
    monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")
    url = await fact_authority._owning_endpoint(MagicMock(), "relationship")
    assert url == _RECEIVER_URL
    assert resolvers == ["receiver:relationship"]


async def test_cutover_refusal_is_unavailable_not_legacy_fallback(monkeypatch, resolvers):
    monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")

    async def refused(pool, target, *, required_capability, route_contract_version):
        resolvers.append(f"receiver:{target}")
        return None, "target_unavailable", True

    monkeypatch.setattr(route_mod, "resolve_receiver_route_target", refused)
    assert await fact_authority._owning_endpoint(MagicMock(), "relationship") is None
    assert resolvers == ["receiver:relationship"]


async def test_without_cutover_uses_legacy_projection(monkeypatch, resolvers):
    monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)
    url = await fact_authority._owning_endpoint(MagicMock(), "relationship")
    assert url == _LEGACY_URL
    assert resolvers == ["legacy:relationship"]


async def test_resolve_identities_calls_receiver_endpoint(monkeypatch, resolvers):
    monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")
    seen: list[str] = []

    class _Client:
        def __init__(self, endpoint, timeout):
            seen.append(endpoint)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call_tool(self, name, args):
            assert name == "identity_resolve_channels"
            return SimpleNamespace(data={value: None for value in args["channel_values"]})

    import fastmcp

    monkeypatch.setattr(fastmcp, "Client", _Client)
    registry = fact_authority.FactSourceContextRegistry(MagicMock())
    assert await registry.resolve_identities("telegram", ["42"]) == {"42": None}
    assert seen == [_RECEIVER_URL]
    assert resolvers == ["receiver:relationship"]
