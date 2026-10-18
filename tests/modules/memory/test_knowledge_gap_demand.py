# ruff: noqa: F811
"""Per-predicate demand over open knowledge gaps, against real Postgres (bu-q7vx1q.73).

The aggregate behind ``GET /api/memory/gaps/demand``: how many open gaps and how many
asking threads each predicate has. Shared provisioning and gap helpers come from the
focused gap tests so each is defined in one place.
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
    _gap,
    _predicate,
    _store,
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


async def _second_predicate(pool, first: str) -> str:
    return await pool.fetchval(
        "SELECT name FROM predicate_registry WHERE NOT is_edge AND status = 'active'"
        " AND name <> $1 ORDER BY name LIMIT 1",
        first,
    )


async def test_demand_counts_open_gaps_and_asks_and_excludes_answerable_ones(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    predicate = await _predicate(pool)
    other = await _second_predicate(pool, predicate)
    asked_twice, asked_once, answered = (await _entity(pool) for _ in range(3))

    # Two open gaps on one predicate; three asking threads in total.
    await _decline(pool, await _conversation(pool), asked_twice, predicate)
    await _decline(pool, await _conversation(pool), asked_twice, predicate)
    await _decline(pool, await _conversation(pool), asked_once, predicate)
    # An answerable gap on the same predicate must not count.
    await _decline(pool, await _conversation(pool), answered, predicate)
    await _store(pool, answered, predicate, sender=owner)
    assert (await _gap(pool, answered, predicate))["status"] == "answerable"
    # A second predicate with one gap and one thread sorts after it.
    await _decline(pool, await _conversation(pool), asked_once, other)

    demand = await knowledge_gaps.demand_by_predicate(pool)

    assert demand == [
        {"predicate": predicate, "open_gaps": 2, "asks": 3},
        {"predicate": other, "open_gaps": 1, "asks": 1},
    ]


async def test_demand_is_empty_when_nothing_is_open(pool_factory, session_hook):
    pool, _owner, _ = await pool_factory()
    session_hook(pool)

    assert await knowledge_gaps.demand_by_predicate(pool) == []
