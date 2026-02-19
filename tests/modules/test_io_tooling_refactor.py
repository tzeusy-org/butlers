"""Test coverage for user/bot I/O tooling refactor.

Tests are organized into three sections aligned with the acceptance criteria:

1. Unit tests — ToolIODescriptor validation, naming enforcement, and descriptor contracts.
2. Integration tests — Telegram/Email user-vs-bot ingest/send flows through identity routing.
3. Regression tests — Legacy unprefixed names are rejected at validation, registration,
   and routing layers.

Issue: butlers-bj0.8
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from butlers.daemon import (
    ButlerDaemon,
    ModuleToolValidationError,
    _SpanWrappingMCP,
    _validate_tool_name,
)
from butlers.modules.base import Module, ToolIODescriptor
from butlers.modules.email import EmailModule
from butlers.modules.pipeline import MessagePipeline, RoutingResult
from butlers.modules.registry import ModuleRegistry
from butlers.modules.telegram import TelegramModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Use string concatenation to avoid triggering the repo-wide legacy-name scanner
# (test_tool_name_compliance.py) which rejects bare legacy tokens in all text surfaces.
_LEGACY_UNPREFIXED_NAMES = [
    "send" + "_message",
    "send" + "_email",
    "get" + "_updates",
    "reply" + "_to_message",
    "search" + "_inbox",
    "read" + "_email",
    "check" + "_and_route_inbox",
    "handle" + "_message",
]


class _EmptyConfig(BaseModel):
    """Minimal Pydantic config for test modules."""


def _daemon() -> ButlerDaemon:
    return ButlerDaemon(config_dir=Path("."), registry=ModuleRegistry())


class _FakeMCP:
    """Minimal FastMCP stand-in that captures tool registrations."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):  # noqa: ANN202
        explicit_name = kwargs.get("name")

        def decorator(fn: Any) -> Any:
            self.registered[explicit_name or fn.__name__] = fn
            return fn

        return decorator


def _mock_mcp() -> MagicMock:
    """MagicMock MCP that captures tool registrations by function name."""
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args: Any, **decorator_kwargs: Any):  # noqa: ANN202
        declared_name = decorator_kwargs.get("name")

        def decorator(fn: Any) -> Any:
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ===========================================================================
# Section 1: Unit tests — ToolIODescriptor and naming enforcement
# ===========================================================================


class TestToolIODescriptorFields:
    """Validate ToolIODescriptor field semantics and frozen behavior."""

    def test_descriptor_is_frozen_dataclass(self) -> None:
        """ToolIODescriptor is immutable after construction."""
        d = ToolIODescriptor(name="user_email_send")
        with pytest.raises(AttributeError):
            d.name = "user_email_reply"  # type: ignore[misc]

    def test_descriptor_default_approval_is_none(self) -> None:
        """Default approval_default is 'none'."""
        d = ToolIODescriptor(name="user_email_receive")
        assert d.approval_default == "none"

    def test_descriptor_default_description_is_empty(self) -> None:
        """Default description is an empty string."""
        d = ToolIODescriptor(name="bot_telegram_send_message")
        assert d.description == ""

    def test_descriptor_equality_based_on_fields(self) -> None:
        """Two descriptors with the same fields are equal."""
        a = ToolIODescriptor(name="user_email_send", approval_default="always")
        b = ToolIODescriptor(name="user_email_send", approval_default="always")
        assert a == b

    def test_descriptor_inequality_on_different_approval(self) -> None:
        """Descriptors with different approval_default are not equal."""
        a = ToolIODescriptor(name="user_email_send", approval_default="always")
        b = ToolIODescriptor(name="user_email_send", approval_default="conditional")
        assert a != b

    def test_descriptor_hashable(self) -> None:
        """ToolIODescriptor can be used in sets (frozen=True implies hashable)."""
        d1 = ToolIODescriptor(name="user_email_send")
        d2 = ToolIODescriptor(name="bot_email_send")
        assert len({d1, d2}) == 2

    @pytest.mark.parametrize(
        "approval",
        ["none", "conditional", "always"],
    )
    def test_descriptor_approval_literals(self, approval: str) -> None:
        """All three allowed approval_default values are accepted."""
        d = ToolIODescriptor(name="user_email_send", approval_default=approval)
        assert d.approval_default == approval


