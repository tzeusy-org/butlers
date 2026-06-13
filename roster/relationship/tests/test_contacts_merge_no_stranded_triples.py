"""Regression: merging contacts via the audited entity-merge path must not strand
channel triples, and must write a ``relationship.merge_reviews`` audit row.

Background (bu-f0i4w): the dashboard contacts-merge surfaces used to POST
``/api/relationship/contacts/{id}/merge``, which called the *memory*
``entity_merge`` helper. That helper re-points ``memory.facts`` but never touches
``relationship.entity_facts`` (different column layout), so the source entity's
``has-email`` / ``has-phone`` / ``has-telegram`` triples were left STRANDED on the
tombstoned source entity — and no ``merge_reviews`` audit row was written.

The fix routes the contacts-merge surfaces through the audited path
``POST /api/relationship/entities/{id}/merge`` (``merge_entities``), which rewires
``relationship.entity_facts`` atomically and writes a ``merge_reviews`` row
regardless of entry path (spec: relationship-merge-review).

This test exercises the audited path end-to-end over a real Postgres pool — the
exact path the contacts surfaces now funnel through — and asserts that every
channel triple is active on the survivor (no stranding) and that an audit row
exists.
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


# ---------------------------------------------------------------------------
# Pool fixture — relationship.entity_facts + predicate_registry + merge_reviews
# ---------------------------------------------------------------------------


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
        # Channel predicates are multi-cardinality: two different emails are two
        # legitimate rows (the three-emails-three-rows rule) and must both survive.
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, cardinality, description)
            VALUES
                ('has-email',    'contact', 'literal', 'multi', 'Email address.'),
                ('has-phone',    'contact', 'literal', 'multi', 'Phone number.'),
                ('has-telegram', 'contact', 'literal', 'multi', 'Telegram handle.')
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
        # memory-module narrative store, read by the compare snapshot's
        # _fetch_narrative_facts_for_compare (LEFT side of the structural diff).
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id     UUID,
                predicate     TEXT        NOT NULL,
                content       TEXT,
                source_butler TEXT,
                confidence       FLOAT    NOT NULL DEFAULT 1.0,
                observed_at      TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
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
        # contacts table used by the contact_merge MCP path. Created unqualified
        # (search_path-resolved) like the `facts` table above so contact_merge's
        # unqualified `SELECT ... FROM contacts` resolves it.
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                name        TEXT,
                entity_id   UUID,
                archived_at TIMESTAMPTZ,
                listed      BOOL        NOT NULL DEFAULT true,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # contact_merge re-points a fixed set of child tables in one transaction;
        # a missing table aborts the whole transaction in Postgres, so create the
        # minimal child tables it touches (single contact_id FK each is enough for
        # the audit-row path under test).
        for _child in (
            "notes",
            "interactions",
            "dates",
            "gifts",
            "loans",
            "group_members",
            "contact_labels",
            "contact_info",
            "addresses",
            "tasks",
            "life_events",
            "stay_in_touch",
        ):
            await p.execute(
                f"CREATE TABLE IF NOT EXISTS {_child} "  # noqa: S608 — fixed literal names
                "(id UUID PRIMARY KEY DEFAULT gen_random_uuid(), contact_id UUID)"
            )
        await p.execute(
            "CREATE TABLE IF NOT EXISTS relationships "
            "(id UUID PRIMARY KEY DEFAULT gen_random_uuid(), contact_a UUID, contact_b UUID)"
        )
        # contact_merge also re-points the legacy contacts-facts table named
        # ``facts``; the narrative ``facts`` table created above has no contact_id,
        # so add it (harmless — compute_merge_evidence reads entity_facts, not facts).
        await p.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS contact_id UUID")
        yield p


def _db_with_pool(pool: asyncpg.Pool) -> MagicMock:
    """A DatabaseManager-shaped stub whose .pool() returns the provisioned pool."""
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


async def _add_channel_fact(
    pool: asyncpg.Pool, subject: uuid.UUID, predicate: str, value: str
) -> None:
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
        VALUES ($1, $2, $3, 'literal', 'test')
        """,
        subject,
        predicate,
        value,
    )


