"""Tests for per-channel timeout configuration."""

from __future__ import annotations

from butlers.tools.messenger.reliability.timeout import TimeoutConfig


class TestTimeoutConfig:
    """Test per-channel timeout configuration."""

    def test_default_timeout_config(self):
        """Test default timeout values."""
        config = TimeoutConfig()

        assert config.default_timeout_seconds == 30.0
        assert config.telegram_timeout_seconds == 15.0
        assert config.email_timeout_seconds == 45.0
        assert config.sms_timeout_seconds == 20.0
        assert config.chat_timeout_seconds == 25.0

    def test_get_timeout_for_telegram(self):
        """Test getting timeout for telegram channel."""
        config = TimeoutConfig()

        timeout = config.get_timeout("telegram")

        assert timeout == 15.0

    def test_get_timeout_for_email(self):
        """Test getting timeout for email channel."""
        config = TimeoutConfig()

        timeout = config.get_timeout("email")

        assert timeout == 45.0

    def test_get_timeout_for_sms(self):
        """Test getting timeout for sms channel."""
        config = TimeoutConfig()

        timeout = config.get_timeout("sms")

        assert timeout == 20.0

    def test_get_timeout_for_chat(self):
        """Test getting timeout for chat channel."""
        config = TimeoutConfig()

        timeout = config.get_timeout("chat")

        assert timeout == 25.0

    def test_get_timeout_for_unknown_channel_returns_default(self):
        """Test that unknown channel returns default timeout."""
        config = TimeoutConfig()

        timeout = config.get_timeout("unknown_channel")

        assert timeout == 30.0

    def test_get_timeout_case_insensitive(self):
        """Test that channel name is case-insensitive."""
        config = TimeoutConfig()

        assert config.get_timeout("TELEGRAM") == 15.0
        assert config.get_timeout("Email") == 45.0
        assert config.get_timeout("SMS") == 20.0

    def test_get_timeout_strips_whitespace(self):
        """Test that channel name whitespace is stripped."""
        config = TimeoutConfig()

        assert config.get_timeout("  telegram  ") == 15.0

    def test_from_config_with_overrides(self):
        """Test creating TimeoutConfig from config dict with overrides."""
        config_dict = {
            "default": 60.0,
            "telegram": 10.0,
            "email": 90.0,
        }

        config = TimeoutConfig.from_config(config_dict)

        assert config.default_timeout_seconds == 60.0
        assert config.telegram_timeout_seconds == 10.0
        assert config.email_timeout_seconds == 90.0
        # Unspecified channels use defaults
        assert config.sms_timeout_seconds == 20.0
        assert config.chat_timeout_seconds == 25.0

    def test_from_config_empty_dict_uses_defaults(self):
        """Test that empty config dict uses all defaults."""
        config = TimeoutConfig.from_config({})

        assert config.default_timeout_seconds == 30.0
        assert config.telegram_timeout_seconds == 15.0
        assert config.email_timeout_seconds == 45.0
        assert config.sms_timeout_seconds == 20.0
        assert config.chat_timeout_seconds == 25.0

    def test_timeout_config_immutability(self):
        """Test that TimeoutConfig is frozen (immutable)."""
        config = TimeoutConfig()

        # Should raise exception when trying to modify frozen dataclass
        try:
            config.default_timeout_seconds = 100.0
            assert False, "Expected exception when modifying frozen dataclass"
        except Exception:
            pass  # Expected
