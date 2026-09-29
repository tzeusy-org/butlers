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
