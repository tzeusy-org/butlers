"""Regression (bu-j820n.1): the dashboard entity-merge handler
(``POST /api/relationship/entities/{id}/merge`` → ``merge_entities``) must
re-point EVERY reference to the source entity onto the survivor, not just
``relationship.entity_facts``.

Background: gifts / loans / interactions / contact-notes / life-events all live
in the memory-module ``facts`` table keyed by ``entity_id`` (edge-facts point at
the source via ``object_entity_id``), and linked CRM contacts live in
``public.contacts`` keyed by ``entity_id``. The old handler moved only
``relationship.entity_facts`` and tombstoned the source, so those narrative
``facts`` rows and linked contacts orphaned onto the merged-away (tombstone)
entity and vanished from the surviving entity.

This test drives the handler end-to-end over a real Postgres pool — the exact
path the dashboard funnels through — and asserts every reference now points at
the survivor, the source is tombstoned, and NO row references the source
``entity_id``. It runs inside the handler's single transaction, so it also
guards the atomicity contract.
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
    """Fresh DB with the full reference surface ``merge_entities`` must repoint.

    Mirrors the live column layout closely enough for the handler:
    - ``public.entities`` (survivor/tombstone),
    - ``relationship.entity_facts`` + predicate registry + ``uq_ef_spo_active``,
    - the memory-module ``facts`` store with ``object_entity_id`` / ``valid_at`` /
      ``supersedes_id`` (the columns ``_repoint_facts_on_conn`` reads/writes),
    - ``public.contacts`` (linked CRM records),
    - ``relationship.merge_reviews`` (the in-transaction audit row).
    """
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
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, cardinality, description)
            VALUES ('has-email', 'contact', 'literal', 'multi', 'Email address.')
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
        # Memory-module narrative store. Columns must include object_entity_id /
        # valid_at / supersedes_id because _repoint_facts_on_conn reads/writes them.
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id        UUID,
                object_entity_id UUID,
                predicate        TEXT        NOT NULL,
                content          TEXT,
                source_butler    TEXT,
                confidence        FLOAT      NOT NULL DEFAULT 1.0,
                observed_at       TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                valid_at          TIMESTAMPTZ,
                supersedes_id     UUID,
                scope            TEXT        NOT NULL DEFAULT 'relationship',
                validity         TEXT        NOT NULL DEFAULT 'active',
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
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
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.contacts (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                name        TEXT,
                entity_id   UUID        REFERENCES public.entities(id) ON DELETE SET NULL,
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


async def _add_narrative_fact(
    pool: asyncpg.Pool, entity_id: uuid.UUID, predicate: str, content: str
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO facts (entity_id, predicate, content, scope, validity)
        VALUES ($1, $2, $3, 'relationship', 'active')
        RETURNING id
        """,
        entity_id,
        predicate,
        content,
    )


