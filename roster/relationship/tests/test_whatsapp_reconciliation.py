"""PostgreSQL contracts for guarded WhatsApp entity reconciliation.

The tests exercise the real catalog, locking, audit, and reference semantics
rather than substituting an in-memory repository.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

import butlers.tools.relationship.whatsapp_reconciliation as reconciliation
from butlers.testing.schema_standins import (
    CONTACT_ENTITY_MAP,
    ENTITY_PREDICATE_REGISTRY,
    PENDING_ACTIONS,
)
from butlers.tools.relationship.entity_merge import (
    LockedEntityPair,
    LockedGuardRejected,
    merge_entity_pair,
)
from butlers.tools.relationship.whatsapp_reconciliation import (
    PlanDigestMismatch,
    ReconciliationCategory,
    apply_whatsapp_reconciliation,
    build_whatsapp_reconciliation_plan,
    validate_empty_shell_locked,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def reconciliation_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool(
        schema="relationship", min_pool_size=2, max_pool_size=8
    ) as pool:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await pool.execute("CREATE SCHEMA IF NOT EXISTS reconciliation_test")
        await pool.execute("CREATE SCHEMA IF NOT EXISTS chronicler")
        await pool.execute(
            """
            CREATE TABLE public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'person',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await pool.execute(
            """
            CREATE TABLE public.whatsmeow_lid_map (
                lid TEXT PRIMARY KEY,
                pn TEXT NOT NULL
            )
            """
        )
        # core_009 deliberately leaves memory_catalog.entity_id without a FK.
        await pool.execute(
            """
            CREATE TABLE public.memory_catalog (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_schema TEXT NOT NULL,
                source_table TEXT NOT NULL,
                source_id UUID NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                entity_id UUID,
                summary TEXT NOT NULL DEFAULT '',
                memory_type TEXT NOT NULL DEFAULT 'fact',
                object_entity_id UUID REFERENCES public.entities(id),
                UNIQUE (source_schema, source_table, source_id)
            )
            """
        )
        await pool.execute(ENTITY_PREDICATE_REGISTRY.ddl(schema="relationship"))
        await pool.execute(
            """
            CREATE TABLE relationship.entity_facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject UUID NOT NULL REFERENCES public.entities(id),
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                src TEXT NOT NULL,
                conf FLOAT NOT NULL DEFAULT 1.0,
                last_seen TIMESTAMPTZ,
                observed_at TIMESTAMPTZ,
                metadata JSONB,
                weight INT,
                verified BOOL NOT NULL DEFAULT false,
                "primary" BOOL,
                validity TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await pool.execute(
            """
            CREATE UNIQUE INDEX uq_reconciliation_entity_facts_active
            ON relationship.entity_facts (subject, predicate, object)
            WHERE validity = 'active'
            """
        )
        await pool.execute(
            """
            CREATE TABLE relationship.facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id UUID,
                object_entity_id UUID,
                predicate TEXT NOT NULL,
                content TEXT,
                source_butler TEXT,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                observed_at TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                valid_at TIMESTAMPTZ,
                supersedes_id UUID,
                scope TEXT NOT NULL DEFAULT 'relationship',
                validity TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await pool.execute(CONTACT_ENTITY_MAP.ddl(schema="relationship"))
        await pool.execute(
            """
            CREATE TABLE relationship.merge_reviews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_a UUID NOT NULL REFERENCES public.entities(id),
                entity_b UUID NOT NULL REFERENCES public.entities(id),
                shared_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
                divergent_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
                outcome TEXT NOT NULL,
                reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await pool.execute(PENDING_ACTIONS.ddl(schema="relationship"))
        await pool.execute(
            """
            CREATE TABLE reconciliation_test.arbitrary_entity_reference (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                arbitrary_owner UUID NOT NULL REFERENCES public.entities(id)
            )
            """
        )
        # Chronicler intentionally carries entity anchors without public.entities
        # FKs (chronicler_013 through chronicler_016).  These are explicit
        # protected references, not discoverable through pg_constraint.
        await pool.execute(
            """
            CREATE TABLE chronicler.episodes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id UUID
            )
            """
        )
        await pool.execute(
            """
            CREATE TABLE chronicler.episode_entities (
                episode_id UUID NOT NULL,
                entity_id UUID NOT NULL,
                PRIMARY KEY (episode_id, entity_id)
            )
            """
        )
        await pool.execute(
            """
            CREATE TABLE chronicler.point_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id UUID
            )
            """
        )
        yield pool


def _source_metadata(**extra: object) -> dict[str, object]:
    return {
        "unidentified": True,
        "source_channel": "whatsapp_user_client",
        "source_value": "structured-provider-value",
        **extra,
    }


def _fact_source_metadata(*, source_scope: str = "general") -> dict[str, object]:
    return {
        "unidentified": True,
        "source": "fact_storage",
        "source_butler": "general",
        "source_scope": source_scope,
    }


async def _entity(
    pool: asyncpg.Pool,
    canonical_name: str,
    *,
    entity_id: UUID | None = None,
    metadata: dict[str, object] | None = None,
    aliases: list[str] | None = None,
    roles: list[str] | None = None,
    entity_type: str = "person",
) -> UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.entities
            (id, canonical_name, entity_type, aliases, metadata, roles)
        VALUES (COALESCE($1, gen_random_uuid()), $2, $3, $4, $5, $6)
        RETURNING id
        """,
        entity_id,
        canonical_name,
        entity_type,
        aliases or [],
        metadata or {},
        roles or [],
    )


