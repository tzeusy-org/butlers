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

``TestEffectiveTimeMutatorFences`` reuses this schema (the widest real
Relationship mutation surface among the fixtures) for the effective-time
mutator inventory (relationship-fact-effective-time): merges, value-selector
lifecycle routes, entity forget and companion hard-delete cascades either keep
every occurrence's packet or refuse before their first write.
"""

from __future__ import annotations

import importlib
import shutil
import uuid
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.testing.schema_standins import (
    CONTACT_ENTITY_MAP,
    ENTITY_GRAPH_EDGES,
    ENTITY_PREDICATE_REGISTRY,
    ENTITY_REBIND_LOG,
)
from butlers.tools.relationship.fact_temporal import (
    MUTATOR_UNSUPPORTED,
    PACKET_COLUMNS,
    TemporalError,
)
from roster.relationship.tests.evidence_schema import (
    apply_evidence_schema,
    simulate_temporal_cutover,
)

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
        await p.execute(ENTITY_REBIND_LOG.ddl(schema="public"))
        await p.execute(ENTITY_PREDICATE_REGISTRY.ddl(schema="relationship"))
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
                content_authority TEXT, authority_entity_id UUID,
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
        # contact_entity_map (rel_029) — merge_entities now updates this instead of
        # public.contacts.entity_id directly (bu-j77a5).
        await p.execute(CONTACT_ENTITY_MAP.ddl())
        # bu-8478w: merge_entity_pair repoints entity_graph_edges rows for
        # rewired entity_facts on the same connection.
        await p.execute(ENTITY_GRAPH_EDGES.ddl())
        # rel_034: the central writer persists evidence and a coverage receipt in
        # the same transaction as the fact, so this schema is not optional.
        await apply_evidence_schema(p)
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
        # Cutover (bu-irphu): contact_merge resolves contacts via contact_entity_map
        # (not public.contacts), so bridge both contacts to their entities.
        await pool.execute(
            "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2), ($3, $4)",
            target_contact,
            target_entity,
            source_contact,
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


# ---------------------------------------------------------------------------
# Effective-time merge fences (relationship-fact-effective-time, bu-h3b7t.1)
# ---------------------------------------------------------------------------


async def _merge_state(pool: asyncpg.Pool) -> tuple:
    """Everything a merge could write, for "nothing changed" assertions."""
    return (
        await pool.fetch(
            f"SELECT id, subject, object, validity, {PACKET_COLUMNS} "
            "FROM relationship.entity_facts ORDER BY id"
        ),
        await pool.fetch("SELECT id, metadata, aliases FROM public.entities ORDER BY id"),
        await pool.fetch("SELECT * FROM contact_entity_map ORDER BY contact_id"),
        await pool.fetchval("SELECT count(*) FROM relationship.merge_reviews"),
        await pool.fetchval("SELECT count(*) FROM public.entity_rebind_log"),
    )


async def _add_temporal_fact(
    pool: asyncpg.Pool, subject: uuid.UUID, value: str, period: uuid.UUID | None
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, effective_period_id,
             effective_from, effective_from_precision)
        VALUES ($1, 'has-email', $2, 'literal', 'test', $3, '2021-01-01Z', 'year')
        RETURNING id
        """,
        subject,
        value,
        period,
    )


