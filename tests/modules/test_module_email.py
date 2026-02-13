"""Tests for the Email module."""

from __future__ import annotations

import smtplib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.email import EmailConfig, EmailModule

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABC:
    """Verify EmailModule satisfies the Module abstract base class."""

    def test_is_subclass_of_module(self):
        assert issubclass(EmailModule, Module)

    def test_instantiates(self):
        mod = EmailModule()
        assert isinstance(mod, Module)

    def test_name(self):
        mod = EmailModule()
        assert mod.name == "email"

    def test_config_schema(self):
        mod = EmailModule()
        assert mod.config_schema is EmailConfig
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_empty(self):
        mod = EmailModule()
        assert mod.dependencies == []

    def test_migration_revisions_none(self):
        mod = EmailModule()
        assert mod.migration_revisions() is None


# ---------------------------------------------------------------------------
# Credentials declaration
# ---------------------------------------------------------------------------


class TestCredentials:
    """Verify credential environment variable declarations."""

    def test_credentials_env_property(self):
        mod = EmailModule()
        assert mod.credentials_env == ["BUTLER_EMAIL_ADDRESS", "BUTLER_EMAIL_PASSWORD"]

    def test_credentials_env_contains_address(self):
        mod = EmailModule()
        assert "BUTLER_EMAIL_ADDRESS" in mod.credentials_env

    def test_credentials_env_contains_password(self):
        mod = EmailModule()
        assert "BUTLER_EMAIL_PASSWORD" in mod.credentials_env


# ---------------------------------------------------------------------------
# EmailConfig validation
# ---------------------------------------------------------------------------


class TestEmailConfig:
    """Verify config schema defaults and custom values."""

    def test_defaults(self):
        cfg = EmailConfig()
        assert cfg.smtp_host == "smtp.gmail.com"
        assert cfg.smtp_port == 587
        assert cfg.imap_host == "imap.gmail.com"
        assert cfg.imap_port == 993
        assert cfg.use_tls is True
        assert cfg.user.enabled is False
        assert cfg.user.address_env == "USER_EMAIL_ADDRESS"
        assert cfg.user.password_env == "USER_EMAIL_PASSWORD"
        assert cfg.bot.enabled is True
        assert cfg.bot.address_env == "BUTLER_EMAIL_ADDRESS"
        assert cfg.bot.password_env == "BUTLER_EMAIL_PASSWORD"

    def test_custom_values(self):
        cfg = EmailConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            imap_host="imap.example.com",
            imap_port=143,
            use_tls=False,
        )
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.smtp_port == 465
        assert cfg.imap_host == "imap.example.com"
        assert cfg.imap_port == 143
        assert cfg.use_tls is False

    def test_partial_override(self):
        cfg = EmailConfig(smtp_host="mail.custom.org")
        assert cfg.smtp_host == "mail.custom.org"
        # Remaining fields keep defaults
        assert cfg.smtp_port == 587
        assert cfg.imap_host == "imap.gmail.com"

    def test_invalid_bot_env_name_rejected(self):
        with pytest.raises(ValueError, match="modules.email.bot.address_env"):
            EmailConfig(**{"bot": {"address_env": "1INVALID"}})

    def test_invalid_user_env_name_rejected(self):
        with pytest.raises(ValueError, match="modules.email.user.password_env"):
            EmailConfig(**{"user": {"password_env": "bad-value"}})


