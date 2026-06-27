"""Contract tests: Channel Egress Ownership Enforcement (core-modules spec).

The core-modules spec mandates that non-messenger butlers cannot register
channel-egress tools matching ``<channel>_(send_message|reply_to_message|
send_email|reply_to_thread)``. Attempting to do so raises a
``ChannelEgressOwnershipError`` at startup. The messenger butler — the
designated owner of outbound channel egress — must still be able to register
its egress tools.

These tests drive the *real* registration path: the ``_SpanWrappingMCP`` proxy
the daemon uses, real module ``register_tools`` over a real ``FastMCP``, and the
daemon's own ``_register_module_tools`` method.

[bu-4dxtw]
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from butlers.exceptions import ChannelEgressOwnershipError, is_channel_egress_tool
from butlers.mcp_wrappers import _SpanWrappingMCP
from butlers.modules.email import EmailConfig, EmailModule
from butlers.modules.telegram import TelegramModule

pytestmark = pytest.mark.contract


# --- Pattern matching -------------------------------------------------------


class TestEgressToolPattern:
    @pytest.mark.parametrize(
        "name",
        [
            "telegram_send_message",
            "telegram_reply_to_message",
            "email_send_message",
            "email_send_email",
            "email_reply_to_thread",
            "whatsapp_send_message",
            "whatsapp_reply_to_message",
        ],
    )
    def test_egress_tools_match(self, name: str) -> None:
        assert is_channel_egress_tool(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "telegram_react_to_message",
            "email_search_inbox",
            "state_get",
            "notify",
            "send_message",  # no channel prefix
            "memory_entity_resolve",
        ],
    )
    def test_non_egress_tools_do_not_match(self, name: str) -> None:
        assert is_channel_egress_tool(name) is False


# --- Real proxy registration path -------------------------------------------


class TestProxyEnforcement:
    """The _SpanWrappingMCP proxy is the chokepoint every module tool flows
    through during daemon startup."""

    async def test_non_messenger_telegram_egress_rejected(self) -> None:
        mcp = FastMCP("test-non-messenger")
        wrapped = _SpanWrappingMCP(mcp, "general", module_name="telegram", is_messenger=False)

        with pytest.raises(ChannelEgressOwnershipError) as exc_info:
            await TelegramModule().register_tools(wrapped, {}, db=None, butler_name="general")

        assert "telegram_send_message" in str(exc_info.value)
        assert exc_info.value.butler_name == "general"

    async def test_non_messenger_email_egress_rejected(self) -> None:
        mcp = FastMCP("test-non-messenger-email")
        wrapped = _SpanWrappingMCP(mcp, "finance", module_name="email", is_messenger=False)

        with pytest.raises(ChannelEgressOwnershipError):
            await EmailModule().register_tools(
                wrapped, EmailConfig(send_tools=True), db=None, butler_name="finance"
            )

    async def test_messenger_can_register_telegram_egress(self) -> None:
        mcp = FastMCP("test-messenger")
        wrapped = _SpanWrappingMCP(mcp, "messenger", module_name="telegram", is_messenger=True)

        # Must NOT raise.
        await TelegramModule().register_tools(wrapped, {}, db=None, butler_name="messenger")

        assert "telegram_send_message" in wrapped._registered_tool_names
        assert "telegram_reply_to_message" in wrapped._registered_tool_names
        tool_names = {t.name for t in await mcp.list_tools()}
        assert "telegram_send_message" in tool_names

    async def test_messenger_can_register_email_egress(self) -> None:
        mcp = FastMCP("test-messenger-email")
        wrapped = _SpanWrappingMCP(mcp, "messenger", module_name="email", is_messenger=True)

        await EmailModule().register_tools(
            wrapped, EmailConfig(send_tools=True), db=None, butler_name="messenger"
        )

        assert "email_send_message" in wrapped._registered_tool_names
        assert "email_reply_to_thread" in wrapped._registered_tool_names

    async def test_positional_name_egress_rejected(self) -> None:
        """A positionally-named egress tool must not bypass the guard.

        FastMCP allows overriding the tool name via the first positional arg
        (``@mcp.tool("telegram_send_message")``). If the proxy only inspected
        ``name=`` kwargs it would resolve to the (non-egress) function name and
        fail open. The guard must catch the declared positional name."""
        mcp = FastMCP("test-positional-egress")
        wrapped = _SpanWrappingMCP(mcp, "general", module_name="custom", is_messenger=False)

        with pytest.raises(ChannelEgressOwnershipError) as exc_info:

            @wrapped.tool("telegram_send_message")
            async def innocuously_named() -> str:  # pragma: no cover - guard raises first
                return "sent"

        assert exc_info.value.tool_name == "telegram_send_message"

    async def test_messenger_positional_name_egress_allowed(self) -> None:
        """The messenger may register a positionally-named egress tool."""
        mcp = FastMCP("test-positional-egress-messenger")
        wrapped = _SpanWrappingMCP(mcp, "messenger", module_name="custom", is_messenger=True)

        @wrapped.tool("telegram_send_message")
        async def innocuously_named() -> str:
            return "sent"

        assert "telegram_send_message" in wrapped._registered_tool_names
        tool_names = {t.name for t in await mcp.list_tools()}
        assert "telegram_send_message" in tool_names

    async def test_non_messenger_non_egress_tools_still_register(self) -> None:
        """A non-messenger butler can still register its non-egress tools.

        The telegram react tool and email search tool are NOT egress — they must
        register fine even for non-messenger butlers (these are read/reaction
        surfaces, not outbound send/reply)."""
        mcp = FastMCP("test-email-search")
        wrapped = _SpanWrappingMCP(mcp, "finance", module_name="email", is_messenger=False)

        # send_tools=False => only email_search_inbox is registered (non-egress).
        await EmailModule().register_tools(
            wrapped, EmailConfig(send_tools=False), db=None, butler_name="finance"
        )

        assert "email_search_inbox" in wrapped._registered_tool_names


# --- Real daemon registration path ------------------------------------------


def _make_daemon(butler_name: str, modules) -> object:
    """Build a ButlerDaemon wired with just enough state to exercise
    _register_module_tools without a real config/DB."""
    from butlers.daemon import ButlerDaemon

    daemon = ButlerDaemon(butler_name=butler_name)
    daemon.config = SimpleNamespace(name=butler_name)
    daemon.mcp = FastMCP(f"daemon-{butler_name}")
    daemon.db = None
    daemon._modules = list(modules)
    return daemon


class TestDaemonEnforcement:
    async def test_daemon_rejects_non_messenger_egress(self) -> None:
        daemon = _make_daemon("general", [TelegramModule()])

        with pytest.raises(ChannelEgressOwnershipError):
            await daemon._register_module_tools()

    async def test_daemon_allows_messenger_egress(self) -> None:
        daemon = _make_daemon("messenger", [TelegramModule()])

        await daemon._register_module_tools()  # must not raise

        assert daemon._tool_module_map.get("telegram_send_message") == "telegram"
