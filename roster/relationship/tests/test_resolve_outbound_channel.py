"""Tests for resolve_outbound_channel — load-bearing preferred-channel resolution.

Covers (entity-keyed-preferred-channel, group 2, bu-upbit; spec core-notify):
  - Preference honored when deliverable + reachable
  - Preference skipped when not deliverable (discord pref, no discord handle)
  - Preference skipped when deliverable but entity not reachable on it
  - No preference → fall back to telegram → email precedence (first reachable)
  - Unknown entity_id → None
  - No reachable channel at all → None

These exercise the real reachability validation reused from the group-1 fact
writer against a live Postgres schema, so the intersection of preference and
deliverability is verified end-to-end rather than mocked.

Spec: openspec/changes/entity-keyed-preferred-channel/specs/core-notify/spec.md
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.identity import resolve_outbound_channel
from butlers.tools.relationship.relationship_assert_fact import (
    assert_prefers_channel,
    relationship_assert_fact,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# notify()'s deliverable set today.
_DELIVERABLE = {"telegram", "email"}


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
        await p.execute(
            """
            CREATE TABLE IF NOT EXISTS public.contacts (
                id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name       TEXT,
                entity_id  UUID REFERENCES public.entities(id) ON DELETE SET NULL,
                metadata   JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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


async def _new_contact(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an entity + linked contact; return (contact_id, entity_id)."""
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ('Alice', 'person') RETURNING id"
    )
    contact_id = await pool.fetchval(
        "INSERT INTO public.contacts (name, entity_id) VALUES ('Alice', $1) RETURNING id",
        entity_id,
    )
    return contact_id, entity_id


async def _add_channel(pool: asyncpg.Pool, subject: uuid.UUID, predicate: str, obj: str) -> None:
    await relationship_assert_fact(pool, subject, predicate, obj, src="test")


class TestPreferenceHonored:
    async def test_preference_honored_when_deliverable_and_reachable(self, pool):
        contact_id, entity_id = await _new_contact(pool)
        await _add_channel(pool, entity_id, "has-handle", "telegram:12345")
        await _add_channel(pool, entity_id, "has-email", "alice@example.com")
        await assert_prefers_channel(pool, entity_id, "telegram")

        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen == "telegram"

    async def test_email_preference_honored_over_telegram_fallback(self, pool):
        # Both channels reachable; preference (email) must beat the telegram-first
        # default precedence — proving the preference is load-bearing.
        contact_id, entity_id = await _new_contact(pool)
        await _add_channel(pool, entity_id, "has-handle", "telegram:999")
        await _add_channel(pool, entity_id, "has-email", "alice@example.com")
        await assert_prefers_channel(pool, entity_id, "email")

        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen == "email"


class TestPreferenceSkipped:
    async def test_skipped_when_not_deliverable_falls_back(self, pool):
        # Entity prefers discord (reachable via a generic handle, so the group-1
        # write would have been accepted) but discord is NOT in the deliverable
        # set, so the preference is skipped and we fall back to email.
        contact_id, entity_id = await _new_contact(pool)
        await _add_channel(pool, entity_id, "has-handle", "discord-handle")
        await _add_channel(pool, entity_id, "has-email", "alice@example.com")
        await assert_prefers_channel(pool, entity_id, "discord")

        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen == "email"

    async def test_skipped_when_deliverable_but_not_reachable(self, pool):
        # A telegram preference can become stale if the telegram handle is later
        # retracted. Resolution must not deliver to an unreachable channel: it
        # skips the preference and falls back to the reachable email.
        contact_id, entity_id = await _new_contact(pool)
        await _add_channel(pool, entity_id, "has-handle", "telegram:42")
        await _add_channel(pool, entity_id, "has-email", "alice@example.com")
        await assert_prefers_channel(pool, entity_id, "telegram")
        # Retract the telegram handle out from under the preference.
        await pool.execute(
            "UPDATE relationship.entity_facts SET validity = 'retracted' "
            "WHERE subject = $1 AND predicate = 'has-handle'",
            entity_id,
        )

        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen == "email"


class TestFallback:
    async def test_no_preference_prefers_telegram(self, pool):
        contact_id, entity_id = await _new_contact(pool)
        await _add_channel(pool, entity_id, "has-handle", "telegram:7")
        await _add_channel(pool, entity_id, "has-email", "alice@example.com")
        # No prefers-channel fact asserted.

        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen == "telegram"

    async def test_no_preference_falls_to_email_when_no_telegram(self, pool):
        contact_id, entity_id = await _new_contact(pool)
        await _add_channel(pool, entity_id, "has-email", "alice@example.com")

        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen == "email"

    async def test_no_reachable_channel_returns_none(self, pool):
        _, entity_id = await _new_contact(pool)
        # No channel facts at all.
        chosen = await resolve_outbound_channel(pool, entity_id, deliverable_channels=_DELIVERABLE)
        assert chosen is None


class TestUnknownEntity:
    async def test_unknown_entity_returns_none(self, pool):
        # An entity_id that names no entity (and thus no facts) resolves to None.
        chosen = await resolve_outbound_channel(
            pool, uuid.uuid4(), deliverable_channels=_DELIVERABLE
        )
        assert chosen is None
