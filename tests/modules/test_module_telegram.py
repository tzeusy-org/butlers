"""Tests for the Telegram module."""

from __future__ import annotations

import asyncio
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
LEGACY_TELEGRAM_TOOLS = {"send_message", "get_updates"}
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

    def tool_decorator():
        def decorator(fn):
            tools[fn.__name__] = fn
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
        descriptions = [d.description.lower() for d in telegram_module.user_outputs()]
        assert all("approval required" in description for description in descriptions)


# ---------------------------------------------------------------------------
# TelegramConfig
# ---------------------------------------------------------------------------


class TestTelegramConfig:
    """Verify TelegramConfig validation and defaults."""

    def test_defaults(self):
        """Default config uses polling mode with 1s interval."""
        config = TelegramConfig()
        assert config.mode == "polling"
        assert config.webhook_url is None
        assert config.poll_interval == 1.0
        assert config.user.enabled is False
        assert config.user.token_env == "USER_TELEGRAM_TOKEN"
        assert config.bot.enabled is True
        assert config.bot.token_env == "BUTLER_TELEGRAM_TOKEN"

    def test_polling_mode(self):
        """Polling mode can be set explicitly."""
        config = TelegramConfig(mode="polling", poll_interval=2.0)
        assert config.mode == "polling"
        assert config.poll_interval == 2.0

    def test_webhook_mode(self):
        """Webhook mode with URL."""
        config = TelegramConfig(mode="webhook", webhook_url="https://example.com/hook")
        assert config.mode == "webhook"
        assert config.webhook_url == "https://example.com/hook"

    def test_from_dict(self):
        """Config can be constructed from a dict (as from butler.toml)."""
        config = TelegramConfig(**{"mode": "webhook", "webhook_url": "https://x.com/hook"})
        assert config.mode == "webhook"

    def test_empty_dict_gives_defaults(self):
        """Empty dict produces default config."""
        config = TelegramConfig(**{})
        assert config.mode == "polling"

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

    async def test_does_not_register_legacy_tool_names(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ):
        """Legacy unprefixed tool names are no longer registered."""
        await telegram_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert LEGACY_TELEGRAM_TOOLS.isdisjoint(mock_mcp._registered_tools.keys())

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
    """Test send_message API interaction."""

    async def test_calls_correct_endpoint(self, telegram_module: TelegramModule, monkeypatch):
        """send_message POSTs to the sendMessage endpoint."""
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
    """Test reply_to_message API interaction."""

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
    """Test get_updates API interaction."""

    async def test_calls_correct_endpoint(self, telegram_module: TelegramModule, monkeypatch):
        """get_updates GETs the getUpdates endpoint."""
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
        """get_updates advances _last_update_id to the latest."""
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
        """get_updates returns empty list when no new messages."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = _mock_response({"ok": True, "result": []})
        telegram_module._client = mock_client

        updates = await telegram_module._get_updates()
        assert updates == []
        assert telegram_module._last_update_id == 0

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


# ---------------------------------------------------------------------------
# Startup — polling mode
# ---------------------------------------------------------------------------


class TestPollingMode:
    """Test polling mode startup behaviour."""

    async def test_starts_poll_task(self, telegram_module: TelegramModule, monkeypatch):
        """Polling mode creates an asyncio task for _poll_loop."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        # Mock _poll_loop to avoid real polling
        telegram_module._poll_loop = AsyncMock()  # type: ignore[method-assign]

        await telegram_module.on_startup(config={"mode": "polling"}, db=None)

        assert telegram_module._poll_task is not None
        assert not telegram_module._poll_task.done()

        # Clean up
        await telegram_module.on_shutdown()

    async def test_poll_loop_calls_get_updates(self, telegram_module: TelegramModule, monkeypatch):
        """The poll loop calls _get_updates repeatedly."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        call_count = 0

        async def mock_get_updates():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError
            return []

        telegram_module._get_updates = mock_get_updates  # type: ignore[method-assign]
        telegram_module._config = TelegramConfig(poll_interval=0.01)
        telegram_module._client = AsyncMock(spec=httpx.AsyncClient)

        with pytest.raises(asyncio.CancelledError):
            await telegram_module._poll_loop()

        assert call_count >= 2

    async def test_poll_loop_calls_process_update_for_each_update(
        self, telegram_module: TelegramModule, monkeypatch
    ):
        """Polling forwards each update into process_update()."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        updates = [
            {"update_id": 1, "message": {"text": "one"}},
            {"update_id": 2, "message": {"text": "two"}},
        ]

        call_count = 0

        async def mock_get_updates():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return updates
            raise asyncio.CancelledError

        telegram_module._get_updates = mock_get_updates  # type: ignore[method-assign]
        telegram_module.process_update = AsyncMock()  # type: ignore[method-assign]
        telegram_module._config = TelegramConfig(poll_interval=0.01)
        telegram_module._client = AsyncMock(spec=httpx.AsyncClient)

        with pytest.raises(asyncio.CancelledError):
            await telegram_module._poll_loop()

        assert telegram_module._updates_buffer == updates
        assert telegram_module.process_update.await_count == len(updates)


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
        mock_pipeline.process.assert_awaited_once_with(
            message_text="Need help",
            tool_name="bot_telegram_handle_message",
            tool_args={
                "source": "telegram",
                "source_channel": "telegram",
                "source_identity": "bot",
                "source_tool": "bot_telegram_get_updates",
                "chat_id": "12345",
                "source_id": None,
            },
            message_inbox_id=None,
        )
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
            config={"mode": "webhook", "webhook_url": "https://example.com/hook"},
            db=None,
        )

        telegram_module._set_webhook.assert_called_once_with("https://example.com/hook")

        # No poll task should be created
        assert telegram_module._poll_task is None

        await telegram_module.on_shutdown()

    async def test_webhook_no_url_does_not_set(self, telegram_module: TelegramModule, monkeypatch):
        """Webhook mode without a URL does not call setWebhook."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        telegram_module._set_webhook = AsyncMock()  # type: ignore[method-assign]

        await telegram_module.on_startup(config={"mode": "webhook"}, db=None)

        telegram_module._set_webhook.assert_not_called()
        assert telegram_module._poll_task is None

        await telegram_module.on_shutdown()


# ---------------------------------------------------------------------------
# Shutdown cleanup
# ---------------------------------------------------------------------------


class TestShutdown:
    """Test on_shutdown cleanup behaviour."""

    async def test_cancels_poll_task(self, telegram_module: TelegramModule, monkeypatch):
        """Shutdown cancels the polling task."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")

        # Create a long-running fake task
        async def forever():
            await asyncio.sleep(3600)

        telegram_module._poll_task = asyncio.create_task(forever())
        telegram_module._client = httpx.AsyncClient()

        await telegram_module.on_shutdown()

        assert telegram_module._poll_task.done()
        assert telegram_module._poll_task.cancelled()

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
        modules = reg.load_from_config({"telegram": {"mode": "polling"}})
        assert len(modules) == 1
        assert modules[0].name == "telegram"