class TestEffectiveTimeMutatorFences:
    async def test_entity_merge_moves_occurrences_intact_or_writes_nothing(self, pool):
        from butlers.tools.relationship.entity_merge import (
            TemporalOccurrenceCollisionError,
            merge_entity_pair,
        )

        await simulate_temporal_cutover(pool)
        target_id = await _insert_entity(pool, name="Target", roles=[])
        source_id = await _insert_entity(pool, name="Source", roles=[])
        # Same triple, different occurrences: the default unknown row on the
        # target and a repeated period on the source. No collision, no collapse.
        await _add_channel_fact(pool, target_id, "has-email", "shared@example.test")
        moved_id = await _add_temporal_fact(pool, source_id, "shared@example.test", uuid.uuid4())
        await pool.execute(
            "INSERT INTO relationship.fact_evidence (fact_id, seq, kind, ref, note, src, origin) "
            "VALUES ($1, 1, 'url', 'https://example.test/e', '', 'test', 'direct')",
            moved_id,
        )
        before = await pool.fetchrow(
            f"SELECT {PACKET_COLUMNS} FROM relationship.entity_facts WHERE id = $1", moved_id
        )

        await merge_entity_pair(pool, source_entity_id=source_id, target_entity_id=target_id)

        moved = await pool.fetchrow(
            f"SELECT subject, validity, {PACKET_COLUMNS} FROM relationship.entity_facts "
            "WHERE id = $1",
            moved_id,
        )
        assert (moved["subject"], moved["validity"]) == (target_id, "active")
        assert {k: moved[k] for k in before.keys()} == dict(before)
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id = $1", moved_id
            )
            == 1
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts "
                "WHERE subject = $1 AND object = 'shared@example.test' AND validity = 'active'",
                target_id,
            )
            == 2
        )

        # A temporal row colliding on its final occurrence key refuses the whole merge.
        second_target = await _insert_entity(pool, name="Target 2", roles=[])
        second_source = await _insert_entity(pool, name="Source 2", roles=[])
        await _add_channel_fact(pool, second_target, "has-email", "clash@example.test")
        await _add_temporal_fact(pool, second_source, "clash@example.test", None)
        state = await _merge_state(pool)
        with pytest.raises(TemporalOccurrenceCollisionError):
            await merge_entity_pair(
                pool, source_entity_id=second_source, target_entity_id=second_target
            )
        assert await _merge_state(pool) == state

    async def test_legacy_contact_merge_is_fenced_before_its_first_write(self, pool):
        from butlers.tools.relationship.contacts import contact_merge

        await simulate_temporal_cutover(pool)
        target_entity = await _insert_entity(pool, name="Carol (canonical)", roles=[])
        source_entity = await _insert_entity(pool, name="Carol (duplicate)", roles=[])
        await _add_temporal_fact(pool, source_entity, "carol@example.test", uuid.uuid4())
        contacts = []
        for name, entity in (("Carol", target_entity), ("Carol dup", source_entity)):
            contact = await pool.fetchval(
                "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id", name, entity
            )
            await pool.execute(
                "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
                contact,
                entity,
            )
            await pool.execute("INSERT INTO notes (contact_id) VALUES ($1)", contact)
            contacts.append(contact)
        state = await _merge_state(pool)
        notes = await pool.fetch("SELECT id, contact_id FROM notes ORDER BY id")

        with pytest.raises(TemporalError) as caught:
            await contact_merge(pool, source_id=contacts[1], target_id=contacts[0])

        assert caught.value.code == MUTATOR_UNSUPPORTED
        assert await _merge_state(pool) == state
        assert await pool.fetch("SELECT id, contact_id FROM notes ORDER BY id") == notes

    @pytest.mark.parametrize(
        ("occurrences", "operation", "refusal"),
        [
            ("repeated", "retract-helper", "temporal_occurrence_ambiguous"),
            ("repeated", "delete", "temporal_occurrence_ambiguous"),
            ("repeated", "verify", "temporal_occurrence_ambiguous"),
            ("repeated", "edit-same-value", "temporal_occurrence_ambiguous"),
            ("temporal", "edit-value", "temporal_mutator_unsupported"),
            ("temporal", "verify", None),
        ],
    )
    async def test_value_selectors_act_on_exactly_one_occurrence(
        self, pool, occurrences, operation, refusal
    ):
        from fastapi import HTTPException, Response

        from butlers.api.router_discovery import discover_butler_routers
        from butlers.tools.relationship.relationship_assert_fact import (
            retract_contact_info_fact,
        )

        router = next(m for name, m in discover_butler_routers() if name == "relationship")
        await simulate_temporal_cutover(pool)
        await _insert_entity(pool, name="Owner", roles=["owner"])
        subject = await _insert_entity(pool, name="Dana", roles=[])
        value = "dana@example.test"
        fact_id = await _add_temporal_fact(pool, subject, value, uuid.uuid4())
        if occurrences == "repeated":
            await _add_channel_fact(pool, subject, "has-email", value)
        value_hash = router._contact_value_hash(value)
        db = _db_with_pool(pool)
        state = await _merge_state(pool)

        async def run():
            if operation == "retract-helper":
                return await retract_contact_info_fact(pool, subject, "email", value)
            if operation == "delete":
                return await router.delete_entity_contact(subject, "has-email", value_hash, db=db)
            if operation == "verify":
                return await router.verify_entity_contact(subject, "has-email", value_hash, db=db)
            new_value = value if operation == "edit-same-value" else "new@example.test"
            return await router.update_entity_contact(
                subject,
                "has-email",
                value_hash,
                router.UpdateContactRequest(new_value=new_value),
                Response(),
                db=db,
            )

        if refusal is None:
            await run()
            verified = await pool.fetchrow(
                f"SELECT verified, validity, {PACKET_COLUMNS} FROM relationship.entity_facts "
                "WHERE id = $1",
                fact_id,
            )
            before = next(row for row in state[0] if row["id"] == fact_id)
            assert (verified["verified"], verified["validity"]) == (True, "active")
            assert all(verified[k] == before[k] for k in verified.keys() if k.startswith("eff"))
            return

        with pytest.raises((TemporalError, HTTPException)) as caught:
            await run()
        code = getattr(caught.value, "code", None) or caught.value.detail["code"]
        assert code == refusal
        if isinstance(caught.value, HTTPException):
            assert caught.value.status_code == 409
        assert await _merge_state(pool) == state

    @pytest.mark.parametrize("path", ["forget", "google", "steam"])
    async def test_all_occurrence_lifecycle_paths_keep_history_and_projections_atomic(
        self, pool, path
    ):
        from butlers.api.router_discovery import discover_butler_routers

        await simulate_temporal_cutover(pool)
        await pool.execute("""
            ALTER TABLE public.entity_graph_edges
                ADD FOREIGN KEY (subject_entity_id) REFERENCES public.entities(id)
                    ON DELETE CASCADE,
                ADD FOREIGN KEY (object_entity_id) REFERENCES public.entities(id)
                    ON DELETE CASCADE
        """)
        await _insert_entity(pool, name="Owner", roles=["owner"])
        companion = await _insert_entity(pool, name="Companion", roles=[])
        other = await _insert_entity(pool, name="Other", roles=[])
        superseded = await _add_temporal_fact(pool, companion, "c@example.test", None)
        await pool.execute(
            "UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = $1",
            superseded,
        )
        await _add_channel_fact(pool, companion, "has-email", "c@example.test")
        await _add_temporal_fact(pool, companion, "c@example.test", uuid.uuid4())
        for subject, obj in ((companion, other), (other, companion)):
            edge_fact = await pool.fetchval(
                """
                INSERT INTO relationship.entity_facts
                    (subject, predicate, object, object_kind, src, effective_period_id)
                VALUES ($1, 'knows', $2, 'entity', 'test', $3)
                RETURNING id
                """,
                subject,
                str(obj),
                uuid.uuid4(),
            )
            await pool.execute(
                """
                INSERT INTO public.entity_graph_edges
                    (source_schema, source_table, source_id, subject_entity_id,
                     predicate, object_entity_id)
                VALUES ('relationship', 'entity_facts', $1, $2, 'knows', $3)
                """,
                edge_fact,
                subject,
                obj,
            )
        await pool.execute(
            "INSERT INTO relationship.fact_evidence (fact_id, seq, kind, ref, note, src, origin) "
            "SELECT id, 1, 'url', 'https://example.test/' || id, '', 'test', 'direct' "
            "FROM relationship.entity_facts"
        )
        involved = f"""
            SELECT id, validity, {PACKET_COLUMNS} FROM relationship.entity_facts
            WHERE subject = $1 OR (object_kind = 'entity' AND object = $1::text)
            ORDER BY id
        """
        before = await pool.fetch(involved, companion)
        evidence_before = await pool.fetchval("SELECT count(*) FROM relationship.fact_evidence")

        if path == "forget":
            router = next(m for name, m in discover_butler_routers() if name == "relationship")
            await router.forget_entity(companion, db=_db_with_pool(pool))
            after = await pool.fetch(involved, companion)
            # Every occurrence is retracted as-is; history stays superseded.
            assert [r["id"] for r in after] == [r["id"] for r in before]
            for old, new in zip(before, after, strict=True):
                expected = "superseded" if old["validity"] == "superseded" else "retracted"
                assert new["validity"] == expected
                assert {k: new[k] for k in new.keys() if k != "validity"} == {
                    k: old[k] for k in old.keys() if k != "validity"
                }
            assert (
                await pool.fetchval("SELECT count(*) FROM relationship.fact_evidence")
                == evidence_before
            )
        else:
            registry = importlib.import_module(f"butlers.{path}_account_registry")
            table = f"public.{path}_accounts"
            await pool.execute(f"""
                CREATE TABLE {table} (
                    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entity_id    UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                    is_primary   BOOLEAN NOT NULL DEFAULT false,
                    status       TEXT NOT NULL DEFAULT 'active',
                    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    revoked_at   TIMESTAMPTZ
                )
            """)
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS public.entity_info (
                    entity_id UUID, type TEXT, value TEXT
                )
            """)
            account = await pool.fetchval(
                f"INSERT INTO {table} (entity_id) VALUES ($1) RETURNING id",  # noqa: S608
                companion,
            )
            await registry.disconnect_account(pool, account, hard_delete=True)
            # Every version the companion subjects is gone, with its evidence.
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM relationship.entity_facts WHERE subject = $1", companion
                )
                == 0
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM relationship.fact_evidence e "
                    "WHERE NOT EXISTS (SELECT 1 FROM relationship.entity_facts f WHERE f.id = e.fact_id)"
                )
                == 0
            )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.entity_graph_edges "
                "WHERE subject_entity_id = $1 OR object_entity_id = $1",
                companion,
            )
            == 0
        )

    # -- Post-cutover race windows (bu-p2bjsf) --------------------------------

    @staticmethod
    async def _linked_contacts(
        pool: asyncpg.Pool, target_entity: uuid.UUID, source_entity: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """(target contact, source contact), each bridged and carrying one note."""
        contacts = []
        for name, entity in (("Target", target_entity), ("Source", source_entity)):
            contact = await pool.fetchval(
                "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id", name, entity
            )
            await pool.execute(
                "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
                contact,
                entity,
            )
            await pool.execute("INSERT INTO notes (contact_id) VALUES ($1)", contact)
            contacts.append(contact)
        return contacts[0], contacts[1]

    @pytest.mark.parametrize("side", ["subject", "object"])
    async def test_contact_merge_refuses_a_row_turned_temporal_after_its_preflight(
        self, pool, monkeypatch, side
    ):
        """A row that gains effective time between the unlocked preflight and the
        locked re-check refuses the whole merge: no contact, entity or fact write,
        and the memory entity merge never runs."""
        from butlers.modules.memory.tools import entities as memory_entities
        from butlers.tools.relationship import contacts as contacts_mod

        await simulate_temporal_cutover(pool)
        target_entity = await _insert_entity(pool, name="Erin", roles=[])
        source_entity = await _insert_entity(pool, name="Erin dup", roles=[])
        await pool.execute(
            'UPDATE public.entities SET metadata = \'{"profile": {"city": "Oslo"}}\' WHERE id = $1',
            source_entity,
        )
        if side == "subject":
            await _add_channel_fact(pool, source_entity, "has-email", "erin@example.test")
            raced = await pool.fetchval(
                "SELECT id FROM relationship.entity_facts WHERE subject = $1", source_entity
            )
        else:
            other = await _insert_entity(pool, name="Frank", roles=[])
            raced = await pool.fetchval(
                """
                INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
                VALUES ($1, 'knows', $2, 'entity', 'test')
                RETURNING id
                """,
                other,
                str(source_entity),
            )
        target_contact, source_contact = await self._linked_contacts(
            pool, target_entity, source_entity
        )

        real_fence = contacts_mod._fence_legacy_fact_repoint
        calls = []
        injected = {}

        async def racing_fence(conn, source_id, target_id):
            calls.append(conn)
            await real_fence(conn, source_id, target_id)
            if len(calls) == 1:
                # Another session commits an effective-time packet onto an
                # affected row after the unlocked preflight passed.
                await pool.execute(
                    "UPDATE relationship.entity_facts SET effective_period_id = $2, "
                    "effective_from = '2021-01-01Z', effective_from_precision = 'year' "
                    "WHERE id = $1",
                    raced,
                    uuid.uuid4(),
                )
                injected["state"] = await _merge_state(pool)
                injected["notes"] = await pool.fetch("SELECT id, contact_id FROM notes ORDER BY id")

        memory_merges = []

        async def spy_entity_merge(*args, **kwargs):
            memory_merges.append(args)

        monkeypatch.setattr(contacts_mod, "_fence_legacy_fact_repoint", racing_fence)
        monkeypatch.setattr(memory_entities, "entity_merge", spy_entity_merge)

        with pytest.raises(TemporalError) as caught:
            await contacts_mod.contact_merge(
                pool, source_id=source_contact, target_id=target_contact
            )

        assert caught.value.code == MUTATOR_UNSUPPORTED
        assert len(calls) == 2  # the preflight, then the re-check under locks
        assert await _merge_state(pool) == injected["state"]
        assert await pool.fetch("SELECT id, contact_id FROM notes ORDER BY id") == injected["notes"]
        assert memory_merges == []

    async def test_contact_merge_locks_affected_rows_against_a_concurrent_correction(
        self, pool, monkeypatch
    ):
        """A correction started after the merge takes its locks waits for the
        commit, then applies once. A correction naming a row the merge moved fails
        visibly instead of silently landing on the merged-away subject."""
        import asyncio

        from butlers.tools.relationship import contacts as contacts_mod
        from butlers.tools.relationship.relationship_assert_fact import (
            AssertOutcome,
            relationship_assert_fact,
        )

        await simulate_temporal_cutover(pool)
        target_entity = await _insert_entity(pool, name="Gale", roles=[])
        source_entity = await _insert_entity(pool, name="Gale dup", roles=[])
        await _add_channel_fact(pool, target_entity, "has-email", "gale@work.test")
        await _add_channel_fact(pool, source_entity, "has-email", "gale@home.test")
        kept_id, moved_id = [
            await pool.fetchval(
                "SELECT id FROM relationship.entity_facts WHERE subject = $1", entity
            )
            for entity in (target_entity, source_entity)
        ]
        target_contact, source_contact = await self._linked_contacts(
            pool, target_entity, source_entity
        )

        def correct(subject, value, fact_id):
            return relationship_assert_fact(
                pool,
                subject,
                "has-email",
                value,
                src="test",
                corrects_fact_id=fact_id,
                effective_from="2019",
                effective_from_precision="year",
            )

        real_fence = contacts_mod._fence_legacy_fact_repoint
        corrections = []

        async def fence_then_race(conn, source_id, target_id):
            await real_fence(conn, source_id, target_id)
            if isinstance(conn, asyncpg.Connection) and not corrections:
                # The merge now holds its row locks: start both corrections and
                # wait until they are blocked on them.
                corrections.append(
                    asyncio.create_task(correct(target_entity, "gale@work.test", kept_id))
                )
                corrections.append(
                    asyncio.create_task(correct(source_entity, "gale@home.test", moved_id))
                )
                for _ in range(500):
                    waiting = await pool.fetchval(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                    if waiting >= 2:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("corrections did not block on the merge locks")
                assert not any(task.done() for task in corrections)

        monkeypatch.setattr(contacts_mod, "_fence_legacy_fact_repoint", fence_then_race)
        # The post-commit merge_entity_pair runs for real: the writer takes its
        # entity rows before any fact row (bu-ab0zys), the same order the merge
        # uses, so the correction can no longer deadlock against it.

        await contacts_mod.contact_merge(pool, source_id=source_contact, target_id=target_contact)
        kept_result, moved_result = await asyncio.gather(*corrections, return_exceptions=True)

        # The locked, unmoved target row: corrected exactly once after the commit.
        assert kept_result.outcome == AssertOutcome.superseded
        active = await pool.fetch(
            "SELECT id, effective_from_precision FROM relationship.entity_facts "
            "WHERE subject = $1 AND object = 'gale@work.test' AND validity = 'active'",
            target_entity,
        )
        assert [(r["id"], r["effective_from_precision"]) for r in active] == [
            (kept_result.fact_id, "year")
        ]
        # The moved row: repointed with its packet, and the stale correction
        # naming its old subject is refused rather than lost or duplicated.
        assert isinstance(moved_result, TemporalError)
        moved = await pool.fetchrow(
            "SELECT subject, validity, effective_from FROM relationship.entity_facts WHERE id = $1",
            moved_id,
        )
        assert (moved["subject"], moved["validity"], moved["effective_from"]) == (
            target_entity,
            "active",
            None,
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts "
                "WHERE object = 'gale@home.test' AND validity = 'active'"
            )
            == 1
        )
        # The unstubbed post-commit entity merge ran and tombstoned the source.
        assert await pool.fetchval(
            "SELECT metadata->>'merged_into' FROM public.entities WHERE id = $1", source_entity
        ) == str(target_entity)

    async def test_contact_merge_skips_a_missing_optional_child_table(self, pool):
        """A missing optional child table no longer aborts the merge transaction."""
        from butlers.tools.relationship.contacts import contact_merge

        await pool.execute("DROP TABLE stay_in_touch")
        target_entity = await _insert_entity(pool, name="Hana", roles=[])
        source_entity = await _insert_entity(pool, name="Hana dup", roles=[])
        await _add_channel_fact(pool, source_entity, "has-email", "hana@example.test")
        target_contact, source_contact = await self._linked_contacts(
            pool, target_entity, source_entity
        )

        await contact_merge(pool, source_id=source_contact, target_id=target_contact)

        assert (
            await pool.fetchval("SELECT count(*) FROM notes WHERE contact_id = $1", target_contact)
            == 2
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", source_contact
            )
            == 0
        )
        assert (
            await pool.fetchval(
                "SELECT subject FROM relationship.entity_facts WHERE object = 'hana@example.test' "
                "AND validity = 'active'"
            )
            == target_entity
        )

    @pytest.mark.parametrize(
        ("race", "code"),
        [
            ("temporal", "temporal_mutator_unsupported"),
            ("retracted", "contact_fact_changed"),
            ("revalued", "contact_fact_changed"),
        ],
    )
    async def test_contact_value_edit_rechecks_its_row_under_lock(
        self, pool, monkeypatch, race, code
    ):
        """A row changed between hash selection and the edit transaction is refused
        with 409 before its retraction, and nothing is written."""
        from fastapi import HTTPException, Response

        from butlers.api.router_discovery import discover_butler_routers

        router = next(m for name, m in discover_butler_routers() if name == "relationship")
        await simulate_temporal_cutover(pool)
        await _insert_entity(pool, name="Owner", roles=["owner"])
        subject = await _insert_entity(pool, name="Ivy", roles=[])
        value = "ivy@example.test"
        await _add_channel_fact(pool, subject, "has-email", value)
        fact_id = await pool.fetchval(
            "SELECT id FROM relationship.entity_facts WHERE subject = $1", subject
        )
        mutation = {
            "temporal": (
                "UPDATE relationship.entity_facts SET effective_period_id = gen_random_uuid(), "
                "effective_from = '2021-01-01Z', effective_from_precision = 'year' WHERE id = $1"
            ),
            "retracted": "UPDATE relationship.entity_facts SET validity = 'retracted' WHERE id = $1",
            "revalued": "UPDATE relationship.entity_facts SET object = 'ivy@new.test' WHERE id = $1",
        }[race]
        real_resolve = router._resolve_contact_fact_by_hash
        injected = {}

        async def resolve_then_race(*args):
            row = await real_resolve(*args)
            await pool.execute(mutation, fact_id)
            injected["state"] = await _merge_state(pool)
            return row

        monkeypatch.setattr(router, "_resolve_contact_fact_by_hash", resolve_then_race)

        with pytest.raises(HTTPException) as caught:
            await router.update_entity_contact(
                subject,
                "has-email",
                router._contact_value_hash(value),
                router.UpdateContactRequest(new_value="ivy@edited.test"),
                Response(),
                db=_db_with_pool(pool),
            )

        assert (caught.value.status_code, caught.value.detail["code"]) == (409, code)
        assert await _merge_state(pool) == injected["state"]
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts WHERE object = 'ivy@edited.test'"
            )
            == 0
        )


# ---------------------------------------------------------------------------
# Lock order: entity rows before fact rows (bu-ab0zys)
# ---------------------------------------------------------------------------

#: Advisory-lock key the latch tests hold to pause one side mid-transaction.
_LATCH_KEY = 0x0AB0_2E75

#: A backend blocked on a row lock of *relation* whose query matches *pattern*.
#: A row-lock waiter holds the tuple lock on the row while it waits for the
#: holder's transaction, so the tuple lock names the table it is queued on.
_ROW_LOCK_WAITERS_SQL = """
    SELECT a.pid
    FROM pg_stat_activity a
    JOIN pg_locks l ON l.pid = a.pid
    WHERE a.datname = current_database()
      AND a.wait_event_type = 'Lock'
      AND a.query ILIKE $2
      AND l.locktype = 'tuple'
      AND l.relation = $1::regclass
