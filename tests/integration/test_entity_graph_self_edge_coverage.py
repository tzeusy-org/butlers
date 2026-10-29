"""Real PostgreSQL coverage/dossier agreement (REQ-entity-graph-008).

Reuse the existing dossier's isolated projection-table fixture. This proves
query semantics, not the migrated schema or a runtime-role authorization grant.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from butlers.core.entity_graph_edges import coverage_for_entities, dossier_for_entity
from tests.integration.test_entity_graph_dossier import (
    _live_edge,
    _withheld_edge,
    docker_available,
)
from tests.integration.test_entity_graph_dossier import pool as pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def test_self_edges_count_once_without_collapsing_distinct_edges(
    pool: asyncpg.Pool,
) -> None:
    me, other, isolated = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _live_edge(pool, subject=me, predicate="self_ref", obj=me)

    assert await coverage_for_entities(pool, [me]) == {me: (1, 0)}
    dossier = await dossier_for_entity(pool, me)
    assert sum(dossier.live_by_source.values()) == len(dossier.receipts) == 1

    await _live_edge(pool, subject=me, predicate="knows", obj=other)

    assert await coverage_for_entities(pool, [me, other, me, isolated]) == {
        me: (2, 0),
        other: (1, 0),
    }
    for entity, expected in ((me, 2), (other, 1)):
        dossier = await dossier_for_entity(pool, entity)
        assert sum(dossier.live_by_source.values()) == len(dossier.receipts) == expected

    # A different source row remains a different edge even with the same ends.
    await _live_edge(pool, subject=me, predicate="knows", obj=other)
    await _withheld_edge(pool, subject=me)

    assert await coverage_for_entities(pool, [me, other]) == {me: (3, 1), other: (2, 0)}
    dossier = await dossier_for_entity(pool, me)
    assert sum(dossier.live_by_source.values()) == len(dossier.receipts) == 3
    assert dossier.withheld == 1