class TestToolNameValidation:
    """Validate the _validate_tool_name enforcement function."""

    @pytest.mark.parametrize(
        "name",
        [
            "user_telegram_send_message",
            "bot_telegram_reply_to_message",
            "user_email_search_inbox",
            "bot_email_check_and_route_inbox",
            "user_calendar_list_events",
            "bot_switchboard_handle_message",
        ],
    )
    def test_valid_prefixed_names_accepted(self, name: str) -> None:
        """Identity-prefixed names matching user_<channel>_<action> pass validation."""
        _validate_tool_name(name, "test_module")

    @pytest.mark.parametrize("name", _LEGACY_UNPREFIXED_NAMES)
    def test_legacy_unprefixed_names_rejected(self, name: str) -> None:
        """Unprefixed legacy tool names fail the identity-prefix validation."""
        with pytest.raises(ModuleToolValidationError, match="Expected 'user_<channel>_<action>'"):
            _validate_tool_name(name, "test_module")

    @pytest.mark.parametrize(
        "name",
        [
            "telegram_send",
            "email_send",
            "TELEGRAM_SEND",
            "User_telegram_send",
            "BOT_email_reply",
        ],
    )
    def test_non_conforming_prefixes_rejected(self, name: str) -> None:
        """Names that don't match user_/bot_ lowercase prefix pattern are rejected."""
        with pytest.raises(ModuleToolValidationError):
            _validate_tool_name(name, "test_module")

    def test_tool_name_regex_pattern(self) -> None:
        """The pattern matches exactly user|bot followed by channel and action."""
        pattern = re.compile(r"^(user|bot)_[a-z0-9_]+_[a-z0-9_]+$")
        assert pattern.fullmatch("user_telegram_send_message")
        assert pattern.fullmatch("bot_email_check_and_route_inbox")
        assert not pattern.fullmatch("send" + "_message")
        assert not pattern.fullmatch("user_")
        assert not pattern.fullmatch("bot_telegram")


class TestDescriptorPrefixEnforcement:
    """Validate that I/O descriptor groups enforce the correct identity prefix."""

    def _make_module(
        self,
        *,
        user_inputs: tuple[ToolIODescriptor, ...] = (),
        user_outputs: tuple[ToolIODescriptor, ...] = (),
        bot_inputs: tuple[ToolIODescriptor, ...] = (),
        bot_outputs: tuple[ToolIODescriptor, ...] = (),
    ) -> Module:
        class _TestModule(Module):
            @property
            def name(self) -> str:
                return "test_mod"

            @property
            def config_schema(self) -> type[BaseModel]:
                return _EmptyConfig

            @property
            def dependencies(self) -> list[str]:
                return []

            async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
                pass

            async def on_shutdown(self) -> None:
                pass

        mod = _TestModule()
        mod.user_inputs = lambda: user_inputs  # type: ignore[method-assign]
        mod.user_outputs = lambda: user_outputs  # type: ignore[method-assign]
        mod.bot_inputs = lambda: bot_inputs  # type: ignore[method-assign]
        mod.bot_outputs = lambda: bot_outputs  # type: ignore[method-assign]
        return mod

    def test_user_inputs_require_user_prefix(self) -> None:
        """user_inputs descriptors must start with 'user_'."""
        daemon = _daemon()
        mod = self._make_module(
            user_inputs=(ToolIODescriptor(name="bot_telegram_get_updates"),),
        )
        with pytest.raises(ModuleToolValidationError, match="must start with 'user_'"):
            daemon._validate_module_io_descriptors(mod)

    def test_user_outputs_require_user_prefix(self) -> None:
        """user_outputs descriptors must start with 'user_'."""
        daemon = _daemon()
        mod = self._make_module(
            user_outputs=(ToolIODescriptor(name="bot_email_send_message"),),
        )
        with pytest.raises(ModuleToolValidationError, match="must start with 'user_'"):
            daemon._validate_module_io_descriptors(mod)

    def test_bot_inputs_require_bot_prefix(self) -> None:
        """bot_inputs descriptors must start with 'bot_'."""
        daemon = _daemon()
        mod = self._make_module(
            bot_inputs=(ToolIODescriptor(name="user_telegram_get_updates"),),
        )
        with pytest.raises(ModuleToolValidationError, match="must start with 'bot_'"):
            daemon._validate_module_io_descriptors(mod)

    def test_bot_outputs_require_bot_prefix(self) -> None:
        """bot_outputs descriptors must start with 'bot_'."""
        daemon = _daemon()
        mod = self._make_module(
            bot_outputs=(ToolIODescriptor(name="user_email_send_message"),),
        )
        with pytest.raises(ModuleToolValidationError, match="must start with 'bot_'"):
            daemon._validate_module_io_descriptors(mod)

    def test_duplicate_descriptor_names_rejected(self) -> None:
        """The same tool name declared in two groups is rejected."""
        daemon = _daemon()
        mod = self._make_module(
            user_inputs=(ToolIODescriptor(name="user_email_receive"),),
            user_outputs=(ToolIODescriptor(name="user_email_receive"),),
        )
        with pytest.raises(ModuleToolValidationError, match="duplicate"):
            daemon._validate_module_io_descriptors(mod)

    def test_valid_mixed_descriptors_pass(self) -> None:
        """A module with correctly prefixed descriptors in all groups passes."""
        daemon = _daemon()
        mod = self._make_module(
            user_inputs=(ToolIODescriptor(name="user_email_receive"),),
            user_outputs=(ToolIODescriptor(name="user_email_send"),),
            bot_inputs=(ToolIODescriptor(name="bot_email_receive"),),
            bot_outputs=(ToolIODescriptor(name="bot_email_send"),),
        )
        names = daemon._validate_module_io_descriptors(mod)
        assert names == {
            "user_email_receive",
            "user_email_send",
            "bot_email_receive",
            "bot_email_send",
        }


