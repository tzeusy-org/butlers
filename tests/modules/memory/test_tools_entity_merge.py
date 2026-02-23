"""Tests for entity_merge MCP tool in entities.py.

Tests cover:
- Basic merge: facts re-pointed, aliases merged, metadata merged, source tombstoned
- Source and target must be different
- Source not found raises ValueError
- Target not found raises ValueError
- Already-tombstoned source raises ValueError
- Uniqueness conflict: target wins when confidence is equal or higher
- Uniqueness conflict: source wins when source confidence is higher
- Alias deduplication (no duplicate aliases added)
- Metadata merge: target values win on conflict
- Audit event emitted to memory_events
- Return value contains correct keys and counts
- entity_resolve excludes tombstoned entities
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory.tools.entities import entity_merge, entity_resolve

pytestmark = pytest.mark.unit

TENANT_ID = "tenant-test"

SOURCE_UUID = uuid.UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
TARGET_UUID = uuid.UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
SOURCE_ID = str(SOURCE_UUID)
TARGET_ID = str(TARGET_UUID)

FACT_UUID_1 = uuid.UUID("cccccccc-1111-1111-1111-cccccccccccc")
FACT_UUID_2 = uuid.UUID("dddddddd-1111-1111-1111-dddddddddddd")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entity_row(
    entity_id: uuid.UUID,
    canonical_name: str = "Test Entity",
    aliases: list[str] | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Return a mock asyncpg Record-like row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": entity_id,
        "canonical_name": canonical_name,
        "aliases": aliases or [],
        "metadata": metadata or {},
    }[key]
    return row


def _make_fact_row(
    fact_id: uuid.UUID,
    scope: str = "global",
    predicate: str = "likes",
    confidence: float = 1.0,
) -> MagicMock:
    """Return a mock asyncpg Record-like fact row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": fact_id,
        "scope": scope,
        "predicate": predicate,
        "confidence": confidence,
    }[key]
    return row


@pytest.fixture()
def mock_pool() -> MagicMock:
    """Return a mock asyncpg pool with acquire() as an async context manager."""
    pool = MagicMock()
    conn = AsyncMock()

    # Set up acquire() as async context manager
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)

    # Set up transaction() as async context manager on the connection
    transaction_cm = MagicMock()
    transaction_cm.__aenter__ = AsyncMock(return_value=None)
    transaction_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_cm)

    return pool


def _get_conn(mock_pool: MagicMock) -> AsyncMock:
    """Helper to retrieve the mock connection from a pool fixture."""
    return mock_pool.acquire.return_value.__aenter__.return_value


# ---------------------------------------------------------------------------
# Basic merge tests
# ---------------------------------------------------------------------------


