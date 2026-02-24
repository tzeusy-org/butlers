"""Tests for the Telegram module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import BaseModel

from butlers.modules import telegram as telegram_module_impl
from butlers.modules.base import Module
from butlers.modules.pipeline import RoutingResult
from butlers.modules.telegram import TelegramConfig, TelegramModule

pytestmark = pytest.mark.unit

EXPECTED_TELEGRAM_TOOLS = {
    "user_telegram_get_updates",
    "user_telegram_send_message",
    "user_telegram_reply_to_message",
    "bot_telegram_get_updates",
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

    def test_io_descriptors_expose_identity_prefixed_tools(
        self, telegram_module: TelegramModule
    ) -> None:
        """Module I/O descriptors expose the expected prefixed Telegram tools."""
        assert tuple(d.name for d in telegram_module.user_inputs()) == (
            "user_telegram_get_updates",
        )
        assert tuple(d.name for d in telegram_module.user_outputs()) == (
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
        )
        assert tuple(d.name for d in telegram_module.bot_inputs()) == ("bot_telegram_get_updates",)
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
        """register_tools creates only identity-prefixed Telegram tools."""
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


class TestGetUpdates:
    """Test _get_updates API interaction."""

    async def test_calls_correct_endpoint(self, telegram_module: TelegramModule, monkeypatch):
        """_get_updates GETs the getUpdates endpoint."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token-123")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response(
            {
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"text": "hi"}},
                    {"update_id": 2, "message": {"text": "hello"}},
                ],
            }
        )
        telegram_module._client = mock_client

        updates = await telegram_module._get_updates()

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert "getUpdates" in call_args[0][0]
        assert len(updates) == 2
        assert updates[0]["message"]["text"] == "hi"

    async def test_updates_last_update_id(self, telegram_module: TelegramModule, monkeypatch):
        """_get_updates advances _last_update_id to the latest."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response(
            {"ok": True, "result": [{"update_id": 100, "message": {"text": "x"}}]}
        )
        telegram_module._client = mock_client

        await telegram_module._get_updates()
        assert telegram_module._last_update_id == 100

    async def test_uses_offset_after_first_call(self, telegram_module: TelegramModule, monkeypatch):
        """After receiving updates, subsequent calls use offset parameter."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response(
            {"ok": True, "result": [{"update_id": 50, "message": {"text": "a"}}]}
        )
        telegram_module._client = mock_client

        # First call
        await telegram_module._get_updates()
        assert telegram_module._last_update_id == 50

        # Second call should include offset=51
        mock_client.get.return_value = _mock_response({"ok": True, "result": []})
        await telegram_module._get_updates()

        second_call = mock_client.get.call_args
        assert second_call[1]["params"]["offset"] == 51

    async def test_empty_updates(self, telegram_module: TelegramModule, monkeypatch):
        """_get_updates returns empty list when no new messages."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response({"ok": True, "result": []})
        telegram_module._client = mock_client

        updates = await telegram_module._get_updates()
        assert updates == []
        assert telegram_module._last_update_id == 0

    async def test_conflict_returns_empty_updates(
        self, telegram_module: TelegramModule, monkeypatch, caplog: pytest.LogCaptureFixture
    ):
        """409 Conflict returns no updates and logs an actionable warning."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response(
            {"ok": False, "description": "Conflict: terminated by other getUpdates request"},
            status_code=409,
        )
        telegram_module._client = mock_client

        with caplog.at_level("WARNING"):
            updates = await telegram_module._get_updates()

        assert updates == []
        assert telegram_module._last_update_id == 0
        assert any("getUpdates conflict" in rec.message for rec in caplog.records)

    @pytest.mark.parametrize("tool_name", ["user_telegram_get_updates", "bot_telegram_get_updates"])
    async def test_get_updates_via_tools(
        self,
        telegram_module: TelegramModule,
        mock_mcp: MagicMock,
        monkeypatch,
        tool_name: str,
    ):
        """Identity-prefixed get-updates tools delegate to _get_updates."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response({"ok": True, "result": []})
        telegram_module._client = mock_client

        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tool_fn = mock_mcp._registered_tools[tool_name]
        result = await tool_fn()

        assert result == []


class TestPipelineIntegration:
    """Verify classification pipeline integration for incoming updates."""

    async def test_process_update_forwards_to_pipeline(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """process_update forwards text and metadata into pipeline.process()."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_pipeline = MagicMock()
        mock_result = RoutingResult(target_butler="general", route_result={"status": "ok"})
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        telegram_module.set_pipeline(mock_pipeline)

        update = {"message": {"text": "Need help", "chat": {"id": 12345}}}
        result = await telegram_module.process_update(update)

        assert result is mock_result
        mock_pipeline.process.assert_awaited_once()
        _, call_kwargs = mock_pipeline.process.await_args
        assert call_kwargs["message_text"] == "Need help"
        assert call_kwargs["tool_name"] == "bot_telegram_handle_message"
        # external_event_id is message_key or chat_id (here: "12345")
        # request_id is generated, so we check it exists but not the value
        tool_args = call_kwargs["tool_args"]
        assert "request_id" in tool_args
        assert tool_args["source"] == "telegram"
        assert tool_args["source_channel"] == "telegram"
        assert tool_args["source_identity"] == "bot"
        assert tool_args["source_endpoint_identity"] == "telegram:bot"
        assert tool_args["sender_identity"] == "12345"
        assert tool_args["external_event_id"] == "12345"
        assert tool_args["external_thread_id"] == "12345"
        assert tool_args["source_tool"] == "bot_telegram_get_updates"
        assert tool_args["chat_id"] == "12345"
        assert tool_args["source_id"] is None
        assert tool_args["raw_metadata"] == update
        assert call_kwargs["message_inbox_id"] is None
        assert telegram_module._routed_messages == [mock_result]

    async def test_reaction_lifecycle_single_route_success(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """Single-route success transitions :eye -> :done."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        telegram_module._set_message_reaction = AsyncMock(  # type: ignore[method-assign]
            return_value={"ok": True}
        )

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="general",
                route_result={"result": "ok"},
                routed_targets=["general"],
                acked_targets=["general"],
            )
        )
        telegram_module.set_pipeline(mock_pipeline)

        update = {
            "update_id": 101,
            "message": {
                "message_id": 77,
                "text": "Need help",
                "chat": {"id": 12345},
            },
        }

        await telegram_module.process_update(update)

        assert telegram_module._set_message_reaction.await_count == 2
        calls = telegram_module._set_message_reaction.await_args_list
        assert calls[0].kwargs["reaction"] == ":eye"
        assert calls[1].kwargs["reaction"] == ":done"

    async def test_reaction_lifecycle_multi_route_waits_for_all_acks(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """Fan-out keeps :eye until all routed targets are acknowledged."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        telegram_module._set_message_reaction = AsyncMock(  # type: ignore[method-assign]
            return_value={"ok": True}
        )

        first = RoutingResult(
            target_butler="multi",
            route_result={"result": "partial"},
            routed_targets=["health", "general"],
            acked_targets=["health"],
            failed_targets=[],
        )
        second = RoutingResult(
            target_butler="multi",
            route_result={"result": "done"},
            routed_targets=["general"],
            acked_targets=["general"],
            failed_targets=[],
        )

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(side_effect=[first, second])
        telegram_module.set_pipeline(mock_pipeline)

        update = {
            "update_id": 202,
            "message": {
                "message_id": 88,
                "text": "Split this message",
                "chat": {"id": 222},
            },
        }

        await telegram_module.process_update(update)
        await telegram_module.process_update(update)

        calls = telegram_module._set_message_reaction.await_args_list
        reactions = [call.kwargs["reaction"] for call in calls]
        assert reactions == [":eye", ":eye", ":done"]

    async def test_reaction_lifecycle_any_failed_route_sets_failure(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """Any sub-route failure transitions to :space invader."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        telegram_module._set_message_reaction = AsyncMock(  # type: ignore[method-assign]
            return_value={"ok": True}
        )

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="multi",
                route_result={"result": "partial"},
                routing_error="general: ConnectionError: timeout",
                routed_targets=["health", "general"],
                acked_targets=["health"],
                failed_targets=["general"],
            )
        )
        telegram_module.set_pipeline(mock_pipeline)

        update = {
            "update_id": 303,
            "message": {
                "message_id": 99,
                "text": "Fanout with error",
                "chat": {"id": 333},
            },
        }

        await telegram_module.process_update(update)

        calls = telegram_module._set_message_reaction.await_args_list
        reactions = [call.kwargs["reaction"] for call in calls]
        assert reactions == [":eye", ":space invader"]

    async def test_failure_reaction_400_logs_warning_and_skips_stack_trace(
        self, telegram_module: TelegramModule, monkeypatch
    ) -> None:
        """Terminal failure reaction 400 is treated as expected and non-fatal."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        response = _mock_response(
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message reactions are disabled in this chat",
            },
            status_code=400,
        )
        status_error = httpx.HTTPStatusError(
            "400 Client Error",
            request=response.request,
            response=response,
        )

        async def mock_set_message_reaction(**kwargs: Any) -> dict[str, Any]:
            if kwargs["reaction"] == ":space invader":
                raise status_error
            return {"ok": True}

        telegram_module._set_message_reaction = mock_set_message_reaction  # type: ignore[method-assign]

        warning_mock = MagicMock()
        exception_mock = MagicMock()
        monkeypatch.setattr(telegram_module_impl.logger, "warning", warning_mock)
        monkeypatch.setattr(telegram_module_impl.logger, "exception", exception_mock)

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="multi",
                route_result={"result": "partial"},
                routing_error="general: ConnectionError: timeout",
                routed_targets=["health", "general"],
                acked_targets=["health"],
                failed_targets=["general"],
            )
        )
        telegram_module.set_pipeline(mock_pipeline)

        update = {
            "update_id": 304,
            "message": {
                "message_id": 100,
                "text": "Fanout with reaction-disabled error",
                "chat": {"id": 334},
            },
        }

        result = await telegram_module.process_update(update)

        assert result is not None
        exception_mock.assert_not_called()
        warning_mock.assert_called_once()
        warning_extra = warning_mock.call_args.kwargs["extra"]
        assert warning_extra["reaction"] == ":space invader"
        assert warning_extra["telegram_status_code"] == 400
        assert warning_extra["telegram_error_description"] == (
            "Bad Request: message reactions are disabled in this chat"
        )
        assert telegram_module._terminal_reactions["334:100"] == ":space invader"

    async def test_terminal_reaction_does_not_regress_to_in_progress(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """Terminal reaction is idempotent and never regresses back to :eye."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        telegram_module._set_message_reaction = AsyncMock(  # type: ignore[method-assign]
            return_value={"ok": True}
        )

        done = RoutingResult(
            target_butler="general",
            route_result={"result": "ok"},
            routed_targets=["general"],
            acked_targets=["general"],
        )
        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(side_effect=[done, done])
        telegram_module.set_pipeline(mock_pipeline)

        update = {
            "update_id": 404,
            "message": {
                "message_id": 1001,
                "text": "Finalize once",
                "chat": {"id": 444},
            },
        }

        await telegram_module.process_update(update)
        first_count = telegram_module._set_message_reaction.await_count
        assert first_count == 2

        await telegram_module.process_update(update)

        assert telegram_module._set_message_reaction.await_count == first_count

    async def test_terminal_cleanup_prunes_per_message_tracking(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """Terminal transitions clear per-message lock/lifecycle tracking."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        telegram_module._set_message_reaction = AsyncMock(  # type: ignore[method-assign]
            return_value={"ok": True}
        )

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="general",
                route_result={"result": "ok"},
                routed_targets=["general"],
                acked_targets=["general"],
            )
        )
        telegram_module.set_pipeline(mock_pipeline)

        update = {
            "update_id": 505,
            "message": {
                "message_id": 111,
                "text": "cleanup tracking",
                "chat": {"id": 555},
            },
        }

        await telegram_module.process_update(update)

        message_key = "555:111"
        assert message_key not in telegram_module._processing_lifecycle
        assert message_key not in telegram_module._reaction_locks
        assert telegram_module._terminal_reactions[message_key] == ":done"

    def test_terminal_reaction_cache_is_bounded(self, telegram_module: TelegramModule, monkeypatch):
        """Terminal cache evicts oldest keys to avoid unbounded growth."""
        monkeypatch.setattr(telegram_module_impl, "TERMINAL_REACTION_CACHE_SIZE", 2)

        telegram_module._record_terminal_reaction("one", ":done")
        telegram_module._record_terminal_reaction("two", ":done")
        telegram_module._record_terminal_reaction("three", ":done")

        assert list(telegram_module._terminal_reactions.keys()) == ["two", "three"]


