"""Tests for contact_info tools — structured contact details for the relationship butler."""

from __future__ import annotations

import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(autouse=True, scope="session")
def patch_embedding_engine():
    """Patch the embedding engine so store_fact works without a real model."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"

    with patch(
        "butlers.modules.memory.tools.get_embedding_engine",
        return_value=engine,
    ):
        # Reset the module-level _embedding_engine cache so the patch is picked up
        for mod_name in (
            "butlers.tools.relationship.feed",
            "butlers.tools.relationship.contacts",
        ):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with relationship + contact_info tables."""
    async with provisioned_postgres_pool() as p:
        # Create public.entities first so contacts.entity_id FK resolves
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
                predicate TEXT NOT NULL PRIMARY KEY,
                kind TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES
                ('has-email', 'contact', 'literal', 'Email address for the entity.'),
                ('has-phone', 'contact', 'literal', 'Phone number for the entity.'),
                ('has-handle', 'contact', 'literal', 'Channel-scoped handle.'),
                ('has-website', 'contact', 'literal', 'Web URL associated with the entity.')
            ON CONFLICT (predicate) DO NOTHING
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                object_kind TEXT NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src TEXT NOT NULL,
                conf FLOAT NOT NULL DEFAULT 1.0 CHECK (conf >= 0.0 AND conf <= 1.0),
                last_seen TIMESTAMPTZ,
                weight INT,
                verified BOOL NOT NULL DEFAULT false,
                "primary" BOOL,
                validity TEXT NOT NULL DEFAULT 'active'
                    CHECK (validity IN ('active', 'retracted', 'superseded')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
                ON relationship.entity_facts (subject, predicate, object)
                WHERE validity = 'active'
        """)

        # Create base relationship tables (from 001 migration)
        # entity_id column present so _is_owner_contact JOIN works
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name TEXT,
                last_name TEXT,
                nickname TEXT,
                company TEXT,
                job_title TEXT,
                gender TEXT,
                pronouns TEXT,
                avatar_url TEXT,
                listed BOOLEAN NOT NULL DEFAULT true,
                metadata JSONB NOT NULL DEFAULT '{}',
                entity_id UUID REFERENCES public.entities(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts (first_name, last_name)
        """)
        # Create public.contact_info
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.contact_info (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL,
                type VARCHAR NOT NULL,
                value TEXT NOT NULL,
                label VARCHAR,
                is_primary BOOLEAN DEFAULT false,
                context VARCHAR CHECK (context IN ('personal', 'work', 'other')),
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_contact_info_type_value
                ON public.contact_info (type, value)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_contact_info_contact_id
                ON public.contact_info (contact_id)
        """)

        # pending_actions — used by the owner gate in contact_info_add/update
        await p.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                agent_summary TEXT,
                session_id UUID,
                status VARCHAR NOT NULL DEFAULT 'pending',
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                execution_result JSONB,
                approval_rule_id UUID,
                why TEXT,
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb
            )
        """)

        # Predicate registry — columns must match what store_fact() queries
        await p.execute("""
            CREATE TABLE IF NOT EXISTS predicate_registry (
                name TEXT PRIMARY KEY,
                expected_subject_type TEXT,
                expected_object_type TEXT,
                is_edge BOOLEAN NOT NULL DEFAULT false,
                is_temporal BOOLEAN NOT NULL DEFAULT false,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by TEXT,
                deprecated_at TIMESTAMPTZ,
                inverse_of TEXT,
                is_symmetric BOOLEAN NOT NULL DEFAULT false,
                aliases TEXT[] NOT NULL DEFAULT '{}',
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMPTZ
            )
        """)
        await p.execute("""
            INSERT INTO predicate_registry (name, is_temporal) VALUES
                ('interaction', true),
                ('life_event', true),
                ('contact_note', true),
                ('activity', true)
            ON CONFLICT (name) DO NOTHING
        """)

        # Facts table (TEXT embedding avoids pgvector dependency in tests)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT,
                search_vector TSVECTOR,
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                permanence TEXT NOT NULL DEFAULT 'standard',
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID REFERENCES facts(id) ON DELETE SET NULL,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{}'::jsonb,
                entity_id UUID REFERENCES public.entities(id),
                object_entity_id UUID REFERENCES public.entities(id),
                valid_at TIMESTAMPTZ DEFAULT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                idempotency_key TEXT,
                observed_at TIMESTAMPTZ DEFAULT now(),
                invalid_at TIMESTAMPTZ,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                embedding_model_version TEXT DEFAULT 'unknown'
            )
        """)

        # memory_links table (used by store_fact for supersession links)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_type TEXT NOT NULL,
                source_id UUID NOT NULL,
                target_type TEXT NOT NULL,
                target_id UUID NOT NULL,
                relation TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_type, source_id, target_type, target_id)
            )
        """)

        yield p