class TestEntityMergeBasic:
    """Tests for the happy-path entity merge operation."""

    async def test_returns_expected_keys(self, mock_pool: MagicMock) -> None:
        """entity_merge returns a dict with the correct keys."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID, aliases=["src-alias"], metadata={})
        tgt_row = _make_entity_row(TARGET_UUID, aliases=["tgt-alias"], metadata={})

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, None])
        conn.fetch = AsyncMock(return_value=[])  # no facts on source
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert set(result.keys()) == {
            "target_entity_id",
            "source_entity_id",
            "facts_repointed",
            "facts_superseded",
            "aliases_added",
        }

    async def test_correct_entity_ids_in_result(self, mock_pool: MagicMock) -> None:
        """result contains source and target entity IDs as strings."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, None])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["source_entity_id"] == SOURCE_ID
        assert result["target_entity_id"] == TARGET_ID

    async def test_no_facts_zero_counts(self, mock_pool: MagicMock) -> None:
        """When source has no facts, facts_repointed and facts_superseded are 0."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["facts_repointed"] == 0
        assert result["facts_superseded"] == 0

    async def test_fact_without_conflict_is_repointed(self, mock_pool: MagicMock) -> None:
        """Facts with no target conflict are re-pointed to target entity."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        fact = _make_fact_row(FACT_UUID_1, scope="global", predicate="likes")

        # fetchrow calls: src entity, tgt entity, conflict check (None = no conflict)
        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, None])
        conn.fetch = AsyncMock(return_value=[fact])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["facts_repointed"] == 1
        assert result["facts_superseded"] == 0

        # Verify UPDATE facts SET entity_id was called
        execute_calls = conn.execute.call_args_list
        repoint_calls = [c for c in execute_calls if "UPDATE facts SET entity_id" in c[0][0]]
        assert len(repoint_calls) == 1
        # Should re-point to target UUID
        assert TARGET_UUID in repoint_calls[0][0]

    async def test_new_alias_added_to_target(self, mock_pool: MagicMock) -> None:
        """Source aliases not present in target are added and counted."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID, aliases=["src-alias"])
        tgt_row = _make_entity_row(TARGET_UUID, aliases=["tgt-alias"])

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["aliases_added"] == 1

        # Check that target entity was updated with merged aliases
        execute_calls = conn.execute.call_args_list
        update_entity_calls = [
            c
            for c in execute_calls
            if "UPDATE entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        assert len(update_entity_calls) == 1
        aliases_arg = update_entity_calls[0][0][1]
        assert "tgt-alias" in aliases_arg
        assert "src-alias" in aliases_arg

    async def test_duplicate_alias_not_added_twice(self, mock_pool: MagicMock) -> None:
        """Aliases already present in target (case-insensitive) are not duplicated."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID, aliases=["Shared Alias"])
        tgt_row = _make_entity_row(TARGET_UUID, aliases=["shared alias"])  # same, different case

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["aliases_added"] == 0

        execute_calls = conn.execute.call_args_list
        update_entity_calls = [
            c
            for c in execute_calls
            if "UPDATE entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        assert len(update_entity_calls) == 1
        aliases_arg = update_entity_calls[0][0][1]
        # Should only have one entry (not duplicated)
        assert len(aliases_arg) == 1

    async def test_metadata_merged_target_wins(self, mock_pool: MagicMock) -> None:
        """Target metadata wins on conflict; source-only keys are also included."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID, metadata={"role": "engineer", "team": "backend"})
        tgt_row = _make_entity_row(TARGET_UUID, metadata={"role": "manager"})

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        execute_calls = conn.execute.call_args_list
        update_entity_calls = [
            c
            for c in execute_calls
            if "UPDATE entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        assert len(update_entity_calls) == 1
        metadata_json_arg = update_entity_calls[0][0][2]
        merged = json.loads(metadata_json_arg)
        # Target wins on conflict
        assert merged["role"] == "manager"
        # Source-only key is included
        assert merged["team"] == "backend"

    async def test_source_tombstoned_with_merged_into(self, mock_pool: MagicMock) -> None:
        """Source entity is tombstoned with merged_into=target_entity_id."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID, metadata={})
        tgt_row = _make_entity_row(TARGET_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        execute_calls = conn.execute.call_args_list
        tombstone_calls = [
            c
            for c in execute_calls
            if "UPDATE entities SET metadata" in c[0][0] and SOURCE_UUID in c[0]
        ]
        assert len(tombstone_calls) == 1
        tombstone_meta = json.loads(tombstone_calls[0][0][1])
        assert tombstone_meta["merged_into"] == TARGET_ID

    async def test_audit_event_emitted(self, mock_pool: MagicMock) -> None:
        """An entity_merge audit event is inserted into memory_events."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        execute_calls = conn.execute.call_args_list
        audit_calls = [c for c in execute_calls if "INSERT INTO memory_events" in c[0][0]]
        assert len(audit_calls) == 1
        _, tenant_arg, payload_json = audit_calls[0][0]
        assert tenant_arg == TENANT_ID
        payload = json.loads(payload_json)
        assert payload["source_entity_id"] == SOURCE_ID
        assert payload["target_entity_id"] == TARGET_ID

    async def test_queries_use_correct_uuids(self, mock_pool: MagicMock) -> None:
        """Source and target are fetched with correct UUID objects."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        # First fetchrow: source entity
        src_call_args = conn.fetchrow.call_args_list[0][0]
        assert SOURCE_UUID in src_call_args
        assert TENANT_ID in src_call_args

        # Second fetchrow: target entity
        tgt_call_args = conn.fetchrow.call_args_list[1][0]
        assert TARGET_UUID in tgt_call_args
        assert TENANT_ID in tgt_call_args


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestEntityMergeErrors:
    """Tests for error conditions in entity_merge."""

    async def test_raises_when_source_equals_target(self, mock_pool: MagicMock) -> None:
        """Raises ValueError when source_entity_id == target_entity_id."""
        with pytest.raises(ValueError, match="must be different"):
            await entity_merge(mock_pool, SOURCE_ID, SOURCE_ID, tenant_id=TENANT_ID)

    async def test_raises_when_source_not_found(self, mock_pool: MagicMock) -> None:
        """Raises ValueError when source entity doesn't exist."""
        conn = _get_conn(mock_pool)
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Source entity"):
            await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

    async def test_raises_when_target_not_found(self, mock_pool: MagicMock) -> None:
        """Raises ValueError when target entity doesn't exist."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, None])

        with pytest.raises(ValueError, match="Target entity"):
            await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

    async def test_raises_when_source_already_tombstoned(self, mock_pool: MagicMock) -> None:
        """Raises ValueError when source entity is already tombstoned."""
        conn = _get_conn(mock_pool)
        # Source already has merged_into in metadata
        src_row = _make_entity_row(SOURCE_UUID, metadata={"merged_into": TARGET_ID})
        tgt_row = _make_entity_row(TARGET_UUID)

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row])

        with pytest.raises(ValueError, match="already tombstoned"):
            await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)


# ---------------------------------------------------------------------------
# Conflict resolution tests
# ---------------------------------------------------------------------------


class TestEntityMergeConflictResolution:
    """Tests for fact conflict resolution during entity merge."""

    async def test_target_wins_on_conflict_equal_confidence(self, mock_pool: MagicMock) -> None:
        """When both facts have equal confidence, target fact survives."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        src_fact = _make_fact_row(FACT_UUID_1, scope="global", predicate="likes", confidence=0.8)
        tgt_conflict = _make_fact_row(
            FACT_UUID_2, scope="global", predicate="likes", confidence=0.8
        )

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, tgt_conflict])
        conn.fetch = AsyncMock(return_value=[src_fact])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["facts_superseded"] == 1
        assert result["facts_repointed"] == 0

        execute_calls = conn.execute.call_args_list
        # Source fact should be superseded (target wins)
        supersede_src_calls = [
            c
            for c in execute_calls
            if "UPDATE facts SET validity = 'superseded'" in c[0][0] and FACT_UUID_1 in c[0]
        ]
        assert len(supersede_src_calls) == 1

    async def test_target_wins_on_conflict_higher_confidence(self, mock_pool: MagicMock) -> None:
        """When target has higher confidence, target fact survives."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        src_fact = _make_fact_row(FACT_UUID_1, scope="global", predicate="likes", confidence=0.5)
        tgt_conflict = _make_fact_row(
            FACT_UUID_2, scope="global", predicate="likes", confidence=0.9
        )

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, tgt_conflict])
        conn.fetch = AsyncMock(return_value=[src_fact])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["facts_superseded"] == 1
        assert result["facts_repointed"] == 0

        # Source fact should be superseded
        execute_calls = conn.execute.call_args_list
        supersede_calls = [
            c
            for c in execute_calls
            if "UPDATE facts SET validity = 'superseded'" in c[0][0] and FACT_UUID_1 in c[0]
        ]
        assert len(supersede_calls) == 1

    async def test_source_wins_on_conflict_higher_confidence(self, mock_pool: MagicMock) -> None:
        """When source has higher confidence, source fact survives and target is superseded."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        src_fact = _make_fact_row(FACT_UUID_1, scope="global", predicate="likes", confidence=0.95)
        tgt_conflict = _make_fact_row(
            FACT_UUID_2, scope="global", predicate="likes", confidence=0.3
        )

        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, tgt_conflict])
        conn.fetch = AsyncMock(return_value=[src_fact])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        assert result["facts_superseded"] == 1
        assert result["facts_repointed"] == 0

        execute_calls = conn.execute.call_args_list

        # Target fact should be superseded
        supersede_tgt_calls = [
            c
            for c in execute_calls
            if "UPDATE facts SET validity = 'superseded'" in c[0][0] and FACT_UUID_2 in c[0]
        ]
        assert len(supersede_tgt_calls) == 1

        # Source fact should be re-pointed to target entity
        repoint_calls = [
            c
            for c in execute_calls
            if "UPDATE facts SET entity_id" in c[0][0] and TARGET_UUID in c[0]
        ]
        assert len(repoint_calls) == 1

    async def test_mixed_conflicts_and_repoints(self, mock_pool: MagicMock) -> None:
        """Multiple facts: some re-pointed, some superseded."""
        conn = _get_conn(mock_pool)
        src_row = _make_entity_row(SOURCE_UUID)
        tgt_row = _make_entity_row(TARGET_UUID)

        FACT_UUID_3 = uuid.UUID("eeeeeeee-1111-1111-1111-eeeeeeeeeeee")
        FACT_UUID_4 = uuid.UUID("ffffffff-1111-1111-1111-ffffffffffff")

        # Two source facts
        src_fact1 = _make_fact_row(FACT_UUID_1, scope="global", predicate="likes", confidence=0.8)
        src_fact2 = _make_fact_row(FACT_UUID_3, scope="global", predicate="knows", confidence=0.9)

        # Only fact1 has a conflict on target
        tgt_conflict = _make_fact_row(
            FACT_UUID_4, scope="global", predicate="likes", confidence=0.6
        )

        # fetchrow calls: src entity, tgt entity,
        # then for fact1: conflict check (returns tgt_conflict)
        # then for fact2: conflict check (returns None = no conflict)
        conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, tgt_conflict, None])
        conn.fetch = AsyncMock(return_value=[src_fact1, src_fact2])
        conn.execute = AsyncMock()

        result = await entity_merge(mock_pool, SOURCE_ID, TARGET_ID, tenant_id=TENANT_ID)

        # fact1 conflicts: src confidence 0.8 > tgt 0.6, so src wins -> superseded
        # fact2 no conflict: re-pointed
        assert result["facts_superseded"] == 1
        assert result["facts_repointed"] == 1


