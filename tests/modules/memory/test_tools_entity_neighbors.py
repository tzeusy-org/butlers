"""Tests for entity_neighbors() graph traversal tool."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory.tools.entities import entity_neighbors

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_ID = "tenant-abc"
START_UUID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
ENTITY_B = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
ENTITY_C = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000003")
ENTITY_D = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000004")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    # Default: entity exists (fetchval returns 1)
    pool.fetchval = AsyncMock(return_value=1)
    return pool


FACT_UUID_1 = uuid.UUID("ffffffff-0000-0000-0000-000000000001")
FACT_UUID_2 = uuid.UUID("ffffffff-0000-0000-0000-000000000002")
FACT_UUID_3 = uuid.UUID("ffffffff-0000-0000-0000-000000000003")


def _neighbor_row(
    entity_id: uuid.UUID,
    canonical_name: str,
    entity_type: str,
    predicate: str,
    depth: int,
    path: list[uuid.UUID],
    *,
    dir: str = "outgoing",
    content: str = "",
    fact_id: uuid.UUID = FACT_UUID_1,
) -> dict:
    return {
        "entity_id": entity_id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "predicate": predicate,
        "dir": dir,
        "content": content,
        "fact_id": fact_id,
        "depth": depth,
        "path": path,
    }


# ---------------------------------------------------------------------------
# Single-hop traversal
# ---------------------------------------------------------------------------


class TestSingleHop:
    async def test_outgoing_single_neighbor(self, mock_pool: AsyncMock) -> None:
        """Single outgoing edge returns one neighbor at depth 1."""
        mock_pool.fetch = AsyncMock(
            return_value=[
                _neighbor_row(ENTITY_B, "Bob", "person", "knows", 1, [START_UUID, ENTITY_B]),
            ]
        )

        result = await entity_neighbors(
            mock_pool, str(START_UUID), tenant_id=TENANT_ID, max_depth=1
        )

        assert len(result) == 1
        assert result[0]["entity"]["id"] == str(ENTITY_B)
        assert result[0]["entity"]["canonical_name"] == "Bob"
        assert result[0]["entity"]["entity_type"] == "person"
        assert result[0]["predicate"] == "knows"
        assert result[0]["depth"] == 1
        assert result[0]["path"] == [str(START_UUID), str(ENTITY_B)]

    async def test_no_edges_returns_empty(self, mock_pool: AsyncMock) -> None:
        """No edge-facts returns an empty list."""
        mock_pool.fetch = AsyncMock(return_value=[])

        result = await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        assert result == []

    async def test_multiple_outgoing_neighbors(self, mock_pool: AsyncMock) -> None:
        """Multiple outgoing edges return multiple neighbors."""
        mock_pool.fetch = AsyncMock(
            return_value=[
                _neighbor_row(ENTITY_B, "Bob", "person", "knows", 1, [START_UUID, ENTITY_B]),
                _neighbor_row(
                    ENTITY_C, "Corp", "organization", "works_at", 1, [START_UUID, ENTITY_C]
                ),
            ]
        )

        result = await entity_neighbors(
            mock_pool, str(START_UUID), tenant_id=TENANT_ID, max_depth=1
        )

        assert len(result) == 2
        names = {r["entity"]["canonical_name"] for r in result}
        assert names == {"Bob", "Corp"}


# ---------------------------------------------------------------------------
# Multi-hop traversal
# ---------------------------------------------------------------------------


class TestMultiHop:
    async def test_two_hop_traversal(self, mock_pool: AsyncMock) -> None:
        """Depth-2 traversal returns neighbors at both depth 1 and 2."""
        mock_pool.fetch = AsyncMock(
            return_value=[
                _neighbor_row(ENTITY_B, "Bob", "person", "knows", 1, [START_UUID, ENTITY_B]),
                _neighbor_row(
                    ENTITY_C,
                    "Carol",
                    "person",
                    "knows",
                    2,
                    [START_UUID, ENTITY_B, ENTITY_C],
                ),
            ]
        )

        result = await entity_neighbors(
            mock_pool, str(START_UUID), tenant_id=TENANT_ID, max_depth=2
        )

        assert len(result) == 2
        depths = [r["depth"] for r in result]
        assert depths == [1, 2]
        assert result[1]["path"] == [str(START_UUID), str(ENTITY_B), str(ENTITY_C)]


# ---------------------------------------------------------------------------
# Predicate filtering
# ---------------------------------------------------------------------------


class TestPredicateFilter:
    async def test_filter_restricts_edges(self, mock_pool: AsyncMock) -> None:
        """Predicate filter is passed as parameter to the query."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(
            mock_pool,
            str(START_UUID),
            tenant_id=TENANT_ID,
            predicate_filter=["knows", "works_at"],
        )

        call_args = mock_pool.fetch.call_args[0]
        sql = call_args[0]
        params = call_args[1:]
        assert "ANY($4)" in sql
        assert ["knows", "works_at"] in params

    async def test_no_filter_omits_predicate_clause(self, mock_pool: AsyncMock) -> None:
        """Without predicate_filter, no ANY clause appears."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        sql = mock_pool.fetch.call_args[0][0]
        assert "ANY($4)" not in sql


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


class TestDirection:
    async def test_outgoing_uses_entity_id_match(self, mock_pool: AsyncMock) -> None:
        """Outgoing direction matches on entity_id in the base case."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(
            mock_pool, str(START_UUID), tenant_id=TENANT_ID, direction="outgoing"
        )

        sql = mock_pool.fetch.call_args[0][0]
        assert "f.entity_id = $1" in sql
        assert "f.object_entity_id AS neighbor_id" in sql

    async def test_incoming_uses_object_entity_id_match(self, mock_pool: AsyncMock) -> None:
        """Incoming direction matches on object_entity_id in the base case."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(
            mock_pool, str(START_UUID), tenant_id=TENANT_ID, direction="incoming"
        )

        sql = mock_pool.fetch.call_args[0][0]
        assert "f.object_entity_id = $1" in sql
        assert "f.entity_id AS neighbor_id" in sql

    async def test_both_direction_includes_both_unions(self, mock_pool: AsyncMock) -> None:
        """Both direction includes both outgoing and incoming UNION ALL branches."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID, direction="both")

        sql = mock_pool.fetch.call_args[0][0]
        assert "f.entity_id = $1" in sql
        assert "f.object_entity_id = $1" in sql
        assert sql.count("UNION ALL") >= 2  # base + recursive unions

    async def test_incoming_returns_subject_entities(self, mock_pool: AsyncMock) -> None:
        """Incoming traversal returns entities that point TO the start entity."""
        mock_pool.fetch = AsyncMock(
            return_value=[
                _neighbor_row(ENTITY_B, "Bob", "person", "knows", 1, [START_UUID, ENTITY_B]),
            ]
        )

        result = await entity_neighbors(
            mock_pool, str(START_UUID), tenant_id=TENANT_ID, direction="incoming"
        )

        assert len(result) == 1
        assert result[0]["entity"]["id"] == str(ENTITY_B)


