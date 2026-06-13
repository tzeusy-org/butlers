"""Tests for prefers-channel write path — single-valued preferred outbound channel.

Covers (entity-keyed-preferred-channel, group 1, bu-ctsgh):
  - Assert a preference for a reachable channel (inserted)
  - Supersede: a second assert replaces the prior active value (exactly one
    active row remains)
  - Idempotent re-assert of the same channel (unchanged, no write)
  - Retract on clear (no active row remains)
  - Reject a preference for an unreachable channel (no has-* fact) with an error
  - OQ2 validation-degrade path: a handle channel with no clean prefix (discord)
    is accepted on the strength of ANY active has-handle fact

Spec: openspec/changes/entity-keyed-preferred-channel/specs/relationship-facts/spec.md
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.tools.relationship.relationship_assert_fact import (
    PREFERS_CHANNEL_PREDICATE,
    AssertOutcome,
    assert_prefers_channel,
    relationship_assert_fact,
    retract_prefers_channel,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Pool fixture — relationship schema + facts + predicate_registry (cardinality)
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as p:
        await p.execute(
            """
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT        NOT NULL DEFAULT '',
                entity_type    TEXT        NOT NULL DEFAULT 'person',
                roles          TEXT[]      NOT NULL DEFAULT '{}',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await p.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
                predicate   TEXT        NOT NULL PRIMARY KEY,
                kind        TEXT        NOT NULL,
                object_kind TEXT        NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                cardinality TEXT        NOT NULL DEFAULT 'multi'
            )
            """
        )
        await p.execute(
            """
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description, cardinality)
            VALUES
                ('has-email',  'contact',  'literal', 'Email.',  'multi'),
                ('has-phone',  'contact',  'literal', 'Phone.',  'multi'),
                ('has-handle', 'contact',  'literal', 'Handle.', 'multi'),
                ('prefers-channel', 'override', 'literal', 'Preferred channel.', 'single')
            ON CONFLICT (predicate) DO NOTHING
            """
        )
        await p.execute(
            """
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
            """
        )
        await p.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
                ON relationship.entity_facts (subject, predicate, object)
                WHERE validity = 'active'
            """
        )
        yield p


@pytest.fixture
async def entity(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Alice', 'person', '{}') RETURNING id"
    )


async def _add_channel(pool: asyncpg.Pool, subject: uuid.UUID, predicate: str, obj: str) -> None:
    """Insert an active has-* contact fact directly (channel identity)."""
    await relationship_assert_fact(pool, subject, predicate, obj, src="test")


