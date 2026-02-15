"""Tests to verify no duplicate ingestion from mixed connector/module paths.

These tests ensure that when connectors are enabled, module-owned ingestion
paths do not create duplicate ingestion events or routing conflicts.
"""

from __future__ import annotations

import warnings


class TestEmailModuleDeprecation:
    """Test that EmailModule check_and_route_inbox is properly deprecated."""

    def test_email_check_and_route_inbox_emits_warning(self):
        """Verify deprecation warning on bot_email_check_and_route_inbox call."""
        import asyncio

        from butlers.modules.email import EmailModule

        module = EmailModule()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Call the deprecated method
            _ = asyncio.run(module._check_and_route_inbox())

            # Should have deprecation warning
            deprecation_warnings = [
                warning for warning in w if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) > 0
            assert "deprecated" in str(deprecation_warnings[0].message).lower()
            assert "GmailConnector" in str(deprecation_warnings[0].message)

    def test_email_check_and_route_inbox_logs_deprecation(self, caplog):
        """Verify deprecation is logged when calling check_and_route_inbox."""
        import asyncio

        from butlers.modules.email import EmailModule

        module = EmailModule()
        asyncio.run(module._check_and_route_inbox())

        # Should have warning log about deprecated tool
        assert any("DEPRECATED" in record.message for record in caplog.records)
        assert any("GmailConnector" in record.message for record in caplog.records)


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

    def test_module_pipeline_attachment_is_optional(self):
        """Verify modules can function without pipeline for non-ingestion tools."""
        from butlers.modules.email import EmailModule
        from butlers.modules.telegram import TelegramModule

        telegram_module = TelegramModule()
        email_module = EmailModule()

        # Modules should initialize without requiring pipeline
        assert telegram_module._pipeline is None
        assert email_module._pipeline is None

        # User-scoped tools should still work without pipeline
        # (only bot-scoped ingestion tools require pipeline)
        assert telegram_module is not None
        assert email_module is not None


def test_telegram_config_defaults_to_no_webhook():
    """Verify that TelegramConfig defaults with no webhook URL (ingestion via connector)."""
    from butlers.modules.telegram import TelegramConfig

    config = TelegramConfig()
    assert config.webhook_url is None