# ---------------------------------------------------------------------------
# on_startup / on_shutdown
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Verify startup and shutdown lifecycle hooks."""

    async def test_on_startup_initializes_config(self):
        mod = EmailModule()
        await mod.on_startup(config={"smtp_host": "smtp.test.com"}, db=None)
        assert mod._config.smtp_host == "smtp.test.com"
        assert mod._config.smtp_port == 587  # default preserved

    async def test_on_startup_with_none_config(self):
        mod = EmailModule()
        await mod.on_startup(config=None, db=None)
        assert mod._config.smtp_host == "smtp.gmail.com"

    async def test_on_startup_with_empty_config(self):
        mod = EmailModule()
        await mod.on_startup(config={}, db=None)
        assert mod._config.smtp_host == "smtp.gmail.com"

    async def test_on_shutdown_completes(self):
        mod = EmailModule()
        await mod.on_startup(config=None, db=None)
        # Should complete without error
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegisterTools:
    """Verify that register_tools creates the expected MCP tools."""

    async def test_registers_four_tools(self):
        mod = EmailModule()
        mcp = MagicMock()
        # mcp.tool() returns a decorator that returns the function unchanged
        mcp.tool.return_value = lambda fn: fn

        await mod.register_tools(mcp=mcp, config=None, db=None)

        # tool() should have been called 4 times
        # (send_email, search_inbox, read_email, check_and_route_inbox)
        assert mcp.tool.call_count == 4

    async def test_tool_decorator_called(self):
        mod = EmailModule()
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        await mod.register_tools(mcp=mcp, config=None, db=None)

        assert "send_email" in registered_tools
        assert "search_inbox" in registered_tools
        assert "read_email" in registered_tools
        assert "check_and_route_inbox" in registered_tools

    async def test_registered_tools_are_async(self):
        mod = EmailModule()
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        await mod.register_tools(mcp=mcp, config=None, db=None)

        import asyncio

        for tool_name, tool_fn in registered_tools.items():
            assert asyncio.iscoroutinefunction(tool_fn), f"{tool_name} should be async"


# ---------------------------------------------------------------------------
# Mocked SMTP — send_email
# ---------------------------------------------------------------------------


class TestSendEmail:
    """Verify send_email with mocked SMTP connections."""

    async def test_send_email_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)

        with patch("butlers.modules.email.smtplib.SMTP", mock_smtp_cls):
            result = await mod._send_email(
                to="recipient@example.com",
                subject="Test Subject",
                body="Hello, World!",
            )

        assert result["status"] == "sent"
        assert result["to"] == "recipient@example.com"
        assert result["subject"] == "Test Subject"

        mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587)
        mock_smtp_instance.starttls.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("test@example.com", "secret123")
        mock_smtp_instance.sendmail.assert_called_once()
        mock_smtp_instance.quit.assert_called_once()

    async def test_send_email_no_tls(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config={"use_tls": False}, db=None)

        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)

        with patch("butlers.modules.email.smtplib.SMTP", mock_smtp_cls):
            result = await mod._send_email(
                to="recipient@example.com",
                subject="Test",
                body="Body",
            )

        assert result["status"] == "sent"
        mock_smtp_instance.starttls.assert_not_called()

    async def test_send_email_missing_credentials(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BUTLER_EMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("BUTLER_EMAIL_PASSWORD", raising=False)

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        with pytest.raises(RuntimeError, match="modules.email.bot"):
            await mod._send_email(
                to="recipient@example.com",
                subject="Test",
                body="Body",
            )

    async def test_send_email_smtp_error_still_quits(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        mock_smtp_instance = MagicMock()
        mock_smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)

        with (
            patch("butlers.modules.email.smtplib.SMTP", mock_smtp_cls),
            pytest.raises(smtplib.SMTPAuthenticationError),
        ):
            await mod._send_email(
                to="recipient@example.com",
                subject="Test",
                body="Body",
            )

        # quit() should still be called via finally block
        mock_smtp_instance.quit.assert_called_once()


# ---------------------------------------------------------------------------
# Mocked IMAP — search_inbox
# ---------------------------------------------------------------------------


class TestSearchInbox:
    """Verify search_inbox with mocked IMAP connections."""

    async def test_search_inbox_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        # Build a minimal RFC822 header
        raw_header = (
            b"From: sender@example.com\r\n"
            b"Subject: Test Email\r\n"
            b"Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
            b"\r\n"
        )

        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b"1 2"])
        mock_conn.fetch.return_value = ("OK", [(b"1 (RFC822.HEADER {100}", raw_header)])

        mock_imap_cls = MagicMock(return_value=mock_conn)

        with patch("butlers.modules.email.imaplib.IMAP4_SSL", mock_imap_cls):
            results = await mod._search_inbox("ALL")

        assert len(results) == 2
        assert results[0]["from"] == "sender@example.com"
        assert results[0]["subject"] == "Test Email"

        mock_imap_cls.assert_called_once_with("imap.gmail.com", 993)
        mock_conn.login.assert_called_once_with("test@example.com", "secret123")
        mock_conn.select.assert_called_once_with("INBOX")
        mock_conn.logout.assert_called_once()

    async def test_search_inbox_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b""])

        mock_imap_cls = MagicMock(return_value=mock_conn)

        with patch("butlers.modules.email.imaplib.IMAP4_SSL", mock_imap_cls):
            results = await mod._search_inbox("UNSEEN")

        assert results == []
        mock_conn.logout.assert_called_once()

    async def test_search_inbox_no_tls(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config={"use_tls": False, "imap_port": 143}, db=None)

        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b""])
        mock_imap_cls = MagicMock(return_value=mock_conn)

        with patch("butlers.modules.email.imaplib.IMAP4", mock_imap_cls):
            results = await mod._search_inbox("ALL")

        assert results == []
        mock_imap_cls.assert_called_once_with("imap.gmail.com", 143)


# ---------------------------------------------------------------------------
# Mocked IMAP — read_email
# ---------------------------------------------------------------------------


class TestReadEmail:
    """Verify read_email with mocked IMAP connections."""

    async def test_read_email_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        # Build a minimal RFC822 message
        raw_msg = (
            b"From: sender@example.com\r\n"
            b"To: test@example.com\r\n"
            b"Subject: Test Email\r\n"
            b"Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello, this is a test email body."
        )

        mock_conn = MagicMock()
        mock_conn.fetch.return_value = ("OK", [(b"1 (RFC822 {200}", raw_msg)])

        mock_imap_cls = MagicMock(return_value=mock_conn)

        with patch("butlers.modules.email.imaplib.IMAP4_SSL", mock_imap_cls):
            result = await mod._read_email("1")

        assert result["message_id"] == "1"
        assert result["from"] == "sender@example.com"
        assert result["to"] == "test@example.com"
        assert result["subject"] == "Test Email"
        assert "Hello, this is a test email body." in result["body"]

        mock_conn.login.assert_called_once_with("test@example.com", "secret123")
        mock_conn.select.assert_called_once_with("INBOX")
        mock_conn.logout.assert_called_once()

    async def test_read_email_not_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "test@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "secret123")

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        mock_conn = MagicMock()
        mock_conn.fetch.return_value = ("OK", [None])

        mock_imap_cls = MagicMock(return_value=mock_conn)

        with patch("butlers.modules.email.imaplib.IMAP4_SSL", mock_imap_cls):
            result = await mod._read_email("999")

        assert "error" in result
        assert "999" in result["error"]

    async def test_read_email_missing_credentials(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BUTLER_EMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("BUTLER_EMAIL_PASSWORD", raising=False)

        mod = EmailModule()
        await mod.on_startup(config=None, db=None)

        with pytest.raises(RuntimeError, match="modules.email.bot"):
            await mod._read_email("1")


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------


class TestGetCredentials:
    """Verify _get_credentials helper."""

    def test_returns_tuple(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "me@test.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "pass123")

        mod = EmailModule()
        addr, pwd = mod._get_credentials()
        assert addr == "me@test.com"
        assert pwd == "pass123"

    def test_raises_on_missing_address(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BUTLER_EMAIL_ADDRESS", raising=False)
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "pass123")

        mod = EmailModule()
        with pytest.raises(RuntimeError):
            mod._get_credentials()

    def test_raises_on_missing_password(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "me@test.com")
        monkeypatch.delenv("BUTLER_EMAIL_PASSWORD", raising=False)

        mod = EmailModule()
        with pytest.raises(RuntimeError):
            mod._get_credentials()

    def test_raises_on_both_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BUTLER_EMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("BUTLER_EMAIL_PASSWORD", raising=False)

        mod = EmailModule()
        with pytest.raises(RuntimeError):
            mod._get_credentials()
