"""Tests for relationship_assert_fact() — central writer for relationship.facts.

Covers:
  - Insert new fact (inserted outcome)
  - Idempotent re-insert with same provenance (unchanged outcome)
  - Supersession when provenance (src/conf/verified/last_seen) differs
  - Predicate validation: unknown predicate raises ValueError
  - Provenance enforcement: src, conf, verified, last_seen stored correctly
  - Owner carve-out: owner-entity subject emits pending_action, NOT fact
  - Transaction safety: works with caller-supplied conn (no deadlock)

Issue: bu-jwllb
Parent epic: bu-uhjxr (entity-redesign)
Spec anchor: openspec/changes/relationship-tabs-to-entities/specs/relationship-facts/spec.md
             §"Requirement: Central writer — relationship_assert_fact()"
             Amendment 14 (dual-write reconciliation contract)
             RFC 0017 §2.3 (owner carve-out)
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    relationship_assert_fact,
)

# ---------------------------------------------------------------------------
# Test markers
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Known predicates from rel_014 seed data
# ---------------------------------------------------------------------------

_PRED_HAS_EMAIL = "has-email"
_PRED_KNOWS = "knows"
_UNKNOWN_PRED = "has-feet"  # not in predicate_registry


# ---------------------------------------------------------------------------
# Pool fixture — provisions relationship schema + facts + predicate_registry
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh DB with relationship.facts and relationship.predicate_registry."""
    async with provisioned_postgres_pool() as p:
        # 1. public.entities (FK target for relationship.facts.subject)
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

        # 2. relationship schema
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")

        # 3. relationship.predicate_registry
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.predicate_registry (
                predicate   TEXT        NOT NULL PRIMARY KEY,
                kind        TEXT        NOT NULL,
                object_kind TEXT        NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Seed the predicates used in tests
        await p.execute("""
            INSERT INTO relationship.predicate_registry (predicate, kind, object_kind, description)
            VALUES
                ('has-email',  'contact',   'literal', 'Email address for the entity.'),
                ('has-phone',  'contact',   'literal', 'Phone number for the entity.'),
                ('has-handle', 'contact',   'literal', 'Channel-scoped handle.'),
                ('knows',      'relational','entity',  'Generic acquaintance or social connection.'),
                ('friend-of',  'relational','entity',  'Close friendship.')
            ON CONFLICT (predicate) DO NOTHING
        """)

        # 4. relationship.facts
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0
                                CHECK (conf >= 0.0 AND conf <= 1.0),
                last_seen   TIMESTAMPTZ,
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
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rf_spo_active
                ON relationship.facts (subject, predicate, object)
                WHERE validity = 'active'
        """)

        # 5. pending_actions (for owner carve-out)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name   TEXT        NOT NULL,
                tool_args   JSONB       NOT NULL,
                agent_summary TEXT,
                session_id  UUID,
                status      VARCHAR     NOT NULL DEFAULT 'pending',
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at  TIMESTAMPTZ,
                decided_by  TEXT,
                decided_at  TIMESTAMPTZ,
                execution_result JSONB,
                approval_rule_id UUID
            )
        """)

        yield p


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def entity(pool: asyncpg.Pool) -> uuid.UUID:
    """Insert a regular (non-owner) entity and return its id."""
    eid = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Alice Foo', 'person', '{}')
        RETURNING id
        """
    )
    return eid


@pytest.fixture
async def owner_entity(pool: asyncpg.Pool) -> uuid.UUID:
    """Insert the owner entity (roles contains 'owner') and return its id."""
    eid = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Owner User', 'person', '{owner}')
        RETURNING id
        """
    )
    return eid


# ---------------------------------------------------------------------------
# Tests: Insert new fact
# ---------------------------------------------------------------------------


