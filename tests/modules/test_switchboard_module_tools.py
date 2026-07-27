"""Unit tests for Switchboard module MCP tool registration."""

from __future__ import annotations

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


async def test_registered_post_mail_delegates_to_routing_function(monkeypatch):
    from butlers.modules._roster_switchboard.tools import register_tools
    from butlers.tools.switchboard import routing

    pool = object()
    post_mail = AsyncMock(return_value={"message_id": "message-1"})
    monkeypatch.setattr(routing, "post_mail", post_mail)
    mcp = _StubMCP()

    register_tools(
        mcp,
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["routing"]),
    )

    result = await mcp.tools["post_mail"](
        target_butler="relationship",
        sender="chronicler",
        sender_channel="mcp",
        body="Recurring companion",
        metadata={"kind": "enrichment_proposal"},
    )

    assert result == {"message_id": "message-1"}
    post_mail.assert_awaited_once_with(
        pool,
        "relationship",
        "chronicler",
        "mcp",
        "Recurring companion",
        subject=None,
        priority=None,
        metadata={"kind": "enrichment_proposal"},
    )
