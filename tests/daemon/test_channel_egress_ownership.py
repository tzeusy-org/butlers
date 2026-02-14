"""Tests for Messenger-only channel egress ownership enforcement.

Validates that:
1. Non-messenger butlers have channel send/reply tools stripped at startup.
2. The Messenger butler retains all channel send/reply tools.
3. Channel input (ingress) tools are not affected by the egress ownership filter.
4. The egress pattern matcher correctly classifies tool names.
5. Non-messenger route.execute routes to spawner, not channel adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from butlers.config import ButlerConfig
from butlers.daemon import (
    ChannelEgressOwnershipError,
    _is_channel_egress_tool,
)
from butlers.modules.base import Module, ToolIODescriptor

pytestmark = pytest.mark.unit

_TEST_UUID7 = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoopConfig(BaseModel):
    """Minimal config schema for test modules."""


class _ChannelEgressModule(Module):
    """Module that declares channel send/reply output tools (egress)."""

    def __init__(self) -> None:
        self._registered_tools: list[str] = []

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _NoopConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        for tool_name in (
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
            "user_telegram_get_updates",
            "bot_telegram_get_updates",
        ):

            async def _noop(**kwargs: Any) -> dict:
                return {}

            _noop.__name__ = tool_name
            result = mcp.tool()(_noop)
            # Track which tools were actually registered (not filtered).
            if result is not _noop:
                self._registered_tools.append(tool_name)

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(
                name="user_telegram_send_message",
                approval_default="always",
            ),
            ToolIODescriptor(
                name="user_telegram_reply_to_message",
                approval_default="always",
            ),
        )

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(
                name="bot_telegram_send_message",
                approval_default="conditional",
            ),
            ToolIODescriptor(
                name="bot_telegram_reply_to_message",
                approval_default="conditional",
            ),
        )

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_telegram_get_updates"),)

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_telegram_get_updates"),)


class _EmailEgressModule(Module):
    """Module that declares email send/reply output tools (egress)."""

    def __init__(self) -> None:
        self._registered_tools: list[str] = []

    @property
    def name(self) -> str:
        return "email"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _NoopConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        for tool_name in (
            "user_email_send_message",
            "user_email_reply_to_thread",
            "bot_email_send_message",
            "bot_email_reply_to_thread",
            "bot_email_search_inbox",
            "bot_email_read_message",
            "bot_email_check_and_route_inbox",
            "user_email_search_inbox",
            "user_email_read_message",
        ):

            async def _noop(**kwargs: Any) -> dict:
                return {}

            _noop.__name__ = tool_name
            result = mcp.tool()(_noop)
            if result is not _noop:
                self._registered_tools.append(tool_name)

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(
                name="user_email_send_message",
                approval_default="always",
            ),
            ToolIODescriptor(
                name="user_email_reply_to_thread",
                approval_default="always",
            ),
        )

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(
                name="bot_email_send_message",
                approval_default="conditional",
            ),
            ToolIODescriptor(
                name="bot_email_reply_to_thread",
                approval_default="conditional",
            ),
        )

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(name="bot_email_search_inbox"),
            ToolIODescriptor(name="bot_email_read_message"),
            ToolIODescriptor(name="bot_email_check_and_route_inbox"),
        )

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(name="user_email_search_inbox"),
            ToolIODescriptor(name="user_email_read_message"),
        )


class _IngressOnlyModule(Module):
    """Module that only declares input (ingress) tools, no send/reply."""

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _NoopConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        for tool_name in ("bot_telegram_get_updates", "user_telegram_get_updates"):

            async def _noop(**kwargs: Any) -> dict:
                return {}

            _noop.__name__ = tool_name
            mcp.tool()(_noop)

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_telegram_get_updates"),)

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_telegram_get_updates"),)


class _NonChannelOutputModule(Module):
    """Module with output tools that are not channel egress (e.g. calendar)."""

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _NoopConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        @mcp.tool()
        async def bot_calendar_create_event() -> dict:
            return {}

        @mcp.tool()
        async def user_calendar_list_events() -> list:
            return []

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_calendar_create_event"),)

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_calendar_list_events"),)


class _EgressMisclassifiedAsInputModule(Module):
    """Module that misclassifies an egress tool as an input (bypass attempt)."""

    def __init__(self) -> None:
        self._registered_tools: list[str] = []

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _NoopConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        for tool_name in (
            "user_telegram_send_message",
            "bot_telegram_get_updates",
        ):

            async def _noop(**kwargs: Any) -> dict:
                return {}

            _noop.__name__ = tool_name
            result = mcp.tool()(_noop)
            if result is not _noop:
                self._registered_tools.append(tool_name)

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        # Misclassified: send_message declared as input to try bypassing filter.
        return (ToolIODescriptor(name="user_telegram_send_message"),)

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_telegram_get_updates"),)


def _make_daemon(butler_name: str, modules: list[Module]) -> Any:
    """Create a ButlerDaemon with stubbed internals for ownership tests."""
    from butlers.daemon import ButlerDaemon

    daemon = ButlerDaemon(Path("."))
    module_configs = {mod.name: {} for mod in modules}
    daemon.config = ButlerConfig(
        name=butler_name,
        port=9999,
        modules=module_configs,
    )
    daemon.db = MagicMock()
    daemon.db.pool = MagicMock(name="pool")
    daemon.mcp = MagicMock(name="mcp")
    daemon._modules = modules
    daemon._module_configs = {mod.name: _NoopConfig() for mod in modules}
    return daemon


def _collect_mcp_tool_calls(mcp_mock: MagicMock) -> set[str]:
    """Collect tool names registered through a MagicMock MCP.

    Returns the set of tool names that were NOT filtered (i.e., actually
    passed through to the real MCP).
    """
    registered: set[str] = set()
    for call in mcp_mock.tool.return_value.call_args_list:
        if call.args:
            fn = call.args[0]
            registered.add(fn.__name__)
    return registered


# ---------------------------------------------------------------------------
# Tests: _is_channel_egress_tool pattern matcher
# ---------------------------------------------------------------------------


class TestChannelEgressPatternMatcher:
    """Verify _is_channel_egress_tool correctly classifies tool names."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "user_telegram_send_message",
            "bot_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_reply_to_message",
            "user_email_send_message",
            "bot_email_send_message",
            "user_email_reply_to_thread",
            "bot_email_reply_to_thread",
            "user_sms_send_message",
            "bot_chat_reply_to_message",
        ],
    )
    def test_egress_tools_are_matched(self, tool_name: str) -> None:
        assert _is_channel_egress_tool(tool_name), f"{tool_name} should be egress"

    @pytest.mark.parametrize(
        "tool_name",
        [
            "bot_telegram_get_updates",
            "user_telegram_get_updates",
            "bot_email_search_inbox",
            "bot_email_read_message",
            "bot_email_check_and_route_inbox",
            "user_email_search_inbox",
            "user_email_read_message",
            "bot_calendar_create_event",
            "user_calendar_list_events",
            "bot_memory_store_episode",
            "user_health_log_measurement",
        ],
    )
    def test_non_egress_tools_are_not_matched(self, tool_name: str) -> None:
        assert not _is_channel_egress_tool(tool_name), f"{tool_name} should not be egress"

    def test_bare_names_are_not_matched(self) -> None:
        """Legacy unprefixed tool names should not match the egress pattern."""
        assert not _is_channel_egress_tool("send" + "_message")
        assert not _is_channel_egress_tool("reply" + "_to_message")