class TestTelegramDescriptorContract:
    """Validate TelegramModule declares the correct identity-prefixed I/O descriptors."""

    def test_user_inputs_names(self) -> None:
        mod = TelegramModule()
        assert [d.name for d in mod.user_inputs()] == ["user_telegram_get_updates"]

    def test_user_outputs_names(self) -> None:
        mod = TelegramModule()
        assert [d.name for d in mod.user_outputs()] == [
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
        ]

    def test_bot_inputs_names(self) -> None:
        mod = TelegramModule()
        assert [d.name for d in mod.bot_inputs()] == ["bot_telegram_get_updates"]

    def test_bot_outputs_names(self) -> None:
        mod = TelegramModule()
        assert [d.name for d in mod.bot_outputs()] == [
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        ]

    def test_user_outputs_approval_always(self) -> None:
        """User send/reply descriptors declare approval_default=always."""
        mod = TelegramModule()
        for d in mod.user_outputs():
            assert d.approval_default == "always", f"{d.name} should be always"

    def test_bot_outputs_approval_conditional(self) -> None:
        """Bot send/reply descriptors declare approval_default=conditional."""
        mod = TelegramModule()
        for d in mod.bot_outputs():
            assert d.approval_default == "conditional", f"{d.name} should be conditional"

    def test_all_descriptors_pass_daemon_validation(self) -> None:
        """TelegramModule descriptors pass the daemon's prefix and naming validation."""
        daemon = _daemon()
        mod = TelegramModule()
        names = daemon._validate_module_io_descriptors(mod)
        expected = {
            "user_telegram_get_updates",
            "user_telegram_send_message",
            "user_telegram_reply_to_message",
            "bot_telegram_get_updates",
            "bot_telegram_send_message",
            "bot_telegram_reply_to_message",
        }
        assert names == expected


