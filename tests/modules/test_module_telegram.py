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
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
            butler_name="test-butler",
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_TELEGRAM_TOOLS


class TestTelegramSendAuditEmit:
    """telegram_send is emitted to dashboard_audit_log on outbound sendMessage."""

    async def test_audit_emitted_on_success(self) -> None:
        mod = TelegramModule()
        mock_pool = MagicMock()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(mock_pool)

        # Fake successful HTTP response
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"ok": True}

        with (
            patch(
                "butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock
            ) as mock_emit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=fake_resp)
            mock_get_client.return_value = mock_client

            await mod._send_message("123456", "Hello world")

        mock_emit.assert_awaited_once()
        call_args = mock_emit.call_args
        assert call_args.args[2] == "telegram_send"
        assert call_args.args[3]["chat_id"] == "123456"
        assert "text_length" in call_args.args[3]

    async def test_audit_emitted_on_error(self) -> None:
        mod = TelegramModule()
        mock_pool = MagicMock()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(mock_pool)

        # Fake 400 HTTP response
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 400
        fake_resp.text = "Bad Request"
        fake_resp.json.return_value = {"description": "chat not found"}
        fake_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=fake_resp
        )

        with (
            patch(
                "butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock
            ) as mock_emit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=fake_resp)
            mock_get_client.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await mod._send_message("bad-id", "Hi")

        mock_emit.assert_awaited_once()
        call_args = mock_emit.call_args
        assert call_args.args[2] == "telegram_send"
        assert call_args.kwargs["result"] == "error"

    def test_wire_audit_pool_stores_pool(self) -> None:
        mod = TelegramModule()
        pool = MagicMock()
        mod.wire_audit_pool(pool)
        assert mod._audit_pool is pool


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
