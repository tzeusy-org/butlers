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

# Patch the central writer in its home module — create_temp_contact uses a
# deferred import, so patching the source module is the only stable anchor.
_ASSERT_FACT_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"

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


def _make_new_temp_contact_pool(contact_id: uuid.UUID, entity_id: uuid.UUID):
    """Pool mock for create_temp_contact's new-contact path (no existing match).

    Write-path cut-over (bu-k9ylx): create_temp_contact first calls
    resolve_contact_by_channel (pool.fetchrow on entity_facts → None here), then
    acquires a conn to INSERT the entity (conn.fetchval) and contact
    (conn.fetchrow), then asserts the channel triple via the central writer.
    """
    pool = MagicMock()
    # resolve_contact_by_channel queries the triple store on the pool → no match.
    pool.fetchrow = AsyncMock(return_value=None)

    conn = AsyncMock()

    async def conn_fetchrow(query, *args):
        if "INSERT INTO public.contacts" in query:
            return {"id": contact_id, "name": args[0], "entity_id": entity_id}
        return None

    conn.fetchrow = AsyncMock(side_effect=conn_fetchrow)
    conn.fetchval = AsyncMock(return_value=entity_id)  # entity INSERT RETURNING id
    conn.execute = AsyncMock()

    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=mock_transaction)

    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


async def test_create_temp_contact():
    """create_temp_contact creates entity + contact and returns it (cut-over path)."""
    entity_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    pool, _conn = _make_new_temp_contact_pool(contact_id, entity_id)

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock):
        result = await create_temp_contact(pool, "telegram", "555")

    assert result is not None
    assert result.contact_id == contact_id
    assert result.entity_id == entity_id
    assert result.name == "Unknown (telegram 555)"
    assert result.roles == []


async def test_create_temp_contact_db_error_returns_none():
    """A DB error during creation returns None (graceful degradation)."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)  # no existing match
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock):
        assert await create_temp_contact(pool, "telegram", "999") is None


async def test_create_temp_contact_returns_existing_on_conflict():
    """create_temp_contact returns the existing contact if one resolves (race).

    Existing-sender detection now goes through resolve_contact_by_channel (the
    triple store), not a contact_info join.
    """
    existing_entity_id = uuid.uuid4()

    pool = MagicMock()
    # resolve_contact_by_channel: triple lookup returns an active row joined to
    # the owner entity.
    pool.fetchrow = AsyncMock(
        return_value={
            "entity_id": existing_entity_id,
            "name": "Existing Person",
            "roles": ["owner"],
        }
    )

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
        result = await create_temp_contact(pool, "telegram", "existing-chat")
        mock_assert.assert_not_awaited()

    assert result is not None
    assert result.contact_id is None  # entity_id is the authoritative key post bead 7
    assert result.entity_id == existing_entity_id
    assert result.name == "Existing Person"
    assert result.roles == ["owner"]


# ---------------------------------------------------------------------------
# create_temp_contact — central-writer cut-over (Migration bead 8, bu-k9ylx)
# ---------------------------------------------------------------------------
# The dual-write shim is removed.  create_temp_contact now writes the sender's
# channel identifier to relationship.entity_facts ONLY, via the central writer
# relationship_assert_fact() — there is NO public.contact_info INSERT.


class TestCreateTempContactCentralWriter:
    """create_temp_contact asserts the channel triple via the central writer."""

    async def test_assert_fact_called_with_mapped_predicate(self):
        """The channel triple is asserted with the mapped predicate + entity subject."""
        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool, _conn = _make_new_temp_contact_pool(contact_id, entity_id)

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
            await create_temp_contact(pool, "telegram", "12345")
            mock_assert.assert_awaited_once()
            call = mock_assert.call_args
            # positional: (pool, subject=entity_id, predicate, object=value)
            assert call.args[1] == entity_id
            assert call.args[2] == "has-handle"  # telegram → has-handle
            assert call.args[3] == "12345"
            assert call.kwargs.get("primary") is True

    async def test_assert_fact_failure_does_not_block_return_value(self):
        """A central-writer failure is swallowed — ResolvedContact is still returned."""
        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool, _conn = _make_new_temp_contact_pool(contact_id, entity_id)

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            result = await create_temp_contact(pool, "telegram", "12345")
            assert result is not None
            assert result.contact_id == contact_id

    async def test_no_contact_info_insert_anywhere(self):
        """No INSERT/UPDATE/DELETE against public.contact_info is ever issued."""
        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool, conn = _make_new_temp_contact_pool(contact_id, entity_id)

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock):
            await create_temp_contact(pool, "telegram", "12345")

        # Inspect every SQL string passed to conn.execute / conn.fetchrow:
        # none may write public.contact_info.
        for mock_call in [*conn.execute.await_args_list, *conn.fetchrow.await_args_list]:
            sql = mock_call.args[0] if mock_call.args else ""
            assert "contact_info" not in sql.lower(), f"unexpected contact_info SQL: {sql!r}"

    async def test_existing_match_short_circuits_before_writes(self):
        """An existing triple match returns immediately without acquiring a conn."""
        existing_entity_id = uuid.uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            return_value={
                "entity_id": existing_entity_id,
                "name": "Existing Person",
                "roles": ["owner"],
            }
        )
        pool.acquire = MagicMock()  # must not be used

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
            result = await create_temp_contact(pool, "telegram", "777")
            mock_assert.assert_not_awaited()
        pool.acquire.assert_not_called()
        assert result is not None
        assert result.entity_id == existing_entity_id
        assert result.roles == ["owner"]
