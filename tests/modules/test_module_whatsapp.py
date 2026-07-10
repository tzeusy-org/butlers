"""WhatsApp module tests — behavioral contract + bridge-ownership topology.

Covers:
- Module ABC compliance
- WhatsAppConfig validation (defaults, extra rejected, socket resolution)
- Tool registration modes
- send_disabled error response (string message, not dict)
- Bridge ownership (bu-0c69e): the module never spawns its own bridge; its send
  tools route to the connector-owned socket for both the send_enabled and
  send_disabled topology paths.

[bu-7sd7a][bu-0c69e]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import butlers.connectors.bridge_manager as bridge_manager
from butlers.modules.base import Module
from butlers.modules.whatsapp import (
    _DEFAULT_BRIDGE_SOCKET,
    _SEND_DISABLED_ERROR,
    WhatsAppConfig,
    WhatsAppModule,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def whatsapp_module() -> WhatsAppModule:
    return WhatsAppModule()


def _make_mcp() -> tuple[dict[str, Any], MagicMock]:
    """Build a mock MCP whose ``@mcp.tool()`` captures registered tools by name."""
    registered: dict[str, Any] = {}
    mcp = MagicMock()

    def _tool_decorator(*args: Any, **kw: Any):
        def _wrap(fn):
            registered[kw.get("name") or fn.__name__] = fn
            return fn

        return _wrap

    mcp.tool = _tool_decorator
    return registered, mcp


class TestModuleABCCompliance:
    def test_module_contract(self, whatsapp_module: WhatsAppModule) -> None:
        """WhatsAppModule satisfies Module ABC: name, config_schema, registry."""
        from butlers.modules.registry import default_registry

        assert issubclass(WhatsAppModule, Module)
        assert whatsapp_module.name == "whatsapp"
        assert whatsapp_module.config_schema is WhatsAppConfig
        assert "whatsapp" in default_registry().available_modules


class TestWhatsAppConfig:
    def test_defaults(self) -> None:
        cfg = WhatsAppConfig()
        assert isinstance(cfg, WhatsAppConfig)

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WhatsAppConfig(unknown_field="x")

    def test_default_socket_is_connector_owned_shared_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Absent an override, the module targets the shared wa_bridge_socket
        # volume path the connector listens on — not a private, self-spawned one.
        monkeypatch.delenv("WHATSAPP_BRIDGE_SOCKET", raising=False)
        assert (
            WhatsAppConfig().bridge_socket == _DEFAULT_BRIDGE_SOCKET == "/tmp/wa-bridge/bridge.sock"
        )

    def test_socket_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SOCKET", "/tmp/custom/bridge.sock")
        assert WhatsAppConfig().bridge_socket == "/tmp/custom/bridge.sock"

    def test_explicit_socket_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SOCKET", "/tmp/env/bridge.sock")
        assert WhatsAppConfig(bridge_socket="/tmp/toml.sock").bridge_socket == "/tmp/toml.sock"


class TestToolRegistration:
    async def test_tool_registration_gated_by_send_tools(
        self, whatsapp_module: WhatsAppModule
    ) -> None:
        registered, mcp = _make_mcp()
        # Default: send_tools=False → no tools registered
        await whatsapp_module.register_tools(mcp=mcp, config={}, db=None, butler_name="test-butler")
        assert len(registered) == 0
        # With send_tools=True → tools registered
        registered.clear()
        await whatsapp_module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": True},
            db=None,
            butler_name="test-butler",
        )
        assert len(registered) >= 1


class TestSendDisabled:
    def test_send_disabled_error_is_string(self) -> None:
        # _SEND_DISABLED_ERROR is an actionable string message (not a dict)
        assert isinstance(_SEND_DISABLED_ERROR, str)
        assert "send_enabled" in _SEND_DISABLED_ERROR or "disabled" in _SEND_DISABLED_ERROR.lower()


class TestBridgeOwnership:
    """bu-0c69e: the module is a client of the connector-owned bridge, never an owner."""

    async def test_startup_never_spawns_a_bridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # If the module ever authenticated its own bridge it would race the
        # connector's session (StreamReplaced). Prove it constructs no manager
        # on either topology path.
        spawn_probe = MagicMock()
        monkeypatch.setattr(bridge_manager, "BridgeSubprocessManager", spawn_probe)

        module = WhatsAppModule()
        for send_enabled in (True, False):
            await module.on_startup(
                config={"send_tools": True, "send_enabled": send_enabled}, db=None
            )
            await module.on_shutdown()

        spawn_probe.assert_not_called()
        assert not hasattr(module, "_bridge_manager")

    async def test_disabled_send_touches_no_bridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_calls: list[Any] = []
        post_calls: list[Any] = []

        async def _fake_get(sock: str, path: str) -> dict:
            get_calls.append((sock, path))
            return {"connected": True, "logged_in": True}

        async def _fake_post(sock: str, path: str, body: dict) -> dict:
            post_calls.append((sock, path, body))
            return {"ok": True}

        monkeypatch.setattr(bridge_manager, "_http_get_unix", _fake_get)
        monkeypatch.setattr(bridge_manager, "_http_post_unix_with_body", _fake_post)

        module = WhatsAppModule()
        registered, mcp = _make_mcp()
        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": False},
            db=None,
            butler_name="messenger",
        )
        result = await registered["whatsapp_send_message"](recipient="+123", text="hi")

        assert result == {"error": _SEND_DISABLED_ERROR}
        # Disabled runtime gate short-circuits before any bridge contact.
        assert get_calls == []
        assert post_calls == []

    async def test_enabled_send_routes_to_connector_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SOCKET", "/tmp/wa-bridge/bridge.sock")
        post_calls: list[Any] = []

        async def _fake_get(sock: str, path: str) -> dict:
            return {"connected": True, "logged_in": True, "state": "connected"}

        async def _fake_post(sock: str, path: str, body: dict) -> dict:
            post_calls.append((sock, path, body))
            return {"message_id": "wamid.1"}

        monkeypatch.setattr(bridge_manager, "_http_get_unix", _fake_get)
        monkeypatch.setattr(bridge_manager, "_http_post_unix_with_body", _fake_post)

        module = WhatsAppModule()
        registered, mcp = _make_mcp()
        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": True},
            db=None,
            butler_name="messenger",
        )

        send_result = await registered["whatsapp_send_message"](recipient="+123", text="hi")
        reply_result = await registered["whatsapp_reply_to_message"](
            chat_jid="123@s.whatsapp.net", message_id="wamid.0", text="re"
        )

        assert send_result == {"message_id": "wamid.1"}
        assert reply_result == {"message_id": "wamid.1"}
        assert [c[0] for c in post_calls] == [
            "/tmp/wa-bridge/bridge.sock",
            "/tmp/wa-bridge/bridge.sock",
        ]
        assert [c[1] for c in post_calls] == ["/send", "/send"]
        assert post_calls[0][2] == {"recipient": "+123", "text": "hi"}
        assert post_calls[1][2] == {
            "recipient": "123@s.whatsapp.net",
            "text": "re",
            "reply_to": "wamid.0",
        }

    async def test_enabled_send_degraded_bridge_returns_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posted: list[Any] = []

        async def _fake_get(sock: str, path: str) -> dict:
            return {"connected": False, "logged_in": False, "state": "disconnected"}

        async def _fake_post(sock: str, path: str, body: dict) -> dict:
            posted.append(body)
            return {}

        monkeypatch.setattr(bridge_manager, "_http_get_unix", _fake_get)
        monkeypatch.setattr(bridge_manager, "_http_post_unix_with_body", _fake_post)

        module = WhatsAppModule()
        registered, mcp = _make_mcp()
        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": True},
            db=None,
            butler_name="messenger",
        )
        result = await registered["whatsapp_send_message"](recipient="+123", text="hi")

        assert "not connected" in result["error"]
        assert posted == []  # never POST /send to a dead link

    async def test_enabled_send_unreachable_bridge_returns_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_get(sock: str, path: str) -> dict:
            raise ConnectionError("socket missing")

        monkeypatch.setattr(bridge_manager, "_http_get_unix", _fake_get)

        module = WhatsAppModule()
        registered, mcp = _make_mcp()
        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": True},
            db=None,
            butler_name="messenger",
        )
        result = await registered["whatsapp_send_message"](recipient="+123", text="hi")

        assert "not reachable" in result["error"]
        assert "connector" in result["error"]
