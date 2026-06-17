"""Tests for the family confidence gate in relationship_assert_fact().

The gate prevents low-confidence inferred kinship claims (parent-of, child-of,
family-of) from writing hard entity-to-entity edges without human confirmation.
Instead, low-confidence kinship assertions are routed to pending_approval, exactly
like the owner carve-out.

Background (bu-u0m00): a mis-extraction was observed where "has a son" was
stored as a parent-of edge when the owner has no son.  The gate requires
conf ≥ 0.8 for any kinship predicate on a non-owner entity to write directly;
lower-confidence assertions park in pending_actions for human review.

Spec anchor: fact-extraction SKILL.md §"Confidence Gate for Family Predicates"
Central writer: roster/relationship/tools/relationship_assert_fact.py
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.tools.relationship.relationship_assert_fact import (
    _FAMILY_GATE_CONF,
    _FAMILY_GATE_PREDICATES,
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
# Shared DB fixture — minimal schema for these gate tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh DB with entities, entity_facts, predicate_registry, pending_actions."""
    async with provisioned_postgres_pool() as p:
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
        # Seed all predicates needed by gate tests: kinship + one non-kinship control
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES
                ('parent-of',  'relational', 'entity', 'Parent-child relationship.'),
                ('child-of',   'relational', 'entity', 'Child-parent relationship.'),
                ('family-of',  'relational', 'entity', 'Family / kinship relationship.'),
                ('partner-of', 'relational', 'entity', 'Spousal or partner relationship.'),
                ('friend-of',  'relational', 'entity', 'Friendship relationship.'),
                ('knows',      'relational', 'entity', 'General acquaintance.')
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
# Entity helpers
# ---------------------------------------------------------------------------


async def _make_entity(pool: asyncpg.Pool, *, roles: list[str] | None = None) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Test Person', 'person', $1) RETURNING id",
        roles or [],
    )


# ---------------------------------------------------------------------------
# Tests: gate constant sanity
# ---------------------------------------------------------------------------


class TestGateConstants:
    """Verify the gate constants have the expected shape.

    These are fixed-value tests so that a future refactor that accidentally
    changes the threshold or predicate set is caught immediately.
    """

    def test_family_gate_conf_is_0_8(self):
        assert _FAMILY_GATE_CONF == 0.8

    def test_family_gate_predicates_are_the_three_kinship_types(self):
        assert _FAMILY_GATE_PREDICATES == {"parent-of", "child-of", "family-of"}

    def test_family_gate_predicates_is_frozenset(self):
        assert isinstance(_FAMILY_GATE_PREDICATES, frozenset)


# ---------------------------------------------------------------------------
# Tests: low-confidence kinship assertions are gated (pending_approval)
# ---------------------------------------------------------------------------


