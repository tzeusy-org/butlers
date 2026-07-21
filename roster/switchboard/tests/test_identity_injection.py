"""Tests for Switchboard entity-first identity injection.

The owner-notification tests exercise the narrow, deterministic seam around a
transitory entity. They deliberately avoid a live delivery transport: the
Switchboard wiring test owns construction of the standard ``notify.v1``
callback, while this module proves the helper only grants that callback one
durable, race-safe attempt.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from butlers.identity import ResolvedContact
from butlers.tools.switchboard.identity.inject import (
    _claim_unknown_sender_notification,
    resolve_and_inject_identity,
)

pytestmark = pytest.mark.unit

_OWNER_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()
_TEMP_ENTITY_ID = uuid.uuid4()


def _resolved_owner() -> ResolvedContact:
    return ResolvedContact(
        contact_id=_OWNER_ID,
        name="Owner",
        roles=["owner"],
        entity_id=None,
    )


def _resolved_known() -> ResolvedContact:
    return ResolvedContact(
        contact_id=_CONTACT_ID,
        name="Chloe",
        roles=[],
        entity_id=_ENTITY_ID,
    )


def _temp_entity(*, name: str = "Unknown (telegram 12345)") -> ResolvedContact:
    """Model the post-entity-migration temporary result: no contact ID."""
    return ResolvedContact(
        contact_id=None,
        name=name,
        roles=[],
        entity_id=_TEMP_ENTITY_ID,
    )


async def _resolve_unknown(
    pool: AsyncMock,
    *,
    notify_owner_fn: AsyncMock | None = None,
    display_name: str | None = "Chloe L",
) -> object:
    with (
        patch(
            "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.tools.switchboard.identity.inject.create_temp_contact",
            new=AsyncMock(return_value=_temp_entity()),
        ),
    ):
        return await resolve_and_inject_identity(
            pool,
            "telegram",
            "12345",
            display_name=display_name,
            notify_owner_fn=notify_owner_fn,
        )


async def test_owner_message_gets_owner_preamble():
    """Owner message produces [Source: Owner, via telegram] preamble."""
    pool = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_owner()),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "12345")

    assert result.preamble == "[Source: Owner, via telegram]"
    assert result.is_owner is True
    assert result.is_known is True
    assert result.is_unknown is False
    assert result.contact_id == _OWNER_ID
    assert result.sender_roles == ["owner"]


async def test_known_contact_gets_identity_preamble_with_entity_id():
    """Known contact produces full preamble with entity_id (contact_id excluded)."""
    pool = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_known()),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "99999")

    assert f"contact_id: {_CONTACT_ID}" not in result.preamble
    assert f"entity_id: {_ENTITY_ID}" in result.preamble
    assert "Chloe" in result.preamble
    assert "via telegram" in result.preamble
    assert result.is_owner is False
    assert result.is_known is True
    assert result.entity_id == _ENTITY_ID


async def test_unknown_sender_creates_entity_only_identity_context():
    """An unknown sender is entity-only even when no delivery callback is wired."""
    pool = AsyncMock()

    result = await _resolve_unknown(pool, notify_owner_fn=None)

    assert "pending disambiguation" in result.preamble
    assert str(_TEMP_ENTITY_ID) in result.preamble
    assert result.contact_id is None
    assert result.entity_id == _TEMP_ENTITY_ID
    assert result.is_unknown is True
    assert result.is_known is False
    pool.fetchrow.assert_not_awaited()


async def test_unknown_sender_claims_once_and_notifies_with_safe_entity_review_link():
    """The first durable claimant gets one content-free entity-review notification."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"key": "identity:unknown_notified:telegram:12345"})
    notify_owner_fn = AsyncMock()

    result = await _resolve_unknown(
        pool,
        notify_owner_fn=notify_owner_fn,
        display_name=" Chloe\n L ",
    )

    assert result.new_unknown_sender is True
    notify_owner_fn.assert_awaited_once()
    message = notify_owner_fn.await_args.args[0]
    assert "Chloe L" in message
    assert "telegram" in message
    assert "/entities/index?state=unidentified" in message
    assert "12345" not in message
    assert "/contacts" not in message
    assert str(_TEMP_ENTITY_ID) not in message
    assert "inbound message body" not in message

    sql, state_key = pool.fetchrow.await_args.args
    assert "INSERT INTO state" in sql
    assert "ON CONFLICT (key) DO NOTHING" in sql
    assert "RETURNING key" in sql
    assert state_key == "identity:unknown_notified:telegram:12345"


