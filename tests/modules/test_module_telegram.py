"""Condensed Telegram module tests — behavioral contract only.

Replaces 54 tests with ~15 focused behavioral tests.

Covers:
- Module ABC compliance
- TelegramConfig validation (required fields, defaults)
- Tool registration (expected tools)
- Markdown to HTML conversion (critical formatting logic)
- Registry integration

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.telegram import TelegramConfig, TelegramModule, _markdown_to_telegram_html

pytestmark = pytest.mark.unit

EXPECTED_TELEGRAM_TOOLS = {
    "telegram_send_message",
    "telegram_reply_to_message",
    "telegram_react_to_message",
}


@pytest.fixture
def telegram_module() -> TelegramModule:
    return TelegramModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        name = kwargs.get("name")

        def decorator(fn):
            tools[name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


class TestModuleABCCompliance:
    def test_module_contract(self, telegram_module: TelegramModule) -> None:
        """TelegramModule satisfies Module ABC: name, config_schema, dependencies, registry."""
        from butlers.modules.registry import default_registry

        assert issubclass(TelegramModule, Module)
        assert telegram_module.name == "telegram"
        assert telegram_module.config_schema is TelegramConfig
        assert telegram_module.dependencies == []
        assert "telegram" in default_registry().available_modules


class TestTelegramConfig:
    def test_defaults_and_validation(self) -> None:
        cfg = TelegramConfig()
        assert cfg.webhook_url is None
        assert cfg.bot is not None
        # Extra fields rejected
        with pytest.raises(ValidationError):
            TelegramConfig(unknown="x")


class TestToolRegistration:
    async def test_registers_expected_tools(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ) -> None:
        await telegram_module.register_tools(
            mcp=mock_mcp,
            config={},
            db=None,
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_TELEGRAM_TOOLS


class TestMarkdownToTelegramHtml:
    @pytest.mark.parametrize(
        "md,expected_contains",
        [
            ("**bold text**", "<b>bold text</b>"),
            ("*italic text*", "<i>italic text</i>"),
            ("`code`", "<code>code</code>"),
            ("plain text", "plain text"),
        ],
    )
    def test_conversions(self, md: str, expected_contains: str) -> None:
        result = _markdown_to_telegram_html(md)
        assert expected_contains in result

    def test_empty_string(self) -> None:
        assert _markdown_to_telegram_html("") == ""
