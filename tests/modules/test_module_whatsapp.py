"""Unit tests for the WhatsApp module.

Covers:
- Module ABC compliance (name, config_schema, dependencies, migration_revisions)
- WhatsAppConfig defaults, custom values, and validation
- Tool registration modes (no send_tools / send_tools+disabled / send_tools+enabled)
- Bridge lifecycle (startup, shutdown, binary-not-found, timeout)
- send_disabled error response
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.whatsapp import (
    _SEND_DISABLED_ERROR,
    WhatsAppConfig,
    WhatsAppModule,
    WhatsAppUserCredentialScope,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def whatsapp_module() -> WhatsAppModule:
    """Create a fresh WhatsAppModule instance."""
    return WhatsAppModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    """Create a mock MCP server that captures registered tools by function name."""
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """Verify WhatsAppModule satisfies the Module abstract base class."""

    def test_is_module_subclass(self):
        assert issubclass(WhatsAppModule, Module)

    def test_instantiates(self, whatsapp_module: WhatsAppModule):
        assert whatsapp_module is not None

    def test_name(self, whatsapp_module: WhatsAppModule):
        assert whatsapp_module.name == "whatsapp"

    def test_config_schema(self, whatsapp_module: WhatsAppModule):
        assert whatsapp_module.config_schema is WhatsAppConfig
        assert issubclass(whatsapp_module.config_schema, BaseModel)

    def test_dependencies_empty(self, whatsapp_module: WhatsAppModule):
        assert whatsapp_module.dependencies == []

    def test_migration_revisions_none(self, whatsapp_module: WhatsAppModule):
        assert whatsapp_module.migration_revisions() is None


# ---------------------------------------------------------------------------
# WhatsAppConfig
# ---------------------------------------------------------------------------


class TestWhatsAppConfig:
    """Verify config schema defaults, custom values, and validation."""

    def test_defaults(self):
        cfg = WhatsAppConfig()
        assert cfg.send_tools is False
        assert cfg.send_enabled is False
        assert cfg.bridge_socket == "/tmp/wa-bridge.sock"
        assert cfg.user.enabled is True
        assert cfg.user.session_env == "WHATSAPP_USER_SESSION"

    def test_custom_bridge_socket(self):
        cfg = WhatsAppConfig(bridge_socket="/run/wa.sock")
        assert cfg.bridge_socket == "/run/wa.sock"

    def test_send_tools_true_send_enabled_false(self):
        """Messenger default: tools registered but disabled."""
        cfg = WhatsAppConfig(send_tools=True, send_enabled=False)
        assert cfg.send_tools is True
        assert cfg.send_enabled is False

    def test_send_tools_true_send_enabled_true(self):
        """Fully enabled for sending."""
        cfg = WhatsAppConfig(send_tools=True, send_enabled=True)
        assert cfg.send_tools is True
        assert cfg.send_enabled is True

    def test_send_enabled_true_without_send_tools_raises(self):
        """send_enabled=true without send_tools=true is invalid."""
        with pytest.raises(ValidationError, match="Cannot enable sending without send_tools"):
            WhatsAppConfig(send_enabled=True, send_tools=False)

    def test_send_enabled_false_with_send_tools_false_ok(self):
        """Default: both false is fine."""
        cfg = WhatsAppConfig(send_enabled=False, send_tools=False)
        assert cfg.send_enabled is False
        assert cfg.send_tools is False

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            WhatsAppConfig(**{"unknown_field": "value"})

    def test_user_scope_defaults(self):
        scope = WhatsAppUserCredentialScope()
        assert scope.enabled is True
        assert scope.session_env == "WHATSAPP_USER_SESSION"

    def test_user_scope_disabled(self):
        scope = WhatsAppUserCredentialScope(enabled=False)
        assert scope.enabled is False


# ---------------------------------------------------------------------------
# Tool registration — no send_tools (default)
# ---------------------------------------------------------------------------


class TestRegisterToolsNoSendTools:
    """When send_tools=false, no tools are registered."""

    async def test_no_tools_registered_by_default(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(mcp=mock_mcp, config=None, db=None)
        assert len(mock_mcp._registered_tools) == 0

    async def test_no_tools_with_explicit_false(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(mcp=mock_mcp, config={"send_tools": False}, db=None)
        assert len(mock_mcp._registered_tools) == 0

    async def test_no_tools_with_empty_config(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert len(mock_mcp._registered_tools) == 0


# ---------------------------------------------------------------------------
# Tool registration — send_tools=true, send_enabled=false
# ---------------------------------------------------------------------------


class TestRegisterToolsSendToolsDisabled:
    """When send_tools=true and send_enabled=false, tools are registered but return error."""

    async def test_tools_registered(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": False}, db=None
        )
        assert "whatsapp_send_message" in mock_mcp._registered_tools
        assert "whatsapp_reply_to_message" in mock_mcp._registered_tools

    async def test_send_message_returns_disabled_error(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": False}, db=None
        )
        send_fn = mock_mcp._registered_tools["whatsapp_send_message"]
        result = await send_fn(recipient="+15551234567", text="hello")
        assert "error" in result
        assert "send_enabled" in result["error"]
        assert "ban risk" in result["error"]

    async def test_reply_returns_disabled_error(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": False}, db=None
        )
        reply_fn = mock_mcp._registered_tools["whatsapp_reply_to_message"]
        result = await reply_fn(
            chat_jid="15551234567@s.whatsapp.net", message_id="abc123", text="hi"
        )
        assert "error" in result
        assert "send_enabled" in result["error"]

    async def test_disabled_error_matches_spec(self, whatsapp_module, mock_mcp):
        """Error message matches the exact spec text."""
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": False}, db=None
        )
        send_fn = mock_mcp._registered_tools["whatsapp_send_message"]
        result = await send_fn(recipient="+1234", text="test")
        assert result["error"] == _SEND_DISABLED_ERROR


# ---------------------------------------------------------------------------
# Tool registration — send_tools=true, send_enabled=true
# ---------------------------------------------------------------------------


class TestRegisterToolsSendEnabled:
    """When send_tools=true and send_enabled=true, tools delegate to bridge helpers."""

    async def test_send_message_calls_helper(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": True}, db=None
        )
        mock_send = AsyncMock(return_value={"message_id": "msg-1", "status": "sent"})
        whatsapp_module._send_message = mock_send

        send_fn = mock_mcp._registered_tools["whatsapp_send_message"]
        result = await send_fn(recipient="+15551234567", text="hello world")

        mock_send.assert_awaited_once_with(recipient="+15551234567", text="hello world")
        assert result["message_id"] == "msg-1"

    async def test_reply_calls_helper(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": True}, db=None
        )
        mock_reply = AsyncMock(return_value={"message_id": "msg-2", "status": "sent"})
        whatsapp_module._reply_to_message = mock_reply

        reply_fn = mock_mcp._registered_tools["whatsapp_reply_to_message"]
        result = await reply_fn(
            chat_jid="15551234567@s.whatsapp.net", message_id="orig-id", text="pong"
        )

        mock_reply.assert_awaited_once_with(
            chat_jid="15551234567@s.whatsapp.net", message_id="orig-id", text="pong"
        )
        assert result["message_id"] == "msg-2"

    async def test_tools_are_async(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True, "send_enabled": True}, db=None
        )
        for name, fn in mock_mcp._registered_tools.items():
            assert asyncio.iscoroutinefunction(fn), f"{name} should be async"


# ---------------------------------------------------------------------------
# Tool registration from WhatsAppConfig instance
# ---------------------------------------------------------------------------


class TestRegisterToolsFromConfigObject:
    """register_tools should accept a WhatsAppConfig instance directly."""

    async def test_accepts_config_instance(self, whatsapp_module, mock_mcp):
        cfg = WhatsAppConfig(send_tools=True, send_enabled=False)
        await whatsapp_module.register_tools(mcp=mock_mcp, config=cfg, db=None)
        assert "whatsapp_send_message" in mock_mcp._registered_tools

    async def test_accepts_none_config(self, whatsapp_module, mock_mcp):
        await whatsapp_module.register_tools(mcp=mock_mcp, config=None, db=None)
        assert len(mock_mcp._registered_tools) == 0


# ---------------------------------------------------------------------------
# Bridge lifecycle — on_startup
# ---------------------------------------------------------------------------


class TestOnStartup:
    """Verify on_startup bridge lifecycle."""

    async def test_startup_starts_bridge(self, whatsapp_module):
        """on_startup calls BridgeSubprocessManager.start()."""
        mock_manager = AsyncMock()
        mock_manager.is_running = True
        mock_manager.is_degraded = False

        with (
            patch("butlers.modules.whatsapp.BridgeSubprocessManager") as mock_cls,
            patch("butlers.modules.whatsapp.BridgeConfig"),
            patch("butlers.modules.whatsapp.resolve_owner_entity_info", return_value=None),
        ):
            mock_cls.return_value = mock_manager
            await whatsapp_module.on_startup(
                config={"send_tools": True, "send_enabled": False},
                db=None,
            )

        mock_manager.start.assert_awaited_once()

    async def test_startup_resolves_phone_from_entity_info(self, whatsapp_module):
        """on_startup resolves whatsapp_phone if DB pool is available."""
        mock_db = MagicMock()
        mock_db.pool = MagicMock()  # non-None pool

        mock_manager = AsyncMock()

        with (
            patch("butlers.modules.whatsapp.BridgeSubprocessManager") as mock_cls,
            patch("butlers.modules.whatsapp.BridgeConfig"),
            patch(
                "butlers.modules.whatsapp.resolve_owner_entity_info",
                return_value="+15551234567",
            ) as mock_resolve,
        ):
            mock_cls.return_value = mock_manager
            await whatsapp_module.on_startup(config=None, db=mock_db)

        mock_resolve.assert_awaited_once_with(mock_db.pool, "whatsapp_phone")
        assert whatsapp_module._whatsapp_phone == "+15551234567"

    async def test_startup_logs_warning_on_missing_phone(self, whatsapp_module):
        """on_startup continues in degraded mode if phone not in entity_info."""
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        mock_manager = AsyncMock()

        with (
            patch("butlers.modules.whatsapp.BridgeSubprocessManager") as mock_cls,
            patch("butlers.modules.whatsapp.BridgeConfig"),
            patch("butlers.modules.whatsapp.resolve_owner_entity_info", return_value=None),
        ):
            mock_cls.return_value = mock_manager
            # Should not raise
            await whatsapp_module.on_startup(config=None, db=mock_db)

        assert whatsapp_module._whatsapp_phone is None

    async def test_startup_raises_on_missing_binary(self, whatsapp_module):
        """RuntimeError propagates when whatsapp-bridge binary is not found."""
        mock_manager = AsyncMock()
        mock_manager.start.side_effect = RuntimeError(
            "whatsapp-bridge binary not found. Build with EXTRAS=whatsapp or install manually."
        )

        with (
            patch("butlers.modules.whatsapp.BridgeSubprocessManager") as mock_cls,
            patch("butlers.modules.whatsapp.BridgeConfig"),
            patch("butlers.modules.whatsapp.resolve_owner_entity_info", return_value=None),
        ):
            mock_cls.return_value = mock_manager
            with pytest.raises(RuntimeError, match="whatsapp-bridge binary not found"):
                await whatsapp_module.on_startup(config=None, db=None)

    async def test_startup_raises_on_timeout(self, whatsapp_module):
        """TimeoutError propagates when bridge does not connect within 30s."""
        mock_manager = AsyncMock()
        mock_manager.start.side_effect = TimeoutError("Bridge did not reach 'connected'")

        with (
            patch("butlers.modules.whatsapp.BridgeSubprocessManager") as mock_cls,
            patch("butlers.modules.whatsapp.BridgeConfig"),
            patch("butlers.modules.whatsapp.resolve_owner_entity_info", return_value=None),
        ):
            mock_cls.return_value = mock_manager
            with pytest.raises(TimeoutError):
                await whatsapp_module.on_startup(config=None, db=None)

    async def test_startup_parses_config_from_dict(self, whatsapp_module):
        """on_startup accepts a raw dict and parses it into WhatsAppConfig."""
        mock_manager = AsyncMock()

        with (
            patch("butlers.modules.whatsapp.BridgeSubprocessManager") as mock_cls,
            patch("butlers.modules.whatsapp.BridgeConfig"),
            patch("butlers.modules.whatsapp.resolve_owner_entity_info", return_value=None),
        ):
            mock_cls.return_value = mock_manager
            await whatsapp_module.on_startup(config={"bridge_socket": "/run/custom.sock"}, db=None)

        assert whatsapp_module._config.bridge_socket == "/run/custom.sock"


# ---------------------------------------------------------------------------
# Bridge lifecycle — on_shutdown
# ---------------------------------------------------------------------------


class TestOnShutdown:
    """Verify on_shutdown delegates to BridgeSubprocessManager.stop()."""

    async def test_shutdown_calls_stop(self, whatsapp_module):
        mock_manager = AsyncMock()
        whatsapp_module._bridge_manager = mock_manager

        await whatsapp_module.on_shutdown()

        mock_manager.stop.assert_awaited_once()
        assert whatsapp_module._bridge_manager is None

    async def test_shutdown_when_no_manager(self, whatsapp_module):
        """on_shutdown is a no-op when bridge was never started."""
        whatsapp_module._bridge_manager = None
        await whatsapp_module.on_shutdown()  # should not raise


# ---------------------------------------------------------------------------
# _send_message / _reply_to_message — bridge not running
# ---------------------------------------------------------------------------


class TestSendHelpersBridgeNotRunning:
    """Helpers return error dict when bridge is unavailable."""

    async def test_send_returns_error_when_not_running(self, whatsapp_module):
        mock_manager = MagicMock()
        mock_manager.is_running = False
        mock_manager.is_degraded = False
        whatsapp_module._bridge_manager = mock_manager

        result = await whatsapp_module._send_message(recipient="+1234", text="hi")
        assert "error" in result
        assert "bridge is not running" in result["error"].lower()

    async def test_send_returns_error_when_degraded(self, whatsapp_module):
        mock_manager = MagicMock()
        mock_manager.is_running = True
        mock_manager.is_degraded = True
        mock_manager.degraded_reason = "Session invalidated"
        whatsapp_module._bridge_manager = mock_manager

        result = await whatsapp_module._send_message(recipient="+1234", text="hi")
        assert "error" in result
        assert "degraded" in result["error"].lower()
        assert "Session invalidated" in result["error"]

    async def test_reply_returns_error_when_not_running(self, whatsapp_module):
        whatsapp_module._bridge_manager = None

        result = await whatsapp_module._reply_to_message(
            chat_jid="1234@s.whatsapp.net", message_id="m1", text="hi"
        )
        assert "error" in result

    async def test_send_returns_error_when_no_manager(self, whatsapp_module):
        whatsapp_module._bridge_manager = None
        result = await whatsapp_module._send_message(recipient="+1234", text="hi")
        assert "error" in result