async def _phone_target(
    pool: asyncpg.Pool,
    phone: str,
    *,
    name: str = "Confirmed target",
    roles: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> UUID:
    target_id = await _entity(pool, name, roles=roles, metadata=metadata)
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src)
        VALUES ($1, 'has-phone', $2, 'literal', 'test')
        """,
        target_id,
        phone,
    )
    return target_id


async def _source_with_target(
    pool: asyncpg.Pool,
    digits: str,
    *,
    metadata: dict[str, object] | None = None,
    aliases: list[str] | None = None,
    roles: list[str] | None = None,
    source_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    source = await _entity(
        pool,
        f"{digits}@s.whatsapp.net",
        entity_id=source_id,
        metadata=metadata or _source_metadata(),
        aliases=aliases,
        roles=roles,
    )
    target = await _phone_target(pool, f"+{digits}", name=f"Target {digits[-4:]}")
    return source, target


def _count(plan, category: ReconciliationCategory) -> int:
    return plan.counts[category]


async def test_planner_enumerates_distinct_phone_and_lid_candidates(reconciliation_pool) -> None:
    """REQ-entity-identity-002: exact matches plan; ambiguity never picks row one."""
    pool = reconciliation_pool
    unique_source, unique_target = await _source_with_target(pool, "6591234567")

    unmatched_source = await _entity(
        pool,
        "6599999999@s.whatsapp.net",
        metadata=_fact_source_metadata(source_scope="global"),
    )

    ambiguous_source = await _entity(
        pool,
        "6598765432:17@s.whatsapp.net",
        metadata=_source_metadata(),
    )
    await _phone_target(pool, "+65 9876 5432", name="Ambiguous A")
    await _phone_target(pool, "98765432", name="Ambiguous B")

    mapped_source = await _entity(pool, "123456:9@lid", metadata=_source_metadata())
    await pool.execute(
        "INSERT INTO public.whatsmeow_lid_map (lid, pn) VALUES ('123456', '6588887777')"
    )
    mapped_target = await _phone_target(pool, "+65 8888 7777", name="Mapped target")

    unmapped_source = await _entity(pool, "654321@lid", metadata=_source_metadata())
    invalid_source = await _entity(pool, "123456@g.us", metadata=_source_metadata())

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert {(pair.source_entity_id, pair.target_entity_id) for pair in plan.pairs} == {
        (unique_source, unique_target),
        (mapped_source, mapped_target),
    }
    assert _count(plan, ReconciliationCategory.UNIQUE_EMPTY_SHELL) == 2
    assert _count(plan, ReconciliationCategory.UNMATCHED) == 2
    assert _count(plan, ReconciliationCategory.AMBIGUOUS) == 1
    assert _count(plan, ReconciliationCategory.INVALID_IDENTIFIER) == 1
    assert unmatched_source not in {pair.source_entity_id for pair in plan.pairs}
    assert ambiguous_source not in {pair.source_entity_id for pair in plan.pairs}
    assert unmapped_source not in {pair.source_entity_id for pair in plan.pairs}
    assert invalid_source not in {pair.source_entity_id for pair in plan.pairs}
    assert tuple(plan.pairs) == tuple(
        sorted(
            plan.pairs, key=lambda pair: (str(pair.source_entity_id), str(pair.target_entity_id))
        )
    )
    assert len(plan.digest) == 64
    assert set(plan.counts) == set(ReconciliationCategory)


async def test_identifier_parser_does_not_trim_surrounding_whitespace(
    reconciliation_pool,
) -> None:
    """Planner parsing stays byte-for-byte aligned with the canonical resolver."""
    pool = reconciliation_pool
    source = await _entity(
        pool,
        " 6591234567@s.whatsapp.net ",
        metadata=_source_metadata(),
    )
    await _phone_target(pool, "+65 9123 4567")

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.INVALID_IDENTIFIER) == 1
    assert source not in {pair.source_entity_id for pair in plan.pairs}


async def test_short_jid_phone_matches_exact_only_not_by_suffix(reconciliation_pool) -> None:
    """The resolver's eight-digit floor applies to the JID side of bounded matching."""
    pool = reconciliation_pool
    suffix_source = await _entity(
        pool,
        "1234567@s.whatsapp.net",
        metadata=_source_metadata(),
    )
    await _phone_target(pool, "+65 1234567", name="Longer suffix-only target")

    exact_source = await _entity(
        pool,
        "7654321@s.whatsapp.net",
        metadata=_source_metadata(),
    )
    exact_target = await _phone_target(pool, "7654321", name="Short exact target")

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert [(pair.source_entity_id, pair.target_entity_id) for pair in plan.pairs] == [
        (exact_source, exact_target)
    ]
    assert _count(plan, ReconciliationCategory.UNMATCHED) == 1
    assert suffix_source not in {pair.source_entity_id for pair in plan.pairs}


