"""Tests for src/butlers/identity.py — resolve_contact_by_channel and helpers.

Covers:
- resolve_contact_by_channel: owner, non-owner, unknown→None, DB error→None, string UUID coercion
- build_identity_preamble: owner, known non-owner with/without entity_id, unknown with temp_id
- create_temp_contact: creates new, returns existing on race, DB error→None
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


def _make_pool_with_row(row: dict[str, Any] | None) -> Any:
    mock_row = None
    if row is not None:
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: row[k]
        mock_row.get = lambda k, default=None: row.get(k, default)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=mock_row)
    return pool


# ---------------------------------------------------------------------------
# resolve_contact_by_channel
# ---------------------------------------------------------------------------


async def test_resolve_contact_by_channel():
    """Owner contact, non-owner contact, unknown→None, DB error→None, string UUID coercion."""
    # Owner
    pool = _make_pool_with_row(
        {"contact_id": _OWNER_ID, "name": "Owner", "roles": ["owner"], "entity_id": None}
    )
    r = await resolve_contact_by_channel(pool, "telegram", "12345")
    assert (
        r is not None and r.contact_id == _OWNER_ID and r.roles == ["owner"] and r.entity_id is None
    )

    # Known non-owner with entity_id
    pool2 = _make_pool_with_row(
        {"contact_id": _CONTACT_ID, "name": "Chloe", "roles": [], "entity_id": _ENTITY_ID}
    )
    r2 = await resolve_contact_by_channel(pool2, "telegram", "99999")
    assert r2 is not None and r2.contact_id == _CONTACT_ID and r2.entity_id == _ENTITY_ID

    # Unknown → None
    pool3 = AsyncMock()
    pool3.fetchrow = AsyncMock(return_value=None)
    assert await resolve_contact_by_channel(pool3, "telegram", "99999999") is None

    # DB error → None
    pool4 = AsyncMock()
    pool4.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))
    assert await resolve_contact_by_channel(pool4, "telegram", "12345") is None

    # String UUID coercion
    pool5 = _make_pool_with_row(
        {
            "contact_id": str(_CONTACT_ID),
            "name": "Alice",
            "roles": ["trusted"],
            "entity_id": str(_ENTITY_ID),
        }
    )
    r5 = await resolve_contact_by_channel(pool5, "email", "alice@example.com")
    assert r5 is not None and isinstance(r5.contact_id, uuid.UUID) and r5.contact_id == _CONTACT_ID


async def test_resolve_contact_by_channel_maps_telegram_user_client_id():
    """telegram_user_client sender ids resolve against telegram_user_id contact rows."""
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "contact_id": _CONTACT_ID,
        "name": "Chloe Wong",
        "roles": [],
        "entity_id": _ENTITY_ID,
    }[k]
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, mock_row])

    result = await resolve_contact_by_channel(pool, "telegram_user_client", "86807245")

    assert result is not None
    assert result.contact_id == _CONTACT_ID
    assert result.entity_id == _ENTITY_ID
    assert "telegram_user_id" in pool.fetchrow.await_args_list[1].args[0]
    assert pool.fetchrow.await_args_list[1].args[1] == "telegram:86807245"


# ---------------------------------------------------------------------------
# build_identity_preamble
# ---------------------------------------------------------------------------


def test_build_identity_preamble():
    """Owner, known contact, unknown with/without temp IDs, null name fallback."""
    # Owner without entity_id
    r = ResolvedContact(contact_id=_OWNER_ID, name="Owner", roles=["owner"], entity_id=None)
    assert (
        build_identity_preamble(r, "telegram")
        == f"[Source: Owner (contact_id: {_OWNER_ID}), via telegram]"
    )

    # Known contact with entity_id
    r2 = ResolvedContact(contact_id=_CONTACT_ID, name="Chloe", roles=[], entity_id=_ENTITY_ID)
    p2 = build_identity_preamble(r2, "telegram")
    assert f"contact_id: {_CONTACT_ID}" in p2 and f"entity_id: {_ENTITY_ID}" in p2

    # Unknown with temp_contact_id
    temp_id = uuid.uuid4()
    p3 = build_identity_preamble(None, "telegram", temp_contact_id=temp_id)
    assert f"contact_id: {temp_id}" in p3 and "pending disambiguation" in p3

    # Unknown with both temp IDs
    temp_eid = uuid.uuid4()
    p4 = build_identity_preamble(None, "telegram", temp_contact_id=temp_id, temp_entity_id=temp_eid)
    assert f"entity_id: {temp_eid}" in p4

    # Unknown without any temp ID
    p5 = build_identity_preamble(None, "telegram")
    assert "Unknown sender" in p5 and "pending disambiguation" in p5

    # Null name fallback
    r6 = ResolvedContact(contact_id=_CONTACT_ID, name=None, roles=[], entity_id=None)
    assert "Unknown Contact" in build_identity_preamble(r6, "email")


# ---------------------------------------------------------------------------
# create_temp_contact
# ---------------------------------------------------------------------------


async def test_create_temp_contact():
    """Creates new contact; returns existing on race; returns None on DB error."""
    new_contact_id = uuid.uuid4()
    new_entity_id = uuid.uuid4()

    mock_conn = AsyncMock()
    mock_contact_row = MagicMock()
    mock_contact_row.__getitem__ = lambda self, k: {
        "id": new_contact_id,
        "name": "Unknown (telegram 555)",
        "entity_id": new_entity_id,
    }[k]
    mock_conn.fetchrow = AsyncMock(side_effect=[None, mock_contact_row])
    mock_conn.fetchval = AsyncMock(return_value=new_entity_id)
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
    assert (
        result is not None
        and result.entity_id == new_entity_id
        and result.name == "Unknown (telegram 555)"
    )

    # Race: existing contact found inside transaction
    existing_id = uuid.uuid4()
    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, k: {
        "contact_id": existing_id,
        "name": "Alice",
        "roles": [],
        "entity_id": None,
    }[k]
    mock_conn2 = AsyncMock()
    mock_conn2.fetchrow = AsyncMock(return_value=existing_row)
    mock_conn2.transaction = MagicMock(return_value=mock_transaction)
    pool2 = AsyncMock()
    pool2.acquire = MagicMock()
    pool2.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn2)
    pool2.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    result2 = await create_temp_contact(pool2, "telegram", "777")
    assert result2 is not None and result2.contact_id == existing_id

    # DB error → None
    pool3 = AsyncMock()
    pool3.acquire = MagicMock()
    pool3.acquire.return_value.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
    pool3.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    assert await create_temp_contact(pool3, "telegram", "999") is None


# ---------------------------------------------------------------------------
# Spec scenario: Telegram chat resolves to entity via has-handle triple
# (relationship-facts/spec.md §"Scenario: Telegram chat resolves to entity via has-handle triple")
# ---------------------------------------------------------------------------


async def test_resolve_telegram_via_has_handle_triple():
    """Telegram chat resolves to entity via has-handle triple in relationship.entity_facts.

    Spec: resolve_contact_by_channel('telegram', 'telegram:12345') MUST return a
    ResolvedContact with entity_id set when a has-handle triple exists in
    relationship.entity_facts with predicate='has-handle', object='telegram:12345',
    object_kind='literal', validity='active'.

    The returned ResolvedContact MUST have entity_id populated; contact_id is None
    (the new triple-store resolution path does not involve public.contacts).

    Reference: openspec/changes/relationship-tabs-to-entities/specs/relationship-facts/spec.md
    Scenario: "Telegram chat resolves to entity via has-handle triple"
    """
    entity_id = uuid.uuid4()

    # Simulate a pool where relationship.entity_facts yields a match for
    # predicate='has-handle', object='telegram:12345'.
    # The new SQL shape (per spec) returns: entity_id, name, roles.
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "entity_id": entity_id,
        "name": "Alice",
        "roles": [],
        # contact_id is absent in the new resolution path
    }[k]
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=mock_row)

    result = await resolve_contact_by_channel(pool, "telegram", "telegram:12345")

    assert result is not None, (
        "resolve_contact_by_channel must return a ResolvedContact when a has-handle triple "
        "exists for telegram:12345 — got None (implementation likely still queries "
        "public.contact_info instead of relationship.entity_facts)"
    )
    assert result.entity_id == entity_id, (
        f"entity_id must equal the entity from the has-handle triple (expected {entity_id}, "
        f"got {result.entity_id})"
    )
    # contact_id is None in the triple-store resolution path (no public.contacts lookup).
    assert result.contact_id is None, (
        "contact_id must be None when resolving via has-handle triple — the entity_facts "
        "path does not join public.contacts"
    )
