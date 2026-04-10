"""Behavioral tests for entity MCP tools.

Covers: entity_create, entity_get, entity_update, entity_resolve, entity_merge,
entity_neighbors — testing through the public tool interface.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory.tools.entities import (
    _SCORE_EXACT_NAME,
    entity_create,
    entity_get,
    entity_merge,
    entity_neighbors,
    entity_resolve,
    entity_update,
)

pytestmark = pytest.mark.unit

ENTITY_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
ENTITY_STR = str(ENTITY_UUID)
SOURCE_UUID = uuid.UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
TARGET_UUID = uuid.UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
SOURCE_ID = str(SOURCE_UUID)
TARGET_ID = str(TARGET_UUID)
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_row(
    entity_id: uuid.UUID = ENTITY_UUID,
    name: str = "Alice",
    entity_type: str = "person",
    aliases: list | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": entity_id,
        "canonical_name": name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "metadata": metadata or {},
        "created_at": NOW,
        "updated_at": NOW,
    }


def _entity_mock_row(
    entity_id: uuid.UUID,
    canonical_name: str = "Test",
    aliases: list | None = None,
    metadata: dict | None = None,
    match_type: str = "exact",
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda s, k: {
        "id": entity_id,
        "canonical_name": canonical_name,
        "entity_type": "person",
        "aliases": aliases or [],
        "metadata": metadata or {},
        "roles": [],
        "match_type": match_type,
    }[k]
    return row


@pytest.fixture()
def pool() -> AsyncMock:
    return AsyncMock()


def _merge_pool(src_row, tgt_row, *, fact_rows=None, edge_rows=None, extra_fetchrow=None):
    """Build a mock pool for entity_merge with conn set up properly."""
    pool = MagicMock()
    conn = AsyncMock()
    extra = extra_fetchrow or []
    conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, *extra])
    conn.fetch = AsyncMock(side_effect=[(fact_rows or []), (edge_rows or [])])
    conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)
    return pool, conn


# ---------------------------------------------------------------------------
# entity_create
# ---------------------------------------------------------------------------


class TestEntityCreate:
    async def test_returns_entity_id_string(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=ENTITY_UUID)
        result = await entity_create(pool, "Alice", "person")
        assert result == {"entity_id": ENTITY_STR}

    async def test_invalid_type_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="Invalid entity_type"):
            await entity_create(pool, "Ghost", "ghost")
        pool.fetchval.assert_not_awaited()

    async def test_unique_constraint_raises_value_error(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(
            side_effect=[Exception("duplicate key value violates unique constraint"), None]
        )
        with pytest.raises(ValueError, match="already exists"):
            await entity_create(pool, "Alice", "person")

    async def test_tombstoned_entity_allows_retry(self, pool: AsyncMock) -> None:
        tombstone_id = uuid.uuid4()
        new_id = uuid.uuid4()
        pool.fetchval = AsyncMock(
            side_effect=[
                Exception("duplicate key value violates unique constraint"),
                tombstone_id,
                new_id,
            ]
        )
        result = await entity_create(pool, "Alice", "person")
        assert result == {"entity_id": str(new_id)}


# ---------------------------------------------------------------------------
# entity_get
# ---------------------------------------------------------------------------


class TestEntityGet:
    async def test_returns_serialized_entity(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=_entity_row())
        result = await entity_get(pool, ENTITY_STR)
        assert result["id"] == ENTITY_STR
        assert result["canonical_name"] == "Alice"
        assert isinstance(result["created_at"], str)

    async def test_returns_none_when_not_found(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=None)
        assert await entity_get(pool, ENTITY_STR) is None


# ---------------------------------------------------------------------------
# entity_update
# ---------------------------------------------------------------------------


class TestEntityUpdate:
    async def test_returns_none_when_not_found(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=None)
        assert await entity_update(pool, ENTITY_STR) is None

    async def test_updates_and_returns_serialized(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(
            side_effect=[
                {"id": ENTITY_UUID, "metadata": {}},
                _entity_row(name="New Name"),
            ]
        )
        result = await entity_update(pool, ENTITY_STR, canonical_name="New Name")
        assert result is not None
        assert result["canonical_name"] == "New Name"


# ---------------------------------------------------------------------------
# entity_resolve
# ---------------------------------------------------------------------------


class TestEntityResolve:
    async def test_empty_name_returns_empty(self, pool: AsyncMock) -> None:
        assert await entity_resolve(pool, "") == []
        pool.fetch.assert_not_called()

    async def test_exact_match_returns_score_exact_name(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Alice", match_type="exact")
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "Alice")
        assert results[0]["score"] == _SCORE_EXACT_NAME
        assert results[0]["name_match"] == "exact"

    async def test_results_sorted_by_score_desc(self, pool: AsyncMock) -> None:
        e1 = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Alice", match_type="exact")
        e2 = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Alicia", match_type="prefix")
        pool.fetch = AsyncMock(return_value=[e2, e1])
        results = await entity_resolve(pool, "Alice")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_identifier_and_name_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="not both"):
            await entity_resolve(pool, "Alice", identifier="Owner")


# ---------------------------------------------------------------------------
# entity_merge
# ---------------------------------------------------------------------------


class TestEntityMerge:
    async def test_raises_when_source_equals_target(self) -> None:
        with pytest.raises(ValueError, match="must be different"):
            await entity_merge(AsyncMock(), SOURCE_ID, SOURCE_ID)

    async def test_raises_when_already_tombstoned(self) -> None:
        src = _entity_mock_row(SOURCE_UUID, metadata={"merged_into": TARGET_ID})
        tgt = _entity_mock_row(TARGET_UUID)
        pool, _conn = _merge_pool(src, tgt)
        with pytest.raises(ValueError, match="already tombstoned"):
            await entity_merge(pool, SOURCE_ID, TARGET_ID)

    async def test_result_has_expected_keys(self) -> None:
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        pool, conn = _merge_pool(src, tgt)
        result = await entity_merge(pool, SOURCE_ID, TARGET_ID)
        assert {
            "target_entity_id",
            "source_entity_id",
            "facts_repointed",
            "facts_superseded",
            "edge_facts_repointed",
            "edge_facts_superseded",
            "aliases_added",
        } == set(result.keys())

    async def test_source_tombstoned_with_merged_into(self) -> None:
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        pool, conn = _merge_pool(src, tgt)
        await entity_merge(pool, SOURCE_ID, TARGET_ID)
        tombstones = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE public.entities SET metadata" in c[0][0] and SOURCE_UUID in c[0]
        ]
        assert len(tombstones) == 1
        assert json.loads(tombstones[0][0][1])["merged_into"] == TARGET_ID

    async def test_unidentified_flag_not_propagated(self) -> None:
        src = _entity_mock_row(SOURCE_UUID, metadata={"unidentified": True, "src_key": "v"})
        tgt = _entity_mock_row(TARGET_UUID, metadata={})
        pool, conn = _merge_pool(src, tgt)
        await entity_merge(pool, SOURCE_ID, TARGET_ID)
        updates = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE public.entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        merged = json.loads(updates[0][0][2])
        assert "unidentified" not in merged
        assert merged.get("src_key") == "v"

    async def test_source_canonical_name_added_to_target_aliases(self) -> None:
        src = _entity_mock_row(SOURCE_UUID, canonical_name="tzeusii", aliases=[])
        tgt = _entity_mock_row(TARGET_UUID, canonical_name="Tze How Lee", aliases=[])
        pool, conn = _merge_pool(src, tgt)
        await entity_merge(pool, SOURCE_ID, TARGET_ID)
        updates = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE public.entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        merged_aliases = updates[0][0][1]
        assert "tzeusii" in merged_aliases


# ---------------------------------------------------------------------------
# entity_neighbors
# ---------------------------------------------------------------------------


class TestEntityNeighbors:
    async def test_raises_for_nonexistent_entity(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="does not exist"):
            await entity_neighbors(pool, ENTITY_STR)

    async def test_result_shape(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=1)
        neighbor_id = uuid.uuid4()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "entity_id": neighbor_id,
                    "canonical_name": "Bob",
                    "entity_type": "person",
                    "predicate": "knows",
                    "dir": "outgoing",
                    "content": "",
                    "fact_id": uuid.uuid4(),
                    "depth": 1,
                    "path": [ENTITY_UUID, neighbor_id],
                }
            ]
        )
        result = await entity_neighbors(pool, ENTITY_STR)
        assert len(result) == 1
        assert {"entity", "predicate", "direction", "content", "depth", "fact_id", "path"} == set(
            result[0].keys()
        )
        assert isinstance(result[0]["entity"]["id"], str)
