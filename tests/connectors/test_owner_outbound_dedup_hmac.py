"""Keyed dedup for owner-outbound point events (bu-szrvwu).

The idempotency key must be an HMAC under a credential-store key, so a stored
row cannot be matched back to its counterpart by enumerating chat ids and
sequential message ids. A missing key fails closed: nothing is written.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import owner_outbound_events as oe

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _restore_key() -> Iterator[None]:
    yield
    oe.configure_dedup_key(None)


def test_dedup_key_is_stable_under_one_key_and_changes_with_the_key() -> None:
    oe.configure_dedup_key("key-one")
    first = oe._hash_dedup_material("telegram", "100:42")
    assert first == oe._hash_dedup_material("telegram", "100:42")

    oe.configure_dedup_key("key-two")
    assert oe._hash_dedup_material("telegram", "100:42") != first


def test_dedup_key_is_not_the_unkeyed_sha256_of_the_sentinel() -> None:
    oe.configure_dedup_key("key-one")
    unkeyed = hashlib.sha256(b"telegram:100:42").hexdigest()

    stored = oe._hash_dedup_material("telegram", "100:42")

    assert stored is not None
    assert unkeyed not in stored


async def test_missing_key_fails_closed_and_warns_once_at_resolve(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = AsyncMock()
    store.resolve = AsyncMock(return_value=None)
    with caplog.at_level(logging.WARNING, logger=oe.__name__):
        assert await oe.resolve_dedup_key(store) is False
    store.resolve.assert_awaited_once_with(oe.OWNER_OUTBOUND_DEDUP_KEY_NAME, env_fallback=False)
    assert sum(oe.OWNER_OUTBOUND_DEDUP_KEY_NAME in r.getMessage() for r in caplog.records) == 1

    pool = AsyncMock()
    inserted = await oe.record_owner_outbound_point(
        pool,
        channel="telegram_user_client",
        provider="telegram",
        endpoint_identity="telegram:user:999",
        occurred_at=_NOW,
        dedup_material="100:42",
    )

    assert inserted is False
    pool.fetchval.assert_not_awaited()