_TYPE_TO_PREDICATE = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
}


async def _insert_legacy_contact_info(
    pool,
    contact_id: uuid.UUID,
    type: str,
    value: str,
    *,
    label: str | None = None,
    is_primary: bool = False,
    context: str | None = None,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO public.contact_info (contact_id, type, value, label, is_primary, context)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        contact_id,
        type,
        value,
        label,
        is_primary,
        context,
    )
    return dict(row)


async def _insert_entity_fact(
    pool,
    entity_id: uuid.UUID,
    predicate: str,
    object_value: str,
    *,
    is_primary: bool = False,
) -> dict[str, Any]:
    """Insert a channel triple directly into relationship.entity_facts.

    Use this helper to seed test data for the migrated read paths
    (``contact_info_list``, ``contact_search_by_info``).  Mirrors what
    ``contact_info_add`` asserts via the central writer.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity, "primary")
        VALUES ($1, $2, $3, 'literal', 'test', 'active', $4)
        RETURNING *
        """,
        entity_id,
        predicate,
        object_value,
        is_primary,
    )
    return dict(row)


async def _fetch_contact_fact(pool, entity_id: uuid.UUID, type: str, value: str):
    return await pool.fetchrow(
        """
        SELECT *
        FROM relationship.entity_facts
        WHERE subject = $1
          AND predicate = $2
          AND object = $3
          AND validity = 'active'
        """,
        entity_id,
        _TYPE_TO_PREDICATE[type],
        value,
    )


# ------------------------------------------------------------------
# contact_info_add
# ------------------------------------------------------------------


async def test_contact_info_add_email(pool):
    """contact_info_add stores an email triple."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Alice")
    info = await contact_info_add(pool, c["id"], "email", "alice@example.com")
    assert info["status"] == "asserted"
    assert info["type"] == "email"
    assert info["value"] == "alice@example.com"
    assert info["is_primary"] is False
    fact = await _fetch_contact_fact(pool, c["entity_id"], "email", "alice@example.com")
    assert fact is not None
    assert fact["src"] == "relationship"


async def test_contact_info_add_with_label(pool):
    """contact_info_add accepts legacy label input but writes a triple."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Bob")
    info = await contact_info_add(pool, c["id"], "phone", "+1-555-0100", label="Work")
    assert info["type"] == "phone"
    assert info["value"] == "+1-555-0100"
    assert "label" not in info
    fact = await _fetch_contact_fact(pool, c["entity_id"], "phone", "+1-555-0100")
    assert fact is not None


async def test_contact_info_add_primary(pool):
    """contact_info_add encodes primary on the triple."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Charlie")
    info = await contact_info_add(pool, c["id"], "email", "c@example.com", is_primary=True)
    assert info["is_primary"] is True
    fact = await _fetch_contact_fact(pool, c["entity_id"], "email", "c@example.com")
    assert fact["primary"] is True


async def test_contact_info_add_primary_unsets_previous(pool):
    """Primary is stored per asserted triple after the write-path cut-over."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Diana")
    await contact_info_add(pool, c["id"], "email", "d1@example.com", is_primary=True)
    await contact_info_add(pool, c["id"], "email", "d2@example.com", is_primary=True)

    rows = await pool.fetch(
        """
        SELECT object, "primary"
        FROM relationship.entity_facts
        WHERE subject = $1 AND predicate = 'has-email' AND validity = 'active'
        ORDER BY object
        """,
        c["entity_id"],
    )
    assert [(row["object"], row["primary"]) for row in rows] == [
        ("d1@example.com", True),
        ("d2@example.com", True),
    ]


