"""Tests for identity-aware approval defaults in ButlerDaemon."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon
from butlers.modules.base import Module, ToolIODescriptor

pytestmark = pytest.mark.unit


class _NoopConfig(BaseModel):
    """Minimal config schema for test modules."""


class _DescriptorModule(Module):
    """Module exposing mixed user/bot outputs for default-gating tests."""

    @property
    def name(self) -> str:
        return "descriptor_module"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _NoopConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        return None

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any) -> None:
        return None

    async def on_shutdown(self) -> None:
        return None

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(name="user_email_send_message", approval_default="always"),
            ToolIODescriptor(name="user_email_reply_to_thread", approval_default="always"),
            ToolIODescriptor(name="user_email_create_draft", approval_default="conditional"),
        )

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (
            ToolIODescriptor(name="bot_email_send_message", approval_default="conditional"),
            ToolIODescriptor(name="bot_email_reply_to_thread", approval_default="conditional"),
        )


def _make_daemon(approvals_config: dict[str, Any]) -> ButlerDaemon:
    daemon = ButlerDaemon(Path("."))
    daemon.config = ButlerConfig(
        name="approval-defaults-test",
        port=9999,
        modules={"approvals": approvals_config},
    )
    daemon.db = MagicMock()
    daemon.db.pool = MagicMock(name="pool")
    daemon.mcp = MagicMock(name="mcp")
    daemon._modules = [_DescriptorModule()]
    return daemon


def test_user_send_and_reply_outputs_are_gated_by_default() -> None:
    daemon = _make_daemon({"enabled": True})

    with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
        daemon._apply_approval_gates()  # noqa: SLF001

    approval_config = mock_apply.call_args.args[1]
    assert set(approval_config.gated_tools) == {
        "user_email_send_message",
        "user_email_reply_to_thread",
    }


def test_user_send_and_reply_outputs_are_gated_by_name_safety_net() -> None:
    daemon = _make_daemon({"enabled": True})
    module = daemon._modules[0]
    unsafe_defaults = (
        ToolIODescriptor(name="user_im_send_message", approval_default="none"),
        ToolIODescriptor(name="user_im_reply_to_message", approval_default="conditional"),
        ToolIODescriptor(name="user_im_create_draft", approval_default="none"),
    )

    with (
        patch.object(module, "user_outputs", return_value=unsafe_defaults),
        patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply,
    ):
        daemon._apply_approval_gates()  # noqa: SLF001

    approval_config = mock_apply.call_args.args[1]
    assert set(approval_config.gated_tools) == {
        "user_im_send_message",
        "user_im_reply_to_message",
    }


def test_bot_outputs_are_not_default_gated() -> None:
    daemon = _make_daemon({"enabled": True})

    with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
        daemon._apply_approval_gates()  # noqa: SLF001

    approval_config = mock_apply.call_args.args[1]
    assert "bot_email_send_message" not in approval_config.gated_tools
    assert "bot_email_reply_to_thread" not in approval_config.gated_tools


def test_explicit_bot_config_and_expiry_overrides_are_preserved() -> None:
    daemon = _make_daemon(
        {
            "enabled": True,
            "default_expiry_hours": 48,
            "gated_tools": {
                "bot_email_send_message": {"expiry_hours": 12},
                "user_email_send_message": {"expiry_hours": 6},
            },
        }
    )

    with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
        daemon._apply_approval_gates()  # noqa: SLF001

    approval_config = mock_apply.call_args.args[1]
    assert approval_config.gated_tools["bot_email_send_message"].expiry_hours == 12
    assert approval_config.gated_tools["user_email_send_message"].expiry_hours == 6
    assert "user_email_reply_to_thread" in approval_config.gated_tools
    assert approval_config.gated_tools["user_email_reply_to_thread"].expiry_hours is None


def test_disabled_approvals_skip_default_gating() -> None:
    daemon = _make_daemon({"enabled": False})

    with patch("butlers.daemon.apply_approval_gates", return_value={}) as mock_apply:
        result = daemon._apply_approval_gates()  # noqa: SLF001

    assert result == {}
    mock_apply.assert_not_called()