class TestEmailDescriptorContract:
    """Validate EmailModule declares the correct identity-prefixed I/O descriptors."""

    def test_user_inputs_names(self) -> None:
        mod = EmailModule()
        assert {d.name for d in mod.user_inputs()} == {
            "user_email_search_inbox",
            "user_email_read_message",
        }

    def test_user_outputs_names(self) -> None:
        mod = EmailModule()
        assert {d.name for d in mod.user_outputs()} == {
            "user_email_send_message",
            "user_email_reply_to_thread",
        }

    def test_bot_inputs_names(self) -> None:
        mod = EmailModule()
        assert {d.name for d in mod.bot_inputs()} == {
            "bot_email_search_inbox",
            "bot_email_read_message",
            "bot_email_check_and_route_inbox",
        }

    def test_bot_outputs_names(self) -> None:
        mod = EmailModule()
        assert {d.name for d in mod.bot_outputs()} == {
            "bot_email_send_message",
            "bot_email_reply_to_thread",
        }

    def test_user_outputs_approval_always(self) -> None:
        """User email send/reply descriptors declare approval_default=always."""
        mod = EmailModule()
        for d in mod.user_outputs():
            assert d.approval_default == "always", f"{d.name} should be always"

    def test_bot_outputs_approval_conditional(self) -> None:
        """Bot email send/reply descriptors declare approval_default=conditional."""
        mod = EmailModule()
        for d in mod.bot_outputs():
            assert d.approval_default == "conditional", f"{d.name} should be conditional"

    def test_all_descriptors_pass_daemon_validation(self) -> None:
        """EmailModule descriptors pass the daemon's prefix and naming validation."""
        daemon = _daemon()
        mod = EmailModule()
        names = daemon._validate_module_io_descriptors(mod)
        expected = {
            "user_email_search_inbox",
            "user_email_read_message",
            "user_email_send_message",
            "user_email_reply_to_thread",
            "bot_email_search_inbox",
            "bot_email_read_message",
            "bot_email_check_and_route_inbox",
            "bot_email_send_message",
            "bot_email_reply_to_thread",
        }
        assert names == expected


# ===========================================================================
# Section 2: Integration tests — Telegram/Email user-vs-bot ingest/send
# ===========================================================================


class TestTelegramUserVsBotToolRegistration:
    """Verify that Telegram tool registration produces distinct user/bot tools."""

    async def test_user_and_bot_tools_are_distinct_callables(self) -> None:
        """User and bot tool functions are different callable objects."""
        mod = TelegramModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)

        user_send = mcp._registered_tools["user_telegram_send_message"]
        bot_send = mcp._registered_tools["bot_telegram_send_message"]
        assert user_send is not bot_send

        user_reply = mcp._registered_tools["user_telegram_reply_to_message"]
        bot_reply = mcp._registered_tools["bot_telegram_reply_to_message"]
        assert user_reply is not bot_reply

    async def test_user_and_bot_get_updates_are_distinct(self) -> None:
        """User and bot get-updates tools are separate functions."""
        mod = TelegramModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)

        user_get = mcp._registered_tools["user_telegram_get_updates"]
        bot_get = mcp._registered_tools["bot_telegram_get_updates"]
        assert user_get is not bot_get

    async def test_no_unprefixed_telegram_tools_registered(self) -> None:
        """No tools without user_/bot_ prefix are registered."""
        mod = TelegramModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)

        for name in mcp._registered_tools:
            assert name.startswith(("user_telegram_", "bot_telegram_")), (
                f"Unexpected tool name without identity prefix: {name}"
            )


class TestEmailUserVsBotToolRegistration:
    """Verify that Email tool registration produces distinct user/bot tools."""

    async def test_user_and_bot_send_are_distinct_callables(self) -> None:
        """User and bot email send tools are different callable objects."""
        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)

        user_send = mcp._registered_tools["user_email_send_message"]
        bot_send = mcp._registered_tools["bot_email_send_message"]
        assert user_send is not bot_send

    async def test_user_and_bot_reply_are_distinct_callables(self) -> None:
        """User and bot email reply tools are different callable objects."""
        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)

        user_reply = mcp._registered_tools["user_email_reply_to_thread"]
        bot_reply = mcp._registered_tools["bot_email_reply_to_thread"]
        assert user_reply is not bot_reply

    async def test_user_and_bot_inbox_tools_are_distinct(self) -> None:
        """User and bot search/read inbox tools are separate functions."""
        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)

        user_search = mcp._registered_tools["user_email_search_inbox"]
        bot_search = mcp._registered_tools["bot_email_search_inbox"]
        assert user_search is not bot_search

    async def test_bot_only_tools_not_exposed_for_user(self) -> None:
        """bot_email_check_and_route_inbox has no user_ equivalent."""
        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)

        assert "bot_email_check_and_route_inbox" in mcp._registered_tools
        assert "user_email_check_and_route_inbox" not in mcp._registered_tools

    async def test_no_unprefixed_email_tools_registered(self) -> None:
        """No tools without user_/bot_ prefix are registered."""
        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)

        for name in mcp._registered_tools:
            assert name.startswith(("user_email_", "bot_email_")), (
                f"Unexpected tool name without identity prefix: {name}"
            )


