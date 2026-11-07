"""Regression (bu-vvvcu): entity merge must RESOLVE single-cardinality divergence.

Background: ``POST /entities/{id}/merge`` (``merge_entities``) retracts only EXACT
``(predicate, object)`` collisions and moves everything else. For a predicate with
``cardinality = 'single'`` in ``relationship.entity_predicate_registry`` (e.g.
``has-birthday``), two DIFFERENT values have no ``(p, o)`` collision, so the old
code moved BOTH rows — leaving the survivor with two active rows for a
single-cardinality predicate. The merge-review spec calls these divergences "the
conflicts a merge must resolve"; the lifecycle spec rationale states merge keeps
higher-``conf`` facts.

Resolution rule under test (registry-driven, no hardcoded predicate list): on
merge, for a ``cardinality = 'single'`` predicate where source and target hold
DIFFERENT active objects, keep the higher-``conf`` row and supersede the loser
(tie → target wins, consistent with the assert-path supersession semantics).
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import MagicMock

import asyncpg
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def rel_pool(db):
    """Core + memory + relationship + approvals chains (flat; entity_facts in ``relationship``)."""
    return await db("core", "memory", "relationship", "approvals")


def _db_with_pool(rel_pool: asyncpg.Pool) -> MagicMock:
    db = MagicMock()
    db.pool = MagicMock(return_value=rel_pool)
    return db


async def _insert_entity(rel_pool: asyncpg.Pool, *, name: str, roles: list[str]) -> uuid.UUID:
    return await rel_pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ($1, 'person', $2)
        RETURNING id
        """,
        name,
        roles,
    )


async def _add_fact(
    rel_pool: asyncpg.Pool,
    subject: uuid.UUID,
    predicate: str,
    value: str,
    *,
    conf: float = 1.0,
) -> None:
    await rel_pool.execute(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, conf)
        VALUES ($1, $2, $3, 'literal', 'test', $4)
        """,
        subject,
        predicate,
        value,
        conf,
    )


def _merge_handle(rel_pool: asyncpg.Pool):
    from butlers.api.router_discovery import discover_butler_routers

    router_mod = next(
        module for butler_name, module in discover_butler_routers() if butler_name == "relationship"
    )
    return router_mod.merge_entities, router_mod.MergeEntitiesRequest


class TestMergeResolvesSingleCardinalityDivergence:
    async def test_conflicting_birthday_leaves_exactly_one_active_row(self, rel_pool):
        """Two DIFFERENT has-birthday values (cardinality=single) → exactly one
        active row on the survivor (higher conf) + the loser superseded."""
        merge_entities, MergeEntitiesRequest = _merge_handle(rel_pool)

        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(rel_pool, name="Alice (canonical)", roles=[])
        source_id = await _insert_entity(rel_pool, name="Alice (duplicate)", roles=[])

        # Target holds the LOWER-confidence birthday; source holds the HIGHER one.
        # The higher-conf value must win, the target's row must be superseded.
        await _add_fact(rel_pool, target_id, "has-birthday", "1990-01-01", conf=0.6)
        await _add_fact(rel_pool, source_id, "has-birthday", "1991-02-02", conf=0.9)

        db = _db_with_pool(rel_pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=db)

        # Exactly one active has-birthday row on the survivor — the higher-conf one.
        active = await rel_pool.fetch(
            """
            SELECT object, conf FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = 'has-birthday' AND validity = 'active'
            """,
            target_id,
        )
        assert len(active) == 1, f"single-cardinality predicate must leave one active row: {active}"
        assert active[0]["object"] == "1991-02-02"
        assert float(active[0]["conf"]) == pytest.approx(0.9)

        # The losing value (target's original lower-conf row) must be superseded,
        # not stranded as active and not deleted.
        superseded = await rel_pool.fetch(
            """
            SELECT object FROM relationship.entity_facts
            WHERE predicate = 'has-birthday' AND validity = 'superseded'
            """,
        )
        assert {r["object"] for r in superseded} == {"1990-01-01"}

        # No active rows remain stranded on the tombstoned source.
        stranded = await rel_pool.fetch(
            "SELECT id FROM relationship.entity_facts WHERE subject = $1 AND validity = 'active'",
            source_id,
        )
        assert stranded == []

    async def test_tie_keeps_target_value(self, rel_pool):
        """On equal conf, the target's value wins (tie → target)."""
        merge_entities, MergeEntitiesRequest = _merge_handle(rel_pool)

        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(rel_pool, name="Bob (canonical)", roles=[])
        source_id = await _insert_entity(rel_pool, name="Bob (duplicate)", roles=[])

        await _add_fact(rel_pool, target_id, "has-birthday", "2000-03-03", conf=0.8)
        await _add_fact(rel_pool, source_id, "has-birthday", "2001-04-04", conf=0.8)

        db = _db_with_pool(rel_pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=db)

        active = await rel_pool.fetch(
            """
            SELECT object FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = 'has-birthday' AND validity = 'active'
            """,
            target_id,
        )
        assert len(active) == 1
        assert active[0]["object"] == "2000-03-03", "tie must keep the target's value"

    async def test_multi_cardinality_predicate_unions(self, rel_pool):
        """Multi-cardinality predicates (has-email) still union — both survive."""
        merge_entities, MergeEntitiesRequest = _merge_handle(rel_pool)

        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(rel_pool, name="Carol (canonical)", roles=[])
        source_id = await _insert_entity(rel_pool, name="Carol (duplicate)", roles=[])

        await _add_fact(rel_pool, target_id, "has-email", "carol@work.com", conf=0.5)
        await _add_fact(rel_pool, source_id, "has-email", "carol@home.com", conf=0.9)

        db = _db_with_pool(rel_pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=db)

        active = await rel_pool.fetch(
            """
            SELECT object FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = 'has-email' AND validity = 'active'
            ORDER BY object
            """,
            target_id,
        )
        assert {r["object"] for r in active} == {"carol@home.com", "carol@work.com"}