# ---------------------------------------------------------------------------
# Startup — webhook mode
# ---------------------------------------------------------------------------


class TestWebhookMode:
    """Test webhook mode startup behaviour."""

    async def test_calls_set_webhook(self, telegram_module: TelegramModule, monkeypatch):
        """Webhook mode calls setWebhook API on startup."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response({"ok": True, "result": True})

        # We need to inject the client before on_startup creates a new one
        original_startup = telegram_module.on_startup

        async def patched_startup(config, db):
            await original_startup(config, db)
            # This won't work because on_startup creates a new client.
            # Instead, patch _set_webhook directly.

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
        telegram_module._poll_task = None

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
# connector_registry counter increment
# ---------------------------------------------------------------------------


def _make_mock_db_pool() -> tuple[MagicMock, AsyncMock]:
    """Create a mock asyncpg pool that supports `async with pool.acquire() as conn`.

    Returns a (db, conn) tuple where db is what gets assigned to telegram_module._db.
    The db mock does NOT have a .pool attribute (so _get_db_pool falls through to the
    .acquire path), and supports `async with db.acquire() as conn`.
    """
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=42)
    mock_conn.execute = AsyncMock(return_value=None)

    # Use a real object to avoid MagicMock auto-creating a .pool attribute
    class FakePool:
        """Minimal asyncpg-pool-like object used in tests."""

        def acquire(self):
            return self

        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return False

    fake_pool = FakePool()
    return fake_pool, mock_conn


class TestConnectorCounterIncrement:
    """Verify connector_registry counters are updated from process_update."""

    async def test_increment_connector_counter_ingested_on_success(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """After a successful message_inbox INSERT, counter_messages_ingested is incremented."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_pool, mock_conn = _make_mock_db_pool()
        telegram_module._db = mock_pool

        mock_pipeline = MagicMock()
        mock_result = RoutingResult(
            target_butler="general",
            route_result={"status": "ok"},
            routed_targets=["general"],
            acked_targets=["general"],
        )
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        telegram_module.set_pipeline(mock_pipeline)
        telegram_module._set_message_reaction = AsyncMock(return_value={"ok": True})

        update = {
            "update_id": 1001,
            "message": {"message_id": 10, "text": "Hello", "chat": {"id": 999}},
        }
        await telegram_module.process_update(update)

        # conn.execute is called for: partition ensure (x2), counter increment
        execute_calls = mock_conn.execute.call_args_list
        increment_calls = [
            call for call in execute_calls if "counter_messages_ingested" in str(call)
        ]
        assert len(increment_calls) == 1, (
            f"Expected 1 counter_messages_ingested UPDATE call, got {len(increment_calls)}"
        )
        call_args = increment_calls[0].args
        assert call_args[1] == "telegram_bot"
        assert call_args[2] == "telegram:bot"

    async def test_increment_connector_counter_failed_on_routing_error(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """On routing failure, counter_messages_failed is incremented."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_pool, mock_conn = _make_mock_db_pool()
        telegram_module._db = mock_pool

        mock_pipeline = MagicMock()
        mock_result = RoutingResult(
            target_butler="general",
            route_result={"error": "routing failed"},
            routing_error="routing failed",
        )
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        telegram_module.set_pipeline(mock_pipeline)
        telegram_module._set_message_reaction = AsyncMock(return_value={"ok": True})

        update = {
            "update_id": 1002,
            "message": {"message_id": 11, "text": "Hello", "chat": {"id": 998}},
        }
        await telegram_module.process_update(update)

        # Ingested counter should be incremented on INSERT success
        ingested_calls = [
            call
            for call in mock_conn.execute.call_args_list
            if "counter_messages_ingested" in str(call)
        ]
        assert len(ingested_calls) == 1

        # Failed counter is incremented via a separate pool.acquire() call
        failed_calls = [
            call
            for call in mock_conn.execute.call_args_list
            if "counter_messages_failed" in str(call)
        ]
        assert len(failed_calls) == 1, (
            f"Expected 1 counter_messages_failed UPDATE call, got {len(failed_calls)}"
        )
        call_args = failed_calls[0].args
        assert call_args[1] == "telegram_bot"
        assert call_args[2] == "telegram:bot"

    async def test_counter_increment_failure_is_non_fatal(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """A failure in counter increment does not prevent message processing."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_pool, mock_conn = _make_mock_db_pool()
        telegram_module._db = mock_pool

        # Make execute raise for counter_messages_ingested but not for partition calls
        async def execute_side_effect(query, *args):
            if "counter_messages_ingested" in query:
                raise RuntimeError("DB counter update failed")
            return None

        mock_conn.execute.side_effect = execute_side_effect

        mock_pipeline = MagicMock()
        mock_result = RoutingResult(
            target_butler="general",
            route_result={"status": "ok"},
            routed_targets=["general"],
            acked_targets=["general"],
        )
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        telegram_module.set_pipeline(mock_pipeline)
        telegram_module._set_message_reaction = AsyncMock(return_value={"ok": True})

        update = {
            "update_id": 1003,
            "message": {"message_id": 12, "text": "Hello", "chat": {"id": 997}},
        }
        # Should NOT raise even though counter increment fails
        result = await telegram_module.process_update(update)
        assert result is not None
        assert result.target_butler == "general"

    async def test_no_counter_increment_without_db_pool(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """When no db pool is configured, process_update completes without DB calls."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        # _db is None by default — no pool available
        assert telegram_module._db is None

        mock_pipeline = MagicMock()
        mock_result = RoutingResult(
            target_butler="general",
            route_result={"status": "ok"},
        )
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        telegram_module.set_pipeline(mock_pipeline)

        update = {"message": {"text": "Hello", "chat": {"id": 111}}}
        result = await telegram_module.process_update(update)
        # No error and result is returned correctly
        assert result is not None
        assert result.target_butler == "general"

    async def test_increment_connector_counter_unknown_field_logs_warning(
        self, telegram_module: TelegramModule
    ):
        """Passing an unknown field name to _increment_connector_counter logs a warning."""
        mock_conn = AsyncMock()

        # Should not raise, just log a warning
        await telegram_module._increment_connector_counter(
            mock_conn,
            connector_type="telegram_bot",
            endpoint_identity="telegram:bot",
            field="counter_unknown_field",
        )

        # execute should NOT have been called with an unknown field
        mock_conn.execute.assert_not_called()