class TestLowConfKinshipGated:
    """conf < 0.8 on kinship predicates → pending_approval, no entity_facts row."""

    async def test_parent_of_low_conf_gated(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )

        assert result.outcome == AssertOutcome.pending_approval
        assert result.fact_id is None
        assert result.action_id is not None

    async def test_child_of_low_conf_gated(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "child-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.3,
        )

        assert result.outcome == AssertOutcome.pending_approval
        assert result.fact_id is None

    async def test_family_of_low_conf_gated(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "family-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.6,
        )

        assert result.outcome == AssertOutcome.pending_approval
        assert result.fact_id is None

    async def test_low_conf_kinship_writes_no_entity_facts_row(self, pool: asyncpg.Pool):
        """Gate must leave entity_facts untouched — no active rows written."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE subject = $1",
            subject,
        )
        assert count == 0

    async def test_low_conf_kinship_writes_pending_action_row(self, pool: asyncpg.Pool):
        """Gate must park the assertion in pending_actions with correct identity fields."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )

        row = await pool.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1",
            result.action_id,
        )
        assert row is not None
        assert row["tool_name"] == "relationship_assert_fact"
        assert row["status"] == "pending"
        args = row["tool_args"]
        assert args["subject"] == str(subject)
        assert args["predicate"] == "parent-of"
        assert args["object"] == object_eid

    async def test_exactly_at_threshold_is_still_gated(self, pool: asyncpg.Pool):
        """conf = 0.8 is the passing threshold — 0.79x must still be gated."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.79,
        )
        assert result.outcome == AssertOutcome.pending_approval

    async def test_repeated_low_conf_kinship_deduplicates_pending_action(self, pool: asyncpg.Pool):
        """Repeated low-conf calls with the same triple return the same action_id.

        Mirrors the owner carve-out dedup behaviour — prevents flooding
        pending_actions when an extraction job re-runs on the same data.
        """
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        r1 = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )
        r2 = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )
        assert r1.action_id == r2.action_id


# ---------------------------------------------------------------------------
# Tests: high-confidence kinship assertions bypass the gate (inserted)
# ---------------------------------------------------------------------------


class TestHighConfKinshipBypasses:
    """conf ≥ 0.8 on kinship predicates → normal upsert (inserted / unchanged)."""

    async def test_parent_of_at_threshold_inserts(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.8,
        )

        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        assert result.action_id is None

    async def test_child_of_high_conf_inserts(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "child-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=1.0,
        )

        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None

    async def test_family_of_high_conf_inserts(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "family-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.9,
        )

        assert result.outcome == AssertOutcome.inserted

    async def test_high_conf_kinship_writes_entity_facts_row(self, pool: asyncpg.Pool):
        """High-confidence kinship bypasses the gate and lands in entity_facts."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "parent-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=1.0,
        )

        row = await pool.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id = $1",
            result.fact_id,
        )
        assert row is not None
        assert row["predicate"] == "parent-of"
        assert row["object"] == object_eid
        assert row["validity"] == "active"


# ---------------------------------------------------------------------------
# Tests: gate does NOT apply to non-kinship predicates
# ---------------------------------------------------------------------------


class TestGateNonKinship:
    """Low-confidence non-kinship predicates must NOT be gated.

    The gate is exclusively for (parent-of, child-of, family-of).  Other
    predicates — including partner-of, friend-of, knows — must write directly
    at any confidence level.
    """

    async def test_partner_of_low_conf_not_gated(self, pool: asyncpg.Pool):
        """partner-of is NOT a kinship predicate — low conf must insert directly."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "partner-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )

        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None

    async def test_friend_of_low_conf_not_gated(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "friend-of",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.4,
        )

        assert result.outcome == AssertOutcome.inserted

    async def test_knows_low_conf_not_gated(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "knows",
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.1,
        )

        assert result.outcome == AssertOutcome.inserted


# ---------------------------------------------------------------------------
# Tests: gate applies to alias-resolved kinship predicates
# ---------------------------------------------------------------------------


class TestGateAliasResolution:
    """Alias names (sibling_of → family-of, child_of → child-of) are still gated.

    The alias map normalises underscore names before the gate check, so callers
    using legacy aliases get the same gate behaviour as canonical hyphenated names.
    """

    async def test_sibling_of_alias_low_conf_gated(self, pool: asyncpg.Pool):
        """sibling_of resolves to family-of — must be gated at low conf."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "sibling_of",  # alias → family-of
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.6,
        )

        assert result.outcome == AssertOutcome.pending_approval

    async def test_parent_of_alias_low_conf_gated(self, pool: asyncpg.Pool):
        """parent_of (underscore alias) → parent-of — must be gated at low conf."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "parent_of",  # alias → parent-of
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=0.5,
        )

        assert result.outcome == AssertOutcome.pending_approval

    async def test_child_of_alias_high_conf_bypasses(self, pool: asyncpg.Pool):
        """child_of alias at high conf must bypass the gate."""
        subject = await _make_entity(pool)
        object_eid = str(await _make_entity(pool))

        result = await relationship_assert_fact(
            pool,
            subject,
            "child_of",  # alias → child-of
            object_eid,
            src="relationship",
            object_kind="entity",
            conf=1.0,
        )

        assert result.outcome == AssertOutcome.inserted