async def test_contact_info_add_invalid_type(pool):
    """contact_info_add rejects invalid info types."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Eve")
    with pytest.raises(ValueError, match="Invalid contact info type"):
        await contact_info_add(pool, c["id"], "fax", "555-0101")


async def test_contact_info_add_nonexistent_contact(pool):
    """contact_info_add raises ValueError for nonexistent contact."""
    from butlers.tools.relationship import contact_info_add

    fake_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await contact_info_add(pool, fake_id, "email", "nobody@example.com")


async def test_contact_info_add_all_types(pool):
    """contact_info_add accepts all valid types and maps them to contact predicates."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "AllTypes")
    types_values = [
        ("email", "all@example.com"),
        ("phone", "+1-555-0000"),
        ("telegram", "@alltypes"),
        ("linkedin", "linkedin.com/in/alltypes"),
        ("twitter", "@alltypes_x"),
        ("website", "https://alltypes.dev"),
        ("other", "Signal: +1-555-0001"),
    ]
    for t, v in types_values:
        await contact_info_add(pool, c["id"], t, v)

    facts = await pool.fetch(
        """
        SELECT predicate, object
        FROM relationship.entity_facts
        WHERE subject = $1 AND validity = 'active'
        ORDER BY object
        """,
        c["entity_id"],
    )
    assert len(facts) == 7
    assert {row["predicate"] for row in facts} == {
        "has-email",
        "has-phone",
        "has-handle",
        "has-website",
    }


# ------------------------------------------------------------------
# contact_info_list
# ------------------------------------------------------------------


async def test_contact_info_list_all(pool):
    """contact_info_list returns all info for a contact (reads entity_facts)."""
    from butlers.tools.relationship import contact_create, contact_info_list

    c = await contact_create(pool, "ListAll")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "list@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-phone", "+1-555-0200")

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 2


async def test_contact_info_list_by_type(pool):
    """contact_info_list filters by type when specified (reads entity_facts)."""
    from butlers.tools.relationship import contact_create, contact_info_list

    c = await contact_create(pool, "ListByType")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "a@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "b@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-phone", "+1-555-0300")

    emails = await contact_info_list(pool, c["id"], type="email")
    assert len(emails) == 2
    assert all(i["type"] == "email" for i in emails)

    phones = await contact_info_list(pool, c["id"], type="phone")
    assert len(phones) == 1


async def test_contact_info_list_primary_first(pool):
    """contact_info_list returns primary entries first (reads entity_facts)."""
    from butlers.tools.relationship import contact_create, contact_info_list

    c = await contact_create(pool, "PrimaryFirst")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "secondary@example.com")
    await _insert_entity_fact(
        pool, c["entity_id"], "has-email", "primary@example.com", is_primary=True
    )

    emails = await contact_info_list(pool, c["id"], type="email")
    assert emails[0]["value"] == "primary@example.com"
    assert emails[0]["is_primary"] is True


async def test_contact_info_list_empty(pool):
    """contact_info_list returns empty list when contact has no info."""
    from butlers.tools.relationship import contact_create, contact_info_list

    c = await contact_create(pool, "NoInfo")
    infos = await contact_info_list(pool, c["id"])
    assert infos == []


# ------------------------------------------------------------------
# contact_info_remove
# ------------------------------------------------------------------


async def test_contact_info_remove(pool):
    """contact_info_remove is blocked after the write-path cut-over."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create, contact_info_remove

    c = await contact_create(pool, "RemoveMe")
    info = await _insert_legacy_contact_info(pool, c["id"], "email", "remove@example.com")

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_remove(pool, info["id"])


async def test_contact_info_remove_nonexistent(pool):
    """contact_info_remove fails fast before looking up the legacy row."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_info_remove

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_remove(pool, uuid.uuid4())


