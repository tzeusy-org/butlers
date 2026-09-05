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

Schema is the ``ENTITY_GRAPH_EDGES`` stand-in
(``src/butlers/testing/schema_standins.py``) rather than the full "core"
Alembic chain: the stand-in drops the FK to ``public.entities`` (per its
no-FK rule, so it stays independently creatable), which conveniently means
these tests can use arbitrary UUIDs as entity ids without provisioning real
entity rows — exactly what a pure traversal test over the edge table needs.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.entity_graph_edges import (
    MAX_WALK_HOPS,
    find_entity_graph_path,
    walk_entity_graph,
)
from butlers.testing.schema_standins import ENTITY_GRAPH_EDGES

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(postgres_container) -> asyncpg.Pool:
    """A fresh database per test, hand-rolled with only the edges stand-in."""
    admin_url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(admin_url)
    database_name = f"entity_graph_walk_{uuid.uuid4().hex[:12]}"
    database_url = urlunsplit(parsed._replace(path=f"/{database_name}"))

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await admin.close()

    p = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    await p.execute(ENTITY_GRAPH_EDGES.ddl())
    yield p
    await p.close()


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


async def _live_edge(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    obj: uuid.UUID,
    sensitivity: str = "normal",
) -> None:
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
    async def test_direct_neighbor_at_hop_one(self, pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)

        rows = await walk_entity_graph(pool, entity_id=a)

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

    async def test_multi_hop_reports_nearest_hop(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=b, predicate="knows", obj=c)

        rows = await walk_entity_graph(pool, entity_id=a, max_hops=3)

        by_entity = {r["entity_id"]: r["hop"] for r in rows}
        assert by_entity == {b: 1, c: 2}

    async def test_hop_cap_stops_traversal(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=b, predicate="knows", obj=c)

        rows = await walk_entity_graph(pool, entity_id=a, max_hops=1)

        assert {r["entity_id"] for r in rows} == {b}

    async def test_direction_out_only_follows_subject_to_object(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        # c -> a: an inbound edge relative to a, invisible to an 'out' walk.
        await _live_edge(pool, subject=c, predicate="knows", obj=a)

        rows = await walk_entity_graph(pool, entity_id=a, direction="out")

        assert {r["entity_id"] for r in rows} == {b}

    async def test_direction_in_only_follows_object_to_subject(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=c, predicate="knows", obj=a)

        rows = await walk_entity_graph(pool, entity_id=a, direction="in")

        assert {r["entity_id"] for r in rows} == {c}

    async def test_edge_types_filters_by_predicate(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=a, predicate="committed-to", obj=c)

        rows = await walk_entity_graph(pool, entity_id=a, edge_types=["committed-to"])

        assert {r["entity_id"] for r in rows} == {c}

    async def test_withheld_edges_are_never_traversed(self, pool: asyncpg.Pool) -> None:
        a = _uuid()
        await _withheld_edge(pool, subject=a)

        rows = await walk_entity_graph(pool, entity_id=a)

        assert rows == []

    async def test_cycle_does_not_hang_or_revisit_start(self, pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=b, predicate="knows", obj=a)

        rows = await walk_entity_graph(pool, entity_id=a, max_hops=MAX_WALK_HOPS)

        assert {r["entity_id"] for r in rows} == {b}

    async def test_no_edges_returns_empty_list(self, pool: asyncpg.Pool) -> None:
        assert await walk_entity_graph(pool, entity_id=_uuid()) == []

    async def test_truncation_keeps_nearest_hop_entities_over_farther_ones(
        self, pool: asyncpg.Pool
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
            await _live_edge(pool, subject=a, predicate="knows", obj=n)
        for n, f in zip(near, far, strict=True):
            await _live_edge(pool, subject=n, predicate="knows", obj=f)

        rows = await walk_entity_graph(pool, entity_id=a, max_hops=2, limit=3)

        assert {r["entity_id"] for r in rows} == set(near)

    async def test_max_hops_out_of_range_raises(self, pool: asyncpg.Pool) -> None:
        with pytest.raises(ValueError, match="max_hops"):
            await walk_entity_graph(pool, entity_id=_uuid(), max_hops=MAX_WALK_HOPS + 1)

    async def test_invalid_direction_raises(self, pool: asyncpg.Pool) -> None:
        with pytest.raises(ValueError, match="direction"):
            await walk_entity_graph(pool, entity_id=_uuid(), direction="sideways")


class TestEntityGraphPath:
    async def test_finds_shortest_path_in_order(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=b, predicate="knows", obj=c)

        path = await find_entity_graph_path(pool, from_entity_id=a, to_entity_id=c, max_hops=3)

        assert path is not None
        assert [(e["subject_entity_id"], e["object_entity_id"]) for e in path] == [
            (a, b),
            (b, c),
        ]

    async def test_returns_none_when_unreachable(self, pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()

        assert await find_entity_graph_path(pool, from_entity_id=a, to_entity_id=b) is None

    async def test_returns_none_when_beyond_max_hops(self, pool: asyncpg.Pool) -> None:
        a, b, c = _uuid(), _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)
        await _live_edge(pool, subject=b, predicate="knows", obj=c)

        assert (
            await find_entity_graph_path(pool, from_entity_id=a, to_entity_id=c, max_hops=1) is None
        )

    async def test_same_entity_returns_empty_path(self, pool: asyncpg.Pool) -> None:
        a = _uuid()

        assert await find_entity_graph_path(pool, from_entity_id=a, to_entity_id=a) == []

    async def test_respects_edge_type_filter(self, pool: asyncpg.Pool) -> None:
        a, b = _uuid(), _uuid()
        await _live_edge(pool, subject=a, predicate="knows", obj=b)

        path = await find_entity_graph_path(
            pool, from_entity_id=a, to_entity_id=b, edge_types=["committed-to"]
        )

        assert path is None