# ---------------------------------------------------------------------------
# max_depth capping
# ---------------------------------------------------------------------------


class TestMaxDepthCapping:
    async def test_caps_at_5(self, mock_pool: AsyncMock) -> None:
        """max_depth > 5 is capped to 5."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID, max_depth=10)

        params = mock_pool.fetch.call_args[0]
        # $3 is max_depth
        assert params[3] == 5

    async def test_floors_at_1(self, mock_pool: AsyncMock) -> None:
        """max_depth < 1 is floored to 1."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID, max_depth=0)

        params = mock_pool.fetch.call_args[0]
        assert params[3] == 1

    async def test_default_depth_is_2(self, mock_pool: AsyncMock) -> None:
        """Default max_depth is 2."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        params = mock_pool.fetch.call_args[0]
        assert params[3] == 2


# ---------------------------------------------------------------------------
# Parameter passing
# ---------------------------------------------------------------------------


class TestParameterPassing:
    async def test_passes_entity_uuid_and_tenant(self, mock_pool: AsyncMock) -> None:
        """Entity UUID and tenant_id are passed as first two params."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        params = mock_pool.fetch.call_args[0]
        assert params[1] == START_UUID
        assert params[2] == TENANT_ID

    async def test_sql_uses_recursive_cte(self, mock_pool: AsyncMock) -> None:
        """Generated SQL uses WITH RECURSIVE."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        sql = mock_pool.fetch.call_args[0][0]
        assert "WITH RECURSIVE" in sql
        assert "neighbors" in sql

    async def test_joins_entities_for_metadata(self, mock_pool: AsyncMock) -> None:
        """SQL joins entities table to get canonical_name and entity_type."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        sql = mock_pool.fetch.call_args[0][0]
        assert "JOIN shared.entities e" in sql
        assert "e.canonical_name" in sql
        assert "e.entity_type" in sql

    async def test_filters_active_facts_only(self, mock_pool: AsyncMock) -> None:
        """SQL filters for validity = 'active' facts only."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        sql = mock_pool.fetch.call_args[0][0]
        assert "validity = 'active'" in sql


# ---------------------------------------------------------------------------
# Result serialization
# ---------------------------------------------------------------------------


class TestResultSerialization:
    async def test_uuids_serialized_to_strings(self, mock_pool: AsyncMock) -> None:
        """UUIDs in entity.id and path are serialized to strings."""
        mock_pool.fetch = AsyncMock(
            return_value=[
                _neighbor_row(ENTITY_B, "Bob", "person", "knows", 1, [START_UUID, ENTITY_B]),
            ]
        )

        result = await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        assert isinstance(result[0]["entity"]["id"], str)
        assert all(isinstance(p, str) for p in result[0]["path"])

    async def test_result_structure(self, mock_pool: AsyncMock) -> None:
        """Result dicts have entity, predicate, direction, content, depth, fact_id, path."""
        mock_pool.fetch = AsyncMock(
            return_value=[
                _neighbor_row(
                    ENTITY_B,
                    "Bob",
                    "person",
                    "knows",
                    1,
                    [START_UUID, ENTITY_B],
                    dir="outgoing",
                    content="friends since college",
                    fact_id=FACT_UUID_1,
                ),
            ]
        )

        result = await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        item = result[0]
        assert set(item.keys()) == {
            "entity",
            "predicate",
            "direction",
            "content",
            "depth",
            "fact_id",
            "path",
        }
        assert set(item["entity"].keys()) == {"id", "canonical_name", "entity_type"}
        assert item["direction"] == "outgoing"
        assert item["content"] == "friends since college"
        assert item["fact_id"] == str(FACT_UUID_1)


# ---------------------------------------------------------------------------
# Entity existence validation
# ---------------------------------------------------------------------------


class TestEntityExistenceValidation:
    async def test_raises_for_nonexistent_entity(self, mock_pool: AsyncMock) -> None:
        """ValueError raised when entity_id does not exist."""
        mock_pool.fetchval = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="does not exist"):
            await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

    async def test_validates_entity_before_query(self, mock_pool: AsyncMock) -> None:
        """Entity existence check happens before the main fetch query."""
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.fetch = AsyncMock(return_value=[])

        with pytest.raises(ValueError):
            await entity_neighbors(mock_pool, str(START_UUID), tenant_id=TENANT_ID)

        mock_pool.fetch.assert_not_called()
