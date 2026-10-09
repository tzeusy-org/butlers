"""Regression (bu-j820n.1): the dashboard entity-merge handler
(``POST /api/relationship/entities/{id}/merge`` → ``merge_entities``) must
re-point EVERY reference to the source entity onto the survivor, not just
``relationship.entity_facts``.

Background: gifts / loans / interactions / contact-notes / life-events all live
in the memory-module ``facts`` table keyed by ``entity_id`` (edge-facts point at
the source via ``object_entity_id``), and linked CRM contacts live in
``relationship.contact_entity_map`` keyed by ``entity_id``. The old handler moved only
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

from butlers.testing.migrated_templates import MigrationStage
from butlers.testing.migration import migrated_pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def pool(postgres_container):
    """Real current CRM/triple chain; legacy contact IDs are compatibility data."""
    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship", schema="relationship"),
        ),
        pool_schema="relationship",
        min_pool_size=2,
        max_pool_size=8,
    ) as p:
        await p.execute("""
                    INSERT INTO relationship.entity_predicate_registry
                        (predicate, kind, object_kind, cardinality, description)
                    VALUES ('has-email', 'contact', 'literal', 'multi', 'Email address.')
                    ON CONFLICT (predicate) DO NOTHING
                """)
        yield p


def _db_with_pool(pool: asyncpg.Pool) -> MagicMock:
    db = MagicMock()
    db.pool = MagicMock(return_value=pool)
    return db


async def _insert_entity(pool: asyncpg.Pool, *, name: str, roles: list[str]) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ($1, 'person', $2)
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
        INSERT INTO facts (subject, entity_id, predicate, content, scope, validity)
        VALUES ('entity:' || $1::text, $1, $2, $3, 'relationship', 'active')
        RETURNING id
        """,
        entity_id,
        predicate,
        content,
    )


class TestMergeRepointsAllSourceRefs:
    async def test_gifts_loans_notes_lifeevents_and_contacts_follow_survivor(self, pool):
        """A source carrying gifts/loans/interactions/notes/life-events in the
        memory ``facts`` store AND a linked ``contact_entity_map`` row merges into a
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
            INSERT INTO facts (subject, entity_id, object_entity_id, predicate, content, scope, validity)
            VALUES ('entity:' || $1::text, $1, $2, 'interacted_with', 'lunch', 'relationship', 'active')
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

        # A legacy contact ID is compatibility data, not a newly registered
        # runtime identity. The current consumer reads only the real bridge.
        source_contact = uuid.uuid4()
        # Populate contact_entity_map (rel_029) — this is what merge_entities now updates.
        await pool.execute(
            "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
            source_contact,
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

        # 3. The linked contact now points at the survivor (via contact_entity_map).
        contact_entity = await pool.fetchval(
            "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", source_contact
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
            "SELECT count(*) FROM contact_entity_map WHERE entity_id = $1", source_id
        )
        orphan_triples = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts "
            "WHERE subject = $1 AND validity = 'active'",
            source_id,
        )
        assert orphan_narrative == 0, "narrative facts stranded on tombstoned source"
        assert orphan_edge == 0, "edge-facts stranded on tombstoned source"
        assert orphan_contacts == 0, "contact_entity_map rows stranded on tombstoned source"
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
