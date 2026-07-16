"""Unit tests for the roster Switchboard module tool registrations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.modules._roster_switchboard import SwitchboardModule, SwitchboardModuleConfig

pytestmark = pytest.mark.unit


class _FakeMCP:
    """Capture decorated handlers by their MCP-visible function name."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **kwargs: Any):
        def decorator(fn):
            self.tools[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator


async def test_post_mail_registration_delegates_to_post_mail_function(monkeypatch) -> None:
    """The registered tool calls the routing package's post_mail export."""
    from butlers.tools.switchboard import routing

    post_mail = AsyncMock(return_value={"message_id": "message-1"})
    monkeypatch.setattr(routing, "post_mail", post_mail)

    pool = object()
    mcp = _FakeMCP()
    module = SwitchboardModule()
    await module.register_tools(
        mcp,
        SwitchboardModuleConfig(groups=["routing"]),
        SimpleNamespace(pool=pool),
        "switchboard",
    )

    result = await mcp.tools["post_mail"](
        target_butler="travel",
        sender="chronicler",
        sender_channel="internal",
        body="A routed message",
        subject="Travel note",
        priority=2,
        metadata={"source": "writeback"},
    )

    assert result == {"message_id": "message-1"}
    post_mail.assert_awaited_once_with(
        pool,
        "travel",
        "chronicler",
        "internal",
        "A routed message",
        subject="Travel note",
        priority=2,
        metadata={"source": "writeback"},
    )
