"""Contract tests for the audited entity merge service.

Spec anchor: ``REQ-entity-identity-002`` (guarded WhatsApp transitory
reconciliation), especially "Apply revalidates under lock" and "Successful
reconciliation remains auditable".
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from butlers.testing.schema_standins import CONTACT_ENTITY_MAP, ENTITY_PREDICATE_REGISTRY
from butlers.tools.relationship.entity_merge import (
    AuditEntityOrderError,
    LockedGuardRejected,
    SameEntityError,
    SourceEntityNotFoundError,
    SourceEntityTombstonedError,
    TargetEntityNotFoundError,
    TargetEntityTombstonedError,
    merge_entity_pair,
)


def _locked_row(entity_id: UUID, *, tombstoned: bool = False) -> dict:
    return {
        "id": entity_id,
        "canonical_name": "Opaque test entity",
        "entity_type": "person",
        "aliases": [],
        "metadata": {"merged_into": str(uuid4())} if tombstoned else {},
        "roles": [],
        "updated_at": None,
    }


def _mock_pool(lock_rows: list[dict]) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    conn = AsyncMock()

    async def _fetch(query, *args):
        return lock_rows if "FOR UPDATE" in query else []

    conn.fetch = AsyncMock(side_effect=_fetch)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchval = AsyncMock(side_effect=[0, 0, uuid4()])
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_acquire())
    return pool, conn, transaction


@pytest.mark.asyncio
async def test_locks_pair_deterministically_before_guard_and_rolls_back_rejection() -> None:
    """The reconciliation seam runs after complete locks and before any write."""
    source_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    target_id = UUID("00000000-0000-0000-0000-000000000001")
    source = _locked_row(source_id)
    target = _locked_row(target_id)
    pool, conn, transaction = _mock_pool([target, source])

    async def reject_locked_pair(guard_conn, pair) -> None:
        assert guard_conn is conn
        assert pair.source is source
        assert pair.target is target
        assert set(pair.source) == {
            "id",
            "canonical_name",
            "entity_type",
            "aliases",
            "metadata",
            "roles",
            "updated_at",
        }
        assert conn.execute.await_count == 0
        assert conn.fetchval.await_count == 0
        raise LockedGuardRejected("plan_drift")

    with pytest.raises(LockedGuardRejected, match="^plan_drift$"):
        await merge_entity_pair(
            pool,
            source_entity_id=source_id,
            target_entity_id=target_id,
            locked_guard=reject_locked_pair,
        )

    lock_call = conn.fetch.await_args_list[0]
    lock_sql = " ".join(lock_call.args[0].split())
    assert "ORDER BY id FOR UPDATE" in lock_sql
    assert lock_call.args[1] == [source_id, target_id]
    assert conn.execute.await_count == 0
    assert conn.fetchval.await_count == 0
    assert transaction.__aexit__.await_args.args[0] is LockedGuardRejected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "expected_error"),
    [
        ("source_missing", SourceEntityNotFoundError),
        ("target_missing", TargetEntityNotFoundError),
        ("source_tombstoned", SourceEntityTombstonedError),
        ("target_tombstoned", TargetEntityTombstonedError),
    ],
)
async def test_entity_state_errors_are_stable_and_identifier_blind(rows, expected_error) -> None:
    source_id = uuid4()
    target_id = uuid4()
    source = _locked_row(source_id, tombstoned=rows == "source_tombstoned")
    target = _locked_row(target_id, tombstoned=rows == "target_tombstoned")
    lock_rows = {
        "source_missing": [target],
        "target_missing": [source],
        "source_tombstoned": [source, target],
        "target_tombstoned": [source, target],
    }[rows]
    pool, _, _ = _mock_pool(lock_rows)

    with pytest.raises(expected_error) as caught:
        await merge_entity_pair(
            pool,
            source_entity_id=source_id,
            target_entity_id=target_id,
        )

    message = str(caught.value)
    assert str(source_id) not in message
    assert str(target_id) not in message
    assert message == rows


@pytest.mark.asyncio
async def test_same_entity_error_is_identifier_blind_and_precedes_database_access() -> None:
    entity_id = uuid4()
    pool = MagicMock()

    with pytest.raises(SameEntityError, match="^same_entity$") as caught:
        await merge_entity_pair(
            pool,
            source_entity_id=entity_id,
            target_entity_id=entity_id,
        )

    assert str(entity_id) not in str(caught.value)
    pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_empty_explicit_audit_order_fails_before_database_access() -> None:
    source_id = uuid4()
    target_id = uuid4()
    pool = MagicMock()

    with pytest.raises(AuditEntityOrderError, match="^audit_entity_order_mismatch$"):
        await merge_entity_pair(
            pool,
            source_entity_id=source_id,
            target_entity_id=target_id,
            _audit_entity_order=(),
        )

    pool.acquire.assert_not_called()


@pytest.fixture
async def merge_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=8) as pool:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await pool.execute("""
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
        """)
        await pool.execute(ENTITY_PREDICATE_REGISTRY.ddl(schema="relationship"))
        await pool.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, cardinality)
            VALUES
                ('has-email', 'contact', 'literal', 'multi'),
                ('knows', 'relational', 'entity', 'multi')
        """)
        await pool.execute("""
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
        """)
        await pool.execute("""
            CREATE UNIQUE INDEX uq_ef_spo_active
            ON relationship.entity_facts (subject, predicate, object)
            WHERE validity = 'active'
        """)
        await pool.execute("""
            CREATE TABLE facts (
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
        """)
        await pool.execute(CONTACT_ENTITY_MAP.ddl())
        await pool.execute("""
            CREATE TABLE relationship.merge_reviews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_a UUID NOT NULL REFERENCES public.entities(id),
                entity_b UUID NOT NULL REFERENCES public.entities(id),
                shared_facts JSONB NOT NULL,
                divergent_facts JSONB NOT NULL,
                outcome TEXT NOT NULL,
                reviewed_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield pool


async def _insert_entity(pool, name: str) -> UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name) VALUES ($1) RETURNING id", name
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_conflicts_rewire_tombstone_and_audit_commit_atomically(merge_pool) -> None:
    """Existing subject/object conflict semantics survive the extraction."""
    pool = merge_pool
    target_id = await _insert_entity(pool, "Target")
    source_id = await _insert_entity(pool, "Source")
    other_id = await _insert_entity(pool, "Other")
    second_other_id = await _insert_entity(pool, "Second other")

    await pool.executemany(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src)
        VALUES ($1, $2, $3, $4, 'test')
        """,
        [
            (target_id, "has-email", "shared@example.test", "literal"),
            (source_id, "has-email", "shared@example.test", "literal"),
            (source_id, "has-email", "source-only@example.test", "literal"),
            (other_id, "knows", str(target_id), "entity"),
            (other_id, "knows", str(source_id), "entity"),
            (second_other_id, "knows", str(source_id), "entity"),
        ],
    )

    result = await merge_entity_pair(
        pool,
        source_entity_id=source_id,
        target_entity_id=target_id,
    )

    assert result.kept_entity_id == target_id
    assert result.tombstoned_entity_id == source_id
    assert result.subject_facts_rewired == 1
    assert result.object_facts_rewired == 1

    source_metadata = await pool.fetchval(
        "SELECT metadata FROM public.entities WHERE id = $1", source_id
    )
    assert source_metadata["merged_into"] == str(target_id)
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts "
            "WHERE validity = 'active' AND (subject = $1 OR object = $2)",
            source_id,
            str(source_id),
        )
        == 0
    )
    review = await pool.fetchrow(
        """
        SELECT id, entity_a, entity_b, outcome
        FROM relationship.merge_reviews
        WHERE id = $1
        """,
        result.review_id,
    )
    assert dict(review) == {
        "id": result.review_id,
        "entity_a": target_id,
        "entity_b": source_id,
        "outcome": "merged",
    }


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_explicit_audit_order_preserves_endpoint_entity_a_and_b(merge_pool) -> None:
    """The endpoint may preserve request A/B order when B is the survivor."""
    pool = merge_pool
    source_id = await _insert_entity(pool, "Request entity A")
    target_id = await _insert_entity(pool, "Request entity B")

    result = await merge_entity_pair(
        pool,
        source_entity_id=source_id,
        target_entity_id=target_id,
        _audit_entity_order=(source_id, target_id),
    )

    review = await pool.fetchrow(
        "SELECT entity_a, entity_b FROM relationship.merge_reviews WHERE id = $1",
        result.review_id,
    )
    assert tuple(review.values()) == (source_id, target_id)
