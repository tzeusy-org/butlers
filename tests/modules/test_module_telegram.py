"""Tests for the Telegram module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.telegram import TelegramConfig, TelegramModule

pytestmark = pytest.mark.unit

EXPECTED_TELEGRAM_TOOLS = {
    "user_telegram_send_message",
    "user_telegram_reply_to_message",
    "bot_telegram_send_message",
    "bot_telegram_reply_to_message",
}
# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def telegram_module() -> TelegramModule:
    """Create a fresh TelegramModule instance."""
    return TelegramModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    """Create a mock MCP server that captures registered tools."""
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
# Module ABC implementation
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """Verify TelegramModule implements the Module ABC correctly."""

    def test_is_module_subclass(self):
        """TelegramModule is a subclass of Module."""
        assert issubclass(TelegramModule, Module)

    def test_instantiates(self, telegram_module: TelegramModule):
        """TelegramModule can be instantiated."""
        assert telegram_module is not None

    def test_name(self, telegram_module: TelegramModule):
        """Module name is 'telegram'."""
        assert telegram_module.name == "telegram"

    def test_config_schema(self, telegram_module: TelegramModule):
        """config_schema returns TelegramConfig."""
        assert telegram_module.config_schema is TelegramConfig
        assert issubclass(telegram_module.config_schema, BaseModel)

    def test_dependencies_empty(self, telegram_module: TelegramModule):
        """Telegram module has no dependencies."""
        assert telegram_module.dependencies == []

    def test_migration_revisions_none(self, telegram_module: TelegramModule):
        """Telegram module needs no custom tables."""
        assert telegram_module.migration_revisions() is None

    def test_credentials_env(self, telegram_module: TelegramModule):
        """Module declares BUTLER_TELEGRAM_TOKEN as required credential."""
        assert telegram_module.credentials_env == ["BUTLER_TELEGRAM_TOKEN"]

    def test_io_descriptors_expose_identity_prefixed_output_tools(
        self, telegram_module: TelegramModule
    ) -> None:
        """Module I/O descriptors expose send/reply output tools only (no input tools)."""
        assert telegram_module.user_inputs() == ()
        assert telegram_module.bot_inputs() == ()
        assert tuple(d.name for d in telegram_module.user_outputs()) == (
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
        )
        assert tuple(d.name for d in telegram_module.bot_outputs()) == (
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        )

    def test_user_output_descriptors_mark_approval_required_default(
        self, telegram_module: TelegramModule
    ) -> None:
        """User send/reply descriptors are marked as approval-required defaults."""
        defaults = {d.approval_default for d in telegram_module.user_outputs()}
        assert defaults == {"always"}

    def test_bot_output_descriptors_mark_conditional_default(
        self, telegram_module: TelegramModule
    ) -> None:
        """Bot send/reply descriptors default to conditional approvals."""
        defaults = {d.approval_default for d in telegram_module.bot_outputs()}
        assert defaults == {"conditional"}

    def test_no_pipeline_attribute(self, telegram_module: TelegramModule) -> None:
        """TelegramModule has no pipeline attribute (ingestion removed)."""
        assert not hasattr(telegram_module, "_pipeline")

    def test_no_set_pipeline_method(self, telegram_module: TelegramModule) -> None:
        """TelegramModule has no set_pipeline method (ingestion removed)."""
        assert not hasattr(telegram_module, "set_pipeline")

    def test_no_process_update_method(self, telegram_module: TelegramModule) -> None:
        """TelegramModule has no process_update method (ingestion removed)."""
        assert not hasattr(telegram_module, "process_update")


# ---------------------------------------------------------------------------
# TelegramConfig
# ---------------------------------------------------------------------------


class TestTelegramConfig:
    """Verify TelegramConfig validation and defaults."""

    def test_defaults(self):
        """Default config has no webhook URL and standard credential scopes."""
        config = TelegramConfig()
        assert config.webhook_url is None
        assert config.user.enabled is False
        assert config.user.token_env == "USER_TELEGRAM_TOKEN"
        assert config.bot.enabled is True
        assert config.bot.token_env == "BUTLER_TELEGRAM_TOKEN"

    def test_webhook_with_url(self):
        """Webhook URL can be set."""
        config = TelegramConfig(webhook_url="https://example.com/hook")
        assert config.webhook_url == "https://example.com/hook"

    def test_from_dict(self):
        """Config can be constructed from a dict (as from butler.toml)."""
        config = TelegramConfig(**{"webhook_url": "https://x.com/hook"})
        assert config.webhook_url == "https://x.com/hook"

    def test_empty_dict_gives_defaults(self):
        """Empty dict produces default config."""
        config = TelegramConfig(**{})
        assert config.webhook_url is None

    def test_invalid_bot_token_env_rejected(self):
        with pytest.raises(ValueError, match="modules.telegram.bot.token_env"):
            TelegramConfig(**{"bot": {"token_env": "1INVALID"}})

    def test_invalid_user_token_env_rejected(self):
        with pytest.raises(ValueError, match="modules.telegram.user.token_env"):
            TelegramConfig(**{"user": {"token_env": "bad-value"}})


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify register_tools creates the expected MCP tools."""

    async def test_registers_prefixed_tools(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ):
        """register_tools creates only identity-prefixed send/reply tools."""
        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_TELEGRAM_TOOLS

    async def test_registered_tool_names_stay_identity_prefixed(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ):
        """Registered Telegram tools stay within user_/bot_ namespaces."""
        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert all(
            name.startswith(("user_telegram_", "bot_telegram_"))
            for name in mock_mcp._registered_tools.keys()
        )

    async def test_all_registered_tools_are_callable(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ):
        """Every prefixed Telegram tool registration is callable."""
        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        for name in EXPECTED_TELEGRAM_TOOLS:
            assert callable(mock_mcp._registered_tools[name])


