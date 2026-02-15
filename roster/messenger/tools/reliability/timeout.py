"""Per-channel timeout configuration for Messenger butler.

Implements configurable timeout per delivery channel from
docs/roles/messenger_butler.md section 9.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeoutConfig:
    """Per-channel timeout configuration."""

    default_timeout_seconds: float = 30.0
    """Default timeout for all channels."""

    telegram_timeout_seconds: float = 15.0
    """Timeout for Telegram API calls."""

    email_timeout_seconds: float = 45.0
    """Timeout for Email SMTP/IMAP calls."""

    sms_timeout_seconds: float = 20.0
    """Timeout for SMS provider calls."""

    chat_timeout_seconds: float = 25.0
    """Timeout for chat provider calls."""

    def get_timeout(self, channel: str) -> float:
        """Get timeout for a specific channel.

        Parameters
        ----------
        channel:
            Channel name (telegram, email, sms, chat).

        Returns
        -------
        float
            Timeout in seconds for the channel.
        """
        channel_lower = channel.lower().strip()

        timeout_map = {
            "telegram": self.telegram_timeout_seconds,
            "email": self.email_timeout_seconds,
            "sms": self.sms_timeout_seconds,
            "chat": self.chat_timeout_seconds,
        }

        return timeout_map.get(channel_lower, self.default_timeout_seconds)

    @classmethod
    def from_config(cls, config: dict[str, float]) -> TimeoutConfig:
        """Create TimeoutConfig from a configuration dictionary.

        Parameters
        ----------
        config:
            Configuration dictionary with channel timeout overrides.
            Keys: default, telegram, email, sms, chat (all optional).
            Values: timeout in seconds.

        Returns
        -------
        TimeoutConfig
            Configured timeout config with specified overrides.
        """
        return cls(
            default_timeout_seconds=config.get("default", 30.0),
            telegram_timeout_seconds=config.get("telegram", 15.0),
            email_timeout_seconds=config.get("email", 45.0),
            sms_timeout_seconds=config.get("sms", 20.0),
            chat_timeout_seconds=config.get("chat", 25.0),
        )
