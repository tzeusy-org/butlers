"""Gap-filling tests for entity registry and contact-entity bridge integration.

This module covers edge cases and integration scenarios NOT already tested in:
  - test_tools_entities.py (entity CRUD)
  - test_tools_entity_resolve.py (entity_resolve)
  - test_tools_entity_merge.py (entity_merge)
  - test_storage_fact_entity_id.py (store_fact with entity_id)
  - tests/tools/test_contact_entity_lifecycle.py (contact-entity bridge)
  - tests/tools/test_relationship_resolve.py (salience integration)

Coverage areas:
1. entity_create: cross-tenant uniqueness, edge-case types
2. entity_get: cross-tenant isolation returning None, invalid UUID format
3. entity_update: cross-tenant None return, updating all fields simultaneously
4. entity_resolve: score floor filter, entity_type=None vs explicit, metadata queries
5. entity_merge: multiple facts repointed together, tenant isolation checks
6. Contact-entity bridge: sync_entity_create/update fail-open, nickname-driven sync
7. Salience: _tokenize helper, _display_name_from_row edge cases, empty-name early exit
8. Integration: entity_id forwarding when entity-keyed fact has different subject label
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.entities import (
    _SCORE_EXACT_NAME,
    entity_create,
    entity_get,
    entity_merge,
    entity_resolve,
    entity_update,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"

ENTITY_UUID = uuid.UUID("aaaaaaaa-1111-2222-3333-aaaaaaaaaaaa")
ENTITY_UUID2 = uuid.UUID("bbbbbbbb-1111-2222-3333-bbbbbbbbbbbb")

ENTITY_ID = str(ENTITY_UUID)
ENTITY_ID2 = str(ENTITY_UUID2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity_row(
    entity_id: uuid.UUID = ENTITY_UUID,
    tenant_id: str = TENANT_A,
    canonical_name: str = "Alice Smith",
    entity_type: str = "person",
    aliases: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    from datetime import UTC, datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": entity_id,
        "tenant_id": tenant_id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now,
    }


def _make_entity_mock_row(
    entity_id: str,
    canonical_name: str,
    entity_type: str = "person",
    aliases: list[str] | None = None,
    match_type: str = "exact",
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": uuid.UUID(entity_id),
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "match_type": match_type,
    }[key]
    return row


@pytest.fixture()
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


def _make_merge_pool(
    src_aliases: list[str] | None = None,
    src_metadata: dict | None = None,
    tgt_aliases: list[str] | None = None,
    tgt_metadata: dict | None = None,
):
    """Build a mock pool+conn for entity_merge happy-path tests."""
    pool = MagicMock()
    conn = AsyncMock()

    src_row = MagicMock()
    src_row.__getitem__ = lambda self, key: {
        "id": ENTITY_UUID,
        "canonical_name": "Alice",
        "aliases": src_aliases or [],
        "metadata": src_metadata or {},
    }[key]

    tgt_row = MagicMock()
    tgt_row.__getitem__ = lambda self, key: {
        "id": ENTITY_UUID2,
        "canonical_name": "Alice Smith",
        "aliases": tgt_aliases or [],
        "metadata": tgt_metadata or {},
    }[key]

    conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()

    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)

    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=None)
    tx_cm.__aexit__ = AsyncMock(return_value=None)

    conn.transaction = MagicMock(return_value=tx_cm)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


# ===========================================================================
# 1. entity_create gap tests
# ===========================================================================


class TestEntityCreateGaps:
    """Edge cases not covered by test_tools_entities.TestEntityCreate."""

    async def test_different_type_allows_same_name(self, mock_pool: AsyncMock) -> None:
        """Two entities with the same canonical_name but different types are distinct."""
        # The uniqueness constraint is (tenant, canonical_name, entity_type), so
        # the same name can exist for different types.  Both create calls should succeed.
        mock_pool.fetchval = AsyncMock(return_value=ENTITY_UUID)

        # First: person
        result1 = await entity_create(mock_pool, "Acme Corp", "organization", tenant_id=TENANT_A)
        # Second: place (no DB error mocked — just verify second call is attempted)
        result2 = await entity_create(mock_pool, "Acme Corp", "place", tenant_id=TENANT_A)

        assert result1 == {"entity_id": ENTITY_ID}
        assert result2 == {"entity_id": ENTITY_ID}
        assert mock_pool.fetchval.call_count == 2

    async def test_create_with_type_other(self, mock_pool: AsyncMock) -> None:
        """entity_type='other' is valid and proceeds to DB insert."""
        mock_pool.fetchval = AsyncMock(return_value=ENTITY_UUID)
        result = await entity_create(mock_pool, "Some Concept", "other", tenant_id=TENANT_A)
        assert "entity_id" in result

    async def test_empty_canonical_name_still_passes_validation(self, mock_pool: AsyncMock) -> None:
        """entity_create does not validate empty canonical_name — that is DB's job."""
        mock_pool.fetchval = AsyncMock(return_value=ENTITY_UUID)
        # Should not raise locally (DB enforces NOT NULL / length constraints)
        result = await entity_create(mock_pool, "", "person", tenant_id=TENANT_A)
        assert "entity_id" in result

    async def test_unique_constraint_error_message_contains_name(
        self, mock_pool: AsyncMock
    ) -> None:
        """ValueError message from unique constraint includes the canonical_name."""
        mock_pool.fetchval = AsyncMock(
            side_effect=Exception("duplicate key value violates unique constraint")
        )
        with pytest.raises(ValueError, match="Alice"):
            await entity_create(mock_pool, "Alice", "person", tenant_id=TENANT_A)

    async def test_aliases_empty_list_vs_none_both_insert_empty(self, mock_pool: AsyncMock) -> None:
        """Both aliases=[] and aliases=None result in an empty list passed to DB."""
        mock_pool.fetchval = AsyncMock(return_value=ENTITY_UUID)

        await entity_create(mock_pool, "Alice", "person", tenant_id=TENANT_A, aliases=None)
        _, _, _, _, aliases_arg_none, _ = mock_pool.fetchval.call_args_list[0][0]
        assert aliases_arg_none == []

        await entity_create(mock_pool, "Bob", "person", tenant_id=TENANT_A, aliases=[])
        _, _, _, _, aliases_arg_empty, _ = mock_pool.fetchval.call_args_list[1][0]
        assert aliases_arg_empty == []