async def test_lid_mapping_does_not_trim_phone_whitespace(reconciliation_pool) -> None:
    """Mapped LID phone validation preserves the connector/resolver byte shape."""
    pool = reconciliation_pool
    source = await _entity(pool, "123456@lid", metadata=_source_metadata())
    await pool.execute(
        "INSERT INTO public.whatsmeow_lid_map (lid, pn) VALUES ('123456', ' 6591234567 ')"
    )
    await _phone_target(pool, "+65 9123 4567")

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.UNMATCHED) == 1
    assert source not in {pair.source_entity_id for pair in plan.pairs}


@pytest.mark.parametrize("protected_role", ["owner", "system"])
async def test_owner_or_system_target_is_never_planned(
    reconciliation_pool, protected_role: str
) -> None:
    pool = reconciliation_pool
    source = await _entity(
        pool,
        "6591112222@s.whatsapp.net",
        metadata=_source_metadata(),
    )
    await _phone_target(pool, "+65 9111 2222", roles=[protected_role])

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.OWNER_OR_SYSTEM_TARGET) == 1
    assert source not in {pair.source_entity_id for pair in plan.pairs}


async def test_every_shell_and_reference_class_is_excluded(reconciliation_pool) -> None:
    """Aliases, roles, metadata, semantic refs, text refs, and catalog FKs all block."""
    pool = reconciliation_pool
    sources: list[UUID] = []

    source, _ = await _source_with_target(pool, "6591000001", aliases=["user alias"])
    sources.append(source)

    source, _ = await _source_with_target(pool, "6591000002", roles=["friend"])
    sources.append(source)

    source, _ = await _source_with_target(
        pool,
        "6591000003",
        metadata=_source_metadata(unexpected="protected"),
    )
    sources.append(source)

    subject_source, _ = await _source_with_target(pool, "6591000004")
    subject_object = await _entity(pool, "Fact object")
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src)
        VALUES ($1, 'knows', $2, 'entity', 'test')
        """,
        subject_source,
        str(subject_object),
    )
    sources.append(subject_source)

    text_object_source, _ = await _source_with_target(pool, "6591000005")
    text_subject = await _entity(pool, "Text fact subject")
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src)
        VALUES ($1, 'knows', $2, 'entity', 'test')
        """,
        text_subject,
        str(text_object_source),
    )
    sources.append(text_object_source)

    memory_subject_source, _ = await _source_with_target(pool, "6591000006")
    await pool.execute(
        "INSERT INTO relationship.facts (entity_id, predicate) VALUES ($1, 'contact_note')",
        memory_subject_source,
    )
    sources.append(memory_subject_source)

    memory_object_source, _ = await _source_with_target(pool, "6591000007")
    await pool.execute(
        """
        INSERT INTO relationship.facts (entity_id, object_entity_id, predicate)
        VALUES ($1, $2, 'knows')
        """,
        await _entity(pool, "Memory fact subject"),
        memory_object_source,
    )
    sources.append(memory_object_source)

    contact_source, _ = await _source_with_target(pool, "6591000008")
    await pool.execute(
        "INSERT INTO relationship.contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        uuid4(),
        contact_source,
    )
    sources.append(contact_source)

    catalog_source, _ = await _source_with_target(pool, "6591000009")
    await pool.execute(
        """
        INSERT INTO reconciliation_test.arbitrary_entity_reference (arbitrary_owner)
        VALUES ($1)
        """,
        catalog_source,
    )
    sources.append(catalog_source)

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.REFERENCED_SOURCE) == len(sources)


