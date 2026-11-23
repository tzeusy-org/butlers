"""Real-Postgres regression: entity_graph_walk / entity_graph_path traversal.

RFC 0031 (about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md),
bu-8cdl1.8 Slice 3 — the recursive-CTE traversal in
``butlers.core.entity_graph_edges`` (``walk_entity_graph``,
``find_entity_graph_path``). Mocked-pool unit tests
(tests/core_tools/test_graph.py) already cover the MCP-tool control flow with
a fake pool; this file exercises the actual recursive CTE against a real
Postgres so multi-hop traversal, direction filtering, edge-type filtering,
cycle safety, and withheld-edge exclusion are verified against real SQL
semantics, not asserted against a mock.

Schema is the real migrated "core" chain. The edge helpers create a bare
``public.entities`` row for every endpoint first, so the tests can keep using
arbitrary UUIDs as entity ids while the real foreign keys hold.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.entity_graph_edges import (
    MAX_WALK_HOPS,
    coverage_for_entities,
    find_entity_graph_path,
    walk_entity_graph,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


async def _ensure_entities(pool: asyncpg.Pool, *ids: uuid.UUID) -> None:
    """Bare ``public.entities`` rows, so edges between arbitrary ids satisfy the FKs."""
    await pool.execute(
        "INSERT INTO public.entities (id, canonical_name, entity_type) "
        "SELECT id, 'entity ' || id::text, 'person' FROM unnest($1::uuid[]) AS id "
        "ON CONFLICT (id) DO NOTHING",
        list(ids),
    )


async def _live_edge(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    obj: uuid.UUID,
    sensitivity: str = "normal",
) -> None:
    await _ensure_entities(pool, subject, obj)
    await pool.execute(
        """
        INSERT INTO public.entity_graph_edges (
            source_schema, source_table, source_id,
            subject_entity_id, predicate, object_entity_id, sensitivity
        ) VALUES ('test', 'facts', gen_random_uuid(), $1, $2, $3, $4)
        """,
        subject,
        predicate,
        obj,
        sensitivity,
    )


async def _withheld_edge(pool: asyncpg.Pool, *, subject: uuid.UUID) -> None:
    await _ensure_entities(pool, subject)
    await pool.execute(
        """
        INSERT INTO public.entity_graph_edges (
            source_schema, source_table, source_id,
            subject_entity_id, predicate, object_entity_id,
            sensitivity, withheld_reason
        ) VALUES ('test', 'facts', gen_random_uuid(), $1, NULL, NULL, 'pii', 'sensitivity')
        """,
        subject,
    )


class TestEntityGraphWalk:
    # REQ-entity-graph-009: public graph read seam only; broader reader boundary remains uncovered.
    async def test_direct_neighbor_at_hop_one(self, db_pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)

        rows = await walk_entity_graph(db_pool, entity_id=a)

        assert [dict(r) for r in rows] == [
            {
                "entity_id": b,
                "hop": 1,
                "id": rows[0]["id"],
                "subject_entity_id": a,
                "predicate": "knows",
                "object_entity_id": b,
            }
        ]

    # REQ-entity-graph-005: bounded traversal or wrapper-filter assertions only.
    async def test_multi_hop_reports_nearest_hop(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=b, predicate="knows", obj=c)

        rows = await walk_entity_graph(db_pool, entity_id=a, max_hops=3)

        by_entity = {r["entity_id"]: r["hop"] for r in rows}
        assert by_entity == {b: 1, c: 2}

    async def test_hop_cap_stops_traversal(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=b, predicate="knows", obj=c)

        rows = await walk_entity_graph(db_pool, entity_id=a, max_hops=1)

        assert {r["entity_id"] for r in rows} == {b}

    async def test_direction_out_only_follows_subject_to_object(
        self, db_pool: asyncpg.Pool
    ) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        # c -> a: an inbound edge relative to a, invisible to an 'out' walk.
        await _live_edge(db_pool, subject=c, predicate="knows", obj=a)

        rows = await walk_entity_graph(db_pool, entity_id=a, direction="out")

        assert {r["entity_id"] for r in rows} == {b}

    async def test_direction_in_only_follows_object_to_subject(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=c, predicate="knows", obj=a)

        rows = await walk_entity_graph(db_pool, entity_id=a, direction="in")

        assert {r["entity_id"] for r in rows} == {c}

    async def test_edge_types_filters_by_predicate(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=a, predicate="committed-to", obj=c)

        rows = await walk_entity_graph(db_pool, entity_id=a, edge_types=["committed-to"])

        assert {r["entity_id"] for r in rows} == {c}

    # REQ-entity-graph-003: sensitivity shape or withheld traversal assertions only.
    async def test_withheld_edges_are_never_traversed(self, db_pool: asyncpg.Pool) -> None:
        a = _uuid()
        await _withheld_edge(db_pool, subject=a)

        rows = await walk_entity_graph(db_pool, entity_id=a)

        assert rows == []

    # REQ-entity-graph-005: bounded traversal or wrapper-filter assertions only.
    async def test_cycle_does_not_hang_or_revisit_start(self, db_pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=b, predicate="knows", obj=a)

        rows = await walk_entity_graph(db_pool, entity_id=a, max_hops=MAX_WALK_HOPS)

        assert {r["entity_id"] for r in rows} == {b}

    async def test_no_edges_returns_empty_list(self, db_pool: asyncpg.Pool) -> None:
        assert await walk_entity_graph(db_pool, entity_id=_uuid()) == []

    # REQ-entity-graph-005: bounded traversal or wrapper-filter assertions only.
    async def test_truncation_keeps_nearest_hop_entities_over_farther_ones(
        self, db_pool: asyncpg.Pool
    ) -> None:
        """A limit smaller than the reachable set must drop farthest hops first.

        Hop-2 entities are given small UUID ints (sorting *before* the hop-1
        entities' large-int UUIDs) so that truncating by raw entity_id order
        instead of hop distance would keep the wrong (farther) entities.
        """
        a = _uuid()
        near = [uuid.UUID(int=100 + i) for i in range(3)]
        far = [uuid.UUID(int=1 + i) for i in range(3)]
        for n in near:
            await _live_edge(db_pool, subject=a, predicate="knows", obj=n)
        for n, f in zip(near, far, strict=True):
            await _live_edge(db_pool, subject=n, predicate="knows", obj=f)

        rows = await walk_entity_graph(db_pool, entity_id=a, max_hops=2, limit=3)

        assert {r["entity_id"] for r in rows} == set(near)

    async def test_max_hops_out_of_range_raises(self, db_pool: asyncpg.Pool) -> None:
        with pytest.raises(ValueError, match="max_hops"):
            await walk_entity_graph(db_pool, entity_id=_uuid(), max_hops=MAX_WALK_HOPS + 1)

    async def test_invalid_direction_raises(self, db_pool: asyncpg.Pool) -> None:
        with pytest.raises(ValueError, match="direction"):
            await walk_entity_graph(db_pool, entity_id=_uuid(), direction="sideways")


class TestEntityGraphPath:
    # REQ-entity-graph-006: ordered/missing/zero-hop path or wrapper receipt assertions only.
    async def test_finds_shortest_path_in_order(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=b, predicate="knows", obj=c)

        path = await find_entity_graph_path(db_pool, from_entity_id=a, to_entity_id=c, max_hops=3)

        assert path is not None
        assert [(e["subject_entity_id"], e["object_entity_id"]) for e in path] == [
            (a, b),
            (b, c),
        ]

    async def test_returns_none_when_unreachable(self, db_pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()

        assert await find_entity_graph_path(db_pool, from_entity_id=a, to_entity_id=b) is None

    # REQ-entity-graph-006: ordered/missing/zero-hop path or wrapper receipt assertions only.
    async def test_returns_none_when_beyond_max_hops(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=b, predicate="knows", obj=c)

        assert (
            await find_entity_graph_path(db_pool, from_entity_id=a, to_entity_id=c, max_hops=1)
            is None
        )

    # REQ-entity-graph-006: ordered/missing/zero-hop path or wrapper receipt assertions only.
    async def test_same_entity_returns_empty_path(self, db_pool: asyncpg.Pool) -> None:
        a = _uuid()

        assert await find_entity_graph_path(db_pool, from_entity_id=a, to_entity_id=a) == []

    async def test_respects_edge_type_filter(self, db_pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)

        path = await find_entity_graph_path(
            db_pool, from_entity_id=a, to_entity_id=b, edge_types=["committed-to"]
        )

        assert path is None


class TestCoverageForEntities:
    """RFC 0031 Slice 4: batch relationship counts for catalog-search integration."""

    # REQ-entity-graph-008: incident count helper or mocked HTTP coverage mapping only.
    async def test_counts_edges_on_both_subject_and_object_side(
        self, db_pool: asyncpg.Pool
    ) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=c, predicate="knows", obj=a)

        coverage = await coverage_for_entities(db_pool, [a])

        assert coverage == {a: (2, 0)}

    # REQ-entity-graph-008: incident count helper or mocked HTTP coverage mapping only.
    async def test_withheld_edge_counts_separately_from_live(self, db_pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _withheld_edge(db_pool, subject=a)

        coverage = await coverage_for_entities(db_pool, [a])

        assert coverage == {a: (1, 1)}

    async def test_entity_with_no_edges_absent_from_result(self, db_pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)

        lonely = _uuid()
        coverage = await coverage_for_entities(db_pool, [a, lonely])

        assert lonely not in coverage
        assert coverage[a] == (1, 0)

    async def test_empty_entity_ids_returns_empty_without_querying(
        self, db_pool: asyncpg.Pool
    ) -> None:
        assert await coverage_for_entities(db_pool, []) == {}

    async def test_batches_multiple_entities_in_one_call(self, db_pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=a, predicate="knows", obj=b)
        await _live_edge(db_pool, subject=b, predicate="knows", obj=c)

        coverage = await coverage_for_entities(db_pool, [a, b, c])

        assert coverage == {a: (1, 0), b: (2, 0), c: (1, 0)}