# ---------------------------------------------------------------------------
# Tests: Non-messenger butler egress stripping
# ---------------------------------------------------------------------------


class TestNonMessengerEgressStripping:
    """Non-messenger butlers must have channel send/reply tools stripped at startup."""

    @pytest.mark.asyncio
    async def test_telegram_egress_stripped_on_general(self) -> None:
        mod = _ChannelEgressModule()
        daemon = _make_daemon("general", [mod])
        await daemon._register_module_tools()

        # Egress tools should have been silently filtered out.
        egress_names = {
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        }
        for tool_name in egress_names:
            assert tool_name not in mod._registered_tools, (
                f"{tool_name} should have been filtered on non-messenger butler"
            )

    @pytest.mark.asyncio
    async def test_telegram_ingress_preserved_on_general(self) -> None:
        mod = _ChannelEgressModule()
        daemon = _make_daemon("general", [mod])
        await daemon._register_module_tools()

        # Input tools should still be registered.
        ingress_names = {"user_telegram_get_updates", "bot_telegram_get_updates"}
        for tool_name in ingress_names:
            assert tool_name in mod._registered_tools, (
                f"{tool_name} should be registered on non-messenger butler"
            )

    @pytest.mark.asyncio
    async def test_email_egress_stripped_on_health(self) -> None:
        mod = _EmailEgressModule()
        daemon = _make_daemon("health", [mod])
        await daemon._register_module_tools()

        egress_names = {
            "user_email_send_message",
            "user_email_reply_to_thread",
            "bot_email_send_message",
            "bot_email_reply_to_thread",
        }
        for tool_name in egress_names:
            assert tool_name not in mod._registered_tools

    @pytest.mark.asyncio
    async def test_email_ingress_preserved_on_health(self) -> None:
        mod = _EmailEgressModule()
        daemon = _make_daemon("health", [mod])
        await daemon._register_module_tools()

        ingress_names = {
            "bot_email_search_inbox",
            "bot_email_read_message",
            "bot_email_check_and_route_inbox",
            "user_email_search_inbox",
            "user_email_read_message",
        }
        for tool_name in ingress_names:
            assert tool_name in mod._registered_tools

    @pytest.mark.asyncio
    async def test_switchboard_egress_stripped(self) -> None:
        """Even the switchboard must not expose channel egress tools."""
        mod = _ChannelEgressModule()
        daemon = _make_daemon("switchboard", [mod])
        await daemon._register_module_tools()

        egress_names = {
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        }
        for tool_name in egress_names:
            assert tool_name not in mod._registered_tools

    @pytest.mark.asyncio
    async def test_custom_butler_egress_stripped(self) -> None:
        """Custom/user-defined butler names also have egress stripped."""
        mod = _ChannelEgressModule()
        daemon = _make_daemon("my_custom_butler", [mod])
        await daemon._register_module_tools()

        egress_names = {
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        }
        for tool_name in egress_names:
            assert tool_name not in mod._registered_tools

    @pytest.mark.asyncio
    async def test_stripping_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Stripping egress tools should produce an INFO log entry."""
        mod = _ChannelEgressModule()
        daemon = _make_daemon("general", [mod])

        import logging

        with caplog.at_level(logging.INFO, logger="butlers.daemon"):
            await daemon._register_module_tools()

        assert any(
            "Stripping channel egress tools" in record.message
            and "general" in record.message
            and "notify.v1" in record.message
            for record in caplog.records
        ), "Expected stripping log message not found"


# ---------------------------------------------------------------------------
# Tests: Messenger butler egress permitted
# ---------------------------------------------------------------------------


class TestMessengerEgressPermitted:
    """The Messenger butler is the sole owner of channel send/reply tools."""

    @pytest.mark.asyncio
    async def test_messenger_can_register_telegram_egress(self) -> None:
        mod = _ChannelEgressModule()
        daemon = _make_daemon("messenger", [mod])
        await daemon._register_module_tools()

        # All tools including egress should be registered.
        egress_names = {
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        }
        for tool_name in egress_names:
            assert tool_name in mod._registered_tools, (
                f"{tool_name} should be registered on messenger"
            )

    @pytest.mark.asyncio
    async def test_messenger_can_register_email_egress(self) -> None:
        mod = _EmailEgressModule()
        daemon = _make_daemon("messenger", [mod])
        await daemon._register_module_tools()

        egress_names = {
            "user_email_send_message",
            "user_email_reply_to_thread",
            "bot_email_send_message",
            "bot_email_reply_to_thread",
        }
        for tool_name in egress_names:
            assert tool_name in mod._registered_tools


# ---------------------------------------------------------------------------
# Tests: Ingress-only and non-channel modules pass ownership check
# ---------------------------------------------------------------------------


class TestIngressAndNonChannelModulesAllowed:
    """Input-only and non-channel modules should not trigger egress filtering."""

    @pytest.mark.asyncio
    async def test_ingress_only_module_allowed_on_non_messenger(self) -> None:
        """Switchboard should be able to load ingress-only channel modules."""
        daemon = _make_daemon("switchboard", [_IngressOnlyModule()])
        await daemon._register_module_tools()

    @pytest.mark.asyncio
    async def test_non_channel_output_module_allowed(self) -> None:
        """Calendar/memory output tools are not channel egress."""
        daemon = _make_daemon("general", [_NonChannelOutputModule()])
        await daemon._register_module_tools()

    @pytest.mark.asyncio
    async def test_egress_misclassified_as_input_still_filtered(self) -> None:
        """Egress tools declared in user_inputs() are still stripped on non-messenger."""
        mod = _EgressMisclassifiedAsInputModule()
        daemon = _make_daemon("switchboard", [mod])
        await daemon._register_module_tools()

        assert "user_telegram_send_message" not in mod._registered_tools, (
            "Egress tool misclassified as input should still be filtered"
        )
        assert "bot_telegram_get_updates" in mod._registered_tools, (
            "Non-egress input tool should still be registered"
        )


# ---------------------------------------------------------------------------
# Tests: ChannelEgressOwnershipError class exists for programmatic use
# ---------------------------------------------------------------------------


class TestChannelEgressOwnershipErrorClass:
    """ChannelEgressOwnershipError is importable and properly typed."""

    def test_error_is_runtime_error(self) -> None:
        assert issubclass(ChannelEgressOwnershipError, RuntimeError)

    def test_error_can_be_constructed(self) -> None:
        err = ChannelEgressOwnershipError("test message")
        assert str(err) == "test message"


# ---------------------------------------------------------------------------
# Tests: Bypass rejection for direct specialist-to-provider delivery
# ---------------------------------------------------------------------------


class TestDirectDeliveryBypassRejection:
    """Validate that route.execute dispatches non-messenger to spawner, not adapters."""

    @pytest.mark.asyncio
    async def test_non_messenger_route_execute_uses_spawner_not_adapter(self) -> None:
        """route.execute on non-messenger triggers spawner, not channel adapters.

        This validates that even if a routed payload carries notify_request
        context, a non-messenger butler does not attempt direct channel
        delivery; it uses spawner.trigger instead.
        """
        from butlers.daemon import ButlerDaemon

        daemon = ButlerDaemon(Path("."))
        daemon.config = ButlerConfig(name="health", port=9999)
        daemon.db = MagicMock()
        daemon.spawner = MagicMock()
        daemon.spawner.trigger = AsyncMock(
            return_value=MagicMock(
                output="done",
                success=True,
                error=None,
                duration_ms=42,
            )
        )
        daemon._modules = []

        # Register core tools so route.execute is available.
        daemon.mcp = MagicMock()
        tool_handlers: dict[str, Any] = {}
        original_tool = daemon.mcp.tool

        def capture_tool(*args: Any, **kwargs: Any) -> Any:
            decorator = original_tool(*args, **kwargs)

            def wrapper(fn: Any) -> Any:
                name = kwargs.get("name") or fn.__name__
                tool_handlers[name] = fn
                return decorator(fn)

            return wrapper

        daemon.mcp.tool = capture_tool
        daemon._register_core_tools()

        route_execute = tool_handlers.get("route.execute")
        assert route_execute is not None, "route.execute must be registered"

        # Use a valid UUID7 (route envelope validation requires UUID7).
        result = await route_execute(
            schema_version="route.v1",
            request_context={
                "request_id": _TEST_UUID7,
                "received_at": "2026-02-14T00:00:00Z",
                "source_channel": "telegram",
                "source_endpoint_identity": "bot-switchboard",
                "source_sender_identity": "health",
            },
            input={
                "prompt": "Test routed execution",
                "context": {
                    "notify_request": {
                        "schema_version": "notify.v1",
                        "origin_butler": "health",
                        "delivery": {
                            "intent": "send",
                            "channel": "telegram",
                            "message": "Hello",
                            "recipient": "12345",
                        },
                    }
                },
            },
        )

        assert result["status"] == "ok", f"Expected ok, got: {result}"
        # Spawner was called (the non-messenger path), not channel adapters.
        daemon.spawner.trigger.assert_called_once()