@pytest.mark.parametrize(
    ("table_name", "insert_sql"),
    [
        (
            "episodes",
            "INSERT INTO chronicler.episodes (entity_id, id) VALUES ($1, $2)",
        ),
        (
            "episode_entities",
            "INSERT INTO chronicler.episode_entities (episode_id, entity_id) VALUES ($2, $1)",
        ),
        (
            "point_events",
            "INSERT INTO chronicler.point_events (entity_id, id) VALUES ($1, $2)",
        ),
    ],
)
async def test_chronicler_no_fk_entity_references_are_protected(
    reconciliation_pool,
    table_name: str,
    insert_sql: str,
) -> None:
    pool = reconciliation_pool
    source, _target = await _source_with_target(pool, "6591500001")
    await pool.execute(insert_sql, source, uuid4())

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs, table_name
    assert _count(plan, ReconciliationCategory.REFERENCED_SOURCE) == 1


async def test_memory_catalog_no_fk_entity_reference_is_protected(reconciliation_pool) -> None:
    pool = reconciliation_pool
    source, _target = await _source_with_target(pool, "6591750001")
    await pool.execute(
        """
        INSERT INTO public.memory_catalog
            (source_schema, source_table, source_id, entity_id)
        VALUES ('general', 'facts', $1, $2)
        """,
        uuid4(),
        source,
    )

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.REFERENCED_SOURCE) == 1


async def test_exact_pair_decisions_are_preserved_without_invented_columns(
    reconciliation_pool,
) -> None:
    """The established merge_reviews/pending_actions exact-pair contract is authoritative."""
    pool = reconciliation_pool
    decided_pairs: list[tuple[UUID, UUID]] = []

    review_source, review_target = await _source_with_target(pool, "6592000001")
    await pool.execute(
        """
        INSERT INTO relationship.merge_reviews (entity_a, entity_b, outcome)
        VALUES ($1, $2, 'dismissed')
        """,
        review_target,
        review_source,
    )
    decided_pairs.append((review_source, review_target))

    for offset, status in enumerate(("pending", "approved", "rejected", "abandoned"), start=2):
        source, target = await _source_with_target(pool, f"659200000{offset}")
        await pool.execute(
            """
            INSERT INTO relationship.pending_actions (tool_name, tool_args, status)
            VALUES ('memory_entity_merge', $1, $2)
            """,
            {"source_entity_id": str(source), "target_entity_id": str(target)},
            status,
        )
        decided_pairs.append((source, target))

    expired_source, expired_target = await _source_with_target(pool, "6592000006")
    await pool.execute(
        """
        INSERT INTO relationship.pending_actions (tool_name, tool_args, status)
        VALUES ('entity_merge', $1, 'expired')
        """,
        {
            "source_entity_id": str(expired_source),
            "target_entity_id": str(expired_target),
        },
    )

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert _count(plan, ReconciliationCategory.EXISTING_REVIEW_DECISION) == len(decided_pairs)
    assert [(pair.source_entity_id, pair.target_entity_id) for pair in plan.pairs] == [
        (expired_source, expired_target)
    ]
    assert plan.pairs[0].review_state == "none"