async def test_repeated_unknown_sender_losing_claim_does_not_notify_again():
    """A claim conflict is the durable no-repeat result, not a retry trigger."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    notify_owner_fn = AsyncMock()

    result = await _resolve_unknown(pool, notify_owner_fn=notify_owner_fn)

    assert result.new_unknown_sender is False
    notify_owner_fn.assert_not_awaited()


async def test_claim_failure_is_observable_and_does_not_send_unclaimed_notification(
    caplog: pytest.LogCaptureFixture,
):
    """State outages fail open for routing but cannot turn into owner-send storms."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("state unavailable"))
    notify_owner_fn = AsyncMock()

    with caplog.at_level(logging.WARNING):
        result = await _resolve_unknown(pool, notify_owner_fn=notify_owner_fn)

    assert result.is_unknown is True
    assert result.new_unknown_sender is False
    notify_owner_fn.assert_not_awaited()
    assert "could not persist owner-notification claim" in caplog.text


async def test_delivery_failure_is_sealed_after_the_claim_and_does_not_block_result(
    caplog: pytest.LogCaptureFixture,
):
    """A transport failure is logged but leaves identity injection usable and sealed."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"key": "identity:unknown_notified:telegram:12345"})
    notify_owner_fn = AsyncMock(side_effect=RuntimeError("messenger unavailable"))

    with caplog.at_level(logging.WARNING):
        result = await _resolve_unknown(pool, notify_owner_fn=notify_owner_fn)

    assert result.is_unknown is True
    assert result.new_unknown_sender is True
    notify_owner_fn.assert_awaited_once()
    assert "Failed to notify owner about unknown sender" in caplog.text


async def test_atomic_claim_allows_only_one_concurrent_winner():
    """The claim itself, not timing, makes duplicate owner attempts impossible."""

    class AtomicClaimPool:
        def __init__(self) -> None:
            self._claimed = False
            self._lock = asyncio.Lock()

        async def fetchrow(self, sql: str, state_key: str) -> dict[str, str] | None:
            assert "INSERT INTO state" in sql
            assert state_key == "identity:unknown_notified:telegram:12345"
            async with self._lock:
                if self._claimed:
                    return None
                self._claimed = True
                return {"key": state_key}

    pool = AtomicClaimPool()
    first, second = await asyncio.gather(
        _claim_unknown_sender_notification(pool, "telegram", "12345"),
        _claim_unknown_sender_notification(pool, "telegram", "12345"),
    )

    assert sorted((first, second)) == [False, True]


async def test_empty_channel_value_returns_empty_result():
    """Empty channel_value skips resolution and returns an empty result."""
    pool = AsyncMock()
    result = await resolve_and_inject_identity(pool, "telegram", None)
    assert result.preamble == ""
    assert result.contact_id is None
    assert result.is_owner is False
    assert result.is_known is False
    assert result.is_unknown is False


async def test_empty_string_channel_value_returns_empty_result():
    """An empty string channel_value also skips resolution."""
    pool = AsyncMock()
    result = await resolve_and_inject_identity(pool, "telegram", "")

    assert result.preamble == ""
    assert result.contact_id is None
    assert result.is_owner is False
    assert result.is_known is False
    assert result.is_unknown is False


async def test_known_contact_never_claims_or_notifies():
    """Known contacts bypass the owner-notification path entirely."""
    pool = AsyncMock()
    notify_owner_fn = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_known()),
    ):
        result = await resolve_and_inject_identity(
            pool, "telegram", "99999", notify_owner_fn=notify_owner_fn
        )

    assert result.new_unknown_sender is False
    notify_owner_fn.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()
