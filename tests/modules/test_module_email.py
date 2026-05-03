"""Condensed Email module tests — behavioral contract only.

Replaces 41 tests with ~12 focused behavioral tests.

Covers:
- Module ABC compliance
- EmailConfig validation (required credentials_env, defaults)
- Tool registration (expected tools)
- send_email: without credentials returns error dict
- Registry integration

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.email import EmailConfig, EmailModule

pytestmark = pytest.mark.unit

# Default config registers read-only tools; send_tools=True adds send/reply
EXPECTED_EMAIL_READ_TOOLS = {
    "email_search_inbox",
    "email_read_message",
}


@pytest.fixture
def email_module() -> EmailModule:
    return EmailModule()


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
    def test_module_contract(self, email_module: EmailModule) -> None:
        """EmailModule satisfies Module ABC: name, config_schema, registry."""
        from butlers.modules.registry import default_registry

        assert issubclass(EmailModule, Module)
        assert email_module.name == "email"
        assert email_module.config_schema is EmailConfig
        assert "email" in default_registry().available_modules


class TestEmailConfig:
    def test_defaults(self) -> None:
        cfg = EmailConfig()
        assert cfg.smtp_host == "smtp.gmail.com"
        assert cfg.smtp_port == 587
        assert cfg.send_tools is False

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailConfig(unknown_field="x")


class TestToolRegistration:
    async def test_registers_read_tools_by_default(
        self, email_module: EmailModule, mock_mcp: MagicMock
    ) -> None:
        await email_module.register_tools(
            mcp=mock_mcp, config={}, db=None, butler_name="test-butler"
        )
        registered = set(mock_mcp._registered_tools.keys())
        assert EXPECTED_EMAIL_READ_TOOLS.issubset(registered)
        # Send tools NOT registered by default
        assert "email_send_message" not in registered


class TestGmailSendAuditEmit:
    """gmail_send is emitted to dashboard_audit_log on outbound SMTP send."""

    async def test_audit_emitted_on_success(self) -> None:
        mod = EmailModule()
        mock_pool = MagicMock()
        mod.wire_audit_pool(mock_pool)
        mod._butler_name = "test-butler"

        with (
            patch("butlers.modules.email.write_audit_entry", new_callable=AsyncMock) as mock_emit,
            patch.object(
                mod, "_smtp_send", return_value={"status": "sent", "to": "a@b.com", "subject": "Hi"}
            ),
        ):
            result = await mod._send_email("a@b.com", "Hi", "body")

        assert result["status"] == "sent"
        mock_emit.assert_awaited_once()
        call_args = mock_emit.call_args
        assert call_args.args[2] == "gmail_send"
        assert call_args.args[3]["to"] == "a@b.com"
        assert call_args.args[3]["subject"] == "Hi"

    async def test_audit_emitted_on_error(self) -> None:
        mod = EmailModule()
        mock_pool = MagicMock()
        mod.wire_audit_pool(mock_pool)
        mod._butler_name = "test-butler"

        with (
            patch("butlers.modules.email.write_audit_entry", new_callable=AsyncMock) as mock_emit,
            patch.object(mod, "_smtp_send", side_effect=RuntimeError("SMTP failure")),
        ):
            with pytest.raises(RuntimeError, match="SMTP failure"):
                await mod._send_email("a@b.com", "Hi", "body")

        mock_emit.assert_awaited_once()
        call_args = mock_emit.call_args
        assert call_args.args[2] == "gmail_send"
        assert call_args.kwargs["result"] == "error"
        assert "SMTP failure" in call_args.kwargs["error"]

    def test_wire_audit_pool_stores_pool(self) -> None:
        mod = EmailModule()
        pool = MagicMock()
        mod.wire_audit_pool(pool)
        assert mod._audit_pool is pool


class TestSendEmailBehavior:
    async def test_send_without_credentials_raises(
        self, email_module: EmailModule, mock_mcp: MagicMock
    ) -> None:
        await email_module.register_tools(
            mcp=mock_mcp, config={"send_tools": True}, db=None, butler_name="test-butler"
        )
        # No credentials resolved → raises RuntimeError with actionable message
        with pytest.raises(RuntimeError, match="email credentials"):
            await mock_mcp._registered_tools["email_send_message"](
                to="alice@example.com",
                subject="Test",
                body="Hello",
            )
