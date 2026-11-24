"""Unit tests for the central contact → entity_id resolver (bu-ozpyl).

Covers the contacts-schema-free three-step resolution path (rel_029 / bu-ozpyl):

  (a) contacts_source_links hit — local_entity_id returned (synced contacts).
  (b) contacts_source_links row with NULL local_entity_id → ValueError
      (data integrity: every source-linked contact must carry an entity anchor).
  (c) contacts_source_links miss + contact_entity_map hit — entity_id returned
      (CRM contacts created via contact_create; populated by rel_029 migration).
  (d) contacts_source_links + contact_entity_map miss + UUID in public.entities →
      entity_id returned (entity-first / pass-through callers).
  (e) No path resolves → None.
  (f) contacts_source_links table absent (UndefinedTableError) → falls through
      to contact_entity_map / public.entities.

All tests are pure unit tests (no Docker/Postgres). The asyncpg pool is mocked
via unittest.mock.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit

_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()

# Each pool's fetchrow results follow the resolver's lookup order (_entity_resolve.py):
# contacts_source_links, contact_entity_map, public.entities. An exception is a DB error.


# ===========================================================================
# (a) contacts_source_links hit
# ===========================================================================


class TestSourceLinksHit:
    async def test_returns_local_entity_id(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(
            fetchrow=AsyncMock(side_effect=[{"local_entity_id": _ENTITY_ID}, None, None])
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID


# ===========================================================================
# (b) contacts_source_links row with NULL local_entity_id → ValueError
# ===========================================================================


class TestSourceLinksNullEntityId:
    async def test_raises_value_error_with_contact_id(self):
        """bu-ozpyl: a source-linked contact with NULL local_entity_id is a data
        integrity error; the ValueError must name the offending contact_id."""
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(fetchrow=AsyncMock(side_effect=[{"local_entity_id": None}, None, None]))
        with pytest.raises(ValueError, match="data integrity issue"):
            await resolve_contact_entity_id(pool, _CONTACT_ID)
        pool = fake_pool(fetchrow=AsyncMock(side_effect=[{"local_entity_id": None}, None, None]))
        with pytest.raises(ValueError, match=str(_CONTACT_ID)):
            await resolve_contact_entity_id(pool, _CONTACT_ID)


# ===========================================================================
# (c) contacts_source_links miss + contact_entity_map hit (CRM contacts)
# ===========================================================================


class TestContactEntityMapHit:
    async def test_returns_entity_id_from_map(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(fetchrow=AsyncMock(side_effect=[None, {"entity_id": _ENTITY_ID}, None]))
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID


# ===========================================================================
# (d) contacts_source_links + map miss, UUID is a known entity_id
# ===========================================================================


class TestEntityDirectHit:
    async def test_returns_entity_id_when_map_absent(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(fetchrow=AsyncMock(side_effect=[None, None, {"id": _ENTITY_ID}]))
        result = await resolve_contact_entity_id(pool, _ENTITY_ID)
        assert result == _ENTITY_ID


# ===========================================================================
# (e) No path resolves → None
# ===========================================================================


class TestNotFound:
    async def test_returns_none_when_not_found(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(fetchrow=AsyncMock(side_effect=[None, None, None]))
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result is None


# ===========================================================================
# (f) contacts_source_links table absent → falls through to map / entities
# ===========================================================================


class TestSourceLinksTableAbsent:
    async def test_undefined_table_falls_through_to_map(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(
            fetchrow=AsyncMock(
                side_effect=[
                    asyncpg.UndefinedTableError("mocked error"),
                    {"entity_id": _ENTITY_ID},
                    None,
                ]
            )
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID

    async def test_undefined_table_falls_through_to_entities(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(
            fetchrow=AsyncMock(
                side_effect=[asyncpg.UndefinedTableError("mocked error"), None, {"id": _ENTITY_ID}]
            )
        )
        result = await resolve_contact_entity_id(pool, _ENTITY_ID)
        assert result == _ENTITY_ID

    async def test_undefined_column_falls_through(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(
            fetchrow=AsyncMock(
                side_effect=[
                    asyncpg.UndefinedColumnError("mocked error"),
                    {"entity_id": _ENTITY_ID},
                    None,
                ]
            )
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID

    async def test_undefined_table_no_fallback_returns_none(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(
            fetchrow=AsyncMock(
                side_effect=[asyncpg.UndefinedTableError("mocked error"), None, None]
            )
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result is None


# ===========================================================================
# contact_entity_map table absent → falls through to entities
# ===========================================================================


class TestContactEntityMapTableAbsent:
    async def test_undefined_table_falls_through_to_entities(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = fake_pool(
            fetchrow=AsyncMock(
                side_effect=[None, asyncpg.UndefinedTableError("mocked error"), {"id": _ENTITY_ID}]
            )
        )
        result = await resolve_contact_entity_id(pool, _ENTITY_ID)
        assert result == _ENTITY_ID