async def test_contact_info_remove_keeps_others(pool):
    """contact_info_remove is blocked; entity_facts data is unaffected."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create, contact_info_list, contact_info_remove

    c = await contact_create(pool, "KeepOthers")
    # Seed entity_facts — contact_info_list reads from here now
    fact1 = await _insert_entity_fact(pool, c["entity_id"], "has-email", "keep1@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "keep2@example.com")

    # Remove is blocked (uses a legacy contact_info_id — the UUID is arbitrary)
    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_remove(pool, fact1["id"])

    # Both entity_facts rows remain; contact_info_list returns them
    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 2


# ------------------------------------------------------------------
# contact_search_by_info (reverse lookup)
# ------------------------------------------------------------------


async def test_contact_search_by_info_exact(pool):
    """contact_search_by_info finds a contact by exact email value (reads entity_facts)."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_info,
    )

    c = await contact_create(pool, "SearchExact")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "searchexact@example.com")

    results = await contact_search_by_info(pool, "searchexact@example.com")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_partial(pool):
    """contact_search_by_info supports partial matching (ILIKE) via entity_facts."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_info,
    )

    c = await contact_create(pool, "SearchPartial")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "partial_unique_xyz@example.com")

    results = await contact_search_by_info(pool, "partial_unique_xyz")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_with_type_filter(pool):
    """contact_search_by_info filters by type (predicate) when specified."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_info,
    )

    c = await contact_create(pool, "SearchTyped")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "typed_search_unique@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-phone", "+1-555-9999")

    # Search by email type only (maps to has-email)
    results = await contact_search_by_info(pool, "typed_search_unique", type="email")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)

    # Search by phone type should not find email value
    results = await contact_search_by_info(pool, "typed_search_unique", type="phone")
    assert not any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_multiple_contacts(pool):
    """contact_search_by_info finds multiple contacts sharing a domain (entity_facts)."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_info,
    )

    c1 = await contact_create(pool, "Multi-A")
    c2 = await contact_create(pool, "Multi-B")
    await _insert_entity_fact(pool, c1["entity_id"], "has-email", "a@shareduniquedomain.com")
    await _insert_entity_fact(pool, c2["entity_id"], "has-email", "b@shareduniquedomain.com")

    results = await contact_search_by_info(pool, "shareduniquedomain.com")
    found_ids = {r["id"] for r in results}
    assert c1["id"] in found_ids
    assert c2["id"] in found_ids


# ------------------------------------------------------------------
# Work-domain heuristic (context auto-detection)
# ------------------------------------------------------------------


async def test_contact_info_add_work_domain_sets_context_work(pool, monkeypatch):
    """Legacy context heuristic is not written by contact_info_add after cut-over."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "WorkPerson")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    info = await contact_info_add(pool, c["id"], "email", "alice@example.com")

    assert "context" not in info
    fact = await _fetch_contact_fact(pool, c["entity_id"], "email", "alice@example.com")
    assert fact is not None


async def test_contact_info_add_personal_domain_leaves_context_null(pool, monkeypatch):
    """contact_info_add ignores legacy context metadata for non-work domains too."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "PersonalPerson")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    info = await contact_info_add(pool, c["id"], "email", "bob@gmail.com")

    assert "context" not in info


async def test_contact_info_add_explicit_context_not_overridden(pool, monkeypatch):
    """Explicit legacy context is accepted for compatibility and ignored."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "ExplicitContext")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    info = await contact_info_add(pool, c["id"], "email", "boss@example.com", context="personal")

    assert "context" not in info


async def test_contact_info_add_non_email_type_no_heuristic(pool, monkeypatch):
    """contact_info_add ignores legacy context metadata for non-email types."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "PhonePerson")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    # Phone value happens to look like a domain — should not be classified
    info = await contact_info_add(pool, c["id"], "phone", "+1-555-0200")

    assert "context" not in info


async def test_contact_info_add_invalid_context_raises(pool):
    """contact_info_add accepts ignored legacy context values after cut-over."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "InvalidContextPerson")
    info = await contact_info_add(pool, c["id"], "email", "x@example.com", context="bogus")
    assert info["status"] == "asserted"


