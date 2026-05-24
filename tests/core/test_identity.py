"""Tests for src/butlers/identity.py — resolve_contact_by_channel and helpers.

Migration bead 7 (bu-akads): resolve_contact_by_channel now queries
relationship.entity_facts instead of public.contact_info / public.contacts.
contact_id is no longer returned (set to None); entity_id is the primary key.
build_identity_preamble no longer includes contact_id in its output string.

Covers:
- resolve_contact_by_channel: owner, non-owner, unknown→None, DB error→None
- build_identity_preamble: owner, known non-owner with/without entity_id, unknown with temp_id
- create_temp_contact: creates new, returns existing on race, DB error→None
- create_temp_contact dual-write shim (Group A, bu-3jfvv): flag on/off, shim failure swallowed
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.identity import (
    ResolvedContact,
    build_identity_preamble,
    create_temp_contact,
    resolve_contact_by_channel,
)

pytestmark = pytest.mark.unit

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"
# Patch the function in its home module — create_temp_contact uses a deferred import,
# so patching the source module is the only stable anchor.
_EMIT_FACT_PATCH = "butlers.tools.relationship.dual_write.emit_contact_info_fact"

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
    """Bead 7 cut-over: queries entity_facts triples; entity_id is primary key; contact_id=None.

    - Owner entity: roles=['owner'], contact_id is None post-bead-7
    - Known non-owner entity: entity_id returned
    - Unknown → None
    - DB error → None
    - String UUID coercion (entity_id)
    """
    # Owner entity — rows from entity_facts query: entity_id, name (canonical_name), roles
    pool = _make_pool_with_row({"entity_id": _OWNER_ID, "name": "Owner", "roles": ["owner"]})
    r = await resolve_contact_by_channel(pool, "telegram", "12345")
    assert (
        r is not None
        and r.contact_id is None  # bead 7: no contact_id returned
        and r.entity_id == _OWNER_ID
        and r.roles == ["owner"]
    )

    # Known non-owner entity
    pool2 = _make_pool_with_row({"entity_id": _ENTITY_ID, "name": "Chloe", "roles": []})
    r2 = await resolve_contact_by_channel(pool2, "telegram", "99999")
    assert r2 is not None and r2.contact_id is None and r2.entity_id == _ENTITY_ID

    # Unknown → None
    pool3 = AsyncMock()
    pool3.fetchrow = AsyncMock(return_value=None)
    assert await resolve_contact_by_channel(pool3, "telegram", "99999999") is None

    # DB error → None
    pool4 = AsyncMock()
    pool4.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))
    assert await resolve_contact_by_channel(pool4, "telegram", "12345") is None

    # String UUID coercion for entity_id
    pool5 = _make_pool_with_row(
        {
            "entity_id": str(_ENTITY_ID),
            "name": "Alice",
            "roles": ["trusted"],
        }
    )
    r5 = await resolve_contact_by_channel(pool5, "email", "alice@example.com")
    assert r5 is not None and isinstance(r5.entity_id, uuid.UUID) and r5.entity_id == _ENTITY_ID


async def test_resolve_telegram_via_has_handle_triple():
    """Bead 7 (bu-akads): telegram channel resolves via has-handle predicate in entity_facts.

    Verifies that resolve_contact_by_channel queries relationship.entity_facts with
    predicate='has-handle' for telegram channel types, returning entity_id.
    """
    pool = _make_pool_with_row({"entity_id": _ENTITY_ID, "name": "Chloe Wong", "roles": []})
    result = await resolve_contact_by_channel(pool, "telegram", "86807245")

    assert result is not None
    assert result.contact_id is None  # entity_id is authoritative post bead 7
    assert result.entity_id == _ENTITY_ID

    # Verify the query uses entity_facts and has-handle predicate
    query_call = pool.fetchrow.call_args
    query = query_call.args[0]
    assert "entity_facts" in query
    assert query_call.args[1] == "has-handle"  # telegram → has-handle
    assert query_call.args[2] == "86807245"


async def test_resolve_email_via_has_email_triple():
    """Bead 7 (bu-akads): email channel resolves via has-email predicate in entity_facts."""
    pool = _make_pool_with_row({"entity_id": _ENTITY_ID, "name": "Owner", "roles": ["owner"]})
    result = await resolve_contact_by_channel(pool, "email", "owner@example.com")

    assert result is not None
    assert result.entity_id == _ENTITY_ID

    query_call = pool.fetchrow.call_args
    assert "entity_facts" in query_call.args[0]
    assert query_call.args[1] == "has-email"
    assert query_call.args[2] == "owner@example.com"


async def test_resolve_phone_via_has_phone_triple():
    """Bead 7 (bu-akads): phone channel resolves via has-phone predicate in entity_facts."""
    pool = _make_pool_with_row({"entity_id": _ENTITY_ID, "name": "Alice", "roles": []})
    result = await resolve_contact_by_channel(pool, "phone", "+15555551234")

    assert result is not None
    assert result.entity_id == _ENTITY_ID

    query_call = pool.fetchrow.call_args
    assert "entity_facts" in query_call.args[0]
    assert query_call.args[1] == "has-phone"
    assert query_call.args[2] == "+15555551234"


async def test_resolve_contact_by_channel_maps_telegram_user_client_id():
    """telegram_user_client sender ids resolve via has-handle with telegram: prefix."""
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "entity_id": _ENTITY_ID,
        "name": "Chloe Wong",
        "roles": [],
    }[k]
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, mock_row])

    result = await resolve_contact_by_channel(pool, "telegram_user_client", "86807245")

    assert result is not None
    assert result.contact_id is None  # bead 7: entity_id is primary
    assert result.entity_id == _ENTITY_ID
    # Second call uses telegram: prefix for the fallback
    assert pool.fetchrow.await_args_list[1].args[2] == "telegram:86807245"


# ---------------------------------------------------------------------------
# build_identity_preamble
# ---------------------------------------------------------------------------


def test_build_identity_preamble():
    """Bead 7: preamble uses entity_id only (contact_id dropped from output string).

    - Owner with entity_id: shows entity_id, no contact_id
    - Owner without entity_id: minimal form, no contact_id
    - Known contact with entity_id: entity_id only
    - Unknown with temp_entity_id: entity_id shown
    - Unknown with temp_contact_id only (create_temp_contact fallback): contact_id shown
    - Unknown without any temp ID: minimal form
    - Null name fallback: 'Unknown Contact'
    """
    # Owner without entity_id — no contact_id in output (bead 7)
    r = ResolvedContact(contact_id=None, name="Owner", roles=["owner"], entity_id=None)
    p = build_identity_preamble(r, "telegram")
    assert "[Source: Owner" in p and "via telegram" in p
    assert "contact_id" not in p

    # Owner with entity_id — entity_id shown, no contact_id
    r_eid = ResolvedContact(contact_id=None, name="Owner", roles=["owner"], entity_id=_ENTITY_ID)
    p_eid = build_identity_preamble(r_eid, "telegram")
    assert f"entity_id: {_ENTITY_ID}" in p_eid
    assert "contact_id" not in p_eid

    # Known contact with entity_id — entity_id only
    r2 = ResolvedContact(contact_id=None, name="Chloe", roles=[], entity_id=_ENTITY_ID)
    p2 = build_identity_preamble(r2, "telegram")
    assert f"entity_id: {_ENTITY_ID}" in p2
    assert "contact_id" not in p2

    # Unknown with temp_entity_id — entity_id shown (preferred over contact_id)
    temp_eid = uuid.uuid4()
    temp_cid = uuid.uuid4()
    p4 = build_identity_preamble(
        None, "telegram", temp_contact_id=temp_cid, temp_entity_id=temp_eid
    )
    assert f"entity_id: {temp_eid}" in p4
    assert "pending disambiguation" in p4

    # Unknown with only temp_contact_id (create_temp_contact fallback)
    temp_id = uuid.uuid4()
    p3 = build_identity_preamble(None, "telegram", temp_contact_id=temp_id)
    assert f"contact_id: {temp_id}" in p3 and "pending disambiguation" in p3

    # Unknown without any temp ID
    p5 = build_identity_preamble(None, "telegram")
    assert "Unknown sender" in p5 and "pending disambiguation" in p5

    # Null name fallback
    r6 = ResolvedContact(contact_id=None, name=None, roles=[], entity_id=None)
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
# create_temp_contact — dual-write shim parity tests (Group A, bu-3jfvv)
# ---------------------------------------------------------------------------

# Design contract (Amendment 14):
#   - SQL is authoritative. Legacy write commits first; triple write is best-effort.
#   - Shim failures are swallowed; legacy SQL commit is never blocked or rolled back.
#   - Flag is read on every call via ``dual_write_enabled()``.
#
# Test scope:
#   (a) Flag off → emit_contact_info_fact called but returns early internally.
#   (b) Flag on  → emit_contact_info_fact is called after SQL commit with correct args.
#   (c) Shim raises → failure swallowed; ResolvedContact is still returned.


def _make_create_temp_contact_pool(
    new_contact_id: uuid.UUID,
    new_entity_id: uuid.UUID,
) -> MagicMock:
    """Build pool mock wired for create_temp_contact (new-contact path)."""
    mock_contact_row = MagicMock()
    mock_contact_row.__getitem__ = lambda self, k: {
        "id": new_contact_id,
        "name": f"Unknown (telegram {new_contact_id})",
        "entity_id": new_entity_id,
    }[k]

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[None, mock_contact_row])
    mock_conn.fetchval = AsyncMock(return_value=new_entity_id)
    mock_conn.execute = AsyncMock()

    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


class TestCreateTempContactDualWriteShim:
    """create_temp_contact: emit_contact_info_fact is called after the INSERT commits."""

    async def test_emit_fact_called_when_flag_on(self, monkeypatch):
        """(b) emit_contact_info_fact is called once after the INSERT commits."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool = _make_create_temp_contact_pool(contact_id, entity_id)

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            result = await create_temp_contact(pool, "telegram", "12345")

        mock_emit.assert_awaited_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["contact_id"] == contact_id
        assert call_kwargs["ci_type"] == "telegram"
        assert call_kwargs["value"] == "12345"
        assert call_kwargs["is_primary"] is True
        assert result is not None and result.contact_id == contact_id

    async def test_emit_fact_called_when_flag_off(self, monkeypatch):
        """(a) emit_contact_info_fact is called even when flag is off (returns early internally)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)

        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool = _make_create_temp_contact_pool(contact_id, entity_id)

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            result = await create_temp_contact(pool, "telegram", "12345")

        # Call-site always invokes helper; helper checks flag internally.
        mock_emit.assert_awaited_once()
        assert result is not None and result.contact_id == contact_id

    async def test_shim_failure_does_not_block_return_value(self, monkeypatch):
        """(c) emit_contact_info_fact raising does not propagate — ResolvedContact is returned."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool = _make_create_temp_contact_pool(contact_id, entity_id)

        with patch(
            _EMIT_FACT_PATCH,
            new_callable=AsyncMock,
            side_effect=RuntimeError("triple store down"),
        ):
            result = await create_temp_contact(pool, "telegram", "12345")

        assert result is not None and result.contact_id == contact_id

    async def test_shim_not_called_when_existing_contact_found(self, monkeypatch):
        """Shim is NOT called when the race path returns an existing contact (no new INSERT)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        existing_id = uuid.uuid4()
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: {
            "contact_id": existing_id,
            "name": "Alice",
            "roles": [],
            "entity_id": None,
        }[k]

        mock_transaction = AsyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=False)

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=existing_row)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            result = await create_temp_contact(pool, "telegram", "777")

        # Existing path returns inside the transaction — no INSERT, so no shim call.
        mock_emit.assert_not_called()
        assert result is not None and result.contact_id == existing_id
