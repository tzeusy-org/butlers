"""Unit tests for Switchboard module MCP tool registration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock


class _StubMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


async def test_registered_deliver_authenticates_only_material_recovery(monkeypatch):
    from butlers.modules._roster_switchboard import tools

    pool = object()
    deliver = AsyncMock(return_value={"status": "sent"})
    authenticate = MagicMock(return_value="relationship")
    delivery_module = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    monkeypatch.setattr(delivery_module, "deliver", deliver)
    monkeypatch.setattr(tools, "authenticated_daemon_name", authenticate)
    monkeypatch.setattr(tools, "get_access_token", MagicMock(return_value="token"))
    mcp = _StubMCP()

    tools.register_tools(
        mcp,
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["routing"]),
    )

    ordinary = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "ordinary",
            "recipient": "owner",
        },
        "recovery": None,
    }
    await mcp.tools["deliver"](source_butler="health", notify_request=ordinary)

    authenticate.assert_not_called()
    assert deliver.await_args.kwargs["trusted_source"] is None

    material = {**ordinary, "recovery": {"operation": "handoff"}}
    await mcp.tools["deliver"](source_butler="relationship", notify_request=material)

    authenticate.assert_called_once_with("token", required_scope="approval-recovery:source")
    assert deliver.await_args.kwargs["trusted_source"] == "relationship"
