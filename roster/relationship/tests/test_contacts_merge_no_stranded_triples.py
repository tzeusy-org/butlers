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

This test exercises the audited path end-to-end over a real Postgres rel_pool — the
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

from butlers.tools.relationship.fact_temporal import (
    MUTATOR_UNSUPPORTED,
    PACKET_COLUMNS,
    TemporalError,
)
from roster.relationship.tests.evidence_schema import (
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
async def rel_pool(db):
    """Core + memory + relationship chains (flat; entity_facts in ``relationship``)."""
    return await db("core", "memory", "relationship", min_size=2, max_size=8)


def _db_with_pool(rel_pool: asyncpg.Pool) -> MagicMock:
    """A DatabaseManager-shaped stub whose .pool() returns the provisioned rel_pool."""
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


async def _add_channel_fact(
    rel_pool: asyncpg.Pool, subject: uuid.UUID, predicate: str, value: str
) -> None:
    await rel_pool.execute(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
        VALUES ($1, $2, $3, 'literal', 'test')
        """,
        subject,
        predicate,
        value,
    )


class TestContactsMergeNoStrandedTriples:
    async def test_channel_triples_survive_on_survivor(self, rel_pool):
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
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])

        target_id = await _insert_entity(rel_pool, name="Alice (canonical)", roles=[])
        source_id = await _insert_entity(rel_pool, name="Alice (duplicate)", roles=[])

        # Target carries its own channel triples.
        await _add_channel_fact(rel_pool, target_id, "has-email", "alice@work.com")
        await _add_channel_fact(rel_pool, target_id, "has-phone", "+15550001")
        # Source (the duplicate that will be merged away) carries DIFFERENT channel
        # triples — these are exactly the ones the old bypass stranded.
        await _add_channel_fact(rel_pool, source_id, "has-email", "alice@home.com")
        await _add_channel_fact(rel_pool, source_id, "has-telegram", "alice_tg")

        db = _db_with_pool(rel_pool)
        # keepAs='A' keeps entityA (target); entityB (source) is tombstoned.
        body = MergeEntitiesRequest(entityA=target_id, entityB=source_id, keepAs="A")
        resp = await merge_entities(target_id, body, db=db)

        assert resp.kept_entity_id == target_id
        assert resp.tombstoned_entity_id == source_id

        # No active triples may remain stranded on the tombstoned source.
        stranded = await rel_pool.fetch(
            """
            SELECT predicate, object FROM relationship.entity_facts
            WHERE subject = $1 AND validity = 'active'
            """,
            source_id,
        )
        assert stranded == [], f"Triples stranded on tombstoned source: {stranded}"

        # All four channel triples must be active on the survivor.
        survivor = await rel_pool.fetch(
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
        review_count = await rel_pool.fetchval(
            """
            SELECT count(*) FROM relationship.merge_reviews
            WHERE entity_a = $1 AND entity_b = $2 AND outcome = 'merged'
            """,
            target_id,
            source_id,
        )
        assert review_count == 1, "merge_entities must write exactly one merged audit row"

    async def test_contact_merge_writes_merge_reviews_audit_row(self, rel_pool):
        """The session-side ``contact_merge`` MCP tool writes a merge_reviews audit
        row regardless of entry path (bu-csvop; relationship-merge-review spec)."""
        from butlers.tools.relationship.contacts import contact_merge

        target_entity = await _insert_entity(rel_pool, name="Bob (canonical)", roles=[])
        source_entity = await _insert_entity(rel_pool, name="Bob (duplicate)", roles=[])

        # A shared channel triple becomes the audit "shared" evidence.
        await _add_channel_fact(rel_pool, target_entity, "has-email", "bob@work.com")
        await _add_channel_fact(rel_pool, source_entity, "has-email", "bob@work.com")

        target_contact = await rel_pool.fetchval(
            "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
            "Bob (canonical)",
            target_entity,
        )
        source_contact = await rel_pool.fetchval(
            "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
            "Bob (duplicate)",
            source_entity,
        )
        # Cutover (bu-irphu): contact_merge resolves contacts via contact_entity_map
        # (not public.contacts), so bridge both contacts to their entities.
        await rel_pool.execute(
            "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2), ($3, $4)",
            target_contact,
            target_entity,
            source_contact,
            source_entity,
        )

        await contact_merge(rel_pool, source_id=source_contact, target_id=target_contact)

        # contact_merge wrote a merged audit row for the underlying entities.
        review = await rel_pool.fetchrow(
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


async def _merge_state(rel_pool: asyncpg.Pool) -> tuple:
    """Everything a merge could write, for "nothing changed" assertions."""
    return (
        await rel_pool.fetch(
            f"SELECT id, subject, object, validity, {PACKET_COLUMNS} "
            "FROM relationship.entity_facts ORDER BY id"
        ),
        await rel_pool.fetch("SELECT id, metadata, aliases FROM public.entities ORDER BY id"),
        await rel_pool.fetch("SELECT * FROM contact_entity_map ORDER BY contact_id"),
        await rel_pool.fetchval("SELECT count(*) FROM relationship.merge_reviews"),
        await rel_pool.fetchval("SELECT count(*) FROM public.entity_rebind_log"),
    )


async def _add_temporal_fact(
    rel_pool: asyncpg.Pool, subject: uuid.UUID, value: str, period: uuid.UUID | None
) -> uuid.UUID:
    return await rel_pool.fetchval(
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
    async def test_entity_merge_moves_occurrences_intact_or_writes_nothing(self, rel_pool):
        from butlers.tools.relationship.entity_merge import (
            TemporalOccurrenceCollisionError,
            merge_entity_pair,
        )

        await simulate_temporal_cutover(rel_pool)
        target_id = await _insert_entity(rel_pool, name="Target", roles=[])
        source_id = await _insert_entity(rel_pool, name="Source", roles=[])
        # Same triple, different occurrences: the default unknown row on the
        # target and a repeated period on the source. No collision, no collapse.
        await _add_channel_fact(rel_pool, target_id, "has-email", "shared@example.test")
        moved_id = await _add_temporal_fact(
            rel_pool, source_id, "shared@example.test", uuid.uuid4()
        )
        await rel_pool.execute(
            "INSERT INTO relationship.fact_evidence (fact_id, seq, kind, ref, note, src, origin) "
            "VALUES ($1, 1, 'url', 'https://example.test/e', '', 'test', 'direct')",
            moved_id,
        )
        before = await rel_pool.fetchrow(
            f"SELECT {PACKET_COLUMNS} FROM relationship.entity_facts WHERE id = $1", moved_id
        )

        await merge_entity_pair(rel_pool, source_entity_id=source_id, target_entity_id=target_id)

        moved = await rel_pool.fetchrow(
            f"SELECT subject, validity, {PACKET_COLUMNS} FROM relationship.entity_facts "
            "WHERE id = $1",
            moved_id,
        )
        assert (moved["subject"], moved["validity"]) == (target_id, "active")
        assert {k: moved[k] for k in before.keys()} == dict(before)
        assert (
            await rel_pool.fetchval(
                "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id = $1", moved_id
            )
            == 1
        )
        assert (
            await rel_pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts "
                "WHERE subject = $1 AND object = 'shared@example.test' AND validity = 'active'",
                target_id,
            )
            == 2
        )

        # A temporal row colliding on its final occurrence key refuses the whole merge.
        second_target = await _insert_entity(rel_pool, name="Target 2", roles=[])
        second_source = await _insert_entity(rel_pool, name="Source 2", roles=[])
        await _add_channel_fact(rel_pool, second_target, "has-email", "clash@example.test")
        await _add_temporal_fact(rel_pool, second_source, "clash@example.test", None)
        state = await _merge_state(rel_pool)
        with pytest.raises(TemporalOccurrenceCollisionError):
            await merge_entity_pair(
                rel_pool, source_entity_id=second_source, target_entity_id=second_target
            )
        assert await _merge_state(rel_pool) == state

    async def test_legacy_contact_merge_is_fenced_before_its_first_write(self, rel_pool):
        from butlers.tools.relationship.contacts import contact_merge

        await simulate_temporal_cutover(rel_pool)
        target_entity = await _insert_entity(rel_pool, name="Carol (canonical)", roles=[])
        source_entity = await _insert_entity(rel_pool, name="Carol (duplicate)", roles=[])
        await _add_temporal_fact(rel_pool, source_entity, "carol@example.test", uuid.uuid4())
        contacts = []
        for name, entity in (("Carol", target_entity), ("Carol dup", source_entity)):
            contact = await rel_pool.fetchval(
                "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id", name, entity
            )
            await rel_pool.execute(
                "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
                contact,
                entity,
            )
            await rel_pool.execute(
                "INSERT INTO tasks (contact_id, title) VALUES ($1, 'child row')", contact
            )
            contacts.append(contact)
        state = await _merge_state(rel_pool)
        tasks = await rel_pool.fetch("SELECT id, contact_id FROM tasks ORDER BY id")

        with pytest.raises(TemporalError) as caught:
            await contact_merge(rel_pool, source_id=contacts[1], target_id=contacts[0])

        assert caught.value.code == MUTATOR_UNSUPPORTED
        assert await _merge_state(rel_pool) == state
        assert await rel_pool.fetch("SELECT id, contact_id FROM tasks ORDER BY id") == tasks

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
        self, rel_pool, occurrences, operation, refusal
    ):
        from fastapi import HTTPException, Response

        from butlers.api.router_discovery import discover_butler_routers
        from butlers.tools.relationship.relationship_assert_fact import (
            retract_contact_info_fact,
        )

        router = next(m for name, m in discover_butler_routers() if name == "relationship")
        await simulate_temporal_cutover(rel_pool)
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        subject = await _insert_entity(rel_pool, name="Dana", roles=[])
        value = "dana@example.test"
        fact_id = await _add_temporal_fact(rel_pool, subject, value, uuid.uuid4())
        if occurrences == "repeated":
            await _add_channel_fact(rel_pool, subject, "has-email", value)
        value_hash = router._contact_value_hash(value)
        db = _db_with_pool(rel_pool)
        state = await _merge_state(rel_pool)

        async def run():
            if operation == "retract-helper":
                return await retract_contact_info_fact(rel_pool, subject, "email", value)
            if operation == "delete":
                return await router.delete_entity_contact(subject, "has-email", value_hash, db=db)
            if operation == "verify":
                from roster.relationship.tests.fact_authority_fixtures import synthetic_owner

                # Direct handler conformance has no HTTP admission; supply an
                # explicitly synthetic owner, never runtime/authentication proof.
                async with synthetic_owner(rel_pool):
                    return await router.verify_entity_contact(
                        subject, "has-email", value_hash, db=db
                    )
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
            verified = await rel_pool.fetchrow(
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
        assert await _merge_state(rel_pool) == state

    @pytest.mark.parametrize("path", ["forget", "google", "steam"])
    async def test_all_occurrence_lifecycle_paths_keep_history_and_projections_atomic(
        self, rel_pool, path
    ):
        from butlers.api.router_discovery import discover_butler_routers

        await simulate_temporal_cutover(rel_pool)
        await rel_pool.execute("""
            ALTER TABLE public.entity_graph_edges
                ADD FOREIGN KEY (subject_entity_id) REFERENCES public.entities(id)
                    ON DELETE CASCADE,
                ADD FOREIGN KEY (object_entity_id) REFERENCES public.entities(id)
                    ON DELETE CASCADE
        """)
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        companion = await _insert_entity(rel_pool, name="Companion", roles=[])
        other = await _insert_entity(rel_pool, name="Other", roles=[])
        superseded = await _add_temporal_fact(rel_pool, companion, "c@example.test", None)
        await rel_pool.execute(
            "UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = $1",
            superseded,
        )
        await _add_channel_fact(rel_pool, companion, "has-email", "c@example.test")
        await _add_temporal_fact(rel_pool, companion, "c@example.test", uuid.uuid4())
        for subject, obj in ((companion, other), (other, companion)):
            edge_fact = await rel_pool.fetchval(
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
            await rel_pool.execute(
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
        await rel_pool.execute(
            "INSERT INTO relationship.fact_evidence (fact_id, seq, kind, ref, note, src, origin) "
            "SELECT id, 1, 'url', 'https://example.test/' || id, '', 'test', 'direct' "
            "FROM relationship.entity_facts"
        )
        involved = f"""
            SELECT id, validity, {PACKET_COLUMNS} FROM relationship.entity_facts
            WHERE subject = $1 OR (object_kind = 'entity' AND object = $1::text)
            ORDER BY id
        """
        before = await rel_pool.fetch(involved, companion)
        evidence_before = await rel_pool.fetchval("SELECT count(*) FROM relationship.fact_evidence")

        if path == "forget":
            router = next(m for name, m in discover_butler_routers() if name == "relationship")
            await router.forget_entity(companion, db=_db_with_pool(rel_pool))
            after = await rel_pool.fetch(involved, companion)
            # Every occurrence is retracted as-is; history stays superseded.
            assert [r["id"] for r in after] == [r["id"] for r in before]
            for old, new in zip(before, after, strict=True):
                expected = "superseded" if old["validity"] == "superseded" else "retracted"
                assert new["validity"] == expected
                assert {k: new[k] for k in new.keys() if k != "validity"} == {
                    k: old[k] for k in old.keys() if k != "validity"
                }
            assert (
                await rel_pool.fetchval("SELECT count(*) FROM relationship.fact_evidence")
                == evidence_before
            )
        else:
            registry = importlib.import_module(f"butlers.{path}_account_registry")
            table = f"public.{path}_accounts"
            # The real account table; steam_accounts also requires a steam_id.
            extra_col, extra_val = (
                (", steam_id", ", 76561190000000001") if path == "steam" else ("", "")
            )
            account = await rel_pool.fetchval(
                f"INSERT INTO {table} (entity_id{extra_col}) VALUES ($1{extra_val}) RETURNING id",  # noqa: S608
                companion,
            )
            await registry.disconnect_account(rel_pool, account, hard_delete=True)
            # Every version the companion subjects is gone, with its evidence.
            assert (
                await rel_pool.fetchval(
                    "SELECT count(*) FROM relationship.entity_facts WHERE subject = $1", companion
                )
                == 0
            )
            assert (
                await rel_pool.fetchval(
                    "SELECT count(*) FROM relationship.fact_evidence e "
                    "WHERE NOT EXISTS (SELECT 1 FROM relationship.entity_facts f WHERE f.id = e.fact_id)"
                )
                == 0
            )
        assert (
            await rel_pool.fetchval(
                "SELECT count(*) FROM public.entity_graph_edges "
                "WHERE subject_entity_id = $1 OR object_entity_id = $1",
                companion,
            )
            == 0
        )

    # -- Post-cutover race windows (bu-p2bjsf) --------------------------------

    @staticmethod
    async def _linked_contacts(
        rel_pool: asyncpg.Pool, target_entity: uuid.UUID, source_entity: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """(target contact, source contact), each bridged and carrying one note."""
        contacts = []
        for name, entity in (("Target", target_entity), ("Source", source_entity)):
            contact = await rel_pool.fetchval(
                "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id", name, entity
            )
            await rel_pool.execute(
                "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
                contact,
                entity,
            )
            await rel_pool.execute(
                "INSERT INTO tasks (contact_id, title) VALUES ($1, 'child row')", contact
            )
            contacts.append(contact)
        return contacts[0], contacts[1]

    @pytest.mark.parametrize("side", ["subject", "object"])
    async def test_contact_merge_refuses_a_row_turned_temporal_after_its_preflight(
        self, rel_pool, monkeypatch, side
    ):
        """A row that gains effective time between the unlocked preflight and the
        locked re-check refuses the whole merge: no contact, entity or fact write,
        and the memory entity merge never runs."""
        from butlers.modules.memory.tools import entities as memory_entities
        from butlers.tools.relationship import contacts as contacts_mod

        await simulate_temporal_cutover(rel_pool)
        target_entity = await _insert_entity(rel_pool, name="Erin", roles=[])
        source_entity = await _insert_entity(rel_pool, name="Erin dup", roles=[])
        await rel_pool.execute(
            'UPDATE public.entities SET metadata = \'{"profile": {"city": "Oslo"}}\' WHERE id = $1',
            source_entity,
        )
        if side == "subject":
            await _add_channel_fact(rel_pool, source_entity, "has-email", "erin@example.test")
            raced = await rel_pool.fetchval(
                "SELECT id FROM relationship.entity_facts WHERE subject = $1", source_entity
            )
        else:
            other = await _insert_entity(rel_pool, name="Frank", roles=[])
            raced = await rel_pool.fetchval(
                """
                INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
                VALUES ($1, 'knows', $2, 'entity', 'test')
                RETURNING id
                """,
                other,
                str(source_entity),
            )
        target_contact, source_contact = await self._linked_contacts(
            rel_pool, target_entity, source_entity
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
                await rel_pool.execute(
                    "UPDATE relationship.entity_facts SET effective_period_id = $2, "
                    "effective_from = '2021-01-01Z', effective_from_precision = 'year' "
                    "WHERE id = $1",
                    raced,
                    uuid.uuid4(),
                )
                injected["state"] = await _merge_state(rel_pool)
                injected["tasks"] = await rel_pool.fetch(
                    "SELECT id, contact_id FROM tasks ORDER BY id"
                )

        memory_merges = []

        async def spy_entity_merge(*args, **kwargs):
            memory_merges.append(args)

        monkeypatch.setattr(contacts_mod, "_fence_legacy_fact_repoint", racing_fence)
        monkeypatch.setattr(memory_entities, "entity_merge", spy_entity_merge)

        with pytest.raises(TemporalError) as caught:
            await contacts_mod.contact_merge(
                rel_pool, source_id=source_contact, target_id=target_contact
            )

        assert caught.value.code == MUTATOR_UNSUPPORTED
        assert len(calls) == 2  # the preflight, then the re-check under locks
        assert await _merge_state(rel_pool) == injected["state"]
        assert (
            await rel_pool.fetch("SELECT id, contact_id FROM tasks ORDER BY id")
            == injected["tasks"]
        )
        assert memory_merges == []

    async def test_contact_merge_locks_affected_rows_against_a_concurrent_correction(
        self, rel_pool, monkeypatch
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

        await simulate_temporal_cutover(rel_pool)
        target_entity = await _insert_entity(rel_pool, name="Gale", roles=[])
        source_entity = await _insert_entity(rel_pool, name="Gale dup", roles=[])
        await _add_channel_fact(rel_pool, target_entity, "has-email", "gale@work.test")
        await _add_channel_fact(rel_pool, source_entity, "has-email", "gale@home.test")
        kept_id, moved_id = [
            await rel_pool.fetchval(
                "SELECT id FROM relationship.entity_facts WHERE subject = $1", entity
            )
            for entity in (target_entity, source_entity)
        ]
        target_contact, source_contact = await self._linked_contacts(
            rel_pool, target_entity, source_entity
        )

        def correct(subject, value, fact_id):
            return relationship_assert_fact(
                rel_pool,
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
                    waiting = await rel_pool.fetchval(
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

        await contacts_mod.contact_merge(
            rel_pool, source_id=source_contact, target_id=target_contact
        )
        kept_result, moved_result = await asyncio.gather(*corrections, return_exceptions=True)

        # The locked, unmoved target row: corrected exactly once after the commit.
        assert kept_result.outcome == AssertOutcome.superseded
        active = await rel_pool.fetch(
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
        moved = await rel_pool.fetchrow(
            "SELECT subject, validity, effective_from FROM relationship.entity_facts WHERE id = $1",
            moved_id,
        )
        assert (moved["subject"], moved["validity"], moved["effective_from"]) == (
            target_entity,
            "active",
            None,
        )
        assert (
            await rel_pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts "
                "WHERE object = 'gale@home.test' AND validity = 'active'"
            )
            == 1
        )
        # The unstubbed post-commit entity merge ran and tombstoned the source.
        assert await rel_pool.fetchval(
            "SELECT metadata->>'merged_into' FROM public.entities WHERE id = $1", source_entity
        ) == str(target_entity)

    async def test_contact_merge_skips_a_missing_optional_child_table(self, rel_pool):
        """A missing optional child table no longer aborts the merge transaction."""
        from butlers.tools.relationship.contacts import contact_merge

        await rel_pool.execute("DROP TABLE IF EXISTS stay_in_touch")
        target_entity = await _insert_entity(rel_pool, name="Hana", roles=[])
        source_entity = await _insert_entity(rel_pool, name="Hana dup", roles=[])
        await _add_channel_fact(rel_pool, source_entity, "has-email", "hana@example.test")
        target_contact, source_contact = await self._linked_contacts(
            rel_pool, target_entity, source_entity
        )

        await contact_merge(rel_pool, source_id=source_contact, target_id=target_contact)

        assert (
            await rel_pool.fetchval(
                "SELECT count(*) FROM tasks WHERE contact_id = $1", target_contact
            )
            == 2
        )
        assert (
            await rel_pool.fetchval(
                "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", source_contact
            )
            == 0
        )
        assert (
            await rel_pool.fetchval(
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
        self, rel_pool, monkeypatch, race, code
    ):
        """A row changed between hash selection and the edit transaction is refused
        with 409 before its retraction, and nothing is written."""
        from fastapi import HTTPException, Response

        from butlers.api.router_discovery import discover_butler_routers

        router = next(m for name, m in discover_butler_routers() if name == "relationship")
        await simulate_temporal_cutover(rel_pool)
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])
        subject = await _insert_entity(rel_pool, name="Ivy", roles=[])
        value = "ivy@example.test"
        await _add_channel_fact(rel_pool, subject, "has-email", value)
        fact_id = await rel_pool.fetchval(
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
            await rel_pool.execute(mutation, fact_id)
            injected["state"] = await _merge_state(rel_pool)
            return row

        monkeypatch.setattr(router, "_resolve_contact_fact_by_hash", resolve_then_race)

        with pytest.raises(HTTPException) as caught:
            await router.update_entity_contact(
                subject,
                "has-email",
                router._contact_value_hash(value),
                router.UpdateContactRequest(new_value="ivy@edited.test"),
                Response(),
                db=_db_with_pool(rel_pool),
            )

        assert (caught.value.status_code, caught.value.detail["code"]) == (409, code)
        assert await _merge_state(rel_pool) == injected["state"]
        assert (
            await rel_pool.fetchval(
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


async def _poll(rel_pool: asyncpg.Pool, sql: str, *args, what: str):
    import asyncio

    for _ in range(1000):
        value = await rel_pool.fetchval(sql, *args)
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def _wait_on_latch(rel_pool: asyncpg.Pool) -> None:
    await _poll(
        rel_pool,
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted",
        what="a transaction to block on the latch",
    )


async def _latch_on(conn: asyncpg.Connection) -> None:
    """Block *conn*'s transaction until the test releases the latch."""
    await conn.execute("SELECT pg_advisory_xact_lock($1)", _LATCH_KEY)


def _correct(rel_pool: asyncpg.Pool, subject: uuid.UUID, value: str, fact_id: uuid.UUID):
    from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

    return relationship_assert_fact(
        rel_pool,
        subject,
        "has-email",
        value,
        src="test",
        corrects_fact_id=fact_id,
        effective_from="2019",
        effective_from_precision="year",
    )


async def _merge_pair_with_fact(rel_pool: asyncpg.Pool, tag: str) -> tuple:
    target = await _insert_entity(rel_pool, name=f"{tag} target", roles=[])
    source = await _insert_entity(rel_pool, name=f"{tag} source", roles=[])
    value = f"{tag}@source.test"
    await _add_channel_fact(rel_pool, source, "has-email", value)
    fact_id = await rel_pool.fetchval(
        "SELECT id FROM relationship.entity_facts WHERE subject = $1", source
    )
    return target, source, value, fact_id


async def _active_on(rel_pool: asyncpg.Pool, subject: uuid.UUID) -> int:
    return await rel_pool.fetchval(
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

    async def test_correction_waits_on_the_entity_while_a_merge_holds_it(self, rel_pool):
        """AC1a: a merge paused after its entity lock makes a correction of a
        source fact queue on ``public.entities`` without touching the fact row;
        on release the merge completes and the stale correction is refused."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair

        await simulate_temporal_cutover(rel_pool)
        target, source, value, fact_id = await _merge_pair_with_fact(rel_pool, "ac1a")

        async with rel_pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            merge = asyncio.create_task(
                merge_entity_pair(
                    rel_pool,
                    source_entity_id=source,
                    target_entity_id=target,
                    locked_guard=lambda conn, _pair: _latch_on(conn),
                )
            )
            await _wait_on_latch(rel_pool)
            correction = asyncio.create_task(_correct(rel_pool, source, value, fact_id))
            pid = await _poll(
                rel_pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%FOR KEY SHARE%",
                what="the correction to queue on public.entities",
            )
            fact_locks = await rel_pool.fetchval(
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
        row = await rel_pool.fetchrow(
            "SELECT subject, validity, effective_from FROM relationship.entity_facts WHERE id = $1",
            fact_id,
        )
        assert (row["subject"], row["validity"], row["effective_from"]) == (
            target,
            "active",
            None,
        )
        assert await _active_on(rel_pool, source) == 0

    async def test_ordinary_supersede_waits_on_the_entity_while_a_merge_holds_it(self, rel_pool):
        """AC1a for the ordinary supersede path: a re-assertion with new
        provenance queues on ``public.entities`` without locking the fact row it
        would supersede, so the merge that holds the entity cannot deadlock."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import (
            AssertOutcome,
            relationship_assert_fact,
        )

        await simulate_temporal_cutover(rel_pool)
        target, source, value, fact_id = await _merge_pair_with_fact(rel_pool, "ac1a-sup")

        async with rel_pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            merge = asyncio.create_task(
                merge_entity_pair(
                    rel_pool,
                    source_entity_id=source,
                    target_entity_id=target,
                    locked_guard=lambda conn, _pair: _latch_on(conn),
                )
            )
            await _wait_on_latch(rel_pool)
            # Same triple, different src: the writer's supersede path.
            reassert = asyncio.create_task(
                relationship_assert_fact(rel_pool, source, "has-email", value, src="test-reassert")
            )
            pid = await _poll(
                rel_pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%",
                what="the re-assertion to queue on public.entities",
            )
            fact_locks = await rel_pool.fetchval(
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
        row = await rel_pool.fetchrow(
            "SELECT subject, validity, src FROM relationship.entity_facts WHERE id = $1",
            fact_id,
        )
        assert (row["subject"], row["validity"], row["src"]) == (target, "active", "test")
        # Known gap, pinned so its fix is visible: finding nothing left on the
        # source, the writer inserts there, onto the tombstoned entity. Whether
        # it should refuse or follow merged_into instead is bu-gm93xc.
        assert reasserted.outcome == AssertOutcome.inserted
        assert (
            await rel_pool.fetchval(
                "SELECT subject FROM relationship.entity_facts WHERE id = $1", reasserted.fact_id
            )
            == source
        )

    async def test_merge_waits_on_a_correction_holding_the_entity(self, rel_pool, monkeypatch):
        """AC1b: a correction paused after its entity lock makes the merge queue
        on ``public.entities``; the correction commits, then the merge repoints
        the new replacement row."""
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome

        await simulate_temporal_cutover(rel_pool)
        target, source, value, fact_id = await _merge_pair_with_fact(rel_pool, "ac1b")

        # The package re-exports the function under the submodule's name.
        writer = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")
        real_lock = writer._lock_fact_entities_batch

        async def lock_then_latch(conn, *args, **kwargs):
            await real_lock(conn, *args, **kwargs)
            await _latch_on(conn)

        monkeypatch.setattr(writer, "_lock_fact_entities_batch", lock_then_latch)

        async with rel_pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            correction = asyncio.create_task(_correct(rel_pool, source, value, fact_id))
            await _wait_on_latch(rel_pool)
            merge = asyncio.create_task(
                merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)
            )
            await _poll(
                rel_pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%FROM public.entities%FOR UPDATE%",
                what="the merge to queue on public.entities",
            )
            await latch.execute("SELECT pg_advisory_unlock($1)", _LATCH_KEY)
            corrected, merged = await asyncio.gather(correction, merge, return_exceptions=True)

        assert not isinstance(merged, BaseException), merged
        assert corrected.outcome == AssertOutcome.superseded
        replacement = await rel_pool.fetchrow(
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
            await rel_pool.fetchval(
                "SELECT validity FROM relationship.entity_facts WHERE id = $1", fact_id
            )
            == "superseded"
        )
        assert await _active_on(rel_pool, source) == 0

    async def test_concurrent_correction_and_merge_never_deadlock(self, rel_pool):
        """AC1d: 20 unlatched correction + merge races, zero deadlocks.

        A stress loop, not a timing assertion: either side may win, but every
        race ends with the merge applied, the correction applied-then-repointed
        or refused with a typed error, and nothing active on the source.
        """
        import asyncio

        from butlers.tools.relationship.entity_merge import merge_entity_pair
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome

        await simulate_temporal_cutover(rel_pool)
        for i in range(20):
            target, source, value, fact_id = await _merge_pair_with_fact(rel_pool, f"ac1d-{i}")
            merged, corrected = await asyncio.gather(
                merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target),
                _correct(rel_pool, source, value, fact_id),
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
                    await rel_pool.fetchval(
                        "SELECT subject FROM relationship.entity_facts WHERE id = $1",
                        corrected.fact_id,
                    )
                    == target
                ), i
            assert await _active_on(rel_pool, source) == 0, i

    async def test_initial_facts_lock_their_entities_in_one_ordered_batch(
        self, rel_pool, monkeypatch
    ):
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
        await rel_pool.execute(
            "INSERT INTO relationship.entity_predicate_registry "
            "(predicate, kind, object_kind, cardinality, description) "
            "VALUES ('knows', 'relational', 'entity', 'multi', 'Knows.') "
            "ON CONFLICT (predicate) DO NOTHING"
        )
        await _insert_entity(rel_pool, name="Owner", roles=["owner"])

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
                await _insert_entity(rel_pool, name=f"batch-{i}-a", roles=[]),
                await _insert_entity(rel_pool, name=f"batch-{i}-b", roles=[]),
            )
            high, low = max(first, second), min(first, second)
            body = router.PromoteEntityRequest(
                canonical_name=f"Newcomer {i}",
                initial_facts=[
                    {"predicate": "knows", "object": str(high), "object_kind": "entity"},
                    {"predicate": "knows", "object": str(low), "object_kind": "entity"},
                ],
            )

            async with rel_pool.acquire() as latch:
                await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
                create = asyncio.create_task(
                    router.promote_entity(body, db=_db_with_pool(rel_pool))
                )
                await _wait_on_latch(rel_pool)
                merge = asyncio.create_task(
                    merge_entity_pair(rel_pool, source_entity_id=high, target_entity_id=low)
                )
                await _poll(
                    rel_pool,
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
            objects = await rel_pool.fetch(
                "SELECT object FROM relationship.entity_facts "
                "WHERE subject = $1 AND predicate = 'knows' AND validity = 'active'",
                created.id,
            )
            assert {r["object"] for r in objects} == {str(low)}, i

    async def test_correction_waits_on_the_object_entity_while_a_merge_holds_it(self, rel_pool):
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

        await simulate_temporal_cutover(rel_pool)
        await rel_pool.execute(
            "INSERT INTO relationship.entity_predicate_registry "
            "(predicate, kind, object_kind, cardinality, description) "
            "VALUES ('knows', 'relational', 'entity', 'multi', 'Knows.') "
            "ON CONFLICT (predicate) DO NOTHING"
        )
        subject = await _insert_entity(rel_pool, name="obj-lock subject", roles=[])
        merged_object = await _insert_entity(rel_pool, name="obj-lock object", roles=[])
        target = await _insert_entity(rel_pool, name="obj-lock target", roles=[])
        fact_id = await rel_pool.fetchval(
            "INSERT INTO relationship.entity_facts "
            "(subject, predicate, object, object_kind, src) "
            "VALUES ($1, 'knows', $2, 'entity', 'test') RETURNING id",
            subject,
            str(merged_object),
        )

        async with rel_pool.acquire() as latch:
            await latch.execute("SELECT pg_advisory_lock($1)", _LATCH_KEY)
            merge = asyncio.create_task(
                merge_entity_pair(
                    rel_pool,
                    source_entity_id=merged_object,
                    target_entity_id=target,
                    locked_guard=lambda conn, _pair: _latch_on(conn),
                )
            )
            await _wait_on_latch(rel_pool)
            correction = asyncio.create_task(
                relationship_assert_fact(
                    rel_pool,
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
                rel_pool,
                _ROW_LOCK_WAITERS_SQL,
                "public.entities",
                "%FOR KEY SHARE%",
                what="the correction to queue on its object entity",
            )
            fact_locks = await rel_pool.fetchval(
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
        rows = await rel_pool.fetch(
            "SELECT id, object, validity FROM relationship.entity_facts WHERE subject = $1",
            subject,
        )
        assert [(r["id"], r["object"], r["validity"]) for r in rows] == [
            (fact_id, str(target), "active")
        ]
