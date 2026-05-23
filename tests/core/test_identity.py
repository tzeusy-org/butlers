"""Tests for src/butlers/identity.py — resolve_contact_by_channel and helpers.

Covers:
- resolve_contact_by_channel: owner, non-owner, unknown→None, DB error→None, string UUID coercion
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
