"""Tests for relationship_assert_fact() — central writer for relationship.entity_facts.

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
Spec anchor: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/relationship-facts/spec.md
             §"Requirement: Central writer — relationship_assert_fact()"
             Amendment 14 (dual-write reconciliation contract)
             RFC 0017 §2.3 (owner carve-out)
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.tools.relationship.relationship_assert_fact import (
    _PREDICATE_ALIAS_MAP,
    AssertOutcome,
    _insert_active_fact,
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
    """Fresh DB with relationship.entity_facts and relationship.entity_predicate_registry."""
    async with provisioned_postgres_pool() as p:
        # 1. public.entities (FK target for relationship.entity_facts.subject)
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

        # 3. relationship.entity_predicate_registry
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
                predicate   TEXT        NOT NULL PRIMARY KEY,
                kind        TEXT        NOT NULL,
                object_kind TEXT        NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Seed the predicates used in tests
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind, description)
            VALUES
                ('has-email',  'contact',   'literal', 'Email address for the entity.'),
                ('has-phone',  'contact',   'literal', 'Phone number for the entity.'),
                ('has-handle', 'contact',   'literal', 'Channel-scoped handle.'),
                ('knows',      'relational','entity',  'Generic acquaintance or social connection.'),
                ('friend-of',  'relational','entity',  'Close friendship.')
            ON CONFLICT (predicate) DO NOTHING
        """)

        # 4. relationship.entity_facts
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0
                                CHECK (conf >= 0.0 AND conf <= 1.0),
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
                approval_rule_id UUID,
                why         TEXT,
                evidence    JSONB       NOT NULL DEFAULT '[]'::jsonb
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

    async def test_insert_stores_row_in_entity_facts(self, pool, entity):
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="test",
        )
        row = await pool.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id = $1",
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
            "SELECT * FROM relationship.entity_facts WHERE id = $1",
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
            "SELECT object_kind FROM relationship.entity_facts WHERE id = $1",
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
            SELECT COUNT(*) FROM relationship.entity_facts
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
            "SELECT validity FROM relationship.entity_facts WHERE id = $1", r1.fact_id
        )
        assert old_row["validity"] == "superseded"

    async def test_only_one_active_row_after_supersession(self, pool, entity):
        await relationship_assert_fact(pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="a")
        await relationship_assert_fact(pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="b")
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.entity_facts
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
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE predicate = $1",
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
            SELECT COUNT(*) FROM relationship.entity_facts
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

    async def test_owner_carve_out_repeated_calls_dedup_same_action_id(self, pool, owner_entity):
        """Repeated calls with the same (subject, predicate, object) return the same
        pending_action row — dedup prevents duplicate approvals for the same identity
        triple (introduced in fix: dedup owner approvals + populate why/evidence).
        """
        r1 = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="a"
        )
        r2 = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="a"
        )
        assert r1.action_id == r2.action_id

    async def test_owner_carve_out_different_object_creates_new_action(self, pool, owner_entity):
        """Different object value must produce a distinct pending_action row."""
        r1 = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="a"
        )
        r2 = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "other@example.com", src="a"
        )
        assert r1.action_id != r2.action_id

    # ------------------------------------------------------------------
    # Self-identity exemption (bu-oluyt.4)
    # ------------------------------------------------------------------

    async def test_owner_self_source_writes_directly(self, pool, owner_entity):
        """Owner registering own handle with src='owner-self' bypasses pending_actions.

        Acceptance criterion: owner self-registering a telegram/email/phone handle
        writes the triple directly (no pending_actions) when src is a trusted
        owner-self source.
        """
        from butlers.tools.relationship.relationship_assert_fact import _OWNER_SELF_SOURCES

        assert "owner-self" in _OWNER_SELF_SOURCES

        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="owner-self"
        )

        # Direct write: inserted outcome, fact_id present, no action
        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        assert result.action_id is None

        # Fact row exists in entity_facts
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.entity_facts
            WHERE subject = $1 AND predicate = $2 AND object = $3 AND validity = 'active'
            """,
            owner_entity,
            _PRED_HAS_EMAIL,
            "owner@example.com",
        )
        assert count == 1

        # No pending_actions row was created
        pa_count = await pool.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE (tool_args->>'subject')::uuid = $1",
            owner_entity,
        )
        assert pa_count == 0

    async def test_owner_bootstrap_source_writes_directly(self, pool, owner_entity):
        """Owner handle seeded with src='owner-bootstrap' bypasses pending_actions.

        Daemon startup path uses 'owner-bootstrap' to seed identity facts on first
        boot — must write directly without requiring human approval.
        """
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "boot@example.com", src="owner-bootstrap"
        )

        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        assert result.action_id is None

        row = await pool.fetchrow(
            """
            SELECT src FROM relationship.entity_facts
            WHERE id = $1
            """,
            result.fact_id,
        )
        assert row is not None
        assert row["src"] == "owner-bootstrap"

    async def test_third_party_source_about_owner_still_parks(self, pool, owner_entity):
        """Third-party assertions about the owner still route to pending_actions.

        Acceptance criterion: src='relationship' (used by channel_add and
        reconciler jobs) does NOT get the self-identity exemption — owner writes
        from untrusted paths still require human approval.
        """
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="relationship"
        )

        assert result.outcome == AssertOutcome.pending_approval
        assert result.fact_id is None
        assert result.action_id is not None

        # No fact row written
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.entity_facts
            WHERE subject = $1 AND validity = 'active'
            """,
            owner_entity,
        )
        assert count == 0

    async def test_trusted_internal_source_about_owner_writes_directly(self, pool, owner_entity):
        """Owner writes from a trusted internal-derivation job auto-apply.

        interaction_sync / memory_curation / fact_retraction_curation derive facts
        solely from the owner's own stored data, so owner-entity writes from these
        sources bypass pending_actions (RFC 0017 §2.3 parks only UNtrusted sources).
        """
        from butlers.tools.relationship.relationship_assert_fact import (
            _TRUSTED_INTERNAL_SOURCES,
        )

        assert "interaction_sync" in _TRUSTED_INTERNAL_SOURCES

        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.com", src="interaction_sync"
        )

        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        assert result.action_id is None

        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM relationship.entity_facts
            WHERE subject = $1 AND validity = 'active'
            """,
            owner_entity,
        )
        assert count == 1

        pa_count = await pool.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE (tool_args->>'subject')::uuid = $1",
            owner_entity,
        )
        assert pa_count == 0


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
        row = await pool.fetchrow(
            "SELECT id FROM relationship.entity_facts WHERE id = $1", result.fact_id
        )
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
        """Both pool-path and conn-path write to relationship.entity_facts."""
        # Pool path
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="pool-path"
        )
        # Mark old row as superseded so the conn path creates a new row.
        await pool.execute(
            "UPDATE relationship.entity_facts SET validity='superseded' WHERE id=$1", r1.fact_id
        )
        # Conn path
        async with pool.acquire() as conn:
            r2 = await relationship_assert_fact(
                pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="conn-path", conn=conn
            )
        assert r2.outcome == AssertOutcome.inserted
        row = await pool.fetchrow(
            "SELECT src FROM relationship.entity_facts WHERE id = $1", r2.fact_id
        )
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


# ---------------------------------------------------------------------------
# Tests: observed_at stamping (entity v3, relationship-facts spec)
# ---------------------------------------------------------------------------


class TestObservedAt:
    """Central writer stamps observed_at; default now(), explicit honoured."""

    async def test_default_stamps_now(self, pool, entity):
        """Asserting without observed_at stamps the assertion time (~now)."""
        before = datetime.now(UTC)
        result = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="test"
        )
        after = datetime.now(UTC)
        observed_at = await pool.fetchval(
            "SELECT observed_at FROM relationship.entity_facts WHERE id = $1",
            result.fact_id,
        )
        assert observed_at is not None
        # Stamped to "now" — within the wall-clock window of the call.
        assert before <= observed_at <= after

    async def test_explicit_observed_at_is_honored(self, pool, entity):
        """A backdated import value is written verbatim, NOT overwritten by now()."""
        backdated = datetime(2024, 3, 1, 9, 30, 0, tzinfo=UTC)
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="import",
            observed_at=backdated,
        )
        observed_at = await pool.fetchval(
            "SELECT observed_at FROM relationship.entity_facts WHERE id = $1",
            result.fact_id,
        )
        assert observed_at == backdated

    async def test_supersession_preserves_superseded_row_observed_at(self, pool, entity):
        """On supersession each row carries its OWN observed_at; the old row keeps its."""
        old_observed = datetime(2024, 1, 1, tzinfo=UTC)
        new_observed = datetime(2026, 1, 1, tzinfo=UTC)

        r1 = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="source-a",
            observed_at=old_observed,
        )
        r2 = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.com",
            src="source-b",
            observed_at=new_observed,
        )
        assert r2.outcome == AssertOutcome.superseded
        assert r2.fact_id != r1.fact_id

        # Superseded (old) row keeps its own observed_at — NOT overwritten.
        old_observed_at = await pool.fetchval(
            "SELECT observed_at FROM relationship.entity_facts WHERE id = $1",
            r1.fact_id,
        )
        assert old_observed_at == old_observed

        # New active row carries the new observed_at.
        new_observed_at = await pool.fetchval(
            "SELECT observed_at FROM relationship.entity_facts WHERE id = $1",
            r2.fact_id,
        )
        assert new_observed_at == new_observed


# ---------------------------------------------------------------------------
# Tests: conf immutability (entity v3 — supersession only, never in-place)
# ---------------------------------------------------------------------------


class TestConfImmutability:
    """conf is immutable after write: a changed conf supersedes, never mutates.

    The DB-layer guarantee (this class) pairs with the source-scan guardrail in
    ``test_conf_immutability_guardrail.py``.
    """

    async def test_changed_conf_supersedes_not_mutates(self, pool, entity):
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.9
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.4
        )
        assert r2.outcome == AssertOutcome.superseded
        # New row inserted (different id), old row retained.
        assert r2.fact_id != r1.fact_id

    async def test_original_row_conf_unchanged_after_resupersede(self, pool, entity):
        """The superseded row's stored conf is byte-identical to its write-time value."""
        r1 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.9
        )
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.4
        )
        old_conf = await pool.fetchval(
            "SELECT conf FROM relationship.entity_facts WHERE id = $1", r1.fact_id
        )
        old_validity = await pool.fetchval(
            "SELECT validity FROM relationship.entity_facts WHERE id = $1", r1.fact_id
        )
        # conf on the prior row is unchanged; only validity flipped to superseded.
        assert abs(float(old_conf) - 0.9) < 1e-6
        assert old_validity == "superseded"

    async def test_new_active_row_carries_new_conf(self, pool, entity):
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.9
        )
        r2 = await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.com", src="x", conf=0.4
        )
        new_conf = await pool.fetchval(
            "SELECT conf FROM relationship.entity_facts WHERE id = $1", r2.fact_id
        )
        assert abs(float(new_conf) - 0.4) < 1e-6


