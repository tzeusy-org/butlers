"""Unit tests for Switchboard module MCP tool registration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock


class _StubMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


async def test_registered_deliver_authenticates_only_material_recovery(monkeypatch, caplog):
    from butlers.modules._roster_switchboard import tools

    pool = object()
    delivery_module = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    actual_deliver = delivery_module.deliver

    async def delivery_boundary(*args, **kwargs):
        if kwargs["notify_request"].get("recovery") is not None:
            return await actual_deliver(*args, **kwargs)
        return {"status": "sent"}

    deliver = AsyncMock(side_effect=delivery_boundary)
    authenticate = AsyncMock(wraps=tools.verify_switchboard_recovery)
    monkeypatch.setattr(delivery_module, "deliver", deliver)
    monkeypatch.setattr(tools, "verify_switchboard_recovery", authenticate)
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
    assert deliver.await_args.kwargs["verified_context"] is None

    material = {**ordinary, "recovery": {"operation": "handoff"}}
    result = await mcp.tools["deliver"](source_butler="relationship", notify_request=material)

    authenticate.assert_awaited_once()
    assert deliver.await_args.kwargs["verified_context"] is None
    assert result["error"] == "Approval recovery authority rejected."

    # The real registration proxy logs before the business handler. Exercise
    # its actual FunctionTool, so an inner-only guard cannot satisfy this gate.
    from fastmcp import Client, FastMCP

    from butlers.mcp_wrappers import _SpanWrappingMCP

    caplog.set_level("INFO", logger="butlers.mcp_wrappers")
    registered = FastMCP("registered-approval-boundary")
    tools.register_tools(
        _SpanWrappingMCP(registered, "switchboard", module_name="switchboard"),
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["routing"]),
    )
    async with Client(registered) as client:
        positive = (
            await client.call_tool(
                "deliver",
                {
                    "source_butler": "health",
                    "notify_request": ordinary,
                },
            )
        ).data
        assert positive == {"status": "sent"}
        positive_logs = len(caplog.records)
        assert any(record.name == "butlers.mcp_wrappers" for record in caplog.records)
        calls_before_refusal = deliver.await_count
        refused = (
            await client.call_tool(
                "deliver",
                {
                    "source_butler": "relationship",
                    "notify_request": material,
                },
            )
        ).data
        assert refused["error"] == "Approval recovery authority rejected."
        assert deliver.await_count == calls_before_refusal
        assert len(caplog.records) == positive_logs

    # A post-registration guard must never introduce a group-disabled tool.
    disabled = FastMCP("routing-disabled")
    tools.register_tools(
        _SpanWrappingMCP(disabled, "switchboard", module_name="switchboard"),
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["lifecycle"]),
    )
    assert "deliver" not in {tool.name for tool in await disabled.list_tools()}
