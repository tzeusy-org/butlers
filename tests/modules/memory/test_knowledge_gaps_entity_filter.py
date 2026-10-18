# ruff: noqa: F811
"""list_gaps narrows to one entity against real Postgres (bu-q7vx1q.71).

Two entities with open gaps: the filter returns only the asked-for entity's gaps, and
the newest-first keyset cursor behaves the same inside the filter.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.modules.memory import knowledge_gaps
from tests.modules.memory.test_content_authority import (  # noqa: F401
    _engine,
    _routed,
    _session,
    pool_factory,
)
from tests.modules.memory.test_knowledge_gaps import (  # noqa: F401
    BUTLER,
    _conversation,
    _decline,
    _entity,
    session_hook,
)
from tests.modules.memory.test_memory_migration_integration import (  # noqa: F401
    memory_migrated_db,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


async def _predicates(pool, n: int) -> list[str]:
    rows = await pool.fetch(
        "SELECT name FROM predicate_registry WHERE NOT is_edge AND status = 'active'"
        " ORDER BY name LIMIT $1",
        n,
    )
    return [r["name"] for r in rows]


async def test_entity_filter_returns_only_that_entitys_gaps_with_keyset_paging(
    pool_factory, session_hook
):
    pool, _owner, _ = await pool_factory()
    session_hook(pool)
    mei, wei = await _entity(pool), await _entity(pool)
    p1, p2, p3 = await _predicates(pool, 3)
    for predicate in (p1, p2, p3):
        await _decline(pool, await _conversation(pool), mei, predicate)
    await _decline(pool, await _conversation(pool), wei, p1)

    everyone = await knowledge_gaps.list_gaps(pool)
    only_mei = await knowledge_gaps.list_gaps(pool, entity_id=mei)

    assert {g["entity_id"] for g in everyone} == {mei, wei}
    assert [g["entity_id"] for g in only_mei] == [mei] * 3
    assert [g["id"] for g in only_mei] == [g["id"] for g in everyone if g["entity_id"] == mei]

    first = await knowledge_gaps.list_gaps(pool, entity_id=mei, limit=2)
    rest = await knowledge_gaps.list_gaps(
        pool, entity_id=mei, limit=2, after=(first[-1]["asked_at"], first[-1]["id"])
    )
    assert [g["id"] for g in first + rest] == [g["id"] for g in only_mei]
    assert len(rest) == 1


async def test_entity_filter_for_an_entity_without_gaps_is_empty(pool_factory, session_hook):
    pool, _owner, _ = await pool_factory()
    session_hook(pool)
    mei, other = await _entity(pool), await _entity(pool)
    (predicate,) = await _predicates(pool, 1)
    await _decline(pool, await _conversation(pool), mei, predicate)

    assert await knowledge_gaps.list_gaps(pool, entity_id=other) == []