async def test_double_encoded_pending_decision_is_excluded(reconciliation_pool) -> None:
    """REQ-entity-identity-002: historical JSON strings preserve an exact rejection."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6592250001")
    await pool.execute(
        """
        INSERT INTO relationship.pending_actions (tool_name, tool_args, status)
        VALUES ('memory_entity_merge', $1, 'rejected')
        """,
        json.dumps(
            {
                "source_entity_id": str(source),
                "target_entity_id": str(target),
            }
        ),
    )

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.EXISTING_REVIEW_DECISION) == 1


async def test_malformed_pending_decision_fails_planner_closed(reconciliation_pool) -> None:
    """REQ-entity-identity-002: malformed historical decisions never leave a pair eligible."""
    pool = reconciliation_pool
    await _source_with_target(pool, "6592250002")
    await pool.execute(
        """
        INSERT INTO relationship.pending_actions (tool_name, tool_args, status)
        VALUES ('entity_merge', $1, 'abandoned')
        """,
        "not-json",
    )

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.EXISTING_REVIEW_DECISION) == 1


async def test_malformed_pending_decision_fails_locked_guard_closed(
    reconciliation_pool,
) -> None:
    """REQ-entity-identity-002: locked revalidation treats malformed history as drift."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6592250003")
    plan = await build_whatsapp_reconciliation_plan(pool)
    expected = plan.pairs[0]
    await pool.execute(
        """
        INSERT INTO relationship.pending_actions (tool_name, tool_args, status)
        VALUES ('entity_merge', $1, 'pending')
        """,
        "not-json",
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, canonical_name, entity_type, aliases, metadata, roles, updated_at
                FROM public.entities
                WHERE id = ANY($1::uuid[])
                ORDER BY id
                FOR UPDATE
                """,
                [source, target],
            )
            locked = {row["id"]: row for row in rows}
            with pytest.raises(LockedGuardRejected, match="^plan_drift$"):
                await validate_empty_shell_locked(
                    conn,
                    LockedEntityPair(source=locked[source], target=locked[target]),
                    expected=expected,
                )


async def test_review_reference_to_a_different_pair_protects_the_source(
    reconciliation_pool,
) -> None:
    pool = reconciliation_pool
    source, _target = await _source_with_target(pool, "6592500001")
    different_target = await _entity(pool, "Previously reviewed target")
    await pool.execute(
        """
        INSERT INTO relationship.merge_reviews (entity_a, entity_b, outcome)
        VALUES ($1, $2, 'dismissed')
        """,
        source,
        different_target,
    )

    plan = await build_whatsapp_reconciliation_plan(pool)

    assert not plan.pairs
    assert _count(plan, ReconciliationCategory.REFERENCED_SOURCE) == 1


async def test_digest_is_stable_and_changes_with_pair_state(reconciliation_pool) -> None:
    """REQ-entity-identity-002: stale plan digests authorize zero mutations."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6593000001")

    first = await build_whatsapp_reconciliation_plan(pool)
    second = await build_whatsapp_reconciliation_plan(pool)
    assert first.digest == second.digest
    assert first.pairs == second.pairs

    await pool.execute(
        "UPDATE public.entities SET updated_at = updated_at + interval '1 second' WHERE id = $1",
        target,
    )
    drifted = await build_whatsapp_reconciliation_plan(pool)
    assert drifted.digest != first.digest

    with pytest.raises(PlanDigestMismatch, match="^plan_digest_mismatch$"):
        await apply_whatsapp_reconciliation(pool, authorized_digest=first.digest)

    source_metadata = await pool.fetchval(
        "SELECT metadata FROM public.entities WHERE id = $1", source
    )
    assert "merged_into" not in source_metadata
    assert await pool.fetchval("SELECT count(*) FROM relationship.merge_reviews") == 0


