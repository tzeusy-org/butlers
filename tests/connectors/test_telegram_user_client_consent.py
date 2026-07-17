"""Regression coverage for Telegram user-client consent control state."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors import telegram_user_client_consent as consent

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "granted_at",
    [
        "not-a-timestamp",
        "2026-07-17T12:00:00",
        "",
    ],
)
def test_account_wide_consent_rejects_malformed_or_timezone_naive_timestamp(
    granted_at: str,
) -> None:
    """A syntactically non-empty grant must still be an aware timestamp."""
    settings = {
        consent.CONSENT_SETTINGS_KEY: {
            "version": consent.CONSENT_VERSION,
            "granted_at": granted_at,
        }
    }

    assert consent.is_account_wide_ingestion_consent_granted(settings) is False


def test_account_wide_consent_writer_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        consent.account_wide_ingestion_consent_settings(datetime(2026, 7, 17, 12, 0))


async def test_account_wide_consent_uses_public_control_state_not_connector_registry() -> None:
    """Consent is control state, never a synthetic connector liveness row."""
    pool = MagicMock()
    pool.execute = AsyncMock()

    await consent.save_account_wide_ingestion_consent(
        pool,
        datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )

    query = pool.execute.await_args.args[0]
    assert "public.state" in query
    assert "connector_registry" not in query
    assert pool.execute.await_args.args[1] == consent.CONSENT_STATE_KEY


async def test_load_account_wide_consent_reads_the_same_public_control_key() -> None:
    pool = MagicMock()
    settings = {
        consent.CONSENT_SETTINGS_KEY: {
            "version": consent.CONSENT_VERSION,
            "granted_at": "2026-07-17T12:00:00+00:00",
        }
    }
    pool.fetchval = AsyncMock(return_value=settings)

    assert await consent.load_account_wide_ingestion_consent(pool) == settings
    pool.fetchval.assert_awaited_once_with(
        "SELECT value FROM public.state WHERE key = $1",
        consent.CONSENT_STATE_KEY,
    )