# ------------------------------------------------------------------
# classify_email_context (unit tests, no DB needed)
# ------------------------------------------------------------------


def test_classify_email_context_work_domain(monkeypatch):
    """classify_email_context returns 'work' for known work domains."""
    from butlers.tools.relationship.contact_info import classify_email_context

    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com,acme.corp")
    assert classify_email_context("alice@example.com") == "work"
    assert classify_email_context("bob@acme.corp") == "work"


def test_classify_email_context_personal_domain(monkeypatch):
    """classify_email_context returns None for non-work domains."""
    from butlers.tools.relationship.contact_info import classify_email_context

    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    assert classify_email_context("alice@gmail.com") is None
    assert classify_email_context("bob@example.com") is None


def test_classify_email_context_case_insensitive(monkeypatch):
    """classify_email_context is case-insensitive for the domain part."""
    from butlers.tools.relationship.contact_info import classify_email_context

    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    assert classify_email_context("Alice@example.com") == "work"


def test_classify_email_context_no_at_sign(monkeypatch):
    """classify_email_context returns None for malformed addresses."""
    from butlers.tools.relationship.contact_info import classify_email_context

    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    assert classify_email_context("notanemail") is None


def test_classify_email_context_default_list(monkeypatch):
    """classify_email_context uses example.com when env var is unset."""
    from butlers.tools.relationship.contact_info import classify_email_context

    monkeypatch.delenv("BUTLERS_WORK_DOMAINS", raising=False)
    assert classify_email_context("alice@example.com") == "work"
    assert classify_email_context("alice@gmail.com") is None


def test_classify_email_context_empty_env_disables_heuristic(monkeypatch):
    """Setting BUTLERS_WORK_DOMAINS='' (empty string) disables the heuristic entirely."""
    from butlers.tools.relationship.contact_info import classify_email_context

    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "")
    assert classify_email_context("alice@example.com") is None


async def test_contact_search_by_info_case_insensitive(pool):
    """contact_search_by_info is case-insensitive (entity_facts ILIKE)."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_info,
    )

    c = await contact_create(pool, "CaseTest")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "CaseUnique@Example.COM")

    results = await contact_search_by_info(pool, "caseunique@example.com")
    assert any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_excludes_archived(pool):
    """contact_search_by_info excludes archived contacts."""
    from butlers.tools.relationship import (
        contact_archive,
        contact_create,
        contact_search_by_info,
    )

    c = await contact_create(pool, "ArchivedSearch")
    await _insert_entity_fact(
        pool, c["entity_id"], "has-email", "archivedsearch_unique@example.com"
    )
    await contact_archive(pool, c["id"])

    results = await contact_search_by_info(pool, "archivedsearch_unique@example.com")
    assert not any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_no_results(pool):
    """contact_search_by_info returns empty list when nothing matches."""
    from butlers.tools.relationship import contact_search_by_info

    results = await contact_search_by_info(pool, "nonexistent_unique_value_xyz")
    assert results == []


async def test_contact_search_by_info_phone(pool):
    """contact_search_by_info works for phone lookups (entity_facts)."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_info,
    )

    c = await contact_create(pool, "PhoneLookup")
    await _insert_entity_fact(pool, c["entity_id"], "has-phone", "+1-555-7777-unique")

    results = await contact_search_by_info(pool, "+1-555-7777-unique", type="phone")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


# ------------------------------------------------------------------
# Multi-value support
# ------------------------------------------------------------------


async def test_multiple_emails_per_contact(pool):
    """A contact can have multiple email addresses (entity_facts)."""
    from butlers.tools.relationship import contact_create, contact_info_list

    c = await contact_create(pool, "MultiEmail")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "work@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "personal@example.com")

    emails = await contact_info_list(pool, c["id"], type="email")
    assert len(emails) == 2
    values = {i["value"] for i in emails}
    assert values == {"work@example.com", "personal@example.com"}