async def test_apply_revalidates_drift_under_lock_before_writing(
    reconciliation_pool, monkeypatch
) -> None:
    pool = reconciliation_pool
    source, _target = await _source_with_target(pool, "6594000001")
    plan = await build_whatsapp_reconciliation_plan(pool)
    original_merge = merge_entity_pair

    async def drift_then_merge(*args, **kwargs):
        await pool.execute(
            "UPDATE public.entities SET aliases = ARRAY['late alias'] WHERE id = $1", source
        )
        return await original_merge(*args, **kwargs)

    monkeypatch.setattr(reconciliation, "merge_entity_pair", drift_then_merge)

    with pytest.raises(LockedGuardRejected, match="^plan_drift$"):
        await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

    source_metadata = await pool.fetchval(
        "SELECT metadata FROM public.entities WHERE id = $1", source
    )
    assert "merged_into" not in source_metadata
    assert await pool.fetchval("SELECT count(*) FROM relationship.merge_reviews") == 0


async def test_locked_guard_serializes_new_decisions_and_protected_references(
    reconciliation_pool,
) -> None:
    """Writes that could invalidate the guard cannot cross its transaction window."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6594500001")
    plan = await build_whatsapp_reconciliation_plan(pool)
    expected = plan.pairs[0]
    future_candidate = await _entity(pool, "Future candidate")
    fact_subject = await _entity(pool, "Future text-object subject")

    async with pool.acquire() as guard_conn:
        async with guard_conn.transaction():
            rows = await guard_conn.fetch(
                """
                SELECT id, canonical_name, entity_type, aliases, metadata, roles, updated_at
                FROM public.entities
                WHERE id = ANY($1::uuid[])
                ORDER BY id
                FOR UPDATE
                """,
                [source, target],
            )
            locked = {row["id"]: row for row in rows}
            await validate_empty_shell_locked(
                guard_conn,
                LockedEntityPair(source=locked[source], target=locked[target]),
                expected=expected,
            )

            async with pool.acquire() as concurrent:
                await concurrent.execute("SET statement_timeout = '100ms'")
                blocked_writes = (
                    (
                        """
                        INSERT INTO relationship.entity_facts
                            (subject, predicate, object, object_kind, src)
                        VALUES ($1, 'has-phone', '+65 9450 0001', 'literal', 'test')
                        """,
                        future_candidate,
                    ),
                    (
                        """
                        INSERT INTO relationship.entity_facts
                            (subject, predicate, object, object_kind, src)
                        VALUES ($1, 'knows', $2, 'entity', 'test')
                        """,
                        fact_subject,
                        str(source),
                    ),
                    (
                        """
                        INSERT INTO relationship.pending_actions (tool_name, tool_args, status)
                        VALUES ('memory_entity_merge', $1, 'pending')
                        """,
                        {
                            "source_entity_id": str(source),
                            "target_entity_id": str(target),
                        },
                    ),
                    (
                        "INSERT INTO chronicler.point_events (entity_id) VALUES ($1)",
                        source,
                    ),
                    (
                        """
                        INSERT INTO public.memory_catalog
                            (source_schema, source_table, source_id, entity_id)
                        VALUES ('general', 'facts', $1, $2)
                        """,
                        uuid4(),
                        source,
                    ),
                    (
                        """
                        INSERT INTO reconciliation_test.arbitrary_entity_reference
                            (arbitrary_owner)
                        VALUES ($1)
                        """,
                        source,
                    ),
                )
                for sql, *args in blocked_writes:
                    with pytest.raises(asyncpg.QueryCanceledError):
                        await concurrent.execute(sql, *args)
                await concurrent.execute("SET statement_timeout = 0")

    assert await pool.fetchval("SELECT count(*) FROM relationship.pending_actions") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.memory_catalog") == 0
    assert (
        await pool.fetchval("SELECT count(*) FROM reconciliation_test.arbitrary_entity_reference")
        == 0
    )


async def test_apply_fences_third_candidate_eligibility_without_mutation(
    reconciliation_pool,
) -> None:
    """REQ-entity-identity-002: a concurrent third candidate makes apply fail closed."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6594620001")
    third = await _entity(
        pool,
        "Candidate under review",
        metadata={"unidentified": True},
    )
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src)
        VALUES ($1, 'has-phone', '+65 9462 0001', 'literal', 'test')
        """,
        third,
    )
    plan = await build_whatsapp_reconciliation_plan(pool)
    assert [(pair.source_entity_id, pair.target_entity_id) for pair in plan.pairs] == [
        (source, target)
    ]

    async with pool.acquire() as writer:
        transaction = writer.transaction()
        await transaction.start()
        try:
            await writer.execute(
                "UPDATE public.entities SET metadata = '{}'::jsonb WHERE id = $1",
                third,
            )

            async with asyncio.timeout(2):
                with pytest.raises(LockedGuardRejected, match="^plan_drift$"):
                    await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

            assert (
                await pool.fetchval(
                    "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1",
                    source,
                )
                is None
            )
            assert await pool.fetchval("SELECT count(*) FROM relationship.merge_reviews") == 0
        finally:
            await transaction.rollback()


async def test_writer_before_apply_yields_content_blind_drift_without_mutation(
    reconciliation_pool,
) -> None:
    """A pre-existing RowExclusive writer makes NOWAIT fail closed, never deadlock."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6594750001")
    plan = await build_whatsapp_reconciliation_plan(pool)

    async with pool.acquire() as writer:
        transaction = writer.transaction()
        await transaction.start()
        try:
            await writer.execute(
                """
                INSERT INTO public.memory_catalog
                    (source_schema, source_table, source_id, entity_id)
                VALUES ('general', 'facts', $1, $2)
                """,
                uuid4(),
                source,
            )

            async with asyncio.timeout(2):
                with pytest.raises(LockedGuardRejected, match="^plan_drift$") as caught:
                    await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

            assert str(source) not in str(caught.value)
            assert str(target) not in str(caught.value)
            assert await pool.fetchval("SELECT count(*) FROM relationship.merge_reviews") == 0
            source_metadata = await pool.fetchval(
                "SELECT metadata FROM public.entities WHERE id = $1",
                source,
            )
            assert "merged_into" not in source_metadata
        finally:
            await transaction.rollback()


