"""Real-Postgres regression: the entity dossier read (REQ-entity-graph-010, bu-8cdl1.8 S5).

Exercises ``butlers.core.entity_graph_edges.dossier_for_entity`` against the actual projection
SQL: live receipts from several sources on either end of an edge, true per-source counts, a bare
withheld count, and the bound on returned receipts.  Runs on the migrated "core" chain; the edge
helpers create a bare ``public.entities`` row per endpoint, so arbitrary UUIDs stand for entities.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.entity_graph_edges import dossier_for_entity

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
    source: tuple[str, str] = ("test", "facts"),
) -> None:
    await _ensure_entities(pool, subject, obj)
    await pool.execute(
        """
        INSERT INTO public.entity_graph_edges (
            source_schema, source_table, source_id,
            subject_entity_id, predicate, object_entity_id, sensitivity
        ) VALUES ($5, $6, gen_random_uuid(), $1, $2, $3, $4)
        """,
        subject,
        predicate,
        obj,
        sensitivity,
        source[0],
        source[1],
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


class TestEntityDossier:
    # REQ-entity-graph-010: projection SQL over the migrated FK schema.
    async def test_groups_live_receipts_by_source_on_either_end(
        self, db_pool: asyncpg.Pool
    ) -> None:
        me, friend, boss = _uuid(), _uuid(), _uuid()
        await _live_edge(db_pool, subject=me, predicate="friend_of", obj=friend)
        await _live_edge(
            db_pool, subject=boss, predicate="manages", obj=me, source=("general", "facts")
        )

        read = await dossier_for_entity(db_pool, me)

        assert read.live_by_source == {("test", "facts"): 1, ("general", "facts"): 1}
        by_predicate = {r.predicate: r for r in read.receipts}
        assert by_predicate["friend_of"].direction == "subject"
        assert by_predicate["friend_of"].counterpart_entity_id == friend
        assert by_predicate["manages"].direction == "object"
        assert by_predicate["manages"].counterpart_entity_id == boss
        assert by_predicate["manages"].source_schema == "general"
        assert read.withheld == 0

    async def test_withheld_stubs_are_a_bare_count_with_no_payload(
        self, db_pool: asyncpg.Pool
    ) -> None:
        me, friend = _uuid(), _uuid()
        await _live_edge(db_pool, subject=me, predicate="friend_of", obj=friend)
        await _withheld_edge(db_pool, subject=me)
        await _withheld_edge(db_pool, subject=me)
        await _withheld_edge(db_pool, subject=friend)  # someone else's stub: not mine

        read = await dossier_for_entity(db_pool, me)

        assert read.withheld == 2
        assert [r.predicate for r in read.receipts] == ["friend_of"]
        assert sum(read.live_by_source.values()) == 1  # stubs never count as known
        for receipt in read.receipts:
            assert not hasattr(receipt, "withheld_reason")

    async def test_an_entity_with_no_edges_is_empty_not_an_error(
        self, db_pool: asyncpg.Pool
    ) -> None:
        read = await dossier_for_entity(db_pool, _uuid())

        assert read.receipts == []
        assert read.live_by_source == {}
        assert read.withheld == 0

    async def test_the_limit_bounds_receipts_but_not_the_true_counts(
        self, db_pool: asyncpg.Pool
    ) -> None:
        me = _uuid()
        for index in range(5):
            await _live_edge(db_pool, subject=me, predicate=f"p{index}", obj=_uuid())

        read = await dossier_for_entity(db_pool, me, limit=2)

        assert len(read.receipts) == 2
        assert read.live_by_source == {("test", "facts"): 5}

    async def test_a_self_edge_is_one_receipt_and_one_known_relationship(
        self, db_pool: asyncpg.Pool
    ) -> None:
        me = _uuid()
        await _live_edge(db_pool, subject=me, predicate="self_ref", obj=me)

        read = await dossier_for_entity(db_pool, me)

        assert len(read.receipts) == 1
        assert read.live_by_source == {("test", "facts"): 1}