"""


async def _poll(pool: asyncpg.Pool, sql: str, *args, what: str):
    import asyncio

    for _ in range(1000):
        value = await pool.fetchval(sql, *args)
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def _wait_on_latch(pool: asyncpg.Pool) -> None:
    await _poll(
        pool,
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted",
        what="a transaction to block on the latch",
    )


async def _latch_on(conn: asyncpg.Connection) -> None:
    """Block *conn*'s transaction until the test releases the latch."""
    await conn.execute("SELECT pg_advisory_xact_lock($1)", _LATCH_KEY)


def _correct(pool: asyncpg.Pool, subject: uuid.UUID, value: str, fact_id: uuid.UUID):
    from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

    return relationship_assert_fact(
        pool,
        subject,
        "has-email",
        value,
        src="test",
        corrects_fact_id=fact_id,
        effective_from="2019",
        effective_from_precision="year",
    )


async def _merge_pair_with_fact(pool: asyncpg.Pool, tag: str) -> tuple:
    target = await _insert_entity(pool, name=f"{tag} target", roles=[])
    source = await _insert_entity(pool, name=f"{tag} source", roles=[])
    value = f"{tag}@source.test"
    await _add_channel_fact(pool, source, "has-email", value)
    fact_id = await pool.fetchval(
        "SELECT id FROM relationship.entity_facts WHERE subject = $1", source
    )
    return target, source, value, fact_id


