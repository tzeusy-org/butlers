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
async def pool(provisioned_postgres_pool):
    """Fresh DB with the relationship-merge schema surface used by merge_entities."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=8) as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT        NOT NULL DEFAULT '',
                name           TEXT        NOT NULL DEFAULT '',
                entity_type    TEXT        NOT NULL DEFAULT 'person',
                aliases        TEXT[]      NOT NULL DEFAULT '{}',
                metadata       JSONB       DEFAULT '{}'::jsonb,
                roles          TEXT[]      NOT NULL DEFAULT '{}',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
                predicate   TEXT        NOT NULL PRIMARY KEY,
                kind        TEXT        NOT NULL,
                object_kind TEXT        NOT NULL,
                cardinality TEXT        NOT NULL DEFAULT 'multi',
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # has-birthday is single-cardinality: an entity holds at most one active
        # value. has-email is multi-cardinality (the three-emails-three-rows rule).
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, cardinality, description)
            VALUES
                ('has-birthday', 'attribute', 'literal', 'single', 'Birthday.'),
                ('has-email',    'contact',   'literal', 'multi',  'Email address.')
            ON CONFLICT (predicate) DO NOTHING
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0,
                last_seen   TIMESTAMPTZ,
                observed_at TIMESTAMPTZ,
                metadata    JSONB,
                weight      INT,
                verified    BOOL        NOT NULL DEFAULT false,
                "primary"   BOOL,
                validity    TEXT        NOT NULL DEFAULT 'active'
                                CHECK (validity IN ('active', 'retracted', 'superseded')),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
                ON relationship.entity_facts (subject, predicate, object)
                WHERE validity = 'active'
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id     UUID,
                object_entity_id UUID,
                predicate     TEXT        NOT NULL,
                content       TEXT,
                source_butler TEXT,
                confidence       FLOAT    NOT NULL DEFAULT 1.0,
                observed_at      TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                valid_at         TIMESTAMPTZ,
                supersedes_id    UUID,
                scope         TEXT        NOT NULL DEFAULT 'relationship',
                validity      TEXT        NOT NULL DEFAULT 'active',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.merge_reviews (
                id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_a        UUID        NOT NULL REFERENCES public.entities(id),
                entity_b        UUID        NOT NULL REFERENCES public.entities(id),
                shared_facts    JSONB       NOT NULL DEFAULT '[]'::jsonb,
                divergent_facts JSONB       NOT NULL DEFAULT '[]'::jsonb,
                outcome         TEXT        NOT NULL CHECK (outcome IN ('merged', 'dismissed')),
                reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # merge_entities re-points public.contacts.entity_id onto the survivor
        # (bu-j820n.1), so the table must exist even when no contacts are linked.
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.contacts (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                name        TEXT,
                entity_id   UUID,
                archived_at TIMESTAMPTZ,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield p


def _db_with_pool(pool: asyncpg.Pool) -> MagicMock:
    db = MagicMock()
    db.pool = MagicMock(return_value=pool)
    return db


async def _insert_entity(pool: asyncpg.Pool, *, name: str, roles: list[str]) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, name, entity_type, roles)
        VALUES ($1, $1, 'person', $2)
        RETURNING id
        """,
        name,
        roles,
    )


async def _add_fact(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    predicate: str,
    value: str,
    *,
    conf: float = 1.0,
) -> None:
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, conf)
        VALUES ($1, $2, $3, 'literal', 'test', $4)
        """,
        subject,
        predicate,
        value,
        conf,
    )


def _merge_handle(pool: asyncpg.Pool):
    from butlers.api.router_discovery import discover_butler_routers

    router_mod = next(
        module for butler_name, module in discover_butler_routers() if butler_name == "relationship"
    )
    return router_mod.merge_entities, router_mod.MergeEntitiesRequest


class TestMergeResolvesSingleCardinalityDivergence:
    async def test_conflicting_birthday_leaves_exactly_one_active_row(self, pool):
        """Two DIFFERENT has-birthday values (cardinality=single) → exactly one
        active row on the survivor (higher conf) + the loser superseded."""
        merge_entities, MergeEntitiesRequest = _merge_handle(pool)

        await _insert_entity(pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(pool, name="Alice (canonical)", roles=[])
        source_id = await _insert_entity(pool, name="Alice (duplicate)", roles=[])

        # Target holds the LOWER-confidence birthday; source holds the HIGHER one.
        # The higher-conf value must win, the target's row must be superseded.
        await _add_fact(pool, target_id, "has-birthday", "1990-01-01", conf=0.6)
        await _add_fact(pool, source_id, "has-birthday", "1991-02-02", conf=0.9)

        db = _db_with_pool(pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=db)

        # Exactly one active has-birthday row on the survivor — the higher-conf one.
        active = await pool.fetch(
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
        superseded = await pool.fetch(
            """
            SELECT object FROM relationship.entity_facts
            WHERE predicate = 'has-birthday' AND validity = 'superseded'
            """,
        )
        assert {r["object"] for r in superseded} == {"1990-01-01"}

        # No active rows remain stranded on the tombstoned source.
        stranded = await pool.fetch(
            "SELECT id FROM relationship.entity_facts WHERE subject = $1 AND validity = 'active'",
            source_id,
        )
        assert stranded == []

    async def test_tie_keeps_target_value(self, pool):
        """On equal conf, the target's value wins (tie → target)."""
        merge_entities, MergeEntitiesRequest = _merge_handle(pool)

        await _insert_entity(pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(pool, name="Bob (canonical)", roles=[])
        source_id = await _insert_entity(pool, name="Bob (duplicate)", roles=[])

        await _add_fact(pool, target_id, "has-birthday", "2000-03-03", conf=0.8)
        await _add_fact(pool, source_id, "has-birthday", "2001-04-04", conf=0.8)

        db = _db_with_pool(pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=db)

        active = await pool.fetch(
            """
            SELECT object FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = 'has-birthday' AND validity = 'active'
            """,
            target_id,
        )
        assert len(active) == 1
        assert active[0]["object"] == "2000-03-03", "tie must keep the target's value"

    async def test_multi_cardinality_predicate_unions(self, pool):
        """Multi-cardinality predicates (has-email) still union — both survive."""
        merge_entities, MergeEntitiesRequest = _merge_handle(pool)

        await _insert_entity(pool, name="Owner", roles=["owner"])
        target_id = await _insert_entity(pool, name="Carol (canonical)", roles=[])
        source_id = await _insert_entity(pool, name="Carol (duplicate)", roles=[])

        await _add_fact(pool, target_id, "has-email", "carol@work.com", conf=0.5)
        await _add_fact(pool, source_id, "has-email", "carol@home.com", conf=0.9)

        db = _db_with_pool(pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        await merge_entities(target_id, body, db=db)

        active = await pool.fetch(
            """
            SELECT object FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = 'has-email' AND validity = 'active'
            ORDER BY object
            """,
            target_id,
        )
        assert {r["object"] for r in active} == {"carol@home.com", "carol@work.com"}
