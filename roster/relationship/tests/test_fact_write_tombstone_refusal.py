"""Real-Postgres: the central fact writer refuses merge-tombstoned entities. [bu-gm93xc]

Reuses the merge schema and latch helpers of the stranded-triples suite.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from butlers.tools.relationship.entity_merge import merge_entity_pair
from butlers.tools.relationship.fact_authority import EntityTombstonedError
from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    assert_prefers_channel,
    relationship_assert_fact,
)
from roster.relationship.tests.test_contacts_merge_no_stranded_triples import (  # noqa: F401
    _LATCH_KEY,
    _ROW_LOCK_WAITERS_SQL,
    _active_on,
    _insert_entity,
    _latch_on,
    _poll,
    _wait_on_latch,
)
from roster.relationship.tests.test_contacts_merge_no_stranded_triples import (  # noqa: F401
    pool as merge_schema_pool,
)


@pytest.fixture
async def pool(merge_schema_pool):  # noqa: F811
    return merge_schema_pool


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def _merged_pair(pool, tag):
    survivor = await _insert_entity(pool, name=f"{tag} survivor", roles=[])
    source = await _insert_entity(pool, name=f"{tag} source", roles=[])
    await merge_entity_pair(pool, source_entity_id=source, target_entity_id=survivor)
    return source, survivor


async def _register_knows(pool) -> None:
    await pool.execute(
        "INSERT INTO relationship.entity_predicate_registry "
        "(predicate, kind, object_kind, cardinality, description) "
        "VALUES ('knows', 'relational', 'entity', 'multi', 'Knows.') "
        "ON CONFLICT (predicate) DO NOTHING"
    )


async def test_write_on_a_tombstoned_subject_is_refused_and_writes_nothing(pool):
    source, survivor = await _merged_pair(pool, "subj")
    before = await pool.fetchval("SELECT count(*) FROM relationship.entity_facts")

    with pytest.raises(EntityTombstonedError) as caught:
        await relationship_assert_fact(pool, source, "has-email", "new@x.test", src="test")

    assert caught.value.code == "entity_tombstoned" and caught.value.side == "subject"
    assert caught.value.survivor_id == str(survivor)
    assert await pool.fetchval("SELECT count(*) FROM relationship.entity_facts") == before
    assert await _active_on(pool, source) == 0


async def test_edge_to_a_tombstoned_object_is_refused_and_a_live_one_is_written(pool):
    await _register_knows(pool)
    live = await _insert_entity(pool, name="edge subject", roles=[])
    other = await _insert_entity(pool, name="edge live object", roles=[])
    gone, survivor = await _merged_pair(pool, "obj")

    with pytest.raises(EntityTombstonedError) as caught:
        await relationship_assert_fact(pool, live, "knows", str(gone), "entity", src="test")
    assert caught.value.side == "object" and caught.value.survivor_id == str(survivor)
    assert (
        await pool.fetchval("SELECT count(*) FROM relationship.entity_facts WHERE subject=$1", live)
        == 0
    )

    ok = await relationship_assert_fact(pool, live, "knows", str(other), "entity", src="test")
    assert ok.outcome == AssertOutcome.inserted


async def test_write_queued_behind_a_committing_merge_is_refused_after_the_lock(pool):
    survivor = await _insert_entity(pool, name="race survivor", roles=[])
    source = await _insert_entity(pool, name="race source", roles=[])

    async with pool.acquire() as latch:
        await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
        merge = asyncio.create_task(
            merge_entity_pair(
                pool,
                source_entity_id=source,
                target_entity_id=survivor,
                locked_guard=lambda conn, _pair: _latch_on(conn),
            )
        )
        await _wait_on_latch(pool)
        write = asyncio.create_task(
            relationship_assert_fact(pool, source, "has-email", "race@x.test", src="test")
        )
        await _poll(
            pool,
            _ROW_LOCK_WAITERS_SQL,
            "public.entities",
            "%FOR KEY SHARE%",
            what="the write to queue on public.entities",
        )
        await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
        merged, written = await asyncio.gather(merge, write, return_exceptions=True)

    assert not isinstance(merged, BaseException), merged
    assert isinstance(written, EntityTombstonedError), written
    assert written.survivor_id == str(survivor)
    assert await _active_on(pool, source) == 0
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object = 'race@x.test'"
        )
        == 0
    )


async def test_prefers_channel_on_a_tombstoned_subject_is_refused_as_tombstoned(pool):
    source = await _insert_entity(pool, name="pref source", roles=[])
    survivor = await _insert_entity(pool, name="pref survivor", roles=[])
    await relationship_assert_fact(pool, source, "has-email", "pref@x.test", src="test")
    await merge_entity_pair(pool, source_entity_id=source, target_entity_id=survivor)

    with pytest.raises(EntityTombstonedError) as caught:
        await assert_prefers_channel(pool, source, "email")

    # The refusal, not the reachability error: the merge moved the contact fact away.
    assert caught.value.code == "entity_tombstoned" and caught.value.side == "subject"
    assert caught.value.survivor_id == str(survivor)
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts "
            "WHERE predicate = 'prefers-channel' AND subject = ANY($1::uuid[])",
            [source, survivor],
        )
        == 0
    )