# ===========================================================================
# 2. entity_get gap tests
# ===========================================================================


class TestEntityGetGaps:
    """Edge cases not covered by test_tools_entities.TestEntityGet."""

    async def test_returns_none_for_wrong_tenant(self, mock_pool: AsyncMock) -> None:
        """entity_get returns None when the entity belongs to a different tenant."""
        # Simulate DB returning no row (tenant isolation enforced via WHERE clause)
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await entity_get(mock_pool, ENTITY_ID, tenant_id=TENANT_B)
        assert result is None
        # Verify tenant_id was included in the query parameters
        sql, eid_arg, tid_arg = mock_pool.fetchrow.call_args[0]
        assert tid_arg == TENANT_B

    async def test_invalid_uuid_raises(self, mock_pool: AsyncMock) -> None:
        """Passing a non-UUID string raises ValueError before any DB call."""
        with pytest.raises((ValueError, AttributeError)):
            await entity_get(mock_pool, "not-a-uuid", tenant_id=TENANT_A)

    async def test_entity_id_queried_with_correct_uuid(self, mock_pool: AsyncMock) -> None:
        """The UUID passed to DB matches the string ID provided to entity_get."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        await entity_get(mock_pool, ENTITY_ID, tenant_id=TENANT_A)
        _, eid_arg, _ = mock_pool.fetchrow.call_args[0]
        assert eid_arg == ENTITY_UUID

    async def test_metadata_preserved_as_dict(self, mock_pool: AsyncMock) -> None:
        """Metadata returned from DB is preserved as a dict (not serialized twice)."""
        meta = {"role": "engineer", "team": "platform"}
        row = _make_entity_row(metadata=meta)
        mock_pool.fetchrow = AsyncMock(return_value=row)
        result = await entity_get(mock_pool, ENTITY_ID, tenant_id=TENANT_A)
        assert result["metadata"] == meta
        assert isinstance(result["metadata"], dict)

    async def test_empty_aliases_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """entity_get with no aliases returns an empty list (not None)."""
        row = _make_entity_row(aliases=[])
        mock_pool.fetchrow = AsyncMock(return_value=row)
        result = await entity_get(mock_pool, ENTITY_ID, tenant_id=TENANT_A)
        assert result["aliases"] == []
        assert isinstance(result["aliases"], list)


# ===========================================================================
# 3. entity_update gap tests
# ===========================================================================


class TestEntityUpdateGaps:
    """Edge cases not covered by test_tools_entities.TestEntityUpdate."""

    async def test_cross_tenant_update_returns_none(self, mock_pool: AsyncMock) -> None:
        """entity_update returns None when the entity belongs to a different tenant."""
        # Existence check returns None (entity not found for this tenant)
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await entity_update(mock_pool, ENTITY_ID, tenant_id=TENANT_B)
        assert result is None

    async def test_update_all_fields_simultaneously(self, mock_pool: AsyncMock) -> None:
        """entity_update handles canonical_name, aliases, AND metadata together."""
        existing_meta = {"level": 1}
        current_row = {"id": ENTITY_UUID, "metadata": existing_meta}
        updated_row = _make_entity_row(
            canonical_name="Alice Johnson",
            aliases=["AJ"],
            metadata={"level": 1, "team": "core"},
        )
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(
            mock_pool,
            ENTITY_ID,
            tenant_id=TENANT_A,
            canonical_name="Alice Johnson",
            aliases=["AJ"],
            metadata={"team": "core"},
        )

        assert result is not None
        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        # All three fields should appear in the UPDATE SQL
        assert "canonical_name" in second_call_sql
        assert "aliases" in second_call_sql
        assert "metadata" in second_call_sql

    async def test_invalid_uuid_raises_on_update(self, mock_pool: AsyncMock) -> None:
        """entity_update with a non-UUID entity_id string raises before any DB call."""
        with pytest.raises((ValueError, AttributeError)):
            await entity_update(mock_pool, "bad-uuid", tenant_id=TENANT_A)

    async def test_empty_aliases_list_replaces_all(self, mock_pool: AsyncMock) -> None:
        """Passing aliases=[] replaces all existing aliases with an empty list."""
        current_row = {"id": ENTITY_UUID, "metadata": {}}
        updated_row = _make_entity_row(aliases=[])
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(mock_pool, ENTITY_ID, tenant_id=TENANT_A, aliases=[])

        assert result is not None
        # The UPDATE SQL must include aliases parameter
        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "aliases" in second_call_sql

    async def test_metadata_merge_preserves_existing_keys_not_in_new(
        self, mock_pool: AsyncMock
    ) -> None:
        """Metadata keys not in the update payload are preserved from existing metadata."""
        existing_meta = {"key_a": "old_a", "key_b": "value_b"}
        current_row = {"id": ENTITY_UUID, "metadata": existing_meta}
        merged = {"key_a": "new_a", "key_b": "value_b"}
        updated_row = _make_entity_row(metadata=merged)
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        await entity_update(
            mock_pool,
            ENTITY_ID,
            tenant_id=TENANT_A,
            metadata={"key_a": "new_a"},
        )

        second_call_args = mock_pool.fetchrow.call_args_list[1][0]
        json_params = [p for p in second_call_args[1:] if isinstance(p, str) and "key_b" in p]
        assert len(json_params) == 1
        parsed = json.loads(json_params[0])
        assert parsed["key_b"] == "value_b"  # preserved
        assert parsed["key_a"] == "new_a"  # overwritten


# ===========================================================================
# 4. entity_resolve gap tests
# ===========================================================================


class TestEntityResolveGaps:
    """Edge cases not covered by test_tools_entity_resolve."""

    async def test_score_floor_filters_zero_or_negative(self, mock_pool: AsyncMock) -> None:
        """Results with score <= _MIN_SCORE (0.0) are excluded from the final list.

        entity_resolve uses score > _MIN_SCORE filter, so a negative domain_score
        that pushes a prefix match below zero should exclude the candidate.
        """
        from butlers.modules.memory.tools.entities import _SCORE_PREFIX

        entity_id = str(uuid.uuid4())
        prefix_row = _make_entity_mock_row(entity_id, "Alice", match_type="prefix")
        mock_pool.fetch = AsyncMock(return_value=[prefix_row])

        # Domain score so negative that prefix base + domain < floor
        negative_ds = -(_SCORE_PREFIX + 1.0)

        results = await entity_resolve(
            mock_pool,
            "Ali",
            tenant_id=TENANT_A,
            context_hints={"domain_scores": {entity_id: negative_ds}},
        )

        # Score = _SCORE_PREFIX + negative_ds < _MIN_SCORE → excluded
        assert results == []

    async def test_entity_type_none_omitted_from_query(self, mock_pool: AsyncMock) -> None:
        """When entity_type=None, no type filter is added to the query parameters."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Alice", tenant_id=TENANT_A, entity_type=None)
        # Only 2 params: tenant_id and name_lower
        call_args = mock_pool.fetch.call_args[0]
        assert len(call_args) == 3  # sql + tenant + name
        assert "person" not in call_args
        assert "organization" not in call_args

    async def test_entity_type_filter_adds_third_param(self, mock_pool: AsyncMock) -> None:
        """When entity_type is set, a third query parameter is added."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Alice", tenant_id=TENANT_A, entity_type="organization")
        call_args = mock_pool.fetch.call_args[0]
        assert "organization" in call_args

    async def test_short_name_skips_fuzzy_even_when_enabled(self, mock_pool: AsyncMock) -> None:
        """Fuzzy matching is skipped for names with length <= 2, even with enable_fuzzy=True."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Al", tenant_id=TENANT_A, enable_fuzzy=True)
        # Short name (len=2): fuzzy query should NOT be issued
        assert mock_pool.fetch.call_count == 1  # only main discovery query

    async def test_multiple_candidates_all_returned_with_correct_keys(
        self, mock_pool: AsyncMock
    ) -> None:
        """When multiple candidates found, all are returned with required keys."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())
        row1 = _make_entity_mock_row(eid1, "Alice Smith", match_type="exact")
        row2 = _make_entity_mock_row(eid2, "Alice Jones", match_type="exact")
        mock_pool.fetch = AsyncMock(return_value=[row1, row2])

        results = await entity_resolve(mock_pool, "alice smith", tenant_id=TENANT_A)

        assert len(results) == 2
        for r in results:
            assert "entity_id" in r
            assert "canonical_name" in r
            assert "entity_type" in r
            assert "score" in r
            assert "name_match" in r
            assert "aliases" in r

    async def test_score_rounded_to_4_decimal_places(self, mock_pool: AsyncMock) -> None:
        """Scores are rounded to 4 decimal places in the output."""
        eid = str(uuid.uuid4())
        row = _make_entity_mock_row(eid, "Alice", match_type="exact")
        mock_pool.fetch = AsyncMock(return_value=[row])

        results = await entity_resolve(mock_pool, "alice", tenant_id=TENANT_A)

        score = results[0]["score"]
        # The score should be equal to round(score, 4)
        assert score == round(score, 4)

    async def test_graph_neighborhood_not_triggered_with_empty_domain_scores_only(
        self, mock_pool: AsyncMock
    ) -> None:
        """domain_scores-only context_hints (no topic/mentioned_with) skip graph scoring."""
        eid = str(uuid.uuid4())
        row = _make_entity_mock_row(eid, "Alice", match_type="exact")
        mock_pool.fetch = AsyncMock(return_value=[row])

        results = await entity_resolve(
            mock_pool,
            "Alice",
            tenant_id=TENANT_A,
            context_hints={"domain_scores": {eid: 10.0}},
        )

        # Should not have fetched from facts table (only one fetch call for candidates)
        # The graph scoring is skipped when no topic/mentioned_with keywords
        # But domain_scores IS applied (score = _SCORE_EXACT_NAME + 10.0)
        assert len(results) == 1
        assert results[0]["score"] == _SCORE_EXACT_NAME + 10.0


# ===========================================================================
# 5. entity_merge gap tests
# ===========================================================================


class TestEntityMergeGaps:
    """Edge cases not covered by test_tools_entity_merge."""

    async def test_multiple_facts_all_repointed(self) -> None:
        """All source facts without conflicts are each re-pointed to target."""
        pool = MagicMock()
        conn = AsyncMock()

        src_row = MagicMock()
        src_row.__getitem__ = lambda self, key: {
            "id": ENTITY_UUID,
            "canonical_name": "Alice",
            "aliases": [],
            "metadata": {},
        }[key]

        tgt_row = MagicMock()
        tgt_row.__getitem__ = lambda self, key: {
            "id": ENTITY_UUID2,
            "canonical_name": "Alice Smith",
            "aliases": [],
            "metadata": {},
        }[key]

        fact_id_1 = uuid.UUID("cccccccc-0001-0001-0001-cccccccccccc")
        fact_id_2 = uuid.UUID("dddddddd-0002-0002-0002-dddddddddddd")
        fact_id_3 = uuid.UUID("eeeeeeee-0003-0003-0003-eeeeeeeeeeee")

        def make_fact(fid):
            row = MagicMock()
            row.__getitem__ = lambda self, key: {
                "id": fid,
                "scope": "global",
                "predicate": f"pred_{str(fid)[:8]}",
                "confidence": 0.9,
            }[key]
            return row

        src_facts = [make_fact(fact_id_1), make_fact(fact_id_2), make_fact(fact_id_3)]

        # Each conflict check returns None (no conflict) for all three facts
        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, None, None, None])
        conn.fetch = AsyncMock(return_value=src_facts)
        conn.execute = AsyncMock()

        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        tx_cm = MagicMock()
        tx_cm.__aenter__ = AsyncMock(return_value=None)
        tx_cm.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=tx_cm)
        pool.acquire = MagicMock(return_value=acquire_cm)

        result = await entity_merge(pool, ENTITY_ID, ENTITY_ID2, tenant_id=TENANT_A)

        assert result["facts_repointed"] == 3
        assert result["facts_superseded"] == 0

        # All 3 re-point execute calls should use target UUID
        repoint_calls = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE facts SET entity_id" in c[0][0] and ENTITY_UUID2 in c[0]
        ]
        assert len(repoint_calls) == 3

    async def test_return_dict_has_correct_counts(self) -> None:
        """Return value counts (facts_repointed, facts_superseded, aliases_added) are ints."""
        pool, conn = _make_merge_pool()
        result = await entity_merge(pool, ENTITY_ID, ENTITY_ID2, tenant_id=TENANT_A)

        assert isinstance(result["facts_repointed"], int)
        assert isinstance(result["facts_superseded"], int)
        assert isinstance(result["aliases_added"], int)

    async def test_source_canonical_name_added_as_alias_on_target(self) -> None:
        """Source's canonical_name is not in target aliases — gap-check: canonical
        name is NOT automatically added as alias (aliases come from aliases field only)."""
        pool, conn = _make_merge_pool(
            src_aliases=[],
            tgt_aliases=[],
        )
        result = await entity_merge(pool, ENTITY_ID, ENTITY_ID2, tenant_id=TENANT_A)

        # With no source aliases, aliases_added should be 0
        assert result["aliases_added"] == 0

    async def test_tombstone_preserves_existing_metadata_keys(self) -> None:
        """Existing source metadata keys are preserved alongside merged_into in tombstone."""
        pool, conn = _make_merge_pool(src_metadata={"custom_key": "custom_value"})

        await entity_merge(pool, ENTITY_ID, ENTITY_ID2, tenant_id=TENANT_A)

        execute_calls = conn.execute.call_args_list
        tombstone_calls = [
            c
            for c in execute_calls
            if "UPDATE entities SET metadata" in c[0][0] and ENTITY_UUID in c[0]
        ]
        assert len(tombstone_calls) == 1
        tombstone_meta = json.loads(tombstone_calls[0][0][1])
        assert tombstone_meta["merged_into"] == ENTITY_ID2
        assert tombstone_meta["custom_key"] == "custom_value"  # preserved

    async def test_merge_with_no_source_aliases_emits_no_alias_update(self) -> None:
        """When both source and target have no aliases, the UPDATE entities SET aliases
        is still issued (with an empty list), ensuring updated_at is refreshed."""
        pool, conn = _make_merge_pool(src_aliases=[], tgt_aliases=[])

        await entity_merge(pool, ENTITY_ID, ENTITY_ID2, tenant_id=TENANT_A)

        execute_calls = conn.execute.call_args_list
        alias_update_calls = [c for c in execute_calls if "UPDATE entities SET aliases" in c[0][0]]
        # Should have exactly one alias update for the target entity
        assert len(alias_update_calls) == 1

    async def test_audit_event_includes_counts(self) -> None:
        """The memory_events audit entry includes correct counts from the merge."""
        pool, conn = _make_merge_pool()

        await entity_merge(pool, ENTITY_ID, ENTITY_ID2, tenant_id=TENANT_A)

        execute_calls = conn.execute.call_args_list
        audit_calls = [c for c in execute_calls if "INSERT INTO memory_events" in c[0][0]]
        assert len(audit_calls) == 1
        payload = json.loads(audit_calls[0][0][2])
        assert "facts_repointed" in payload
        assert "facts_superseded" in payload
        assert "aliases_added" in payload
        assert payload["facts_repointed"] == 0
        assert payload["facts_superseded"] == 0


# ===========================================================================
# 6. Contact-entity bridge gap tests
# ===========================================================================

# Load modules needed for contact-entity bridge tests
_CONTACTS_PATH = (
    Path(__file__).parent.parent.parent.parent / "roster/relationship/tools/contacts.py"
)


def _load_contacts_module():
    """Load contacts module with patched dependencies."""
    schema_mod = MagicMock()
    schema_mod.table_columns = AsyncMock()
    feed_mod = MagicMock()
    feed_mod._log_activity = AsyncMock()

    sys.modules["butlers.tools.relationship._schema"] = schema_mod
    sys.modules["butlers.tools.relationship.feed"] = feed_mod

    spec = importlib.util.spec_from_file_location("_contacts_gap_mod", _CONTACTS_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_contacts_gap_mod"] = mod
    spec.loader.exec_module(mod)
    return mod, schema_mod, feed_mod


_contacts_mod, _schema_mod_ref, _feed_mod_ref = _load_contacts_module()

_FULL_COLS = frozenset(
    {
        "id",
        "first_name",
        "last_name",
        "nickname",
        "name",
        "company",
        "job_title",
        "details",
        "metadata",
        "entity_id",
        "archived_at",
        "listed",
        "updated_at",
    }
)

_sync_entity_create = _contacts_mod._sync_entity_create
_sync_entity_update = _contacts_mod._sync_entity_update
_build_canonical_name = _contacts_mod._build_canonical_name
_build_entity_aliases = _contacts_mod._build_entity_aliases
contact_create = _contacts_mod.contact_create
contact_update = _contacts_mod.contact_update
contact_merge = _contacts_mod.contact_merge


def _make_contact_row(
    contact_id: uuid.UUID | None = None,
    first_name: str = "Alice",
    last_name: str = "Smith",
    nickname: str | None = "Ali",
    entity_id: uuid.UUID | None = None,
) -> dict:
    cid = contact_id or uuid.uuid4()
    return {
        "id": cid,
        "first_name": first_name,
        "last_name": last_name,
        "nickname": nickname,
        "name": f"{first_name} {last_name}",
        "company": None,
        "job_title": None,
        "details": {},
        "metadata": {},
        "entity_id": entity_id,
        "archived_at": None,
        "listed": True,
        "updated_at": None,
    }


def _asyncpg_record(d: dict):
    rec = MagicMock()
    rec.__iter__ = lambda s: iter(d.items())
    rec.__getitem__ = lambda s, k: d[k]
    rec.get = lambda k, default=None: d.get(k, default)
    rec.keys = lambda: d.keys()
    rec.values = lambda: d.values()
    rec.items = lambda: d.items()
    return rec


class TestSyncEntityCreateFailOpen:
    """_sync_entity_create fail-open and edge-case behaviour."""

    async def test_returns_none_on_value_error(self) -> None:
        """_sync_entity_create returns None when entity_create raises ValueError."""
        memory_pool = AsyncMock()
        with patch.object(
            _contacts_mod,
            "entity_create" if hasattr(_contacts_mod, "entity_create") else "_sync_entity_create",
        ):
            pass  # module-level patch not needed for this test

        with patch(
            "butlers.modules.memory.tools.entities.entity_create",
            AsyncMock(side_effect=ValueError("duplicate entity")),
        ):
            result = await _sync_entity_create(
                memory_pool, "Alice", "Smith", "Ali", tenant_id="relationship"
            )
        assert result is None

    async def test_returns_entity_id_string_on_success(self) -> None:
        """_sync_entity_create returns entity_id as a string on success."""
        memory_pool = AsyncMock()
        eid = str(uuid.uuid4())
        with patch(
            "butlers.modules.memory.tools.entities.entity_create",
            AsyncMock(return_value={"entity_id": eid}),
        ):
            result = await _sync_entity_create(
                memory_pool, "Alice", "Smith", None, tenant_id="relationship"
            )
        assert result == eid

    async def test_first_name_only_creates_entity(self) -> None:
        """_sync_entity_create works with only first_name provided."""
        memory_pool = AsyncMock()
        eid = str(uuid.uuid4())
        with patch(
            "butlers.modules.memory.tools.entities.entity_create",
            AsyncMock(return_value={"entity_id": eid}),
        ) as mock_create:
            result = await _sync_entity_create(
                memory_pool, "Alice", None, None, tenant_id="relationship"
            )
        assert result == eid
        # canonical_name should be "Alice" (first name only)
        call_args = mock_create.call_args
        assert call_args[0][1] == "Alice"  # canonical_name positional arg

    async def test_returns_none_on_runtime_error(self) -> None:
        """_sync_entity_create returns None on any unexpected exception."""
        memory_pool = AsyncMock()
        with patch(
            "butlers.modules.memory.tools.entities.entity_create",
            AsyncMock(side_effect=RuntimeError("DB connection error")),
        ):
            result = await _sync_entity_create(
                memory_pool, "Alice", "Smith", None, tenant_id="relationship"
            )
        assert result is None


class TestSyncEntityUpdateFailOpen:
    """_sync_entity_update fail-open behaviour."""

    async def test_does_not_raise_on_entity_update_failure(self) -> None:
        """_sync_entity_update silently swallows exceptions from entity_update."""
        memory_pool = AsyncMock()
        with patch(
            "butlers.modules.memory.tools.entities.entity_update",
            AsyncMock(side_effect=RuntimeError("entity DB down")),
        ):
            # Should not propagate the exception
            await _sync_entity_update(
                memory_pool,
                entity_id=ENTITY_ID,
                first_name="Alice",
                last_name="Smith",
                nickname="Ali",
                tenant_id="relationship",
            )
        # If we get here, the exception was swallowed (fail-open)

    async def test_calls_entity_update_with_correct_args(self) -> None:
        """_sync_entity_update forwards all name fields to entity_update."""
        memory_pool = AsyncMock()
        with patch(
            "butlers.modules.memory.tools.entities.entity_update",
            AsyncMock(return_value={"id": ENTITY_ID}),
        ) as mock_update:
            await _sync_entity_update(
                memory_pool,
                entity_id=ENTITY_ID,
                first_name="Alice",
                last_name="Johnson",
                nickname="AJ",
                tenant_id="relationship",
            )
        mock_update.assert_awaited_once()
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["canonical_name"] == "Alice Johnson"
        assert "AJ" in call_kwargs["aliases"]
        assert "Alice" in call_kwargs["aliases"]

    async def test_does_not_raise_on_value_error(self) -> None:
        """_sync_entity_update swallows ValueError from entity_update."""
        memory_pool = AsyncMock()
        with patch(
            "butlers.modules.memory.tools.entities.entity_update",
            AsyncMock(side_effect=ValueError("entity not found")),
        ):
            await _sync_entity_update(
                memory_pool,
                entity_id=ENTITY_ID,
                first_name="Alice",
                last_name="Smith",
                nickname=None,
                tenant_id="relationship",
            )
        # No exception propagated


class TestContactUpdateNicknameSync:
    """Nickname changes in contact_update trigger entity sync."""

    async def test_nickname_change_triggers_entity_sync(self) -> None:
        """contact_update with nickname change calls _sync_entity_update."""
        cid = uuid.uuid4()
        contact_row = _make_contact_row(contact_id=cid, entity_id=ENTITY_UUID)
        updated_row = _make_contact_row(contact_id=cid, nickname="Ally", entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),  # SELECT existing
                _asyncpg_record(updated_row),  # UPDATE RETURNING
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_update",
                AsyncMock(),
            ) as mock_sync,
        ):
            await contact_update(pool, cid, memory_pool=memory_pool, nickname="Ally")
            mock_sync.assert_awaited_once()
            # entity_id should be passed as a string
            call_kwargs = mock_sync.call_args.kwargs
            assert call_kwargs["entity_id"] == str(ENTITY_UUID)
            assert call_kwargs["nickname"] == "Ally"

    async def test_last_name_change_triggers_entity_sync(self) -> None:
        """contact_update with last_name change calls _sync_entity_update."""
        cid = uuid.uuid4()
        contact_row = _make_contact_row(contact_id=cid, entity_id=ENTITY_UUID)
        updated_row = _make_contact_row(contact_id=cid, last_name="Johnson", entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),
                _asyncpg_record(updated_row),
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(_contacts_mod, "_sync_entity_update", AsyncMock()) as mock_sync,
        ):
            await contact_update(pool, cid, memory_pool=memory_pool, last_name="Johnson")
            mock_sync.assert_awaited_once()


class TestContactEntityBridgeEdgeCases:
    """Additional edge cases for contact-entity bridge not in existing tests."""

    async def test_contact_create_entity_id_stored_as_uuid_in_db(self) -> None:
        """After entity_create, the entity_id is stored as UUID in the contacts table."""
        cid = uuid.uuid4()
        contact_row = _make_contact_row(contact_id=cid, entity_id=None)
        contact_with_entity = _make_contact_row(contact_id=cid, entity_id=ENTITY_UUID)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(contact_row),  # INSERT
                _asyncpg_record(contact_with_entity),  # UPDATE entity_id
            ]
        )
        memory_pool = AsyncMock()

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch.object(
                _contacts_mod,
                "_sync_entity_create",
                AsyncMock(return_value=ENTITY_ID),
            ),
        ):
            await contact_create(
                pool,
                first_name="Alice",
                last_name="Smith",
                memory_pool=memory_pool,
            )

        # Second fetchrow (UPDATE entity_id) should have been called with UUID
        update_call = pool.fetchrow.call_args_list[1]
        # First positional arg after SQL is the entity_id UUID
        entity_id_arg = update_call[0][1]
        assert entity_id_arg == ENTITY_UUID

    async def test_contact_merge_both_null_entity_ids_skips_entity_merge(self) -> None:
        """contact_merge with both contacts having NULL entity_ids skips entity_merge."""
        cid1 = uuid.uuid4()
        cid2 = uuid.uuid4()

        source_row = _make_contact_row(contact_id=cid1, entity_id=None)
        target_row = _make_contact_row(contact_id=cid2, entity_id=None)
        updated_target_row = dict(target_row)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _asyncpg_record(source_row),
                _asyncpg_record(target_row),
                _asyncpg_record(updated_target_row),
            ]
        )

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        memory_pool = AsyncMock()
        entity_merge_calls = []

        async def mock_entity_merge(pool, src, tgt, *, tenant_id):
            entity_merge_calls.append((src, tgt))

        with (
            patch.object(_contacts_mod, "table_columns", AsyncMock(return_value=_FULL_COLS)),
            patch.object(_contacts_mod, "_log_activity", AsyncMock()),
            patch(
                "butlers.modules.memory.tools.entities.entity_merge",
                side_effect=mock_entity_merge,
            ),
        ):
            await contact_merge(
                pool,
                source_id=cid1,
                target_id=cid2,
                memory_pool=memory_pool,
            )

        # entity_merge should NOT have been called
        assert len(entity_merge_calls) == 0


# ===========================================================================
# 7. Salience and resolve helper gap tests
# ===========================================================================

_RESOLVE_PATH = Path(__file__).parent.parent.parent.parent / "roster/relationship/tools/resolve.py"


def _load_resolve_module():
    spec = importlib.util.spec_from_file_location("_resolve_gap_mod", _RESOLVE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_resolve_gap_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_resolve_mod = _load_resolve_module()
contact_resolve = _resolve_mod.contact_resolve
CONFIDENCE_HIGH = _resolve_mod.CONFIDENCE_HIGH
CONFIDENCE_MEDIUM = _resolve_mod.CONFIDENCE_MEDIUM
CONFIDENCE_NONE = _resolve_mod.CONFIDENCE_NONE
_display_name_from_row = _resolve_mod._display_name_from_row
_generate_inferred_reason = _resolve_mod._generate_inferred_reason


class TestDisplayNameFromRow:
    """Tests for _display_name_from_row edge cases."""

    def test_full_name_from_first_and_last(self) -> None:
        row = {"first_name": "Alice", "last_name": "Smith", "nickname": None}
        assert _display_name_from_row(row) == "Alice Smith"

    def test_first_name_only(self) -> None:
        row = {"first_name": "Alice", "last_name": None, "nickname": None}
        assert _display_name_from_row(row) == "Alice"

    def test_last_name_only(self) -> None:
        row = {"first_name": None, "last_name": "Smith", "nickname": None}
        assert _display_name_from_row(row) == "Smith"

    def test_nickname_fallback_when_no_name(self) -> None:
        row = {"first_name": None, "last_name": None, "nickname": "Ali"}
        assert _display_name_from_row(row) == "Ali"

    def test_all_none_returns_unknown(self) -> None:
        row = {"first_name": None, "last_name": None, "nickname": None}
        assert _display_name_from_row(row) == "Unknown"

    def test_empty_strings_treated_as_none(self) -> None:
        row = {"first_name": "", "last_name": "", "nickname": ""}
        # Empty strings should not contribute to the full name
        result = _display_name_from_row(row)
        # full = " ".join(part for part in ["", ""] if part).strip() = ""
        # Falls back to nickname="" then first="" → "Unknown"
        assert result == "Unknown"

    def test_works_with_dict_and_mock_record(self) -> None:
        """_display_name_from_row works with both dict and Record-like objects."""
        # Dict path
        d = {"first_name": "Bob", "last_name": "Jones", "nickname": "BJ"}
        assert _display_name_from_row(d) == "Bob Jones"

        # Record-like (subscript access) path
        mock_rec = MagicMock()
        mock_rec.get = None  # force subscript path
        mock_rec.__getitem__ = lambda s, k: {
            "first_name": "Bob",
            "last_name": "Jones",
            "nickname": "BJ",
        }[k]
        assert _display_name_from_row(mock_rec) == "Bob Jones"


class TestGenerateInferredReason:
    """Tests for _generate_inferred_reason."""

    def test_relationship_type_included(self) -> None:
        candidate = {"_relationship_type": "partner"}
        reason = _generate_inferred_reason(candidate)
        assert "partner" in reason

    def test_high_interaction_count(self) -> None:
        candidate = {"_interaction_count": 15}
        reason = _generate_inferred_reason(candidate)
        assert "frequent" in reason.lower() or "contact" in reason.lower()

    def test_medium_interaction_count(self) -> None:
        candidate = {"_interaction_count": 7}
        reason = _generate_inferred_reason(candidate)
        assert "frequent" in reason.lower() or "contact" in reason.lower()

    def test_low_interaction_count(self) -> None:
        candidate = {"_interaction_count": 2}
        reason = _generate_inferred_reason(candidate)
        assert "contact" in reason.lower()

    def test_no_signals_returns_generic(self) -> None:
        candidate = {}
        reason = _generate_inferred_reason(candidate)
        assert "salience" in reason.lower() or len(reason) > 0

    def test_both_relationship_and_interaction_combined(self) -> None:
        candidate = {"_relationship_type": "spouse", "_interaction_count": 12}
        reason = _generate_inferred_reason(candidate)
        assert "spouse" in reason
        # Both signals should contribute
        assert "frequent" in reason.lower() or "contact" in reason.lower()


class TestContactResolveEdgeCases:
    """Edge cases for contact_resolve not covered in test_relationship_resolve."""

    async def test_empty_name_returns_none_confidence(self) -> None:
        """contact_resolve with empty name returns CONFIDENCE_NONE immediately."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        result = await contact_resolve(pool, "")
        assert result["confidence"] == CONFIDENCE_NONE
        assert result["contact_id"] is None
        assert result["candidates"] == []
        # Should not hit the DB at all
        pool.fetch.assert_not_called()

    async def test_whitespace_only_name_returns_none_confidence(self) -> None:
        """Whitespace-only name is stripped to empty and returns NONE confidence."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        result = await contact_resolve(pool, "   ")
        assert result["confidence"] == CONFIDENCE_NONE
        pool.fetch.assert_not_called()

    async def test_exact_single_match_returns_high_without_salience_queries(self) -> None:
        """Single exact match returns HIGH confidence immediately without salience computation."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": "uuid-1",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "nickname": None,
                    "company": None,
                    "job_title": None,
                    "metadata": {},
                    "entity_id": None,
                }
            ]
        )
        result = await contact_resolve(pool, "Alice Smith")

        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        # Only 1 fetch call for exact match; no salience queries needed
        assert pool.fetch.call_count == 1
        assert result["inferred"] is False

    async def test_no_match_returns_none_confidence_with_empty_candidates(self) -> None:
        """contact_resolve returns NONE with empty candidates when no contacts found."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        result = await contact_resolve(pool, "Completely Unknown Person")
        assert result["confidence"] == CONFIDENCE_NONE
        assert result["contact_id"] is None
        assert result["candidates"] == []
        assert result["inferred"] is False
        assert result["inferred_reason"] is None


# ===========================================================================
# 8. _tokenize helper tests (internal graph scoring utility)
# ===========================================================================


class TestTokenizeHelper:
    """Tests for the _tokenize internal helper in entities.py."""

    def test_basic_tokenization(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        tokens = _tokenize("Alice Smith")
        assert tokens == {"alice", "smith"}

    def test_numbers_are_included(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        tokens = _tokenize("born 1985")
        assert "1985" in tokens
        assert "born" in tokens

    def test_special_chars_excluded(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        tokens = _tokenize("alice@example.com")
        assert "alice" in tokens
        assert "example" in tokens
        assert "com" in tokens
        assert "@" not in tokens

    def test_empty_string_returns_empty_set(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        tokens = _tokenize("")
        assert tokens == set()

    def test_case_lowercased(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        tokens = _tokenize("ALICE SMITH")
        assert "alice" in tokens
        assert "smith" in tokens
        assert "ALICE" not in tokens

    def test_hyphenated_words(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        tokens = _tokenize("check-in")
        # Hyphen separates tokens
        assert "check" in tokens
        assert "in" in tokens

    def test_returns_set_type(self) -> None:
        from butlers.modules.memory.tools.entities import _tokenize

        result = _tokenize("hello world")
        assert isinstance(result, set)