class TestContactsMergeNoStrandedTriples:
    async def test_channel_triples_survive_on_survivor(self, pool):
        """Every channel triple from both entities is active on the survivor; none
        remain on the tombstoned source. A merge_reviews audit row is written."""
        # The relationship router + its request model are loaded from the roster
        # package via router discovery (same path the app uses).
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

        # Target carries its own channel triples.
        await _add_channel_fact(pool, target_id, "has-email", "alice@work.com")
        await _add_channel_fact(pool, target_id, "has-phone", "+15550001")
        # Source (the duplicate that will be merged away) carries DIFFERENT channel
        # triples — these are exactly the ones the old bypass stranded.
        await _add_channel_fact(pool, source_id, "has-email", "alice@home.com")
        await _add_channel_fact(pool, source_id, "has-telegram", "alice_tg")

        db = _db_with_pool(pool)
        # keepAs='A' keeps entityA (target); entityB (source) is tombstoned.
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        resp = await merge_entities(target_id, body, db=db)

        assert resp.kept_entity_id == target_id
        assert resp.tombstoned_entity_id == source_id

        # No active triples may remain stranded on the tombstoned source.
        stranded = await pool.fetch(
            """
            SELECT predicate, object FROM relationship.entity_facts
            WHERE subject = $1 AND validity = 'active'
            """,
            source_id,
        )
        assert stranded == [], f"Triples stranded on tombstoned source: {stranded}"

        # All four channel triples must be active on the survivor.
        survivor = await pool.fetch(
            """
            SELECT predicate, object FROM relationship.entity_facts
            WHERE subject = $1 AND validity = 'active'
            ORDER BY predicate, object
            """,
            target_id,
        )
        survivor_pairs = {(r["predicate"], r["object"]) for r in survivor}
        assert survivor_pairs == {
            ("has-email", "alice@work.com"),
            ("has-email", "alice@home.com"),
            ("has-phone", "+15550001"),
            ("has-telegram", "alice_tg"),
        }, f"Survivor channel triples incomplete: {survivor_pairs}"

        # The audited path wrote a merge_reviews row regardless of entry path.
        review_count = await pool.fetchval(
            """
            SELECT count(*) FROM relationship.merge_reviews
            WHERE entity_a = $1 AND entity_b = $2 AND outcome = 'merged'
            """,
            target_id,
            source_id,
        )
        assert review_count == 1, "merge_entities must write exactly one merged audit row"

    async def test_contact_merge_writes_merge_reviews_audit_row(self, pool):
        """The session-side ``contact_merge`` MCP tool writes a merge_reviews audit
        row regardless of entry path (bu-csvop; relationship-merge-review spec)."""
        from butlers.tools.relationship.contacts import contact_merge

        target_entity = await _insert_entity(pool, name="Bob (canonical)", roles=[])
        source_entity = await _insert_entity(pool, name="Bob (duplicate)", roles=[])

        # A shared channel triple becomes the audit "shared" evidence.
        await _add_channel_fact(pool, target_entity, "has-email", "bob@work.com")
        await _add_channel_fact(pool, source_entity, "has-email", "bob@work.com")

        target_contact = await pool.fetchval(
            "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
            "Bob (canonical)",
            target_entity,
        )
        source_contact = await pool.fetchval(
            "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
            "Bob (duplicate)",
            source_entity,
        )

        await contact_merge(pool, source_id=source_contact, target_id=target_contact)

        # contact_merge wrote a merged audit row for the underlying entities.
        review = await pool.fetchrow(
            """
            SELECT shared_facts, outcome FROM relationship.merge_reviews
            WHERE entity_a = $1 AND entity_b = $2 AND outcome = 'merged'
            """,
            source_entity,
            target_entity,
        )
        assert review is not None, "contact_merge must write a merged merge_reviews row"
        # The shared has-email evidence (one row per entity) is captured pre-merge.
        import json as _json

        shared = _json.loads(review["shared_facts"])
        assert len(shared) == 2, f"expected the shared has-email pair in evidence, got {shared}"
        assert {f["object"] for f in shared} == {"bob@work.com"}