class TestInsertNewFact:
    async def test_insert_returns_inserted_outcome(self, pool, entity):
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="test",
        )
        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        assert result.action_id is None

    async def test_insert_stores_row_in_relationship_facts(self, pool, entity):
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="test",
        )
        row = await pool.fetchrow(
            "SELECT * FROM relationship.facts WHERE id = $1",
            result.fact_id,
        )
        assert row is not None
        assert row["subject"] == entity
        assert row["predicate"] == _PRED_HAS_EMAIL
        assert row["object"] == "alice@example.com"
        assert row["object_kind"] == "literal"
        assert row["src"] == "test"
        assert float(row["conf"]) == 1.0
        assert row["verified"] is False
        assert row["validity"] == "active"

    async def test_insert_stores_provenance_fields(self, pool, entity):
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="ingestion",
            conf=0.85,
            last_seen=ts,
            weight=3,
            verified=True,
            primary=True,
        )
        assert result.outcome == AssertOutcome.inserted
        row = await pool.fetchrow(
            "SELECT * FROM relationship.facts WHERE id = $1",
            result.fact_id,
        )
        assert row["src"] == "ingestion"
        assert abs(float(row["conf"]) - 0.85) < 1e-6
        assert row["last_seen"] == ts
        assert row["weight"] == 3
        assert row["verified"] is True
        assert row["primary"] is True

    async def test_insert_entity_predicate(self, pool, entity):
        """Relational predicates with object_kind='entity' are stored correctly."""
        other_entity_id = await pool.fetchval(
            """
            INSERT INTO public.entities (canonical_name, entity_type, roles)
            VALUES ('Bob Bar', 'person', '{}') RETURNING id
            """
        )
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_KNOWS,
            str(other_entity_id),
            src="test",
            object_kind="entity",
        )
        assert result.outcome == AssertOutcome.inserted
        row = await pool.fetchrow(
            "SELECT object_kind FROM relationship.facts WHERE id = $1",
            result.fact_id,
        )
        assert row["object_kind"] == "entity"


# ---------------------------------------------------------------------------
# Tests: Idempotency (unchanged)
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_same_call_twice_returns_unchanged(self, pool, entity):
        kwargs = dict(
            src="test",
            conf=1.0,
            verified=False,
            last_seen=None,
        )
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", **kwargs
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", **kwargs
        )
        assert r1.outcome == AssertOutcome.inserted
        assert r2.outcome == AssertOutcome.unchanged
        assert r2.fact_id == r1.fact_id

    async def test_unchanged_produces_exactly_one_active_row(self, pool, entity):
        for _ in range(3):
            await relationship_assert_fact(
                pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="test"
            )
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.facts
            WHERE subject = $1 AND predicate = $2 AND object = $3
              AND validity = 'active'
            """,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
        )
        assert count == 1


# ---------------------------------------------------------------------------
# Tests: Supersession
# ---------------------------------------------------------------------------


class TestSupersession:
    async def test_changed_src_triggers_supersession(self, pool, entity):
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="source-a"
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="source-b"
        )
        assert r1.outcome == AssertOutcome.inserted
        assert r2.outcome == AssertOutcome.superseded
        assert r2.fact_id != r1.fact_id

    async def test_superseded_row_has_validity_superseded(self, pool, entity):
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="a"
        )
        await relationship_assert_fact(pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="b")
        old_row = await pool.fetchrow(
            "SELECT validity FROM relationship.facts WHERE id = $1", r1.fact_id
        )
        assert old_row["validity"] == "superseded"

    async def test_only_one_active_row_after_supersession(self, pool, entity):
        await relationship_assert_fact(pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="a")
        await relationship_assert_fact(pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="b")
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.facts
            WHERE subject = $1 AND predicate = $2 AND object = $3
              AND validity = 'active'
            """,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
        )
        assert count == 1

    async def test_changed_conf_triggers_supersession(self, pool, entity):
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=1.0
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.7
        )
        assert r2.outcome == AssertOutcome.superseded

    async def test_changed_verified_triggers_supersession(self, pool, entity):
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", verified=False
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", verified=True
        )
        assert r2.outcome == AssertOutcome.superseded

    async def test_changed_last_seen_triggers_supersession(self, pool, entity):
        ts1 = datetime(2026, 1, 1, tzinfo=UTC)
        ts2 = datetime(2026, 6, 1, tzinfo=UTC)
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", last_seen=ts1
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", last_seen=ts2
        )
        assert r2.outcome == AssertOutcome.superseded


# ---------------------------------------------------------------------------
# Tests: Predicate validation
# ---------------------------------------------------------------------------


class TestPredicateValidation:
    async def test_unknown_predicate_raises_value_error(self, pool, entity):
        with pytest.raises(ValueError, match="Unknown predicate.*not registered"):
            await relationship_assert_fact(pool, entity, _UNKNOWN_PRED, "some-value", src="test")

    async def test_unknown_predicate_writes_no_row(self, pool, entity):
        try:
            await relationship_assert_fact(pool, entity, _UNKNOWN_PRED, "some-value", src="test")
        except ValueError:
            pass
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.facts WHERE predicate = $1",
            _UNKNOWN_PRED,
        )
        assert count == 0

    async def test_invalid_object_kind_raises_value_error(self, pool, entity):
        with pytest.raises(ValueError, match="Invalid object_kind"):
            await relationship_assert_fact(
                pool,
                entity,
                _PRED_HAS_EMAIL,
                "alice@example.com",
                src="test",
                object_kind="bad-kind",
            )

    async def test_conf_out_of_range_raises_value_error(self, pool, entity):
        with pytest.raises(ValueError, match="conf must be in"):
            await relationship_assert_fact(
                pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="test", conf=1.5
            )


