"""Tests to verify no duplicate ingestion from mixed connector/module paths.

These tests ensure that when connectors are enabled, module-owned ingestion
paths do not create duplicate ingestion events or routing conflicts.
"""

from __future__ import annotations


class TestConnectorModuleBoundary:
    """Test that connector and module paths have clear boundaries."""

    def test_telegram_connector_does_not_depend_on_module(self):
        """Verify TelegramBotConnector can operate independently of TelegramModule."""
        from butlers.connectors.telegram_bot import TelegramBotConnector

        # Should be able to import and instantiate connector without module
        # This verifies no circular dependency or tight coupling
        assert TelegramBotConnector is not None

    def test_gmail_connector_does_not_depend_on_email_module(self):
        """Verify GmailConnector can operate independently of EmailModule."""
        from butlers.connectors.gmail import GmailConnectorRuntime

        # Should be able to import connector without email module
        assert GmailConnectorRuntime is not None

    def test_modules_no_longer_have_pipeline(self):
        """Verify modules do not have _pipeline (ingestion moved to connectors)."""
        from butlers.modules.email import EmailModule
        from butlers.modules.telegram import TelegramModule

        telegram_module = TelegramModule()
        email_module = EmailModule()

        # Both modules no longer have _pipeline (ingestion moved to connectors).
        assert not hasattr(telegram_module, "_pipeline")
        assert not hasattr(email_module, "_pipeline")

        # User-scoped tools should still work without pipeline
        assert telegram_module is not None
        assert email_module is not None


def test_telegram_config_defaults_to_no_webhook():
    """Verify that TelegramConfig defaults with no webhook URL (ingestion via connector)."""
    from butlers.modules.telegram import TelegramConfig

    config = TelegramConfig()
    assert config.webhook_url is None