async def _active_on(pool: asyncpg.Pool, subject: uuid.UUID) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM relationship.entity_facts WHERE subject = $1 AND validity = 'active'",
        subject,
    )


class TestFactEntityLockOrder:
    """The central writer locks entity rows before fact rows, like both merges.

    Before bu-ab0zys a correction locked its fact row and then, through the
    replacement insert's FK, the subject entity; merge_entity_pair locks the
    entities and then the facts. Run together they formed a cycle Postgres broke
    with ``DeadlockDetectedError``.
    """

    async def test_correction_waits_on_the_entity_while_a_merge_holds_it(self, pool):
        """AC1a: a merge paused after its entity lock makes a correction of a
        source fact queue on ``public.entities`` without touching the fact row;
        on release the merge completes and the stale correction is refused."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair

        await simulate_temporal_cutover(pool)
        target, source, value, fact_id = await _merge_pair_with_fact(pool, "ac1a")

        async with pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            merge = asyncio.create_task(
                merge_entity_pair(
                    pool,
                    source_entity_id=source,
                    target_entity_id=target,
                    locked_guard=lambda conn, _pair: _latch_on(conn),
                )
            )
            await _wait_on_latch(pool)
            correction = asyncio.create_task(_correct(pool, source, value, fact_id))
            pid = await _poll(
                pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%FOR KEY SHARE%",
                what="the correction to queue on public.entities",
            )
            fact_locks = await pool.fetchval(
                "SELECT count(*) FROM pg_locks WHERE pid = $1 AND relation = "
                "'relationship.entity_facts'::regclass "
                "AND (locktype = 'tuple' OR mode IN ('RowShareLock', 'RowExclusiveLock'))",
                pid,
            )
            assert fact_locks == 0, "the correction locked a fact row before the entity"
            await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
            merged, corrected = await asyncio.gather(merge, correction, return_exceptions=True)

        assert not isinstance(merged, BaseException), merged
        # The merge repointed the row in place, so a correction naming the old
        # subject is a typed refusal with no write.
        assert isinstance(corrected, TemporalError), corrected
        row = await pool.fetchrow(
            "SELECT subject, validity, effective_from FROM relationship.entity_facts WHERE id = $1",
            fact_id,
        )
        assert (row["subject"], row["validity"], row["effective_from"]) == (
            target,
            "active",
            None,
        )
        assert await _active_on(pool, source) == 0

    async def test_ordinary_supersede_waits_on_the_entity_while_a_merge_holds_it(self, pool):
        """AC1a for the ordinary supersede path: a re-assertion with new
        provenance queues on ``public.entities`` without locking the fact row it
        would supersede, so the merge that holds the entity cannot deadlock."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import (
            AssertOutcome,
            relationship_assert_fact,
        )

        await simulate_temporal_cutover(pool)
        target, source, value, fact_id = await _merge_pair_with_fact(pool, "ac1a-sup")

        async with pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            merge = asyncio.create_task(
                merge_entity_pair(
                    pool,
                    source_entity_id=source,
                    target_entity_id=target,
                    locked_guard=lambda conn, _pair: _latch_on(conn),
                )
            )
            await _wait_on_latch(pool)
            # Same triple, different src: the writer's supersede path.
            reassert = asyncio.create_task(
                relationship_assert_fact(pool, source, "has-email", value, src="test-reassert")
            )
            pid = await _poll(
                pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%",
                what="the re-assertion to queue on public.entities",
            )
            fact_locks = await pool.fetchval(
                "SELECT count(*) FROM pg_locks WHERE pid = $1 AND relation = "
                "'relationship.entity_facts'::regclass "
                "AND (locktype = 'tuple' OR mode IN ('RowShareLock', 'RowExclusiveLock'))",
                pid,
            )
            assert fact_locks == 0, "the supersede path locked a fact row before the entity"
            await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
            merged, reasserted = await asyncio.gather(merge, reassert, return_exceptions=True)

        assert not isinstance(merged, BaseException), merged
        assert not isinstance(reasserted, BaseException), reasserted
        # The merge moved the original row intact before the writer re-read.
        row = await pool.fetchrow(
            "SELECT subject, validity, src FROM relationship.entity_facts WHERE id = $1",
            fact_id,
        )
        assert (row["subject"], row["validity"], row["src"]) == (target, "active", "test")
        # Known gap, pinned so its fix is visible: finding nothing left on the
        # source, the writer inserts there, onto the tombstoned entity. Whether
        # it should refuse or follow merged_into instead is bu-gm93xc.
        assert reasserted.outcome == AssertOutcome.inserted
        assert (
            await pool.fetchval(
                "SELECT subject FROM relationship.entity_facts WHERE id = $1", reasserted.fact_id
            )
            == source
        )

    async def test_merge_waits_on_a_correction_holding_the_entity(self, pool, monkeypatch):
        """AC1b: a correction paused after its entity lock makes the merge queue
        on ``public.entities``; the correction commits, then the merge repoints
        the new replacement row."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome

        await simulate_temporal_cutover(pool)
        target, source, value, fact_id = await _merge_pair_with_fact(pool, "ac1b")

        # The package re-exports the function under the submodule's name.
        writer = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")
        real_lock = writer._lock_fact_entities

        async def lock_then_latch(conn, *args, **kwargs):
            await real_lock(conn, *args, **kwargs)
            await _latch_on(conn)

        monkeypatch.setattr(writer, "_lock_fact_entities", lock_then_latch)

        async with pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            correction = asyncio.create_task(_correct(pool, source, value, fact_id))
            await _wait_on_latch(pool)
            merge = asyncio.create_task(
                merge_entity_pair(pool, source_entity_id=source, target_entity_id=target)
            )
            await _poll(
                pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%FROM public.entities%FOR UPDATE%",
                what="the merge to queue on public.entities",
            )
            await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
            corrected, merged = await asyncio.gather(correction, merge, return_exceptions=True)

        assert not isinstance(merged, BaseException), merged
        assert corrected.outcome == AssertOutcome.superseded
        replacement = await pool.fetchrow(
            "SELECT subject, validity, effective_from_precision "
            "FROM relationship.entity_facts WHERE id = $1",
            corrected.fact_id,
        )
        assert (
            replacement["subject"],
            replacement["validity"],
            replacement["effective_from_precision"],
        ) == (target, "active", "year")
        assert (
            await pool.fetchval(
                "SELECT validity FROM relationship.entity_facts WHERE id = $1", fact_id
            )
            == "superseded"
        )
        assert await _active_on(pool, source) == 0

    async def test_concurrent_correction_and_merge_never_deadlock(self, pool):
        """AC1d: 20 unlatched correction + merge races, zero deadlocks.

        A stress loop, not a timing assertion: either side may win, but every
        race ends with the merge applied, the correction applied-then-repointed
        or refused with a typed error, and nothing active on the source.
        """
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome

        await simulate_temporal_cutover(pool)
        for i in range(20):
            target, source, value, fact_id = await _merge_pair_with_fact(pool, f"ac1d-{i}")
            merged, corrected = await asyncio.gather(
                merge_entity_pair(pool, source_entity_id=source, target_entity_id=target),
                _correct(pool, source, value, fact_id),
                return_exceptions=True,
            )
            assert not isinstance(merged, asyncpg.exceptions.DeadlockDetectedError), i
            assert not isinstance(corrected, asyncpg.exceptions.DeadlockDetectedError), i
            assert not isinstance(merged, BaseException), (i, merged)
            if isinstance(corrected, BaseException):
                assert isinstance(corrected, TemporalError), (i, corrected)
            else:
                assert corrected.outcome == AssertOutcome.superseded, i
                assert (
                    await pool.fetchval(
                        "SELECT subject FROM relationship.entity_facts WHERE id = $1",
                        corrected.fact_id,
                    )
                    == target
                ), i
            assert await _active_on(pool, source) == 0, i

    async def test_initial_facts_lock_their_entities_in_one_ordered_batch(self, pool, monkeypatch):
        """bu-7s41je: ``promote_entity`` makes one writer call per initial fact in
        ONE transaction. Locked per call, entity objects X > Y are taken in body
        order (X, then Y) while a merge of (X, Y) takes Y, then X: a deadlock. The
        request now locks every entity it writes against once, ascending, before
        its first writer call, so the merge queues on ``public.entities`` behind
        it instead. 20 latched races, zero deadlocks."""
        import asyncio

        from butlers.api.router_discovery import discover_butler_routers
        from butlers.tools.relationship.entity_merge import merge_entity_pair

        router = next(m for name, m in discover_butler_routers() if name == "relationship")
        writer = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")
        await pool.execute(
            "INSERT INTO relationship.entity_predicate_registry "
            "(predicate, kind, object_kind, cardinality, description) "
            "VALUES ('knows', 'relational', 'entity', 'multi', 'Knows.') "
            "ON CONFLICT (predicate) DO NOTHING"
        )
        await _insert_entity(pool, name="Owner", roles=["owner"])

        real_assert = writer.relationship_assert_fact
        writer_calls: list[int] = []

        async def assert_then_latch(*args, **kwargs):
            result = await real_assert(*args, **kwargs)
            writer_calls.append(1)
            if len(writer_calls) == 1:
                # Pause between the first and second initial fact, inside the
                # request's transaction and holding whatever it has locked.
                await _latch_on(kwargs["conn"])
            return result

        monkeypatch.setattr(writer, "relationship_assert_fact", assert_then_latch)

        for i in range(20):
            writer_calls.clear()
            first, second = (
                await _insert_entity(pool, name=f"batch-{i}-a", roles=[]),
                await _insert_entity(pool, name=f"batch-{i}-b", roles=[]),
            )
            high, low = max(first, second), min(first, second)
            body = router.PromoteEntityRequest(
                canonical_name=f"Newcomer {i}",
                initial_facts=[
                    {"predicate": "knows", "object": str(high), "object_kind": "entity"},
                    {"predicate": "knows", "object": str(low), "object_kind": "entity"},
                ],
            )

            async with pool.acquire() as latch:
                await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
                create = asyncio.create_task(router.promote_entity(body, db=_db_with_pool(pool)))
                await _wait_on_latch(pool)
                merge = asyncio.create_task(
                    merge_entity_pair(pool, source_entity_id=high, target_entity_id=low)
                )
                await _poll(
                    pool,
                    _ROW_LOCK_WAITERS_SQL,
                    "public.entities",
                    "%FROM public.entities%FOR UPDATE%",
                    what="the merge to queue on public.entities",
                )
                await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
                created, merged = await asyncio.gather(create, merge, return_exceptions=True)

            assert not isinstance(created, BaseException), (i, created)
            assert not isinstance(merged, BaseException), (i, merged)
            assert len(writer_calls) == 2, i
            # Both edges committed before the merge, which then repointed the one
            # naming the merged-away source onto its survivor.
            objects = await pool.fetch(
                "SELECT object FROM relationship.entity_facts "
                "WHERE subject = $1 AND predicate = 'knows' AND validity = 'active'",
                created.id,
            )
            assert {r["object"] for r in objects} == {str(low)}, i

    async def test_correction_waits_on_the_object_entity_while_a_merge_holds_it(self, pool):
        """The writer itself locks a fact's OBJECT entity before any fact row.

        Driven through ``relationship_assert_fact`` directly, not
        ``promote_entity``: the router's batch lock (bu-7s41je) would take the
        object first and mask a writer that stopped locking it. A merge of the
        object X, paused after its entity lock, makes a correction of the edge
        naming X queue on ``public.entities`` holding no fact lock; without the
        object lock it would hold the fact row and wait on X against the merge's
        wait for that row: a deadlock."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

        await simulate_temporal_cutover(pool)
        await pool.execute(
            "INSERT INTO relationship.entity_predicate_registry "
            "(predicate, kind, object_kind, cardinality, description) "
            "VALUES ('knows', 'relational', 'entity', 'multi', 'Knows.') "
            "ON CONFLICT (predicate) DO NOTHING"
        )
        subject = await _insert_entity(pool, name="obj-lock subject", roles=[])
        merged_object = await _insert_entity(pool, name="obj-lock object", roles=[])
        target = await _insert_entity(pool, name="obj-lock target", roles=[])
        fact_id = await pool.fetchval(
            "INSERT INTO relationship.entity_facts "
            "(subject, predicate, object, object_kind, src) "
            "VALUES ($1, 'knows', $2, 'entity', 'test') RETURNING id",
            subject,
            str(merged_object),
        )

        async with pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            merge = asyncio.create_task(
                merge_entity_pair(
                    pool,
                    source_entity_id=merged_object,
                    target_entity_id=target,
                    locked_guard=lambda conn, _pair: _latch_on(conn),
                )
            )
            await _wait_on_latch(pool)
            correction = asyncio.create_task(
                relationship_assert_fact(
                    pool,
                    subject,
                    "knows",
                    str(merged_object),
                    object_kind="entity",
                    src="test",
                    corrects_fact_id=fact_id,
                    effective_from="2019",
                    effective_from_precision="year",
                )
            )
            pid = await _poll(
                pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%FOR KEY SHARE%",
                what="the correction to queue on its object entity",
            )
            fact_locks = await pool.fetchval(
                "SELECT count(*) FROM pg_locks WHERE pid = $1 AND relation = "
                "'relationship.entity_facts'::regclass "
                "AND (locktype = 'tuple' OR mode IN ('RowShareLock', 'RowExclusiveLock'))",
                pid,
            )
            assert fact_locks == 0, "the correction locked a fact row before its object entity"
            await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
            merged, corrected = await asyncio.gather(merge, correction, return_exceptions=True)

        assert not isinstance(merged, BaseException), merged
        # The merge repointed the edge's object in place, so a correction naming
        # the old object is a typed refusal with no write. This is CURRENT
        # behavior, an accident of the writer's subject/predicate/object match on
        # ``corrects_fact_id``. Whether a write against a merged-away entity
        # should refuse or follow ``metadata.merged_into`` is bu-gm93xc; if that
        # changes this outcome, update this assertion deliberately.
        assert isinstance(corrected, TemporalError), corrected
        rows = await pool.fetch(
            "SELECT id, object, validity FROM relationship.entity_facts WHERE subject = $1",
            subject,
        )
        assert [(r["id"], r["object"], r["validity"]) for r in rows] == [
            (fact_id, str(target), "active")
        ]
