"""Condensed WhatsApp module tests — behavioral contract only.

Replaces 39 tests with ~8 focused behavioral tests.

Covers:
- Module ABC compliance
- WhatsAppConfig validation (defaults, extra rejected)
- Tool registration modes
- send_disabled error response (string message, not dict)

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.whatsapp import (
    _SEND_DISABLED_ERROR,
    WhatsAppConfig,
    WhatsAppModule,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def whatsapp_module() -> WhatsAppModule:
    return WhatsAppModule()


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


class TestToolRegistration:
    async def test_tool_registration_gated_by_send_tools(
        self, whatsapp_module: WhatsAppModule
    ) -> None:
        registered: dict[str, Any] = {}
        mcp = MagicMock()

        def _tool_decorator(*args, **kw):
            def _wrap(fn):
                registered[kw.get("name") or fn.__name__] = fn
                return fn

            return _wrap

        mcp.tool = _tool_decorator
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
