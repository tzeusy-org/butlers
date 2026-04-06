"""Tests for WhatsApp identity resolution — JID lookup and phone cross-reference."""

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


def _make_pool_with_rows(*rows: dict[str, Any] | None) -> Any:
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


def test_extract_whatsapp_jid_phone():
    """Individual JIDs return phone; group/broadcast/bad inputs return None."""
    assert _extract_whatsapp_jid_phone("1234567890@s.whatsapp.net") == "1234567890"
    assert _extract_whatsapp_jid_phone("441234567890@s.whatsapp.net") == "441234567890"
    assert _extract_whatsapp_jid_phone("120363012345@g.us") is None
    assert _extract_whatsapp_jid_phone("status@broadcast") is None
    assert _extract_whatsapp_jid_phone("not-a-jid") is None
    assert _extract_whatsapp_jid_phone("") is None


async def test_resolve_whatsapp_jid():
    """Direct JID match; owner JID; phone fallback on miss; group JID→None (no fallback);
    both miss→None; DB error→None; non-whatsapp→no fallback."""
    # Direct match
    pool = _make_pool_with_rows(
        {"contact_id": _CONTACT_ID, "name": "Alice", "roles": [], "entity_id": _ENTITY_ID}
    )
    r = await resolve_contact_by_channel(pool, "whatsapp_jid", "1234567890@s.whatsapp.net")
    assert r is not None and r.contact_id == _CONTACT_ID
    pool.fetchrow.assert_called_once()
    assert pool.fetchrow.call_args[0][1] == "whatsapp_jid"

    # Owner direct match
    pool2 = _make_pool_with_rows(
        {"contact_id": _OWNER_ID, "name": "Owner", "roles": ["owner"], "entity_id": None}
    )
    r2 = await resolve_contact_by_channel(pool2, "whatsapp_jid", "9876543210@s.whatsapp.net")
    assert r2 is not None and "owner" in r2.roles

    # Phone fallback: direct miss → phone lookup
    pool3 = _make_pool_with_rows(
        None, {"contact_id": _CONTACT_ID, "name": "Bob", "roles": [], "entity_id": _ENTITY_ID}
    )
    r3 = await resolve_contact_by_channel(pool3, "whatsapp_jid", "1234567890@s.whatsapp.net")
    assert r3 is not None and r3.name == "Bob"
    assert pool3.fetchrow.call_count == 2
    assert "phone" in pool3.fetchrow.call_args_list[1][0][0]

    # Group JID: no phone fallback
    pool4 = _make_pool_with_rows(None)
    r4 = await resolve_contact_by_channel(pool4, "whatsapp_jid", "120363012345@g.us")
    assert r4 is None
    pool4.fetchrow.assert_called_once()

    # Both miss → None
    pool5 = _make_pool_with_rows(None, None)
    assert (
        await resolve_contact_by_channel(pool5, "whatsapp_jid", "9999999999@s.whatsapp.net") is None
    )
    assert pool5.fetchrow.call_count == 2

    # DB error on fallback → None
    pool6 = AsyncMock()
    pool6.fetchrow = AsyncMock(side_effect=[None, Exception("connection refused")])
    assert (
        await resolve_contact_by_channel(pool6, "whatsapp_jid", "5555555555@s.whatsapp.net") is None
    )

    # Non-whatsapp_jid channel: no phone fallback on miss
    pool7 = _make_pool_with_rows(None)
    assert await resolve_contact_by_channel(pool7, "telegram", "99999") is None
    pool7.fetchrow.assert_called_once()