async def _active_prefs(pool: asyncpg.Pool, subject: uuid.UUID) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT object, validity FROM relationship.entity_facts
        WHERE subject = $1 AND predicate = $2 AND validity = 'active'
        """,
        subject,
        PREFERS_CHANNEL_PREDICATE,
    )


# ---------------------------------------------------------------------------
# Assert
# ---------------------------------------------------------------------------


class TestAssert:
    async def test_assert_reachable_channel_inserts(self, pool, entity):
        await _add_channel(pool, entity, "has-email", "alice@example.com")
        result = await assert_prefers_channel(pool, entity, "email")
        assert result.outcome == AssertOutcome.inserted
        active = await _active_prefs(pool, entity)
        assert [r["object"] for r in active] == ["email"]

    async def test_assert_telegram_requires_prefixed_handle(self, pool, entity):
        # Reliable per-channel proof: telegram needs a telegram:-prefixed handle.
        await _add_channel(pool, entity, "has-handle", "telegram:12345")
        result = await assert_prefers_channel(pool, entity, "telegram")
        assert result.outcome == AssertOutcome.inserted

    async def test_assert_stores_provenance(self, pool, entity):
        await _add_channel(pool, entity, "has-email", "a@b.com")
        result = await assert_prefers_channel(pool, entity, "email", src="dashboard", verified=True)
        row = await pool.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id = $1", result.fact_id
        )
        assert row["src"] == "dashboard"
        assert row["verified"] is True
        assert row["object_kind"] == "literal"


# ---------------------------------------------------------------------------
# Supersede (single-valued)
# ---------------------------------------------------------------------------


class TestSupersede:
    async def test_new_preference_supersedes_prior(self, pool, entity):
        await _add_channel(pool, entity, "has-email", "a@b.com")
        await _add_channel(pool, entity, "has-handle", "telegram:99")
        await assert_prefers_channel(pool, entity, "email")
        result = await assert_prefers_channel(pool, entity, "telegram")
        assert result.outcome == AssertOutcome.superseded

        active = await _active_prefs(pool, entity)
        assert [r["object"] for r in active] == ["telegram"], "exactly one active value remains"

        superseded = await pool.fetch(
            "SELECT object FROM relationship.entity_facts "
            "WHERE subject = $1 AND predicate = $2 AND validity = 'superseded'",
            entity,
            PREFERS_CHANNEL_PREDICATE,
        )
        assert [r["object"] for r in superseded] == ["email"]

    async def test_reassert_same_channel_is_unchanged(self, pool, entity):
        await _add_channel(pool, entity, "has-email", "a@b.com")
        first = await assert_prefers_channel(pool, entity, "email")
        second = await assert_prefers_channel(pool, entity, "email")
        assert second.outcome == AssertOutcome.unchanged
        assert second.fact_id == first.fact_id
        active = await _active_prefs(pool, entity)
        assert len(active) == 1


# ---------------------------------------------------------------------------
# Retract
# ---------------------------------------------------------------------------


class TestRetract:
    async def test_retract_clears_active_preference(self, pool, entity):
        await _add_channel(pool, entity, "has-email", "a@b.com")
        await assert_prefers_channel(pool, entity, "email")
        retracted = await retract_prefers_channel(pool, entity)
        assert retracted == 1
        assert await _active_prefs(pool, entity) == []

    async def test_retract_no_preference_is_noop(self, pool, entity):
        retracted = await retract_prefers_channel(pool, entity)
        assert retracted == 0


# ---------------------------------------------------------------------------
# Reject unreachable
# ---------------------------------------------------------------------------


class TestRejectUnreachable:
    async def test_reject_channel_with_no_contact_fact(self, pool, entity):
        # Entity has only an email; preferring telegram must be rejected.
        await _add_channel(pool, entity, "has-email", "a@b.com")
        with pytest.raises(ValueError, match="no active contact fact"):
            await assert_prefers_channel(pool, entity, "telegram")
        assert await _active_prefs(pool, entity) == []

    async def test_reject_telegram_when_only_bare_handle(self, pool, entity):
        # A non-telegram (bare, unprefixed) handle does NOT prove telegram reach.
        await _add_channel(pool, entity, "has-handle", "linkedin-bare-handle")
        with pytest.raises(ValueError, match="no active contact fact"):
            await assert_prefers_channel(pool, entity, "telegram")

    async def test_reject_email_when_no_email_fact(self, pool, entity):
        await _add_channel(pool, entity, "has-handle", "telegram:5")
        with pytest.raises(ValueError, match="no active contact fact"):
            await assert_prefers_channel(pool, entity, "email")


# ---------------------------------------------------------------------------
# OQ2 validation-degrade path
# ---------------------------------------------------------------------------


class TestValidationDegrade:
    async def test_discord_accepted_on_any_handle(self, pool, entity):
        """discord has no clean channel prefix → degrade to 'any has-handle'.

        Per OQ2 resolution: handle channels without a reliable prefix validate
        against the presence of ANY active has-handle fact. A bare (non-telegram)
        handle therefore satisfies a discord preference.
        """
        await _add_channel(pool, entity, "has-handle", "some-bare-handle")
        result = await assert_prefers_channel(pool, entity, "discord")
        assert result.outcome == AssertOutcome.inserted
        active = await _active_prefs(pool, entity)
        assert [r["object"] for r in active] == ["discord"]

    async def test_degraded_channel_rejected_without_any_handle(self, pool, entity):
        """The degrade path still rejects when the entity has NO handle at all."""
        await _add_channel(pool, entity, "has-email", "a@b.com")
        with pytest.raises(ValueError, match="no active contact fact"):
            await assert_prefers_channel(pool, entity, "discord")
