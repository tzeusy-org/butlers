"""Tests for WhatsApp identity resolution — JID lookup and phone cross-reference.

Covers:
- resolve_contact_by_channel with type="whatsapp_jid": direct JID match
- resolve_contact_by_channel with type="whatsapp_jid": phone-number fallback when
  no direct JID match but a "phone" type entry exists for the same number
- resolve_contact_by_channel with type="whatsapp_jid": group JID returns None
  (group JIDs don't match individual-JID phone extraction pattern)
- _extract_whatsapp_jid_phone helper edge cases
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.identity import (
    _extract_whatsapp_jid_phone,
    resolve_contact_by_channel,
)

pytestmark = pytest.mark.unit

_OWNER_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# _extract_whatsapp_jid_phone helper
# ---------------------------------------------------------------------------


def test_extract_whatsapp_jid_phone_individual_jid():
    """Individual JIDs return the phone number prefix."""
    assert _extract_whatsapp_jid_phone("1234567890@s.whatsapp.net") == "1234567890"


def test_extract_whatsapp_jid_phone_international():
    """International numbers with country code are extracted correctly."""
    assert _extract_whatsapp_jid_phone("441234567890@s.whatsapp.net") == "441234567890"


def test_extract_whatsapp_jid_phone_group_jid_returns_none():
    """Group JIDs (ending in @g.us) return None."""
    assert _extract_whatsapp_jid_phone("120363012345@g.us") is None


def test_extract_whatsapp_jid_phone_broadcast_returns_none():
    """Broadcast list JIDs return None."""
    assert _extract_whatsapp_jid_phone("status@broadcast") is None


def test_extract_whatsapp_jid_phone_plain_string_returns_none():
    """Arbitrary strings without JID structure return None."""
    assert _extract_whatsapp_jid_phone("not-a-jid") is None


def test_extract_whatsapp_jid_phone_empty_returns_none():
    """Empty string returns None."""
    assert _extract_whatsapp_jid_phone("") is None


# ---------------------------------------------------------------------------
# resolve_contact_by_channel — direct whatsapp_jid match
# ---------------------------------------------------------------------------


def _make_pool_with_rows(*rows: dict[str, Any] | None) -> Any:
    """Return a mock asyncpg pool whose fetchrow returns each value in sequence."""
    mock_rows = []
    for row in rows:
        if row is None:
            mock_rows.append(None)
        else:
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda self, k, _r=row: _r[k]
            mock_row.get = lambda k, default=None, _r=row: _r.get(k, default)
            mock_rows.append(mock_row)

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=mock_rows)
    return pool


async def test_resolve_whatsapp_jid_direct_match():
    """Direct whatsapp_jid lookup returns the matching contact."""
    pool = _make_pool_with_rows(
        {
            "contact_id": _CONTACT_ID,
            "name": "Alice",
            "roles": [],
            "entity_id": _ENTITY_ID,
        }
    )
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "1234567890@s.whatsapp.net")
    assert result is not None
    assert result.contact_id == _CONTACT_ID
    assert result.name == "Alice"
    assert result.entity_id == _ENTITY_ID
    # Only one query should be made (direct hit)
    pool.fetchrow.assert_called_once()
    call_args = pool.fetchrow.call_args
    assert call_args[0][1] == "whatsapp_jid"
    assert call_args[0][2] == "1234567890@s.whatsapp.net"


async def test_resolve_whatsapp_jid_owner_direct_match():
    """whatsapp_jid lookup for owner returns owner role."""
    pool = _make_pool_with_rows(
        {
            "contact_id": _OWNER_ID,
            "name": "Owner",
            "roles": ["owner"],
            "entity_id": None,
        }
    )
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "9876543210@s.whatsapp.net")
    assert result is not None
    assert result.contact_id == _OWNER_ID
    assert "owner" in result.roles


# ---------------------------------------------------------------------------
# resolve_contact_by_channel — phone-number fallback
# ---------------------------------------------------------------------------


async def test_resolve_whatsapp_jid_phone_fallback():
    """Falls back to phone lookup when no direct JID match exists."""
    # First call (direct JID lookup) → None
    # Second call (phone fallback) → contact row
    pool = _make_pool_with_rows(
        None,  # direct JID miss
        {
            "contact_id": _CONTACT_ID,
            "name": "Bob",
            "roles": [],
            "entity_id": _ENTITY_ID,
        },
    )
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "1234567890@s.whatsapp.net")
    assert result is not None
    assert result.contact_id == _CONTACT_ID
    assert result.name == "Bob"
    # Two queries: first direct JID, then phone fallback
    assert pool.fetchrow.call_count == 2
    second_call = pool.fetchrow.call_args_list[1]
    # Phone fallback uses type='phone' and value=extracted number
    assert "phone" in second_call[0][0]
    assert second_call[0][1] == "1234567890"


async def test_resolve_whatsapp_jid_no_phone_fallback_for_group_jid():
    """Group JID (non-individual) returns None without attempting phone fallback."""
    pool = _make_pool_with_rows(None)  # direct lookup would never match group JIDs
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "120363012345@g.us")
    assert result is None
    # Only one query attempted (direct JID lookup — group JID has no phone fallback)
    pool.fetchrow.assert_called_once()


async def test_resolve_whatsapp_jid_both_lookups_miss():
    """Returns None when both direct JID and phone fallback miss."""
    pool = _make_pool_with_rows(None, None)
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "9999999999@s.whatsapp.net")
    assert result is None
    assert pool.fetchrow.call_count == 2


async def test_resolve_whatsapp_jid_phone_fallback_db_error_returns_none():
    """Phone fallback DB error returns None gracefully."""
    # First call succeeds with None; second call raises
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, Exception("connection refused")])
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "5555555555@s.whatsapp.net")
    assert result is None


async def test_resolve_non_whatsapp_channel_no_phone_fallback():
    """Non-whatsapp_jid channel type does not attempt phone fallback on miss."""
    pool = _make_pool_with_rows(None)
    result = await resolve_contact_by_channel(pool, "telegram", "99999")
    assert result is None
    # Only one query (no fallback logic for telegram)
    pool.fetchrow.assert_called_once()


async def test_resolve_whatsapp_jid_phone_fallback_owner():
    """Phone fallback can resolve an owner contact from phone type."""
    pool = _make_pool_with_rows(
        None,  # direct JID miss
        {
            "contact_id": _OWNER_ID,
            "name": "Owner",
            "roles": ["owner"],
            "entity_id": _ENTITY_ID,
        },
    )
    result = await resolve_contact_by_channel(pool, "whatsapp_jid", "15551234567@s.whatsapp.net")
    assert result is not None
    assert "owner" in result.roles
    assert result.contact_id == _OWNER_ID