class TestMergeLeavesTemporalSingleValuesForPeriodPolicy:
    async def test_a_predicate_with_an_effective_period_is_repointed_not_collapsed(self, rel_pool):
        """An occurrence with a period is not a rival value until bu-4ss0u defines the policy."""
        merge_entities, MergeEntitiesRequest = _merge_handle(rel_pool)
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(rel_pool, name="Alice (canonical)", roles=[])
        source_id = await _insert_entity(rel_pool, name="Alice (duplicate)", roles=[])
        await _add_fact(rel_pool, target_id, "has-birthday", "1990-01-01", conf=0.9)
        await rel_pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, conf,
                 effective_from, effective_from_precision)
            VALUES ($1, 'has-birthday', '1991-02-02', 'literal', 'test', 0.1,
                    '2020-01-01T00:00:00Z', 'year')
            """,
            source_id,
        )

        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=_db_with_pool(rel_pool))

        rows = await rel_pool.fetch(
            "SELECT object FROM relationship.entity_facts "
            "WHERE subject = $1 AND predicate = 'has-birthday' AND validity = 'active'",
            target_id,
        )
        assert {row["object"] for row in rows} == {"1990-01-01", "1991-02-02"}


class TestMergeKeepsSingleValuedReadersWorking:
    async def test_notify_reads_one_preferred_channel_after_a_merge(self, rel_pool):
        """The notify reader takes ``LIMIT 1`` with no order, so two rows would pick arbitrarily."""
        merge_entities, MergeEntitiesRequest = _merge_handle(rel_pool)
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(rel_pool, name="Alice (canonical)", roles=[])
        source_id = await _insert_entity(rel_pool, name="Alice (duplicate)", roles=[])
        await _add_fact(rel_pool, target_id, "prefers-channel", "email", conf=0.5)
        await _add_fact(rel_pool, source_id, "prefers-channel", "telegram", conf=0.9)

        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=_db_with_pool(rel_pool))

        rows = await rel_pool.fetch(
            "SELECT object FROM relationship.entity_facts "
            "WHERE subject = $1 AND predicate = 'prefers-channel' AND validity = 'active'",
            target_id,
        )
        assert [row["object"] for row in rows] == ["telegram"]