# ---------------------------------------------------------------------------
# entity_resolve tombstone exclusion tests
# ---------------------------------------------------------------------------

_SOURCE_PATH = pathlib.Path(
    importlib.util.find_spec("butlers.modules.memory.tools.entities").origin  # type: ignore[union-attr]
)


class TestEntityResolveTombstoneExclusion:
    """Tests that entity_resolve excludes tombstoned entities."""

    async def test_tombstone_filter_in_discovery_sql(self) -> None:
        """The discovery SQL for all tiers excludes tombstoned entities."""
        with open(_SOURCE_PATH) as f:
            source = f.read()

        assert "(metadata->>'merged_into') IS NULL" in source, (
            "Tombstone filter missing from entity_resolve SQL"
        )

    async def test_fuzzy_tombstone_filter_present(self) -> None:
        """The fuzzy candidate SQL also excludes tombstoned entities."""
        with open(_SOURCE_PATH) as f:
            source = f.read()

        # Count occurrences — should be at least 4 (3 tiers + fuzzy)
        count = source.count("(metadata->>'merged_into') IS NULL")
        assert count >= 4, f"Expected at least 4 tombstone filters, found {count}"

    async def test_resolve_excludes_tombstoned_mock(self) -> None:
        """entity_resolve SQL includes tombstone exclusion clause — verified via mock pool."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=None)

        result = await entity_resolve(pool, "Alice", tenant_id=TENANT_ID)
        assert result == []

        # Verify fetch was called with SQL containing tombstone filter
        call_args = pool.fetch.call_args_list
        assert len(call_args) >= 1
        sql = call_args[0][0][0]
        assert "(metadata->>'merged_into') IS NULL" in sql