async def test_apply_audits_tombstone_and_removes_pair_from_fresh_plan(
    reconciliation_pool,
) -> None:
    """REQ-entity-identity-002: apply is audited and removes the reconciled shell."""
    pool = reconciliation_pool
    source, target = await _source_with_target(pool, "6595000001")
    plan = await build_whatsapp_reconciliation_plan(pool)

    report = await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

    assert report.mode == "apply"
    assert report.planned == 1
    assert report.applied == 1
    assert report.plan_digest == plan.digest
    assert report.counts[ReconciliationCategory.UNIQUE_EMPTY_SHELL.value] == 1
    assert await pool.fetchval(
        "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1", source
    ) == str(target)
    assert (
        await pool.fetchval(
            """
        SELECT count(*) FROM public.entities
        WHERE id = $1
          AND metadata ->> 'merged_into' IS NULL
          AND metadata ->> 'deleted_at' IS NULL
        """,
            target,
        )
        == 1
    )
    assert (
        await pool.fetchval(
            """
        SELECT count(*) FROM relationship.merge_reviews
        WHERE outcome = 'merged'
          AND ((entity_a = $1 AND entity_b = $2) OR (entity_a = $2 AND entity_b = $1))
        """,
            source,
            target,
        )
        == 1
    )

    after = await build_whatsapp_reconciliation_plan(pool)
    assert not after.pairs
    assert _count(after, ReconciliationCategory.UNIQUE_EMPTY_SHELL) == 0