class TestMergeRepointsAllSourceRefs:
    async def test_gifts_loans_notes_lifeevents_and_contacts_follow_survivor(self, pool):
        """A source carrying gifts/loans/interactions/notes/life-events in the
        memory ``facts`` store AND a linked ``public.contacts`` row merges into a
        target; every reference must move to the survivor, the source must be
        tombstoned, and NO row may reference the source entity_id afterward."""
        from butlers.api.router_discovery import discover_butler_routers

        router_mod = next(
            module
            for butler_name, module in discover_butler_routers()
            if butler_name == "relationship"
        )
        merge_entities = router_mod.merge_entities
        MergeEntitiesRequest = router_mod.MergeEntitiesRequest

        # Owner entity required by the owner-only gate (Amendment 12a).
        await _insert_entity(pool, name="Owner", roles=["owner"])

        target_id = await _insert_entity(pool, name="Alice (canonical)", roles=[])
        source_id = await _insert_entity(pool, name="Alice (duplicate)", roles=[])

        # The duplicate (source) carries one of every timeline predicate that
        # lives in the narrative ``facts`` store, plus a relationship triple.
        narrative_predicates = ("gift", "loan", "contact_note", "life_event")
        for pred in narrative_predicates:
            await _add_narrative_fact(pool, source_id, pred, f"{pred} value")
        # An interaction edge-fact: source appears as object_entity_id.
        other_id = await _insert_entity(pool, name="Bob", roles=[])
        edge_fact_id = await pool.fetchval(
            """
            INSERT INTO facts (entity_id, object_entity_id, predicate, content, scope, validity)
            VALUES ($1, $2, 'interacted_with', 'lunch', 'relationship', 'active')
            RETURNING id
            """,
            other_id,
            source_id,
        )
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
            VALUES ($1, 'has-email', 'alice@home.com', 'literal', 'test')
            """,
            source_id,
        )

        # A linked CRM contact pointing at the source entity.
        source_contact = await pool.fetchval(
            "INSERT INTO public.contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
            "Alice (duplicate)",
            source_id,
        )

        db = _db_with_pool(pool)
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        resp = await merge_entities(target_id, body, db=db)

        assert resp.kept_entity_id == target_id
        assert resp.tombstoned_entity_id == source_id

        # 1. Every narrative fact now points at the survivor.
        survivor_narrative = await pool.fetch(
            "SELECT predicate FROM facts WHERE entity_id = $1 AND validity = 'active'",
            target_id,
        )
        survivor_preds = {r["predicate"] for r in survivor_narrative}
        assert set(narrative_predicates).issubset(survivor_preds), (
            f"narrative facts did not follow survivor: {survivor_preds}"
        )

        # 2. The edge-fact's object_entity_id now points at the survivor.
        edge_target = await pool.fetchval(
            "SELECT object_entity_id FROM facts WHERE id = $1", edge_fact_id
        )
        assert edge_target == target_id, "edge-fact object_entity_id did not follow survivor"

        # 3. The linked contact now points at the survivor.
        contact_entity = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE id = $1", source_contact
        )
        assert contact_entity == target_id, "linked contact did not follow survivor"

        # 4. The relationship triple moved too (existing guarantee, still upheld).
        moved_triple = await pool.fetchval(
            """
            SELECT count(*) FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = 'has-email' AND validity = 'active'
            """,
            target_id,
        )
        assert moved_triple == 1, "relationship triple did not follow survivor"

        # 5. The source is tombstoned.
        src_meta = await pool.fetchval(
            "SELECT metadata FROM public.entities WHERE id = $1", source_id
        )
        import json as _json

        meta = src_meta if isinstance(src_meta, dict) else _json.loads(src_meta)
        assert meta.get("merged_into") == str(target_id), "source not tombstoned"

        # 6. NO row anywhere may still reference the source entity_id.
        orphan_narrative = await pool.fetchval(
            "SELECT count(*) FROM facts WHERE entity_id = $1 AND validity = 'active'",
            source_id,
        )
        orphan_edge = await pool.fetchval(
            "SELECT count(*) FROM facts WHERE object_entity_id = $1 AND validity = 'active'",
            source_id,
        )
        orphan_contacts = await pool.fetchval(
            "SELECT count(*) FROM public.contacts WHERE entity_id = $1", source_id
        )
        orphan_triples = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts "
            "WHERE subject = $1 AND validity = 'active'",
            source_id,
        )
        assert orphan_narrative == 0, "narrative facts stranded on tombstoned source"
        assert orphan_edge == 0, "edge-facts stranded on tombstoned source"
        assert orphan_contacts == 0, "contacts stranded on tombstoned source"
        assert orphan_triples == 0, "relationship triples stranded on tombstoned source"

        # 7. The in-transaction audit row was written.
        review_count = await pool.fetchval(
            """
            SELECT count(*) FROM relationship.merge_reviews
            WHERE entity_a = $1 AND entity_b = $2 AND outcome = 'merged'
            """,
            target_id,
            source_id,
        )
        assert review_count == 1, "merge_entities must write exactly one merged audit row"