# ---------------------------------------------------------------------------
# Mocked API calls
# ---------------------------------------------------------------------------


def _mock_response(json_data: dict[str, Any], status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.telegram.org/test"),
    )


class TestSendMessage:
    """Test _send_message API interaction."""

    async def test_calls_correct_endpoint(self, telegram_module: TelegramModule, monkeypatch):
        """_send_message POSTs to the sendMessage endpoint."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token-123")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response({"ok": True, "result": {"message_id": 42}})
        telegram_module._client = mock_client

        result = await telegram_module._send_message("12345", "Hello!")

        mock_client.post.assert_called_once_with(
            "https://api.telegram.org/bottest-token-123/sendMessage",
            json={"chat_id": "12345", "text": "Hello!"},
        )
        assert result["ok"] is True
        assert result["result"]["message_id"] == 42

    async def test_user_send_message_via_tool(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock, monkeypatch
    ):
        """user_telegram_send_message delegates to _send_message."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response({"ok": True, "result": {}})
        telegram_module._client = mock_client

        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tool_fn = mock_mcp._registered_tools["user_telegram_send_message"]
        result = await tool_fn(chat_id="999", text="Test msg")

        assert result["ok"] is True
        mock_client.post.assert_called_once()


class TestReplyToMessage:
    """Test _reply_to_message API interaction."""

    async def test_reply_to_message_sets_reply_to_message_id(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """_reply_to_message includes reply_to_message_id payload."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response({"ok": True, "result": {}})
        telegram_module._client = mock_client

        result = await telegram_module._reply_to_message("12345", 77, "Reply text")

        assert result["ok"] is True
        mock_client.post.assert_called_once_with(
            "https://api.telegram.org/bottest-token/sendMessage",
            json={"chat_id": "12345", "text": "Reply text", "reply_to_message_id": 77},
        )

    async def test_user_reply_tool_via_registration(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock, monkeypatch
    ):
        """user_telegram_reply_to_message delegates to reply helper."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response({"ok": True, "result": {}})
        telegram_module._client = mock_client

        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tool_fn = mock_mcp._registered_tools["user_telegram_reply_to_message"]
        result = await tool_fn(chat_id="999", message_id=12, text="Thread reply")

        assert result["ok"] is True
        mock_client.post.assert_called_once_with(
            "https://api.telegram.org/bottest-token/sendMessage",
            json={"chat_id": "999", "text": "Thread reply", "reply_to_message_id": 12},
        )


# ---------------------------------------------------------------------------
# Startup — webhook mode
# ---------------------------------------------------------------------------


class TestWebhookMode:
    """Test webhook mode startup behaviour."""

    async def test_calls_set_webhook(self, telegram_module: TelegramModule, monkeypatch):
        """Webhook mode calls setWebhook API on startup."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        telegram_module._set_webhook = AsyncMock(  # type: ignore[method-assign]
            return_value={"ok": True}
        )

        await telegram_module.on_startup(
            config={"webhook_url": "https://example.com/hook"},
            db=None,
        )

        telegram_module._set_webhook.assert_called_once_with("https://example.com/hook")

        await telegram_module.on_shutdown()

    async def test_no_url_does_not_set_webhook(self, telegram_module: TelegramModule, monkeypatch):
        """Startup without a webhook URL does not call setWebhook."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        telegram_module._set_webhook = AsyncMock()  # type: ignore[method-assign]

        await telegram_module.on_startup(config={}, db=None)

        telegram_module._set_webhook.assert_not_called()

        await telegram_module.on_shutdown()


# ---------------------------------------------------------------------------
# Shutdown cleanup
# ---------------------------------------------------------------------------


class TestShutdown:
    """Test on_shutdown cleanup behaviour."""

    async def test_closes_http_client(self, telegram_module: TelegramModule):
        """Shutdown closes the HTTP client."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        telegram_module._client = mock_client

        await telegram_module.on_shutdown()

        mock_client.aclose.assert_called_once()
        assert telegram_module._client is None

    async def test_shutdown_idempotent(self, telegram_module: TelegramModule):
        """Calling shutdown twice does not raise."""
        telegram_module._client = None

        # Should not raise
        await telegram_module.on_shutdown()
        await telegram_module.on_shutdown()


# ---------------------------------------------------------------------------
# Webhook API helpers
# ---------------------------------------------------------------------------


class TestWebhookHelpers:
    """Test setWebhook and deleteWebhook API calls."""

    async def test_set_webhook(self, telegram_module: TelegramModule, monkeypatch):
        """_set_webhook POSTs to the setWebhook endpoint."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token-abc")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response({"ok": True, "result": True})
        telegram_module._client = mock_client

        result = await telegram_module._set_webhook("https://example.com/tg")

        mock_client.post.assert_called_once_with(
            "https://api.telegram.org/bottest-token-abc/setWebhook",
            json={"url": "https://example.com/tg"},
        )
        assert result["ok"] is True

    async def test_delete_webhook(self, telegram_module: TelegramModule, monkeypatch):
        """_delete_webhook POSTs to the deleteWebhook endpoint."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token-abc")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            {"ok": True, "result": True, "description": "Webhook was deleted"}
        )
        telegram_module._client = mock_client

        result = await telegram_module._delete_webhook()

        mock_client.post.assert_called_once_with(
            "https://api.telegram.org/bottest-token-abc/deleteWebhook",
        )
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """Verify TelegramModule works with ModuleRegistry."""

    def test_register_in_registry(self):
        """TelegramModule can be registered in the ModuleRegistry."""
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(TelegramModule)
        assert "telegram" in reg.available_modules

    def test_load_from_config(self):
        """TelegramModule can be loaded from config via registry."""
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(TelegramModule)
        modules = reg.load_from_config({"telegram": {}})
        assert len(modules) == 1
        assert modules[0].name == "telegram"


# ---------------------------------------------------------------------------
# Send/reply tool delegation
# ---------------------------------------------------------------------------


class TestIdentityScopedToolFlows:
    """Verify user/bot send/reply tool behavior."""

    async def test_user_and_bot_send_reply_tools_delegate_helpers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Both identity-scoped send/reply tools invoke shared helpers."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        mod = TelegramModule()
        mcp = MagicMock()
        tools: dict[str, object] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        send_mock = AsyncMock(return_value={"ok": True, "type": "send"})
        reply_mock = AsyncMock(return_value={"ok": True, "type": "reply"})
        mod._send_message = send_mock  # type: ignore[method-assign]
        mod._reply_to_message = reply_mock  # type: ignore[method-assign]

        user_send = await tools["user_telegram_send_message"](chat_id="1", text="hello")  # type: ignore[index]
        bot_send = await tools["bot_telegram_send_message"](chat_id="2", text="hi")  # type: ignore[index]
        user_reply = await tools["user_telegram_reply_to_message"](  # type: ignore[index]
            chat_id="3",
            message_id=11,
            text="user reply",
        )
        bot_reply = await tools["bot_telegram_reply_to_message"](  # type: ignore[index]
            chat_id="4",
            message_id=12,
            text="bot reply",
        )

        assert user_send["type"] == "send"
        assert bot_send["type"] == "send"
        assert user_reply["type"] == "reply"
        assert bot_reply["type"] == "reply"
        assert send_mock.await_args_list[0].args == ("1", "hello")
        assert send_mock.await_args_list[1].args == ("2", "hi")
        assert reply_mock.await_args_list[0].args == ("3", 11, "user reply")
        assert reply_mock.await_args_list[1].args == ("4", 12, "bot reply")
