"""Tests for Email module tool registration and delegation.

Verifies that:
- Registered tools delegate to the correct internal helpers
- Legacy unprefixed tool names are not registered
- The deprecated email_check_and_route_inbox tool has been removed
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.email import EmailModule

pytestmark = pytest.mark.unit


class TestToolFlows:
    """Verify send and ingest tool behavior."""

    async def test_send_reply_tools_delegate_helpers(self):
        """Send/reply tools invoke shared helpers."""
        mod = EmailModule()
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config={"send_tools": True}, db=None)

        send_mock = AsyncMock(return_value={"status": "sent"})
        reply_mock = AsyncMock(return_value={"status": "sent", "thread_id": "thread-1"})
        mod._send_email = send_mock  # type: ignore[method-assign]
        mod._reply_to_thread = reply_mock  # type: ignore[method-assign]

        user_send = await tools["email_send_message"]("a@example.com", "Hi", "Hello")
        bot_send = await tools["email_send_message"]("b@example.com", "Yo", "Sup")
        user_reply = await tools["email_reply_to_thread"]("a@example.com", "thread-1", "Reply body")
        bot_reply = await tools["email_reply_to_thread"](
            "b@example.com", "thread-2", "Another reply"
        )

        assert user_send == {"status": "sent"}
        assert bot_send == {"status": "sent"}
        assert user_reply["thread_id"] == "thread-1"
        assert bot_reply["thread_id"] == "thread-1"
        assert send_mock.await_args_list[0].args == ("a@example.com", "Hi", "Hello")
        assert send_mock.await_args_list[1].args == ("b@example.com", "Yo", "Sup")
        assert reply_mock.await_args_list[0].args == (
            "a@example.com",
            "thread-1",
            "Reply body",
            None,
        )
        assert reply_mock.await_args_list[1].args == (
            "b@example.com",
            "thread-2",
            "Another reply",
            None,
        )

    async def test_inbox_tools_delegate_helpers(self):
        """Inbox search and read tools delegate to internal helpers."""
        mod = EmailModule()
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        search_mock = AsyncMock(return_value=[{"message_id": "1"}])
        read_mock = AsyncMock(return_value={"message_id": "1", "body": "hello"})
        mod._search_inbox = search_mock  # type: ignore[method-assign]
        mod._read_email = read_mock  # type: ignore[method-assign]

        search_result = await tools["email_search_inbox"]("UNSEEN")
        read_result = await tools["email_read_message"]("1")

        assert search_result == [{"message_id": "1"}]
        assert read_result["message_id"] == "1"
        assert search_mock.await_args_list[0].args == ("UNSEEN",)
        assert read_mock.await_args_list[0].args == ("1",)

    async def test_deprecated_check_and_route_inbox_not_registered(self):
        """The removed email_check_and_route_inbox tool is no longer registered."""
        mod = EmailModule()
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        assert "email_check_and_route_inbox" not in tools

    async def test_legacy_unprefixed_email_tool_names_are_not_callable(self):
        """Legacy unprefixed email names are absent from registration surfaces."""
        mod = EmailModule()
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        legacy_send = "send" + "_email"
        legacy_ingest = "check" + "_and_route_inbox"
        assert legacy_send not in tools
        assert legacy_ingest not in tools
        with pytest.raises(KeyError):
            _ = tools[legacy_send]
