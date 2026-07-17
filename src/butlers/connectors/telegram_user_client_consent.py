"""Shared account-wide consent contract for the Telegram user client."""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Account-wide consent is configuration/control state, not a connector endpoint.
# It must never create a ``connector_registry`` row because that registry drives
# liveness and QA attention for runtime connectors.
CONSENT_STATE_KEY = "connector.telegram_user_client.account_wide_ingestion_consent"
CONSENT_SETTINGS_KEY = "account_wide_ingestion_consent"
CONSENT_VERSION = "telegram-user-client-account-wide-v1"


def is_account_wide_ingestion_consent_granted(settings: dict[str, Any] | None) -> bool:
    """Return whether settings contain the current explicit consent grant.

    Unknown, malformed, or earlier grants are deliberately rejected. A changed
    scope requires the owner to review and accept the new disclosure again.
    """
    grant = (settings or {}).get(CONSENT_SETTINGS_KEY)
    if not isinstance(grant, dict) or grant.get("version") != CONSENT_VERSION:
        return False

    granted_at = grant.get("granted_at")
    if not isinstance(granted_at, str) or not granted_at.strip():
        return False

    timestamp = granted_at.strip()
    if timestamp.endswith("Z"):
        timestamp = f"{timestamp[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def account_wide_ingestion_consent_settings(granted_at: datetime) -> dict[str, object]:
    """Build the durable control-plane value for an accepted current scope."""
    if granted_at.tzinfo is None or granted_at.utcoffset() is None:
        raise ValueError("Account-wide ingestion consent timestamp must be timezone-aware")
    return {
        CONSENT_SETTINGS_KEY: {
            "version": CONSENT_VERSION,
            "granted_at": granted_at.isoformat(),
        }
    }


async def load_account_wide_ingestion_consent(pool: Any) -> dict[str, Any] | None:
    """Load account-wide Telegram consent from the shared control store."""
    value = await pool.fetchval(
        "SELECT value FROM public.state WHERE key = $1",
        CONSENT_STATE_KEY,
    )
    return value if isinstance(value, dict) else None


async def save_account_wide_ingestion_consent(
    pool: Any,
    granted_at: datetime,
) -> dict[str, object]:
    """Persist accepted consent without registering a synthetic runtime connector."""
    settings = account_wide_ingestion_consent_settings(granted_at)
    await pool.execute(
        """
        INSERT INTO public.state (key, value, updated_at, version)
        VALUES ($1, $2, now(), 1)
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = now(),
                version = public.state.version + 1
        """,
        CONSENT_STATE_KEY,
        settings,
    )
    return settings