# ---------------------------------------------------------------------------
# Tests: concurrent-writer race (bu-be16a)
# ---------------------------------------------------------------------------
#
# Spec (relationship-facts / lifecycle): no code path may execute an in-place
# UPDATE of conf on an existing ACTIVE row. Supersession is the only path, and
# superseded rows keep their own observed_at. The central writer's INSERT uses
# ON CONFLICT DO NOTHING + re-read/retry, so a concurrent collision NEVER
# overwrites conf/observed_at on the row that already holds the active slot.


def _bigger_pool(provisioned_postgres_pool):
    """Provision a pool with enough connections for concurrent writers."""
    return provisioned_postgres_pool(min_pool_size=2, max_pool_size=8)


async def _provision_schema(p: asyncpg.Pool) -> None:
    """Create the minimal relationship schema used by these tests.

    Mirrors the per-test ``pool`` fixture above so the concurrency tests can run
    on a higher-capacity pool.
    """
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
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await p.execute("""
        INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind, description)
        VALUES ('has-email', 'contact', 'literal', 'Email address for the entity.')
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
            conf        FLOAT       NOT NULL DEFAULT 1.0
                            CHECK (conf >= 0.0 AND conf <= 1.0),
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


class TestConcurrentWriterRace:
    """Two writers asserting the same triple with different conf must NOT mutate
    an existing active row in place — the collision routes through supersession,
    and every row keeps its own observed_at.
    """

    async def test_forced_race_supersedes_without_mutating_active_row(
        self, provisioned_postgres_pool
    ):
        """Deterministically force the read-then-insert race.

        Sequence (single asyncio task, interleaved by hand):
          1. Writer-A reads: no active row.
          2. A *competing* writer inserts the active row first (conf=0.9).
          3. Writer-A's INSERT ... ON CONFLICT DO NOTHING returns None (the slot
             is taken) — proving we do NOT overwrite the competitor's row.
          4. The full writer (relationship_assert_fact) then routes A's intent
             (conf=0.4) through supersession.

        Acceptance: exactly one superseded + one active row, BOTH keeping their
        original observed_at; the superseded row keeps conf=0.9, the active row
        carries conf=0.4.
        """
        async with _bigger_pool(provisioned_postgres_pool) as p:
            await _provision_schema(p)
            entity = await p.fetchval(
                "INSERT INTO public.entities (canonical_name, roles) "
                "VALUES ('Alice Foo', '{}') RETURNING id"
            )
            obs_a = datetime(2026, 1, 1, tzinfo=UTC)
            obs_b = datetime(2026, 2, 2, tzinfo=UTC)

            async with p.acquire() as conn:
                # 1. Writer-A reads: confirm no active row yet.
                pre = await conn.fetchrow(
                    "SELECT id FROM relationship.entity_facts "
                    "WHERE subject=$1 AND predicate=$2 AND object=$3 AND validity='active'",
                    entity,
                    _PRED_HAS_EMAIL,
                    "alice@example.com",
                )
                assert pre is None

                # 2. Competing writer wins the slot first (conf=0.9).
                competitor_id = await _insert_active_fact(
                    conn,
                    subject=entity,
                    predicate=_PRED_HAS_EMAIL,
                    object="alice@example.com",
                    object_kind="literal",
                    src="competitor",
                    conf=0.9,
                    last_seen=None,
                    observed_at=obs_a,
                    weight=None,
                    verified=False,
                    primary=None,
                )
                assert competitor_id is not None

                # 3. Writer-A's own DO NOTHING insert must NOT overwrite the slot.
                lost = await _insert_active_fact(
                    conn,
                    subject=entity,
                    predicate=_PRED_HAS_EMAIL,
                    object="alice@example.com",
                    object_kind="literal",
                    src="writer-a",
                    conf=0.4,
                    last_seen=None,
                    observed_at=obs_b,
                    weight=None,
                    verified=False,
                    primary=None,
                )
                assert lost is None  # DO NOTHING → no in-place mutation

            # 4. Now the full writer routes A's intent through supersession.
            result = await relationship_assert_fact(
                p,
                entity,
                _PRED_HAS_EMAIL,
                "alice@example.com",
                src="writer-a",
                conf=0.4,
                observed_at=obs_b,
            )
            assert result.outcome == AssertOutcome.superseded

            rows = await p.fetch(
                "SELECT id, conf, observed_at, validity FROM relationship.entity_facts "
                "WHERE subject=$1 AND predicate=$2 AND object=$3 ORDER BY created_at",
                entity,
                _PRED_HAS_EMAIL,
                "alice@example.com",
            )
            by_validity = {r["validity"]: r for r in rows}
            assert set(by_validity) == {"superseded", "active"}, rows
            assert len(rows) == 2

            # Superseded row: the competitor's, conf + observed_at untouched.
            superseded = by_validity["superseded"]
            assert superseded["id"] == competitor_id
            assert abs(float(superseded["conf"]) - 0.9) < 1e-6
            assert superseded["observed_at"] == obs_a

            # Active row: writer-A's, with its own conf + observed_at.
            active = by_validity["active"]
            assert abs(float(active["conf"]) - 0.4) < 1e-6
            assert active["observed_at"] == obs_b

    async def test_concurrent_double_assert_one_active_one_superseded(
        self, provisioned_postgres_pool
    ):
        """Two real concurrent writers (gather) on the same triple, different conf.

        The unique partial index on the active slot serialises the writers; the
        DO NOTHING + retry path resolves the collision via supersession. Final
        state: exactly one active + one superseded row, both retaining their
        original observed_at, regardless of which writer wins the slot first.
        """
        async with _bigger_pool(provisioned_postgres_pool) as p:
            await _provision_schema(p)
            entity = await p.fetchval(
                "INSERT INTO public.entities (canonical_name, roles) "
                "VALUES ('Alice Foo', '{}') RETURNING id"
            )
            obs_a = datetime(2025, 6, 1, tzinfo=UTC)
            obs_b = datetime(2025, 7, 1, tzinfo=UTC)

            async def writer(src: str, conf: float, observed_at: datetime):
                return await relationship_assert_fact(
                    p,
                    entity,
                    _PRED_HAS_EMAIL,
                    "alice@example.com",
                    src=src,
                    conf=conf,
                    observed_at=observed_at,
                )

            # Fire both writers concurrently.
            await asyncio.gather(
                writer("writer-a", 0.9, obs_a),
                writer("writer-b", 0.4, obs_b),
            )

            rows = await p.fetch(
                "SELECT conf, observed_at, validity FROM relationship.entity_facts "
                "WHERE subject=$1 AND predicate=$2 AND object=$3",
                entity,
                _PRED_HAS_EMAIL,
                "alice@example.com",
            )
            validities = sorted(r["validity"] for r in rows)
            assert validities == ["active", "superseded"], rows
            assert len(rows) == 2

            # Both observed_at values survive verbatim — neither writer's
            # observed_at was overwritten in place. (Order/winner is racy, but the
            # SET of observed_at values must be exactly {obs_a, obs_b}.)
            observed = {r["observed_at"] for r in rows}
            assert observed == {obs_a, obs_b}, observed

            # The active row's conf matches its observed_at's writer (no in-place
            # conf mutation could have crossed them).
            active = next(r for r in rows if r["validity"] == "active")
            expected_active_conf = 0.9 if active["observed_at"] == obs_a else 0.4
            assert abs(float(active["conf"]) - expected_active_conf) < 1e-6


# ---------------------------------------------------------------------------
# Fixture: pool seeded with relational predicates needed for alias tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool_with_relational_predicates(pool: asyncpg.Pool) -> asyncpg.Pool:
    """Extend the base pool fixture with relational predicates for alias resolution tests.

    Seeds:
    - Original relational predicates from rel_024 (works-at, member-of) and other
      alias targets from rel_014 that may not be in the base fixture.
    - Long-tail relational predicates from rel_026 (bu-kgh8g).
    """
    await pool.execute("""
        INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind, description)
        VALUES
            ('works-at',         'relational', 'entity', 'Employment relationship.'),
            ('child-of',         'relational', 'entity', 'Child-parent relationship.'),
            ('parent-of',        'relational', 'entity', 'Parent-child relationship.'),
            ('colleague-of',     'relational', 'entity', 'Colleague relationship.'),
            ('family-of',        'relational', 'entity', 'Family relationship.'),
            ('partner-of',       'relational', 'entity', 'Partnership or marriage relationship.'),
            ('member-of',        'relational', 'entity', 'Membership relationship.'),
            ('manages',          'relational', 'entity', 'Management role.'),
            ('managed-by',       'relational', 'entity', 'Inverse of manages.'),
            ('manages-property', 'relational', 'entity', 'Property management role.'),
            ('participant-of',   'relational', 'entity', 'Durable participation.'),
            ('invited-by',       'relational', 'entity', 'Referral or sponsorship.'),
            ('rental-agent',     'relational', 'entity', 'Rental agent role.'),
            ('rental-location',  'relational', 'entity', 'Rental relationship.')
        ON CONFLICT (predicate) DO NOTHING
    """)
    return pool


# ---------------------------------------------------------------------------
# Tests: predicate alias resolution (bu-i0pgi)
# ---------------------------------------------------------------------------


class TestPredicateAliasResolution:
    """Underscore alias names are normalised to canonical hyphenated forms at assert time.

    The registry stays hyphenated; only inbound names are normalised.
    """

    async def test_all_aliases_resolve_to_canonical_predicate(
        self, pool_with_relational_predicates: asyncpg.Pool, entity: uuid.UUID
    ) -> None:
        """Every alias in _PREDICATE_ALIAS_MAP inserts via its canonical hyphenated predicate."""
        for alias, canonical in _PREDICATE_ALIAS_MAP.items():
            result = await relationship_assert_fact(
                pool_with_relational_predicates,
                entity,
                alias,
                str(uuid.uuid4()),
                src="test",
                object_kind="entity",
            )
            assert result.outcome == AssertOutcome.inserted, (
                f"alias {alias!r} → canonical {canonical!r}: expected inserted"
            )
            row = await pool_with_relational_predicates.fetchrow(
                "SELECT predicate FROM relationship.entity_facts WHERE id = $1",
                result.fact_id,
            )
            assert row["predicate"] == canonical, (
                f"alias {alias!r} stored as {row['predicate']!r}, want {canonical!r}"
            )

    async def test_sibling_of_maps_to_family_of(
        self, pool_with_relational_predicates: asyncpg.Pool, entity: uuid.UUID
    ) -> None:
        """sibling_of (many-to-one) resolves to family-of, not a distinct predicate."""
        result = await relationship_assert_fact(
            pool_with_relational_predicates,
            entity,
            "sibling_of",
            str(uuid.uuid4()),
            src="test",
            object_kind="entity",
        )
        assert result.outcome == AssertOutcome.inserted
        row = await pool_with_relational_predicates.fetchrow(
            "SELECT predicate FROM relationship.entity_facts WHERE id = $1",
            result.fact_id,
        )
        assert row["predicate"] == "family-of"

    async def test_married_to_maps_to_partner_of(
        self, pool_with_relational_predicates: asyncpg.Pool, entity: uuid.UUID
    ) -> None:
        """married_to (many-to-one) resolves to partner-of, not a distinct predicate."""
        result = await relationship_assert_fact(
            pool_with_relational_predicates,
            entity,
            "married_to",
            str(uuid.uuid4()),
            src="test",
            object_kind="entity",
        )
        assert result.outcome == AssertOutcome.inserted
        row = await pool_with_relational_predicates.fetchrow(
            "SELECT predicate FROM relationship.entity_facts WHERE id = $1",
            result.fact_id,
        )
        assert row["predicate"] == "partner-of"

    async def test_family_of_and_sibling_of_share_canonical_predicate(
        self, pool_with_relational_predicates: asyncpg.Pool, entity: uuid.UUID
    ) -> None:
        """family_of and sibling_of both map to family-of — they are distinct aliases for one canonical."""
        other_a = str(uuid.uuid4())
        other_b = str(uuid.uuid4())

        r_family = await relationship_assert_fact(
            pool_with_relational_predicates,
            entity,
            "family_of",
            other_a,
            src="test",
            object_kind="entity",
        )
        r_sibling = await relationship_assert_fact(
            pool_with_relational_predicates,
            entity,
            "sibling_of",
            other_b,
            src="test",
            object_kind="entity",
        )

        row_family = await pool_with_relational_predicates.fetchrow(
            "SELECT predicate FROM relationship.entity_facts WHERE id = $1", r_family.fact_id
        )
        row_sibling = await pool_with_relational_predicates.fetchrow(
            "SELECT predicate FROM relationship.entity_facts WHERE id = $1", r_sibling.fact_id
        )
        assert row_family["predicate"] == "family-of"
        assert row_sibling["predicate"] == "family-of"

    async def test_partner_of_and_married_to_share_canonical_predicate(
        self, pool_with_relational_predicates: asyncpg.Pool, entity: uuid.UUID
    ) -> None:
        """partner_of and married_to both map to partner-of — distinct aliases for one canonical."""
        other_a = str(uuid.uuid4())
        other_b = str(uuid.uuid4())

        r_partner = await relationship_assert_fact(
            pool_with_relational_predicates,
            entity,
            "partner_of",
            other_a,
            src="test",
            object_kind="entity",
        )
        r_married = await relationship_assert_fact(
            pool_with_relational_predicates,
            entity,
            "married_to",
            other_b,
            src="test",
            object_kind="entity",
        )

        row_partner = await pool_with_relational_predicates.fetchrow(
            "SELECT predicate FROM relationship.entity_facts WHERE id = $1", r_partner.fact_id
        )
        row_married = await pool_with_relational_predicates.fetchrow(
            "SELECT predicate FROM relationship.entity_facts WHERE id = $1", r_married.fact_id
        )
        assert row_partner["predicate"] == "partner-of"
        assert row_married["predicate"] == "partner-of"

    async def test_unrecognised_underscore_name_raises_value_error(
        self, pool_with_relational_predicates: asyncpg.Pool, entity: uuid.UUID
    ) -> None:
        """A name that looks like an alias but is absent from _PREDICATE_ALIAS_MAP fails registry lookup."""
        with pytest.raises(ValueError, match="Unknown predicate.*not registered"):
            await relationship_assert_fact(
                pool_with_relational_predicates,
                entity,
                "drinks_with",
                str(uuid.uuid4()),
                src="test",
                object_kind="entity",
            )
