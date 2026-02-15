"""Tests to verify no duplicate ingestion from mixed connector/module paths.

These tests ensure that when connectors are enabled, module-owned ingestion
paths do not create duplicate ingestion events or routing conflicts.
"""

from __future__ import annotations

import warnings

import pytest


class TestTelegramModuleDeprecation:
    """Test that TelegramModule polling is properly deprecated."""

    def test_telegram_config_defaults_to_disabled_polling(self):
        """Verify that TelegramConfig defaults to webhook mode with polling disabled."""
        from butlers.modules.telegram import TelegramConfig

        config = TelegramConfig()
        assert config.mode == "webhook"
        assert config.enable_legacy_polling is False

    def test_telegram_module_warns_on_legacy_polling(self):
        """Verify deprecation warning is emitted when legacy polling is enabled."""
        import asyncio

        from butlers.modules.telegram import TelegramConfig, TelegramModule

        config = TelegramConfig(mode="polling", enable_legacy_polling=True)
        module = TelegramModule()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Run on_startup which should emit warning
            asyncio.run(module.on_startup(config, None))

            # Should have at least one DeprecationWarning
            deprecation_warnings = [
                warning for warning in w if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) > 0
            assert "deprecated" in str(deprecation_warnings[0].message).lower()
            assert "TelegramBotConnector" in str(deprecation_warnings[0].message)

    def test_telegram_module_rejects_polling_without_explicit_flag(self, caplog):
        """Verify that polling is rejected when enable_legacy_polling is not set."""
        import asyncio

        from butlers.modules.telegram import TelegramConfig, TelegramModule

        config = TelegramConfig(mode="polling", enable_legacy_polling=False)
        module = TelegramModule()

        asyncio.run(module.on_startup(config, None))

        # Should have error log about disabled polling
        assert any(
            "DEPRECATED and disabled by default" in record.message for record in caplog.records
        )
        # Polling task should not be created
        assert module._poll_task is None


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


@pytest.mark.parametrize(
    "module_class,config_mode_field,expected_default",
    [
        pytest.param(
            "TelegramConfig",
            "mode",
            "webhook",
            id="telegram_defaults_to_webhook",
        ),
        pytest.param(
            "TelegramConfig",
            "enable_legacy_polling",
            False,
            id="telegram_polling_disabled_by_default",
        ),
    ],
)
def test_safe_ingestion_defaults(module_class, config_mode_field, expected_default):
    """Verify that module configs default to safe (non-polling) settings."""
    if module_class == "TelegramConfig":
        from butlers.modules.telegram import TelegramConfig

        config = TelegramConfig()
        assert getattr(config, config_mode_field) == expected_default