async def test_postcondition_rejects_late_memory_catalog_entity_reference(
    reconciliation_pool,
    monkeypatch,
) -> None:
    """REQ-entity-identity-002: postconditions expose a committed-but-unsafe stop."""
    pool = reconciliation_pool
    source, _target = await _source_with_target(pool, "6595500001")
    plan = await build_whatsapp_reconciliation_plan(pool)
    original_merge = merge_entity_pair

    async def merge_then_add_catalog_reference(*args, **kwargs):
        result = await original_merge(*args, **kwargs)
        await pool.execute(
            """
            INSERT INTO public.memory_catalog
                (source_schema, source_table, source_id, entity_id)
            VALUES ('general', 'facts', $1, $2)
            """,
            uuid4(),
            source,
        )
        return result

    monkeypatch.setattr(reconciliation, "merge_entity_pair", merge_then_add_catalog_reference)

    with pytest.raises(Exception) as caught:
        await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

    assert type(caught.value).__name__ == "PartialApplyError"
    assert caught.value.applied == 1
    assert caught.value.planned == 1
    assert caught.value.stop_category == "postcondition_failed"
    assert caught.value.plan_digest == plan.digest
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.memory_catalog WHERE entity_id = $1",
            source,
        )
        == 1
    )


async def test_apply_stops_on_first_pair_failure(reconciliation_pool, monkeypatch) -> None:
    pool = reconciliation_pool
    first_source = UUID("00000000-0000-0000-0000-000000000101")
    second_source = UUID("00000000-0000-0000-0000-000000000102")
    await _source_with_target(pool, "6596000001", source_id=first_source)
    await _source_with_target(pool, "6596000002", source_id=second_source)
    plan = await build_whatsapp_reconciliation_plan(pool)
    calls = 0

    async def fail_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)

    monkeypatch.setattr(reconciliation, "merge_entity_pair", fail_first)

    with pytest.raises(LockedGuardRejected, match="^plan_drift$"):
        await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

    assert calls == 1
    assert (
        await pool.fetchval("SELECT count(*) FROM public.entities WHERE metadata ? 'merged_into'")
        == 0
    )


async def test_apply_reports_prior_commits_when_a_later_pair_fails(
    reconciliation_pool,
    monkeypatch,
) -> None:
    """REQ-entity-identity-002: a stopped apply never conceals committed tombstones."""
    pool = reconciliation_pool
    first_source = UUID("00000000-0000-0000-0000-000000000201")
    second_source = UUID("00000000-0000-0000-0000-000000000202")
    first_source, first_target = await _source_with_target(
        pool,
        "6596100001",
        source_id=first_source,
    )
    second_source, _second_target = await _source_with_target(
        pool,
        "6596100002",
        source_id=second_source,
    )
    plan = await build_whatsapp_reconciliation_plan(pool)
    original_merge = merge_entity_pair
    calls = 0

    async def merge_first_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await original_merge(*args, **kwargs)
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)

    monkeypatch.setattr(reconciliation, "merge_entity_pair", merge_first_then_fail)

    with pytest.raises(Exception) as caught:
        await apply_whatsapp_reconciliation(pool, authorized_digest=plan.digest)

    assert type(caught.value).__name__ == "PartialApplyError"
    assert caught.value.applied == 1
    assert caught.value.planned == 2
    assert caught.value.stop_category == "plan_drift"
    assert caught.value.plan_digest == plan.digest
    assert calls == 2
    assert await pool.fetchval(
        "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1",
        first_source,
    ) == str(first_target)
    assert (
        await pool.fetchval(
            "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1",
            second_source,
        )
        is None
    )
    assert await pool.fetchval("SELECT count(*) FROM relationship.merge_reviews") == 1


async def test_report_types_are_content_blind_by_construction() -> None:
    """The public DTOs expose aggregate fields, not display or evidence content."""
    assert set(reconciliation.ContentBlindReconciliationReport.__dataclass_fields__) == {
        "mode",
        "counts",
        "planned",
        "applied",
        "plan_digest",
    }
    assert datetime.now(UTC).tzinfo is UTC