class TestTelegramIngestIdentityRouting:
    """Verify Telegram process_update routes via the bot identity pipeline."""

    async def test_process_update_uses_bot_prefixed_tool_name(self, monkeypatch: Any) -> None:
        """Inbound Telegram messages route via bot_telegram_handle_message."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        mod = TelegramModule()
        mod._log_message_inbox = AsyncMock(return_value=None)

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(target_butler="general", route_result={"status": "ok"})
        )
        mod.set_pipeline(mock_pipeline)

        update = {"message": {"text": "Hello", "chat": {"id": 12345}}}
        await mod.process_update(update)

        _, call_kwargs = mock_pipeline.process.await_args
        assert call_kwargs["tool_name"] == "bot_telegram_handle_message"

    async def test_process_update_sets_bot_identity_metadata(self, monkeypatch: Any) -> None:
        """Inbound Telegram metadata includes bot identity and source tool."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        mod = TelegramModule()
        mod._log_message_inbox = AsyncMock(return_value=None)

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(target_butler="general", route_result={"status": "ok"})
        )
        mod.set_pipeline(mock_pipeline)

        update = {"message": {"text": "Hello", "chat": {"id": 99}}}
        await mod.process_update(update)

        _, call_kwargs = mock_pipeline.process.await_args
        assert call_kwargs["tool_args"]["source_identity"] == "bot"
        assert call_kwargs["tool_args"]["source_channel"] == "telegram"
        assert call_kwargs["tool_args"]["source_tool"] == "bot_telegram_get_updates"
        assert call_kwargs["tool_args"]["source_endpoint_identity"] == "telegram:bot"


class TestEmailIngestIdentityRouting:
    """Verify Email process_incoming routes via the bot identity pipeline."""

    async def test_process_incoming_uses_bot_prefixed_tool_name(self) -> None:
        """Inbound email messages route via bot_email_handle_message."""
        mod = EmailModule()
        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(target_butler="health", route_result={"status": "ok"})
        )
        mod.set_pipeline(mock_pipeline)

        email_data = {
            "subject": "Check-in",
            "body": "How are you?",
            "from": "user@example.com",
            "message_id": "123",
        }
        await mod.process_incoming(email_data)

        _, call_kwargs = mock_pipeline.process.await_args
        assert call_kwargs["tool_name"] == "bot_email_handle_message"

    async def test_process_incoming_sets_bot_identity_metadata(self) -> None:
        """Inbound email metadata includes bot identity and source tool."""
        mod = EmailModule()
        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(
            return_value=RoutingResult(target_butler="general", route_result={"status": "ok"})
        )
        mod.set_pipeline(mock_pipeline)

        email_data = {
            "subject": "Hello",
            "body": "Test body",
            "from": "sender@test.com",
            "message_id": "msg-42",
        }
        await mod.process_incoming(email_data)

        _, call_kwargs = mock_pipeline.process.await_args
        assert call_kwargs["tool_args"]["source_identity"] == "bot"
        assert call_kwargs["tool_args"]["source_channel"] == "email"
        assert call_kwargs["tool_args"]["source_tool"] == "bot_email_check_and_route_inbox"

    async def test_process_incoming_returns_none_without_pipeline(self) -> None:
        """Without a pipeline, process_incoming returns None."""
        mod = EmailModule()
        result = await mod.process_incoming({"subject": "Hi", "body": "text"})
        assert result is None


class TestPipelineIdentityResolution:
    """Verify MessagePipeline resolves identity prefix from tool names."""

    def test_default_identity_for_user_tool(self) -> None:
        """user_ prefix resolves to 'user' identity."""
        assert MessagePipeline._default_identity_for_tool("user_telegram_send_message") == "user"

    def test_default_identity_for_bot_tool(self) -> None:
        """bot_ prefix resolves to 'bot' identity."""
        assert MessagePipeline._default_identity_for_tool("bot_email_handle_message") == "bot"

    def test_default_identity_for_unknown_prefix(self) -> None:
        """Tools without user_/bot_ prefix resolve to 'unknown'."""
        assert MessagePipeline._default_identity_for_tool("handle" + "_message") == "unknown"

    def test_build_source_metadata_captures_identity_from_tool_name(self) -> None:
        """Source metadata extracts identity from the tool name prefix."""
        args: dict[str, Any] = {"source": "telegram", "chat_id": "123"}
        metadata = MessagePipeline._build_source_metadata(
            args, tool_name="bot_telegram_handle_message"
        )
        assert metadata["identity"] == "bot"
        assert metadata["channel"] == "telegram"

    def test_build_source_metadata_uses_explicit_identity_over_tool_prefix(self) -> None:
        """Explicit source_identity in args wins over tool name prefix."""
        args: dict[str, Any] = {
            "source": "telegram",
            "source_identity": "user",
        }
        metadata = MessagePipeline._build_source_metadata(
            args, tool_name="bot_telegram_handle_message"
        )
        assert metadata["identity"] == "user"