async def test_multiple_types_per_contact(pool):
    """A contact can have multiple types of contact info (entity_facts).

    Telegram stored as has-handle with 'telegram:<id>' prefix; the returned
    type is 'telegram_user_id' (numeric id), not 'telegram'.
    """
    from butlers.tools.relationship import contact_create, contact_info_list

    c = await contact_create(pool, "MultiType")
    await _insert_entity_fact(pool, c["entity_id"], "has-email", "multi@example.com")
    await _insert_entity_fact(pool, c["entity_id"], "has-phone", "+1-555-0400")
    # Telegram stored with prefix per the entity_facts convention
    await _insert_entity_fact(pool, c["entity_id"], "has-handle", "telegram:12345678")

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 3
    types = {i["type"] for i in infos}
    # has-handle with telegram: prefix → "telegram_user_id"; email → "email"; phone → "phone"
    assert types == {"email", "phone", "telegram_user_id"}


# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Cascade delete (application-layer)
# ------------------------------------------------------------------


async def test_contact_info_orphan_after_contact_delete(pool):
    """public.contact_info rows persist after contact deletion (no DB-level FK cascade).

    public.contact_info intentionally has no REFERENCES contacts(id) ON DELETE CASCADE,
    since it lives in the shared schema and must be accessible from multiple butler schemas.
    Referential integrity is enforced at the application layer.  This test verifies the
    current DB behaviour: contact_info rows are NOT automatically removed when the parent
    contact is hard-deleted.  Application code that performs hard contact deletes must
    explicitly clean up public.contact_info rows.
    """
    from butlers.tools.relationship import contact_create

    c = await contact_create(pool, "CascadeTest")
    await _insert_legacy_contact_info(pool, c["id"], "email", "cascade@example.com")

    # Hard delete the contact — no FK cascade, so public.contact_info rows persist
    await pool.execute("DELETE FROM contacts WHERE id = $1", c["id"])

    # Rows still exist: application layer must clean them up explicitly
    rows = await pool.fetch("SELECT * FROM public.contact_info WHERE contact_id = $1", c["id"])
    assert len(rows) == 1, (
        "public.contact_info has no FK cascade; orphan rows persist after contact deletion"
    )


# ------------------------------------------------------------------
# Owner gate helpers
# ------------------------------------------------------------------


async def _make_owner_contact(pool):
    """Create an owner entity + contact and return the contact row.

    Inserts a public.entities row with roles=['owner'], then inserts a contacts
    row referencing that entity.  This mirrors the real owner_bootstrap path.
    """
    entity_id = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, name, entity_type, roles)
        VALUES ('Owner', 'Owner', 'person', ARRAY['owner'])
        RETURNING id
        """
    )
    contact_id = await pool.fetchval(
        """
        INSERT INTO contacts (first_name, listed, entity_id)
        VALUES ('Owner', true, $1)
        RETURNING id
        """,
        entity_id,
    )
    return {"id": contact_id, "entity_id": entity_id}


# ------------------------------------------------------------------
# contact_info_add — owner gate
# ------------------------------------------------------------------


async def test_contact_info_add_owner_gate_parks_action(pool):
    """contact_info_add targeting the owner contact creates a pending_action instead of inserting.

    Replays the 2026-04-21 incident shape: runtime LLM calls contact_info_add
    with the owner's contact_id and a speculative work email address.
    Expected: no row in public.contact_info; one pending_actions row with status='pending'.
    """
    from butlers.tools.relationship.contact_info import contact_info_add

    owner = await _make_owner_contact(pool)

    result = await contact_info_add(
        pool,
        owner["id"],
        "email",
        "owner@example.com",
        is_primary=False,
    )

    # Returned dict signals pending approval, not a contact_info row
    assert result["status"] == "pending_approval"
    assert "action_id" in result
    assert "approval" in result["message"].lower()

    # No row written to public.contact_info
    rows = await pool.fetch(
        "SELECT * FROM public.contact_info WHERE contact_id = $1",
        owner["id"],
    )
    assert rows == [], "Owner-targeted contact_info_add must not write to public.contact_info"

    # pending_actions row created with correct shape
    action_id = result["action_id"]
    action = await pool.fetchrow(
        "SELECT * FROM pending_actions WHERE id = $1::uuid",
        action_id,
    )
    assert action is not None, "pending_actions row must exist"
    assert action["tool_name"] == "relationship_assert_fact"
    assert action["status"] == "pending"

    # asyncpg JSONB codec returns a Python dict directly; no json.loads needed
    args = action["tool_args"]
    assert args["object"] == "owner@example.com"
    assert args["predicate"] == "has-email"
    assert args["object_kind"] == "literal"
    assert args["subject"] == str(owner["entity_id"])


async def test_contact_info_add_non_owner_writes_immediately(pool):
    """contact_info_add targeting a non-owner contact writes a fact immediately."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "NonOwner")
    result = await contact_info_add(pool, c["id"], "email", "nonowner@example.com")

    # Returns a real contact_info row, not a pending dict
    assert result.get("status") != "pending_approval"
    assert result["type"] == "email"
    assert result["value"] == "nonowner@example.com"

    fact = await _fetch_contact_fact(pool, c["entity_id"], "email", "nonowner@example.com")
    assert fact is not None


