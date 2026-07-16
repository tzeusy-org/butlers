"""Shared account-wide consent contract for the Telegram user client."""

from __future__ import annotations

from datetime import datetime
from typing import Any

CONNECTOR_TYPE = "telegram_user_client"
CONSENT_ENDPOINT_IDENTITY = "telegram:user:consent-control"
CONSENT_SETTINGS_KEY = "account_wide_ingestion_consent"
CONSENT_VERSION = "telegram-user-client-account-wide-v1"


def is_account_wide_ingestion_consent_granted(settings: dict[str, Any] | None) -> bool:
    """Return whether settings contain the current explicit consent grant.

    Unknown, malformed, or earlier grants are deliberately rejected. A changed
    scope requires the owner to review and accept the new disclosure again.
    """
    grant = (settings or {}).get(CONSENT_SETTINGS_KEY)
    return (
        isinstance(grant, dict)
        and grant.get("version") == CONSENT_VERSION
        and isinstance(grant.get("granted_at"), str)
        and bool(grant["granted_at"].strip())
    )


def account_wide_ingestion_consent_settings(granted_at: datetime) -> dict[str, object]:
    """Build the durable control-plane value for an accepted current scope."""
    return {
        CONSENT_SETTINGS_KEY: {
            "version": CONSENT_VERSION,
            "granted_at": granted_at.isoformat(),
        }
    }
