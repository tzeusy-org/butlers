"""Tests for src/butlers/identity.py — resolve_contact_by_channel and helpers.

These tests cover:
- resolve_contact_by_channel: known contact (owner), known non-owner, unknown returns None
- build_identity_preamble: owner, known non-owner with/without entity_id, unknown with temp_id
- create_temp_contact: basic creation, returns existing on race
- DB query failure gracefully returns None
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.identity import (
    ResolvedContact,
    build_identity_preamble,
    create_temp_contact,
    resolve_contact_by_channel,
)

pytestmark = pytest.mark.unit

_OWNER_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# resolve_contact_by_channel
# ---------------------------------------------------------------------------


def _make_pool_with_row(row: dict[str, Any] | None) -> Any:
    """Return a mock asyncpg pool that returns *row* from fetchrow."""
    mock_row = None
    if row is not None:
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: row[k]
        # Also support iteration / mapping interface used by asyncpg Records.
        mock_row.get = lambda k, default=None: row.get(k, default)

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=mock_row)
    return pool


async def test_resolve_contact_by_channel_owner():
    """Known owner contact resolves with owner role."""
    pool = _make_pool_with_row(
        {
            "contact_id": _OWNER_ID,
            "name": "Owner",
            "roles": ["owner"],
            "entity_id": None,
        }
    )
    result = await resolve_contact_by_channel(pool, "telegram", "12345")
    assert result is not None
    assert result.contact_id == _OWNER_ID
    assert result.name == "Owner"
    assert result.roles == ["owner"]
    assert result.entity_id is None


async def test_resolve_contact_by_channel_known_non_owner():
    """Known non-owner contact resolves with roles and entity_id."""
    pool = _make_pool_with_row(
        {
            "contact_id": _CONTACT_ID,
            "name": "Chloe",
            "roles": [],
            "entity_id": _ENTITY_ID,
        }
    )
    result = await resolve_contact_by_channel(pool, "telegram", "99999")
    assert result is not None
    assert result.contact_id == _CONTACT_ID
    assert result.name == "Chloe"
    assert result.roles == []
    assert result.entity_id == _ENTITY_ID


async def test_resolve_contact_by_channel_unknown_returns_none():
    """Unknown identifier returns None."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    result = await resolve_contact_by_channel(pool, "telegram", "99999999")
    assert result is None


async def test_resolve_contact_by_channel_db_error_returns_none():
    """DB error (e.g. table missing) returns None gracefully."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))
    result = await resolve_contact_by_channel(pool, "telegram", "12345")
    assert result is None


async def test_resolve_contact_by_channel_string_uuid_coercion():
    """String UUIDs in DB rows are coerced to UUID objects."""
    pool = _make_pool_with_row(
        {
            "contact_id": str(_CONTACT_ID),
            "name": "Alice",
            "roles": ["trusted"],
            "entity_id": str(_ENTITY_ID),
        }
    )
    result = await resolve_contact_by_channel(pool, "email", "alice@example.com")
    assert result is not None
    assert isinstance(result.contact_id, uuid.UUID)
    assert isinstance(result.entity_id, uuid.UUID)
    assert result.contact_id == _CONTACT_ID
    assert result.entity_id == _ENTITY_ID


# ---------------------------------------------------------------------------
# build_identity_preamble
# ---------------------------------------------------------------------------


def test_preamble_owner():
    resolved = ResolvedContact(
        contact_id=_OWNER_ID,
        name="Owner",
        roles=["owner"],
        entity_id=None,
    )
    preamble = build_identity_preamble(resolved, "telegram")
    assert preamble == "[Source: Owner, via telegram]"


def test_preamble_known_non_owner_with_entity_id():
    resolved = ResolvedContact(
        contact_id=_CONTACT_ID,
        name="Chloe",
        roles=[],
        entity_id=_ENTITY_ID,
    )
    preamble = build_identity_preamble(resolved, "telegram")
    assert preamble == (
        f"[Source: Chloe (contact_id: {_CONTACT_ID}, entity_id: {_ENTITY_ID}), via telegram]"
    )


def test_preamble_known_non_owner_without_entity_id():
    resolved = ResolvedContact(
        contact_id=_CONTACT_ID,
        name="Bob",
        roles=[],
        entity_id=None,
    )
    preamble = build_identity_preamble(resolved, "email")
    assert preamble == f"[Source: Bob (contact_id: {_CONTACT_ID}), via email]"


def test_preamble_unknown_with_temp_contact_id():
    temp_id = uuid.uuid4()
    preamble = build_identity_preamble(None, "telegram", temp_contact_id=temp_id)
    assert preamble == (
        f"[Source: Unknown sender (contact_id: {temp_id}), via telegram -- pending disambiguation]"
    )


def test_preamble_unknown_with_temp_contact_and_entity():
    temp_id = uuid.uuid4()
    temp_eid = uuid.uuid4()
    preamble = build_identity_preamble(
        None, "telegram", temp_contact_id=temp_id, temp_entity_id=temp_eid
    )
    assert preamble == (
        f"[Source: Unknown sender (contact_id: {temp_id}, entity_id: {temp_eid}), "
        "via telegram -- pending disambiguation]"
    )


def test_preamble_unknown_no_temp_id():
    preamble = build_identity_preamble(None, "telegram")
    assert preamble == "[Source: Unknown sender, via telegram -- pending disambiguation]"


def test_preamble_known_null_name_fallback():
    resolved = ResolvedContact(
        contact_id=_CONTACT_ID,
        name=None,
        roles=[],
        entity_id=None,
    )
    preamble = build_identity_preamble(resolved, "email")
    assert "Unknown Contact" in preamble
    assert str(_CONTACT_ID) in preamble


# ---------------------------------------------------------------------------
# create_temp_contact
# ---------------------------------------------------------------------------


async def test_create_temp_contact_creates_new():
    """create_temp_contact inserts a contact and contact_info row."""
    new_id = uuid.uuid4()

    # Mock the DB interaction
    mock_conn = AsyncMock()
    # First fetchrow (re-check inside transaction) returns None → no existing
    # Second fetchrow (INSERT RETURNING) returns the new contact
    mock_contact_row = MagicMock()
    mock_contact_row.__getitem__ = lambda self, k: {
        "id": new_id,
        "name": "Unknown (telegram 555)",
        "roles": [],
        "entity_id": None,
    }[k]

    mock_conn.fetchrow = AsyncMock(side_effect=[None, mock_contact_row])
    mock_conn.execute = AsyncMock()

    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await create_temp_contact(pool, "telegram", "555")
    assert result is not None
    assert result.roles == []
    assert result.name == "Unknown (telegram 555)"


async def test_create_temp_contact_returns_existing_if_race():
    """create_temp_contact returns existing contact when race detected inside transaction."""
    existing_id = uuid.uuid4()

    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, k: {
        "contact_id": existing_id,
        "name": "Alice",
        "roles": [],
        "entity_id": None,
    }[k]

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=existing_row)

    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await create_temp_contact(pool, "telegram", "777")
    assert result is not None
    assert result.contact_id == existing_id
    assert result.name == "Alice"


async def test_create_temp_contact_db_error_returns_none():
    """create_temp_contact returns None on DB error."""
    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await create_temp_contact(pool, "telegram", "999")
    assert result is None