# ------------------------------------------------------------------
# contact_info_update — new tool
# ------------------------------------------------------------------


async def test_contact_info_update_value(pool):
    """contact_info_update is blocked after the write-path cut-over."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateValue")
    info = await _insert_legacy_contact_info(pool, c["id"], "email", "old@example.com")

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, info["id"], value="new@example.com")


async def test_contact_info_update_label(pool):
    """contact_info_update rejects legacy label updates."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateLabel")
    info = await _insert_legacy_contact_info(pool, c["id"], "phone", "+1-555-0001", label="Home")

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, info["id"], label="Mobile")


async def test_contact_info_update_is_primary(pool):
    """contact_info_update rejects legacy primary updates; entity_facts data unaffected."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create, contact_info_list
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdatePrimary")
    # Seed via entity_facts — contact_info_list reads from here now
    fact1 = await _insert_entity_fact(
        pool, c["entity_id"], "has-email", "first@example.com", is_primary=True
    )
    fact2 = await _insert_entity_fact(
        pool, c["entity_id"], "has-email", "second@example.com", is_primary=False
    )

    # contact_info_update is blocked regardless of which ID is passed
    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, fact2["id"], is_primary=True)

    # entity_facts data is unchanged — fact1 is still the primary entry
    emails = await contact_info_list(pool, c["id"], type="email")
    primary = [e for e in emails if e["is_primary"]]
    assert len(primary) == 1
    assert primary[0]["id"] == fact1["id"]

    # First entry is unchanged because update is blocked.
    first = next(e for e in emails if e["id"] == fact1["id"])
    assert first["is_primary"] is True


async def test_contact_info_update_no_fields_raises(pool):
    """contact_info_update fails fast even when no fields are provided."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateNoFields")
    info = await _insert_legacy_contact_info(pool, c["id"], "email", "noupdate@example.com")

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, info["id"])


async def test_contact_info_update_nonexistent_raises(pool):
    """contact_info_update fails fast before looking up the legacy row."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship.contact_info import contact_info_update

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, uuid.uuid4(), value="x@example.com")


async def test_contact_info_update_owner_gate_parks_action(pool):
    """contact_info_update targeting the owner contact is blocked, not parked."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship.contact_info import contact_info_update

    owner = await _make_owner_contact(pool)

    # First, directly insert an entry bypassing the gate (simulates pre-existing row)
    info_id = await pool.fetchval(
        """
        INSERT INTO public.contact_info (contact_id, type, value, is_primary)
        VALUES ($1, 'email', 'old-owner@example.com', false)
        RETURNING id
        """,
        owner["id"],
    )

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, info_id, value="new-owner@example.com")

    # DB value unchanged
    row = await pool.fetchrow("SELECT value FROM public.contact_info WHERE id = $1", info_id)
    assert row["value"] == "old-owner@example.com"


