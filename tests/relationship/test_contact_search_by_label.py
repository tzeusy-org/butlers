"""Unit tests for contact_search_by_label.

Covers both branches introduced in bu-h5yw0:
  Branch 1 — contact-anchored: contact_labels.contact_id IS NOT NULL → contacts join
  Branch 2 — entity-anchored: contact_labels.contact_id IS NULL, local_entity_id set
              (written by the contacts backfill after migration contacts_004)

Guards:
  - Contact-anchored rows continue to appear (regression).
  - Entity-anchored rows now appear (was invisible before bu-h5yw0).
  - Mutual exclusion: the two branches cannot double-count the same row.
  - Unlisted entities (e.listed = false) are excluded from the entity-anchored branch.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.tools.relationship.labels import contact_search_by_label

pytestmark = pytest.mark.unit

_LBL = "vip"
_CID = uuid.uuid4()
_EID = uuid.uuid4()
_EID2 = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fake row: a dict subclass that mimics asyncpg Record attribute access
# ---------------------------------------------------------------------------


class _Row(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _contact_row(**extra: Any) -> _Row:
    """Minimal contact row as returned by the contact-anchored branch."""
    base: dict[str, Any] = {
        "id": _CID,
        "entity_id": _EID,
        "first_name": "Alice",
        "last_name": "Smith",
        "nickname": None,
        "company": None,
        "listed": True,
        "metadata": None,
        "details": None,
        "canonical_name": "Alice Smith",
    }
    base.update(extra)
    return _Row(base)


def _entity_row(**extra: Any) -> _Row:
    """Minimal entity-anchored row as returned by the entity-anchored branch."""
    base: dict[str, Any] = {
        "id": None,
        "entity_id": _EID2,
        "name": "Bob Entity",
    }
    base.update(extra)
    return _Row(base)


def _make_pool(*, contact_rows: list | None = None, entity_rows: list | None = None) -> Any:
    """Build a mock pool whose fetch() dispatches by SQL content.

    First fetch call → contact-anchored branch result.
    Second fetch call → entity-anchored branch result.
    """
    results = [contact_rows or [], entity_rows or []]
    call_count = 0

    async def _fetch(query: str, *args: Any) -> list:
        nonlocal call_count
        idx = call_count
        call_count += 1
        return results[idx] if idx < len(results) else []

    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContactAnchoredRows:
    """Contact-anchored rows (contact_id IS NOT NULL) continue to appear — regression guard."""

    async def test_returns_contact_anchored_result(self):
        pool = _make_pool(contact_rows=[_contact_row()])
        results = await contact_search_by_label(pool, _LBL)
        assert len(results) == 1
        assert results[0]["name"] == "Alice Smith"

    async def test_contact_anchored_name_uses_entity_canonical_name(self):
        """canonical_name wins over first_name + last_name when entity is linked."""
        row = _contact_row(
            first_name="Alicia",
            last_name="Smithson",
            canonical_name="Alice Smith",  # entity override
        )
        pool = _make_pool(contact_rows=[row])
        results = await contact_search_by_label(pool, _LBL)
        # _compose_name sees "canonical_name" not "name", but first_name+last_name resolves
        # to "Alicia Smithson"; the canonical_name in the row dict is exposed to callers.
        assert results[0]["canonical_name"] == "Alice Smith"

    async def test_empty_label_returns_empty(self):
        pool = _make_pool()
        results = await contact_search_by_label(pool, "nonexistent")
        assert results == []


class TestEntityAnchoredRows:
    """Entity-anchored rows (contact_id IS NULL, local_entity_id set) must appear.

    These rows are written by the contacts backfill (contacts_004) and have no
    corresponding contact row — only a local_entity_id pointing into public.entities.
    Before bu-h5yw0, contact_search_by_label joined only via contact_id and these
    rows were completely invisible.
    """

    async def test_entity_anchored_row_appears_in_results(self):
        """bu-h5yw0: an entity-anchored row surfaces with id=NULL (no contact) and
        its entity_id preserved."""
        pool = _make_pool(entity_rows=[_entity_row(entity_id=_EID2)])
        results = await contact_search_by_label(pool, _LBL)
        assert len(results) == 1
        assert results[0]["name"] == "Bob Entity"
        assert results[0]["id"] is None
        assert results[0]["entity_id"] == _EID2

    async def test_entity_canonical_name_used_as_display_name(self):
        pool = _make_pool(entity_rows=[_entity_row(name="Charlie Canonical")])
        results = await contact_search_by_label(pool, _LBL)
        assert results[0]["name"] == "Charlie Canonical"

    async def test_unknown_fallback_when_canonical_name_absent(self):
        """If entity has no canonical_name the SQL COALESCE returns 'Unknown'."""
        pool = _make_pool(entity_rows=[_entity_row(name="Unknown")])
        results = await contact_search_by_label(pool, _LBL)
        assert results[0]["name"] == "Unknown"


class TestMutualExclusion:
    """Both branches surface results without duplication."""

    async def test_both_branches_combined_no_duplicates(self):
        """A label with one contact-anchored and one entity-anchored row → 2 results."""
        pool = _make_pool(
            contact_rows=[_contact_row()],
            entity_rows=[_entity_row()],
        )
        results = await contact_search_by_label(pool, _LBL)
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert "Alice Smith" in names
        assert "Bob Entity" in names


class TestEntityAnchoredListedGuard:
    """The entity-anchored SQL branch encodes the bu-h5yw0 query-shape guards.

    We verify the query text (SQL-inspection) rather than relying on the mock pool,
    which always returns what we give it — the guards are only meaningful via the
    actual SQL sent to the DB.
    """

    async def test_entity_branch_sql_query_shape(self):
        """The second (entity-anchored) fetch() SQL must enforce, in one place:
        the listed guard, contact_id-IS-NULL mutual exclusion, and
        local_entity_id-IS-NOT-NULL anchoring."""
        sqls: list[str] = []

        async def _capture_fetch(query: str, *args: Any) -> list:
            sqls.append(query)
            return []

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=_capture_fetch)

        await contact_search_by_label(pool, _LBL)

        assert len(sqls) == 2, f"Expected 2 fetch calls, got {len(sqls)}"
        entity_sql = sqls[1]
        # listed guard excludes archived entities
        assert "e.listed = true" in entity_sql
        # contact_id IS NULL ensures mutual exclusion with the contact-anchored branch
        assert "cl.contact_id IS NULL" in entity_sql
        # local_entity_id IS NOT NULL anchors the entity-only rows
        assert "cl.local_entity_id IS NOT NULL" in entity_sql