# ---------------------------------------------------------------------------
# Tests: Owner carve-out (RFC 0017 §2.3)
# ---------------------------------------------------------------------------


class TestOwnerCarveOut:
    async def test_owner_subject_returns_pending_approval(self, pool, owner_entity):
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="test"
        )
        assert result.outcome == AssertOutcome.pending_approval
        assert result.fact_id is None
        assert result.action_id is not None

    async def test_owner_subject_writes_no_fact_row(self, pool, owner_entity):
        await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="test"
        )
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.facts
            WHERE subject = $1 AND validity = 'active'
            """,
            owner_entity,
        )
        assert count == 0

    async def test_owner_subject_writes_pending_action_row(self, pool, owner_entity):
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="test"
        )
        row = await pool.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1",
            result.action_id,
        )
        assert row is not None
        assert row["tool_name"] == "relationship_assert_fact"
        assert row["status"] == "pending"
        args = row["tool_args"]
        assert str(owner_entity) == args["subject"]
        assert args["predicate"] == _PRED_HAS_EMAIL
        assert args["object"] == "owner@example.com"

    async def test_non_owner_subject_writes_fact_directly(self, pool, entity):
        """Regression: non-owner entities MUST NOT trigger the carve-out."""
        result = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="test"
        )
        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        assert result.action_id is None

    async def test_owner_carve_out_repeated_calls_each_create_pending_action(
        self, pool, owner_entity
    ):
        """Each pending_approval call creates a new pending_action row."""
        r1 = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="a"
        )
        r2 = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="a"
        )
        assert r1.action_id != r2.action_id


# ---------------------------------------------------------------------------
# Tests: Transaction safety (caller-supplied conn)
# ---------------------------------------------------------------------------


class TestTransactionSafety:
    async def test_accepts_caller_conn_without_panic(self, pool, entity):
        """Passing conn= must not raise or deadlock."""
        async with pool.acquire() as conn:
            result = await relationship_assert_fact(
                pool,
                entity,
                _PRED_HAS_EMAIL,
                "alice@example.com",
                src="test",
                conn=conn,
            )
        assert result.outcome == AssertOutcome.inserted

    async def test_caller_conn_inside_transaction(self, pool, entity):
        """relationship_assert_fact must be safe inside an open transaction."""
        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await relationship_assert_fact(
                    pool,
                    entity,
                    _PRED_HAS_EMAIL,
                    "alice@example.com",
                    src="test",
                    conn=conn,
                )
        assert result.outcome == AssertOutcome.inserted
        row = await pool.fetchrow("SELECT id FROM relationship.facts WHERE id = $1", result.fact_id)
        assert row is not None

    async def test_caller_conn_idempotent(self, pool, entity):
        """Idempotency works when conn is supplied."""
        async with pool.acquire() as conn:
            r1 = await relationship_assert_fact(
                pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conn=conn
            )
            r2 = await relationship_assert_fact(
                pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conn=conn
            )
        assert r1.outcome == AssertOutcome.inserted
        assert r2.outcome == AssertOutcome.unchanged
        assert r1.fact_id == r2.fact_id

    async def test_pool_and_conn_paths_write_same_schema(self, pool, entity):
        """Both pool-path and conn-path write to relationship.facts."""
        # Pool path
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="pool-path"
        )
        # Mark old row as superseded so the conn path creates a new row.
        await pool.execute(
            "UPDATE relationship.facts SET validity='superseded' WHERE id=$1", r1.fact_id
        )
        # Conn path
        async with pool.acquire() as conn:
            r2 = await relationship_assert_fact(
                pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="conn-path", conn=conn
            )
        assert r2.outcome == AssertOutcome.inserted
        row = await pool.fetchrow("SELECT src FROM relationship.facts WHERE id = $1", r2.fact_id)
        assert row["src"] == "conn-path"


# ---------------------------------------------------------------------------
# Tests: as_dict() helper
# ---------------------------------------------------------------------------


class TestAssertResultDict:
    async def test_inserted_as_dict(self, pool, entity):
        result = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="test"
        )
        d = result.as_dict()
        assert d["outcome"] == "inserted"
        assert d["fact_id"] is not None
        assert d["action_id"] is None

    async def test_pending_approval_as_dict(self, pool, owner_entity):
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="test"
        )
        d = result.as_dict()
        assert d["outcome"] == "pending_approval"
        assert d["fact_id"] is None
        assert d["action_id"] is not None