async def test_contact_info_update_non_owner_writes_immediately(pool):
    """contact_info_update targeting a non-owner contact is blocked."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateNonOwner")
    info = await _insert_legacy_contact_info(pool, c["id"], "email", "nonowner-update@example.com")

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, info["id"], value="updated-nonowner@example.com")


# ------------------------------------------------------------------
# Transaction rollback tests
# ------------------------------------------------------------------


class _ConnProxy:
    """Wraps a real asyncpg PoolConnectionProxy, intercepting fetchrow().

    The first fetchrow() call raises the injected exception; subsequent calls
    delegate to the real connection.  This lets us simulate a mid-transaction
    failure (after the demote-primary execute ran) without needing to set
    read-only attributes on the asyncpg PoolConnectionProxy object.
    """

    def __init__(self, conn, exc: Exception):
        self._conn = conn
        self._exc = exc
        self._fetchrow_called = False

    async def fetchrow(self, query, *args):
        if not self._fetchrow_called:
            self._fetchrow_called = True
            raise self._exc
        return await self._conn.fetchrow(query, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _PoolWithFailingConn:
    """Thin pool wrapper that substitutes connections with _ConnProxy on acquire().

    Everything else (fetchrow, fetch, execute, fetchval) delegates to the real
    pool so pre-transaction reads (e.g. contact-exists lookups) work normally.
    """

    def __init__(self, real_pool, exc: Exception):
        self._pool = real_pool
        self._exc = exc

    def acquire(self):
        real_acquire = self._pool.acquire
        exc = self._exc

        @asynccontextmanager
        async def _ctx():
            async with real_acquire() as conn:
                yield _ConnProxy(conn, exc)

        return _ctx()

    def __getattr__(self, name):
        return getattr(self._pool, name)


async def test_contact_info_add_rollback_on_insert_failure(pool):
    """contact_info_add propagates central-writer failures without legacy writes."""
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.contact_info import contact_info_add as _contact_info_add

    c = await contact_create(pool, "TxnRollbackAdd")

    with (
        patch(
            "butlers.tools.relationship.contact_info.relationship_assert_fact",
            side_effect=RuntimeError("simulated central-writer failure"),
        ),
        pytest.raises(RuntimeError, match="simulated central-writer failure"),
    ):
        await _contact_info_add(
            pool,
            c["id"],
            "email",
            "new@example.com",
            is_primary=True,
        )

    fact_count = await pool.fetchval(
        "SELECT COUNT(*) FROM relationship.entity_facts WHERE subject = $1",
        c["entity_id"],
    )
    legacy_count = await pool.fetchval(
        "SELECT COUNT(*) FROM public.contact_info WHERE contact_id = $1",
        c["id"],
    )
    assert fact_count == 0
    assert legacy_count == 0


async def test_contact_info_update_rollback_on_update_failure(pool):
    """contact_info_update is blocked before any legacy mutation can occur."""
    from butlers.contact_info_write_guard import ContactInfoWriteBlockedError
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "TxnRollbackUpdate")
    first = await _insert_legacy_contact_info(
        pool, c["id"], "email", "first@example.com", is_primary=True
    )
    second = await _insert_legacy_contact_info(
        pool, c["id"], "email", "second@example.com", is_primary=False
    )

    # Verify baseline.
    row_before = await pool.fetchrow(
        "SELECT is_primary FROM public.contact_info WHERE id = $1", first["id"]
    )
    assert row_before["is_primary"] is True

    with pytest.raises(ContactInfoWriteBlockedError, match="read-only table public.contact_info"):
        await contact_info_update(pool, second["id"], is_primary=True)

    # No demote happened — first row remains primary.
    row_after = await pool.fetchrow(
        "SELECT is_primary FROM public.contact_info WHERE id = $1", first["id"]
    )
    assert row_after["is_primary"] is True
