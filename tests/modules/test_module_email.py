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


class TestEmailSendPermissionEnforcement:
    """The email.send permission gates _send_email (the MCP send path + route.execute).

    Mirrors the spawn gate: a revoked grant blocks the SMTP send outright
    (PermissionDenied raised before any SMTP traffic); a granted/default grant
    lets the send proceed. The gate consults public.permissions via
    butlers.modules.email.require_permission, which fails open on DB error.

    [bu-tzlq6]
    """

    async def test_send_blocked_when_email_send_revoked(self) -> None:
        """Revoked email.send blocks the send before any SMTP call.

        Pre-fix this fails: the matrix was ignored, so the send proceeded.
        """
        from butlers.core.permissions import PermissionDenied

        mod = EmailModule()
        mod._butler_name = "test-butler"

        with (
            patch(
                "butlers.modules.email.require_permission",
                new_callable=AsyncMock,
                side_effect=PermissionDenied("test-butler", "email.send", "revoked by owner"),
            ),
            patch.object(mod, "_smtp_send") as mock_smtp,
        ):
            with pytest.raises(PermissionDenied):
                await mod._send_email("a@b.com", "Hi", "body")
        mock_smtp.assert_not_called()

    async def test_send_allowed_when_email_send_granted(self) -> None:
        """Granted (require_permission returns None) lets the send proceed."""
        mod = EmailModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())

        with (
            patch(
                "butlers.modules.email.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.modules.email.write_audit_entry", new_callable=AsyncMock),
            patch.object(
                mod, "_smtp_send", return_value={"status": "sent", "to": "a@b.com", "subject": "Hi"}
            ) as mock_smtp,
        ):
            result = await mod._send_email("a@b.com", "Hi", "body")

        assert result["status"] == "sent"
        mock_smtp.assert_called_once()


class TestEmailModuleExtraStatusFields:
    """extra_status_fields() emits OAuth/credential health based on google_accounts."""

    async def test_no_pool_returns_empty(self) -> None:
        """Without a DB pool, extra_status_fields returns {} (graceful degradation)."""
        mod = EmailModule()
        assert mod._pool is None
        result = await mod.extra_status_fields()
        assert result == {}

    @pytest.mark.parametrize(
        ("fetchrow_return", "fetchrow_side_effect", "expected"),
        [
            # account status row → (oauth_status, credential_health) mapping
            ({"status": "active"}, None, {"oauth_status": "granted", "credential_health": "ok"}),
            (
                {"status": "revoked"},
                None,
                {"oauth_status": "reauth_needed", "credential_health": "error"},
            ),
            (
                {"status": "expired"},
                None,
                {"oauth_status": "reauth_needed", "credential_health": "error"},
            ),
            (
                None,
                None,
                {"oauth_status": "not_configured", "credential_health": "warning"},
            ),
            # DB query failure degrades to {} (no exception propagated)
            (None, Exception("connection refused"), {}),
        ],
    )
    async def test_status_mapping(self, fetchrow_return, fetchrow_side_effect, expected) -> None:
        """extra_status_fields maps each account status to OAuth/credential health."""
        mod = EmailModule()
        pool = MagicMock()
        if fetchrow_side_effect is not None:
            pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        else:
            pool.fetchrow = AsyncMock(return_value=fetchrow_return)
        mod._pool = pool

        result = await mod.extra_status_fields()

        if expected:
            assert result["oauth_status"] == expected["oauth_status"]
            assert result["credential_health"] == expected["credential_health"]
        else:
            assert result == {}

    async def test_on_startup_stores_pool(self) -> None:
        """on_startup stores db.pool into _pool for later OAuth status queries."""
        mod = EmailModule()
        mock_pool = MagicMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool

        with patch("butlers.credential_store.resolve_owner_entity_info", new_callable=AsyncMock):
            await mod.on_startup(config={}, db=mock_db)

        assert mod._pool is mock_pool
