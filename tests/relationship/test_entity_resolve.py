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
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

pytestmark = pytest.mark.unit

_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()


def _make_pool(
    source_links_row=None,
    contact_entity_map_row=None,
    entities_row=None,
    source_links_error=None,
    contact_entity_map_error=None,
):
    """Return a mock asyncpg.Pool for the three-step resolver.

    fetchrow call order (mirrors _entity_resolve.py):
      1. contacts_source_links
      2. contact_entity_map
      3. public.entities

    Pass a row dict for a "hit" and None for a "miss".
    Pass an exception class for *_error to simulate DB errors (UndefinedTableError, etc.).
    """
    pool = MagicMock(spec=asyncpg.Pool)
    call_index = 0

    async def _fetchrow(query, *args):
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            if source_links_error is not None:
                raise source_links_error("mocked error")
            return source_links_row
        if call_index == 2:
            if contact_entity_map_error is not None:
                raise contact_entity_map_error("mocked error")
            return contact_entity_map_row
        # Third call → public.entities lookup
        return entities_row

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


# ===========================================================================
# (a) contacts_source_links hit
# ===========================================================================


class TestSourceLinksHit:
    async def test_returns_local_entity_id(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(source_links_row={"local_entity_id": _ENTITY_ID})
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

        pool = _make_pool(source_links_row={"local_entity_id": None})
        with pytest.raises(ValueError, match="data integrity issue"):
            await resolve_contact_entity_id(pool, _CONTACT_ID)
        pool = _make_pool(source_links_row={"local_entity_id": None})
        with pytest.raises(ValueError, match=str(_CONTACT_ID)):
            await resolve_contact_entity_id(pool, _CONTACT_ID)


# ===========================================================================
# (c) contacts_source_links miss + contact_entity_map hit (CRM contacts)
# ===========================================================================


class TestContactEntityMapHit:
    async def test_returns_entity_id_from_map(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_row=None,
            contact_entity_map_row={"entity_id": _ENTITY_ID},
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID


# ===========================================================================
# (d) contacts_source_links + map miss, UUID is a known entity_id
# ===========================================================================


class TestEntityDirectHit:
    async def test_returns_entity_id_when_map_absent(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_row=None,
            contact_entity_map_row=None,
            entities_row={"id": _ENTITY_ID},
        )
        result = await resolve_contact_entity_id(pool, _ENTITY_ID)
        assert result == _ENTITY_ID


# ===========================================================================
# (e) No path resolves → None
# ===========================================================================


class TestNotFound:
    async def test_returns_none_when_not_found(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_row=None,
            contact_entity_map_row=None,
            entities_row=None,
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result is None


# ===========================================================================
# (f) contacts_source_links table absent → falls through to map / entities
# ===========================================================================


class TestSourceLinksTableAbsent:
    async def test_undefined_table_falls_through_to_map(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_error=asyncpg.UndefinedTableError,
            contact_entity_map_row={"entity_id": _ENTITY_ID},
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID

    async def test_undefined_table_falls_through_to_entities(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_error=asyncpg.UndefinedTableError,
            contact_entity_map_row=None,
            entities_row={"id": _ENTITY_ID},
        )
        result = await resolve_contact_entity_id(pool, _ENTITY_ID)
        assert result == _ENTITY_ID

    async def test_undefined_column_falls_through(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_error=asyncpg.UndefinedColumnError,
            contact_entity_map_row={"entity_id": _ENTITY_ID},
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result == _ENTITY_ID

    async def test_undefined_table_no_fallback_returns_none(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_error=asyncpg.UndefinedTableError,
            contact_entity_map_row=None,
            entities_row=None,
        )
        result = await resolve_contact_entity_id(pool, _CONTACT_ID)
        assert result is None


# ===========================================================================
# contact_entity_map table absent → falls through to entities
# ===========================================================================


class TestContactEntityMapTableAbsent:
    async def test_undefined_table_falls_through_to_entities(self):
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = _make_pool(
            source_links_row=None,
            contact_entity_map_error=asyncpg.UndefinedTableError,
            entities_row={"id": _ENTITY_ID},
        )
        result = await resolve_contact_entity_id(pool, _ENTITY_ID)
        assert result == _ENTITY_ID
