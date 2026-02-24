"""Tests for Switchboard identity resolution and preamble injection.

Covers:
- Owner message gets owner preamble
- Known non-owner message gets identity preamble with entity_id
- Unknown sender creates temp contact and gets disambiguation preamble
- Second message from same unknown reuses existing temp contact (no duplicate create)
- Owner notified once per new unknown sender (not on repeats)
- Empty/None channel_value returns empty result
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.identity import ResolvedContact
from butlers.tools.switchboard.identity.inject import (
    resolve_and_inject_identity,
)

pytestmark = pytest.mark.unit

_OWNER_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()
_TEMP_ID = uuid.uuid4()


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


def _temp_contact() -> ResolvedContact:
    return ResolvedContact(
        contact_id=_TEMP_ID,
        name="Unknown (telegram 12345)",
        roles=[],
        entity_id=None,
    )


async def test_owner_message_gets_owner_preamble():
    """Owner message produces [Source: Owner, via telegram] preamble."""
    pool = AsyncMock()

    with (
        patch(
            "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
            new=AsyncMock(return_value=_resolved_owner()),
        ),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "12345")

    assert result.preamble == "[Source: Owner, via telegram]"
    assert result.is_owner is True
    assert result.is_known is True
    assert result.is_unknown is False
    assert result.contact_id == _OWNER_ID
    assert result.sender_roles == ["owner"]


async def test_known_contact_gets_identity_preamble_with_entity_id():
    """Known contact produces full preamble with contact_id and entity_id."""
    pool = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_known()),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "99999")

    assert f"contact_id: {_CONTACT_ID}" in result.preamble
    assert f"entity_id: {_ENTITY_ID}" in result.preamble
    assert "Chloe" in result.preamble
    assert "via telegram" in result.preamble
    assert result.is_owner is False
    assert result.is_known is True
    assert result.entity_id == _ENTITY_ID


async def test_unknown_sender_creates_temp_contact():
    """Unknown sender: temp contact created, disambiguation preamble returned."""
    pool = AsyncMock()
    # butler_state is missing — _is_new_unknown_sender returns True
    pool.fetchrow = AsyncMock(side_effect=Exception("no such table"))

    with (
        patch(
            "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.tools.switchboard.identity.inject.create_temp_contact",
            new=AsyncMock(return_value=_temp_contact()),
        ),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "12345")

    assert "pending disambiguation" in result.preamble
    assert str(_TEMP_ID) in result.preamble
    assert result.is_unknown is True
    assert result.is_known is False
    assert result.contact_id == _TEMP_ID


async def test_unknown_sender_notifies_owner_once():
    """Owner is notified once when a new unknown sender is detected."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)  # no prior notification state
    pool.execute = AsyncMock()

    notify_calls: list[str] = []

    async def mock_notify(msg: str) -> None:
        notify_calls.append(msg)

    with (
        patch(
            "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.tools.switchboard.identity.inject.create_temp_contact",
            new=AsyncMock(return_value=_temp_contact()),
        ),
    ):
        await resolve_and_inject_identity(pool, "telegram", "12345", notify_owner_fn=mock_notify)

    assert len(notify_calls) == 1
    assert "telegram" in notify_calls[0].lower() or "12345" in notify_calls[0]


async def test_unknown_sender_no_notification_on_second_message():
    """Second message from same unknown sender does NOT re-notify owner."""
    pool = AsyncMock()

    # Simulate already-notified state: fetchrow returns a row
    mock_state_row = MagicMock()
    pool.fetchrow = AsyncMock(return_value=mock_state_row)

    notify_calls: list[str] = []

    async def mock_notify(msg: str) -> None:
        notify_calls.append(msg)

    with (
        patch(
            "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.tools.switchboard.identity.inject.create_temp_contact",
            new=AsyncMock(return_value=_temp_contact()),
        ),
    ):
        await resolve_and_inject_identity(pool, "telegram", "12345", notify_owner_fn=mock_notify)

    assert len(notify_calls) == 0  # owner not re-notified


async def test_empty_channel_value_returns_empty_result():
    """Empty channel_value skips resolution and returns empty result."""
    pool = AsyncMock()
    result = await resolve_and_inject_identity(pool, "telegram", None)
    assert result.preamble == ""
    assert result.contact_id is None
    assert result.is_owner is False
    assert result.is_known is False
    assert result.is_unknown is False


async def test_empty_string_channel_value_returns_empty_result():
    """Empty string channel_value also skips resolution."""
    pool = AsyncMock()
    result = await resolve_and_inject_identity(pool, "telegram", "")
    assert result.preamble == ""


async def test_known_contact_no_notification():
    """Known contact does not trigger owner notification."""
    pool = AsyncMock()

    notify_calls: list[str] = []

    async def mock_notify(msg: str) -> None:
        notify_calls.append(msg)

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_known()),
    ):
        result = await resolve_and_inject_identity(
            pool, "telegram", "99999", notify_owner_fn=mock_notify
        )

    assert len(notify_calls) == 0
    assert result.new_unknown_sender is False