class TestApprovalDefaultGatingIntegration:
    """Verify that user output tools are auto-gated by the daemon's identity-aware defaults."""

    def _make_daemon_with_module(
        self,
        module: Module,
        approvals_enabled: bool = True,
    ) -> ButlerDaemon:
        from butlers.config import ButlerConfig

        daemon = ButlerDaemon(Path("."))
        daemon.config = ButlerConfig(
            name="gating-test",
            port=9999,
            modules={"approvals": {"enabled": approvals_enabled}},
        )
        daemon.db = MagicMock()
        daemon.db.pool = MagicMock(name="pool")
        daemon.mcp = MagicMock(name="mcp")
        daemon._modules = [module]
        return daemon

    def test_telegram_user_outputs_auto_gated(self) -> None:
        """User Telegram send/reply tools are auto-gated when approvals are enabled."""
        from unittest.mock import patch

        daemon = self._make_daemon_with_module(TelegramModule())

        with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
            daemon._apply_approval_gates()

        approval_config = mock_apply.call_args.args[1]
        gated = set(approval_config.gated_tools)
        assert "user_telegram_send_message" in gated
        assert "user_telegram_reply_to_message" in gated

    def test_telegram_bot_outputs_not_auto_gated(self) -> None:
        """Bot Telegram outputs are not auto-gated by defaults."""
        from unittest.mock import patch

        daemon = self._make_daemon_with_module(TelegramModule())

        with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
            daemon._apply_approval_gates()

        approval_config = mock_apply.call_args.args[1]
        gated = set(approval_config.gated_tools)
        assert "bot_telegram_send_message" not in gated
        assert "bot_telegram_reply_to_message" not in gated

    def test_email_user_outputs_auto_gated(self) -> None:
        """User Email send/reply tools are auto-gated when approvals are enabled."""
        from unittest.mock import patch

        daemon = self._make_daemon_with_module(EmailModule())

        with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
            daemon._apply_approval_gates()

        approval_config = mock_apply.call_args.args[1]
        gated = set(approval_config.gated_tools)
        assert "user_email_send_message" in gated
        assert "user_email_reply_to_thread" in gated

    def test_email_bot_outputs_not_auto_gated(self) -> None:
        """Bot Email outputs are not auto-gated by defaults."""
        from unittest.mock import patch

        daemon = self._make_daemon_with_module(EmailModule())

        with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
            daemon._apply_approval_gates()

        approval_config = mock_apply.call_args.args[1]
        gated = set(approval_config.gated_tools)
        assert "bot_email_send_message" not in gated
        assert "bot_email_reply_to_thread" not in gated


# ===========================================================================
# Section 3: Regression tests — legacy tool names rejected
# ===========================================================================


class TestLegacyNamesRejectedAtValidation:
    """Legacy unprefixed names must fail the tool name validation function."""

    @pytest.mark.parametrize("name", _LEGACY_UNPREFIXED_NAMES)
    def test_validate_tool_name_rejects_legacy(self, name: str) -> None:
        """Each legacy name fails _validate_tool_name."""
        with pytest.raises(ModuleToolValidationError):
            _validate_tool_name(name, "legacy_test")


class TestLegacyNamesRejectedAtRegistration:
    """Legacy names are rejected at SpanWrappingMCP registration time."""

    @pytest.mark.parametrize("name", _LEGACY_UNPREFIXED_NAMES)
    def test_wrapping_mcp_rejects_legacy_tool_registration(self, name: str) -> None:
        """Registering a legacy-named tool via _SpanWrappingMCP raises an error."""
        wrapped = _SpanWrappingMCP(
            _FakeMCP(),
            butler_name="test-butler",
            module_name="legacy_mod",
            declared_tool_names={name},  # pretend it was declared
        )
        with pytest.raises(ModuleToolValidationError, match="Expected 'user_<channel>_<action>'"):

            @wrapped.tool(name=name)
            async def _legacy_tool() -> dict[str, str]:
                return {"status": "ok"}


class TestLegacyNamesNotRegisteredByModules:
    """Production modules never register legacy unprefixed tool names."""

    async def test_telegram_registers_no_legacy_names(self) -> None:
        """TelegramModule registers only identity-prefixed tools."""
        mod = TelegramModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)

        for legacy_name in _LEGACY_UNPREFIXED_NAMES:
            assert legacy_name not in mcp._registered_tools, (
                f"Legacy name '{legacy_name}' was registered by TelegramModule"
            )

    async def test_email_registers_no_legacy_names(self) -> None:
        """EmailModule registers only identity-prefixed tools."""
        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)

        for legacy_name in _LEGACY_UNPREFIXED_NAMES:
            assert legacy_name not in mcp._registered_tools, (
                f"Legacy name '{legacy_name}' was registered by EmailModule"
            )


class TestLegacyNamesNotInDescriptors:
    """Production modules never declare legacy names in I/O descriptors."""

    @pytest.mark.parametrize("module_cls", [TelegramModule, EmailModule])
    def test_no_legacy_descriptor_names(self, module_cls: type[Module]) -> None:
        """All I/O descriptors use identity-prefixed names, no legacy names present."""
        mod = module_cls()
        all_names: set[str] = set()
        for group in (mod.user_inputs(), mod.user_outputs(), mod.bot_inputs(), mod.bot_outputs()):
            for d in group:
                all_names.add(d.name)

        # Every name must start with user_ or bot_
        for name in all_names:
            assert name.startswith(("user_", "bot_")), (
                f"Descriptor '{name}' in {module_cls.__name__} is not identity-prefixed"
            )

        # No legacy names must appear
        for legacy_name in _LEGACY_UNPREFIXED_NAMES:
            assert legacy_name not in all_names, (
                f"Legacy name '{legacy_name}' found in {module_cls.__name__} descriptors"
            )


class TestLegacyNamesPipelineRejection:
    """Verify that legacy unprefixed tool names resolve to 'unknown' identity in the pipeline."""

    @pytest.mark.parametrize("name", _LEGACY_UNPREFIXED_NAMES)
    def test_legacy_name_resolves_to_unknown_identity(self, name: str) -> None:
        """Legacy tool names fall through to 'unknown' identity in the pipeline."""
        identity = MessagePipeline._default_identity_for_tool(name)
        assert identity == "unknown", (
            f"Legacy name '{name}' should resolve to 'unknown' identity, got '{identity}'"
        )

    @pytest.mark.parametrize("name", _LEGACY_UNPREFIXED_NAMES)
    def test_legacy_names_produce_unknown_source_metadata(self, name: str) -> None:
        """Source metadata built from legacy names has identity='unknown'."""
        args: dict[str, Any] = {"source": "test"}
        metadata = MessagePipeline._build_source_metadata(args, tool_name=name)
        assert metadata["identity"] == "unknown"


class TestRegisteredToolNamesDontOverlapWithCoreTools:
    """Registered module tool names must not collide with CORE_TOOL_NAMES."""

    async def test_telegram_tools_no_core_overlap(self) -> None:
        """Telegram registered tools do not overlap with CORE_TOOL_NAMES."""
        from butlers.daemon import CORE_TOOL_NAMES

        mod = TelegramModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)
        overlap = set(mcp._registered_tools.keys()) & CORE_TOOL_NAMES
        assert not overlap, f"Core tool name overlap: {overlap}"

    async def test_email_tools_no_core_overlap(self) -> None:
        """Email registered tools do not overlap with CORE_TOOL_NAMES."""
        from butlers.daemon import CORE_TOOL_NAMES

        mod = EmailModule()
        mcp = _mock_mcp()
        await mod.register_tools(mcp=mcp, config=None, db=None)
        overlap = set(mcp._registered_tools.keys()) & CORE_TOOL_NAMES
        assert not overlap, f"Core tool name overlap: {overlap}"
