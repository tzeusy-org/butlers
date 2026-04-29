"""Tests for contact_info tools — structured contact details for the relationship butler."""

from __future__ import annotations

import shutil
import sys
import uuid
from contextlib import asynccontextmanager
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
        await p.execute("""
            CREATE TABLE IF NOT EXISTS activity_feed (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                summary TEXT NOT NULL,
                entity_type TEXT,
                entity_id UUID,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_feed_contact_created
                ON activity_feed (contact_id, created_at)
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
                approval_rule_id UUID
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
                sensitivity TEXT NOT NULL DEFAULT 'normal'
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


# ------------------------------------------------------------------
# contact_info_add
# ------------------------------------------------------------------


async def test_contact_info_add_email(pool):
    """contact_info_add stores an email entry."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Alice")
    info = await contact_info_add(pool, c["id"], "email", "alice@example.com")
    assert info["contact_id"] == c["id"]
    assert info["type"] == "email"
    assert info["value"] == "alice@example.com"
    assert info["is_primary"] is False
    assert info["label"] is None


async def test_contact_info_add_with_label(pool):
    """contact_info_add stores an entry with a label."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Bob")
    info = await contact_info_add(pool, c["id"], "phone", "+1-555-0100", label="Work")
    assert info["label"] == "Work"
    assert info["type"] == "phone"
    assert info["value"] == "+1-555-0100"


async def test_contact_info_add_primary(pool):
    """contact_info_add can mark an entry as primary."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "Charlie")
    info = await contact_info_add(pool, c["id"], "email", "c@example.com", is_primary=True)
    assert info["is_primary"] is True


async def test_contact_info_add_primary_unsets_previous(pool):
    """Setting a new primary unsets the previous primary of the same type."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

    c = await contact_create(pool, "Diana")
    await contact_info_add(pool, c["id"], "email", "d1@example.com", is_primary=True)
    await contact_info_add(pool, c["id"], "email", "d2@example.com", is_primary=True)

    infos = await contact_info_list(pool, c["id"], type="email")
    primary_entries = [i for i in infos if i["is_primary"]]
    assert len(primary_entries) == 1
    assert primary_entries[0]["value"] == "d2@example.com"


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
    """contact_info_add accepts all valid types."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

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

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 7
    stored_types = {i["type"] for i in infos}
    assert stored_types == {"email", "phone", "telegram", "linkedin", "twitter", "website", "other"}


# ------------------------------------------------------------------
# contact_info_list
# ------------------------------------------------------------------


async def test_contact_info_list_all(pool):
    """contact_info_list returns all info for a contact."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

    c = await contact_create(pool, "ListAll")
    await contact_info_add(pool, c["id"], "email", "list@example.com")
    await contact_info_add(pool, c["id"], "phone", "+1-555-0200")

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 2


async def test_contact_info_list_by_type(pool):
    """contact_info_list filters by type when specified."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

    c = await contact_create(pool, "ListByType")
    await contact_info_add(pool, c["id"], "email", "a@example.com")
    await contact_info_add(pool, c["id"], "email", "b@example.com")
    await contact_info_add(pool, c["id"], "phone", "+1-555-0300")

    emails = await contact_info_list(pool, c["id"], type="email")
    assert len(emails) == 2
    assert all(i["type"] == "email" for i in emails)

    phones = await contact_info_list(pool, c["id"], type="phone")
    assert len(phones) == 1


async def test_contact_info_list_primary_first(pool):
    """contact_info_list returns primary entries first."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

    c = await contact_create(pool, "PrimaryFirst")
    await contact_info_add(pool, c["id"], "email", "secondary@example.com")
    await contact_info_add(pool, c["id"], "email", "primary@example.com", is_primary=True)

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
    """contact_info_remove deletes an entry."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_info_list,
        contact_info_remove,
    )

    c = await contact_create(pool, "RemoveMe")
    info = await contact_info_add(pool, c["id"], "email", "remove@example.com")

    await contact_info_remove(pool, info["id"])

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 0


async def test_contact_info_remove_nonexistent(pool):
    """contact_info_remove raises ValueError for nonexistent entry."""
    from butlers.tools.relationship import contact_info_remove

    with pytest.raises(ValueError, match="not found"):
        await contact_info_remove(pool, uuid.uuid4())


async def test_contact_info_remove_keeps_others(pool):
    """contact_info_remove only deletes the specified entry."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_info_list,
        contact_info_remove,
    )

    c = await contact_create(pool, "KeepOthers")
    info1 = await contact_info_add(pool, c["id"], "email", "keep1@example.com")
    await contact_info_add(pool, c["id"], "email", "keep2@example.com")

    await contact_info_remove(pool, info1["id"])

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 1
    assert infos[0]["value"] == "keep2@example.com"


# ------------------------------------------------------------------
# contact_search_by_info (reverse lookup)
# ------------------------------------------------------------------


async def test_contact_search_by_info_exact(pool):
    """contact_search_by_info finds a contact by exact email value."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c = await contact_create(pool, "SearchExact")
    await contact_info_add(pool, c["id"], "email", "searchexact@example.com")

    results = await contact_search_by_info(pool, "searchexact@example.com")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_partial(pool):
    """contact_search_by_info supports partial matching (ILIKE)."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c = await contact_create(pool, "SearchPartial")
    await contact_info_add(pool, c["id"], "email", "partial_unique_xyz@example.com")

    results = await contact_search_by_info(pool, "partial_unique_xyz")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_with_type_filter(pool):
    """contact_search_by_info filters by type when specified."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c = await contact_create(pool, "SearchTyped")
    await contact_info_add(pool, c["id"], "email", "typed_search_unique@example.com")
    await contact_info_add(pool, c["id"], "phone", "+1-555-9999")

    # Search by email type only
    results = await contact_search_by_info(pool, "typed_search_unique", type="email")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)

    # Search by phone type should not find email value
    results = await contact_search_by_info(pool, "typed_search_unique", type="phone")
    assert not any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_multiple_contacts(pool):
    """contact_search_by_info finds multiple contacts sharing a domain."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c1 = await contact_create(pool, "Multi-A")
    c2 = await contact_create(pool, "Multi-B")
    await contact_info_add(pool, c1["id"], "email", "a@shareduniquedomain.com")
    await contact_info_add(pool, c2["id"], "email", "b@shareduniquedomain.com")

    results = await contact_search_by_info(pool, "shareduniquedomain.com")
    found_ids = {r["id"] for r in results}
    assert c1["id"] in found_ids
    assert c2["id"] in found_ids


# ------------------------------------------------------------------
# Work-domain heuristic (context auto-detection)
# ------------------------------------------------------------------


async def test_contact_info_add_work_domain_sets_context_work(pool, monkeypatch):
    """Email at a known work domain gets context='work' automatically."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "WorkPerson")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    info = await contact_info_add(pool, c["id"], "email", "alice@example.com")

    assert info["context"] == "work"


async def test_contact_info_add_personal_domain_leaves_context_null(pool, monkeypatch):
    """Email at a non-work domain leaves context as None."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "PersonalPerson")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    info = await contact_info_add(pool, c["id"], "email", "bob@gmail.com")

    assert info["context"] is None


async def test_contact_info_add_explicit_context_not_overridden(pool, monkeypatch):
    """Explicit context='personal' on a work-domain email is respected."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "ExplicitContext")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    info = await contact_info_add(pool, c["id"], "email", "boss@example.com", context="personal")

    assert info["context"] == "personal"


async def test_contact_info_add_non_email_type_no_heuristic(pool, monkeypatch):
    """Work-domain heuristic does not apply to non-email types."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "PhonePerson")
    monkeypatch.setenv("BUTLERS_WORK_DOMAINS", "example.com")
    # Phone value happens to look like a domain — should not be classified
    info = await contact_info_add(pool, c["id"], "phone", "+1-555-0200")

    assert info["context"] is None


async def test_contact_info_add_invalid_context_raises(pool):
    """contact_info_add raises ValueError for an unrecognised context value."""
    import pytest

    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "InvalidContextPerson")
    with pytest.raises(ValueError, match="Invalid context"):
        await contact_info_add(pool, c["id"], "email", "x@example.com", context="bogus")


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
    """contact_search_by_info is case-insensitive."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c = await contact_create(pool, "CaseTest")
    await contact_info_add(pool, c["id"], "email", "CaseUnique@Example.COM")

    results = await contact_search_by_info(pool, "caseunique@example.com")
    assert any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_excludes_archived(pool):
    """contact_search_by_info excludes archived contacts."""
    from butlers.tools.relationship import (
        contact_archive,
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c = await contact_create(pool, "ArchivedSearch")
    await contact_info_add(pool, c["id"], "email", "archivedsearch_unique@example.com")
    await contact_archive(pool, c["id"])

    results = await contact_search_by_info(pool, "archivedsearch_unique@example.com")
    assert not any(r["id"] == c["id"] for r in results)


async def test_contact_search_by_info_no_results(pool):
    """contact_search_by_info returns empty list when nothing matches."""
    from butlers.tools.relationship import contact_search_by_info

    results = await contact_search_by_info(pool, "nonexistent_unique_value_xyz")
    assert results == []


async def test_contact_search_by_info_phone(pool):
    """contact_search_by_info works for phone lookups."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_search_by_info,
    )

    c = await contact_create(pool, "PhoneLookup")
    await contact_info_add(pool, c["id"], "phone", "+1-555-7777-unique")

    results = await contact_search_by_info(pool, "+1-555-7777-unique", type="phone")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


# ------------------------------------------------------------------
# Multi-value support
# ------------------------------------------------------------------


async def test_multiple_emails_per_contact(pool):
    """A contact can have multiple email addresses."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

    c = await contact_create(pool, "MultiEmail")
    await contact_info_add(pool, c["id"], "email", "work@example.com", label="Work")
    await contact_info_add(pool, c["id"], "email", "personal@example.com", label="Personal")

    emails = await contact_info_list(pool, c["id"], type="email")
    assert len(emails) == 2
    values = {i["value"] for i in emails}
    assert values == {"work@example.com", "personal@example.com"}


async def test_multiple_types_per_contact(pool):
    """A contact can have multiple types of contact info."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list

    c = await contact_create(pool, "MultiType")
    await contact_info_add(pool, c["id"], "email", "multi@example.com")
    await contact_info_add(pool, c["id"], "phone", "+1-555-0400")
    await contact_info_add(pool, c["id"], "telegram", "@multitype")

    infos = await contact_info_list(pool, c["id"])
    assert len(infos) == 3
    types = {i["type"] for i in infos}
    assert types == {"email", "phone", "telegram"}


# ------------------------------------------------------------------
# Activity feed integration
# ------------------------------------------------------------------


async def test_contact_info_add_logs_activity(pool):
    """contact_info_add logs an activity feed entry."""
    from butlers.tools.relationship import contact_create, contact_info_add, feed_get

    c = await contact_create(pool, "FeedAdd")
    await contact_info_add(pool, c["id"], "email", "feed@example.com", label="Work")

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "contact_info_added" in actions

    info_entries = [f for f in feed if f["action"] == "contact_info_added"]
    assert len(info_entries) == 1
    assert "email" in info_entries[0]["summary"]
    assert "feed@example.com" in info_entries[0]["summary"]
    assert "Work" in info_entries[0]["summary"]


async def test_contact_info_remove_logs_activity(pool):
    """contact_info_remove logs an activity feed entry."""
    from butlers.tools.relationship import (
        contact_create,
        contact_info_add,
        contact_info_remove,
        feed_get,
    )

    c = await contact_create(pool, "FeedRemove")
    info = await contact_info_add(pool, c["id"], "phone", "+1-555-0500")
    await contact_info_remove(pool, info["id"])

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "contact_info_removed" in actions

    remove_entries = [f for f in feed if f["action"] == "contact_info_removed"]
    assert len(remove_entries) == 1
    assert "phone" in remove_entries[0]["summary"]
    assert "+1-555-0500" in remove_entries[0]["summary"]


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
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "CascadeTest")
    await contact_info_add(pool, c["id"], "email", "cascade@example.com")

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
    assert action["tool_name"] == "contact_info_add"
    assert action["status"] == "pending"
    import json as _json

    args = _json.loads(action["tool_args"])
    assert args["value"] == "owner@example.com"
    assert args["type"] == "email"
    assert str(args["contact_id"]) == str(owner["id"])


async def test_contact_info_add_non_owner_writes_immediately(pool):
    """contact_info_add targeting a non-owner contact writes immediately (no gate)."""
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "NonOwner")
    result = await contact_info_add(pool, c["id"], "email", "nonowner@example.com")

    # Returns a real contact_info row, not a pending dict
    assert result.get("status") != "pending_approval"
    assert result["type"] == "email"
    assert result["value"] == "nonowner@example.com"

    # Row exists in DB
    rows = await pool.fetch(
        "SELECT * FROM public.contact_info WHERE contact_id = $1",
        c["id"],
    )
    assert len(rows) == 1


async def test_contact_info_add_owner_gate_does_not_log_activity(pool):
    """contact_info_add blocked for owner must not log an activity_feed entry."""
    from butlers.tools.relationship.contact_info import contact_info_add

    owner = await _make_owner_contact(pool)
    await contact_info_add(pool, owner["id"], "email", "blocked@example.com")

    feed_rows = await pool.fetch(
        "SELECT * FROM activity_feed WHERE contact_id = $1",
        owner["id"],
    )
    assert feed_rows == [], "No activity_feed entry expected when gate blocks the mutation"


# ------------------------------------------------------------------
# contact_info_update — new tool
# ------------------------------------------------------------------


async def test_contact_info_update_value(pool):
    """contact_info_update can change the value of a non-owner entry."""
    from butlers.tools.relationship import contact_create, contact_info_add
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateValue")
    info = await contact_info_add(pool, c["id"], "email", "old@example.com")

    updated = await contact_info_update(pool, info["id"], value="new@example.com")
    assert updated["value"] == "new@example.com"
    assert updated["type"] == "email"


async def test_contact_info_update_label(pool):
    """contact_info_update can change the label of a non-owner entry."""
    from butlers.tools.relationship import contact_create, contact_info_add
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateLabel")
    info = await contact_info_add(pool, c["id"], "phone", "+1-555-0001", label="Home")

    updated = await contact_info_update(pool, info["id"], label="Mobile")
    assert updated["label"] == "Mobile"


async def test_contact_info_update_is_primary(pool):
    """contact_info_update can promote an entry to primary and demotes the previous primary."""
    from butlers.tools.relationship import contact_create, contact_info_add, contact_info_list
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdatePrimary")
    i1 = await contact_info_add(pool, c["id"], "email", "first@example.com", is_primary=True)
    i2 = await contact_info_add(pool, c["id"], "email", "second@example.com", is_primary=False)

    await contact_info_update(pool, i2["id"], is_primary=True)

    emails = await contact_info_list(pool, c["id"], type="email")
    primary = [e for e in emails if e["is_primary"]]
    assert len(primary) == 1
    assert primary[0]["id"] == i2["id"]

    # First entry is no longer primary
    first = next(e for e in emails if e["id"] == i1["id"])
    assert first["is_primary"] is False


async def test_contact_info_update_no_fields_raises(pool):
    """contact_info_update raises ValueError when no fields are provided."""
    from butlers.tools.relationship import contact_create, contact_info_add
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateNoFields")
    info = await contact_info_add(pool, c["id"], "email", "noupdate@example.com")

    with pytest.raises(ValueError, match="At least one"):
        await contact_info_update(pool, info["id"])


async def test_contact_info_update_nonexistent_raises(pool):
    """contact_info_update raises ValueError for nonexistent entry."""
    from butlers.tools.relationship.contact_info import contact_info_update

    with pytest.raises(ValueError, match="not found"):
        await contact_info_update(pool, uuid.uuid4(), value="x@example.com")


async def test_contact_info_update_logs_activity(pool):
    """contact_info_update logs an activity_feed entry on success."""
    from butlers.tools.relationship import contact_create, contact_info_add, feed_get
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateActivity")
    info = await contact_info_add(pool, c["id"], "email", "activity@example.com")

    await contact_info_update(pool, info["id"], value="updated@example.com")

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "contact_info_updated" in actions


async def test_contact_info_update_owner_gate_parks_action(pool):
    """contact_info_update targeting the owner contact creates a pending_action."""
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

    result = await contact_info_update(pool, info_id, value="new-owner@example.com")

    # Returns pending_approval, not the updated row
    assert result["status"] == "pending_approval"
    assert "action_id" in result

    # DB value unchanged
    row = await pool.fetchrow("SELECT value FROM public.contact_info WHERE id = $1", info_id)
    assert row["value"] == "old-owner@example.com"

    # pending_actions row created
    import json as _json

    action = await pool.fetchrow(
        "SELECT * FROM pending_actions WHERE id = $1::uuid",
        result["action_id"],
    )
    assert action is not None
    assert action["tool_name"] == "contact_info_update"
    assert action["status"] == "pending"
    args = _json.loads(action["tool_args"])
    assert args["value"] == "new-owner@example.com"


async def test_contact_info_update_non_owner_writes_immediately(pool):
    """contact_info_update targeting a non-owner contact writes immediately."""
    from butlers.tools.relationship import contact_create, contact_info_add
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "UpdateNonOwner")
    info = await contact_info_add(pool, c["id"], "email", "nonowner-update@example.com")

    result = await contact_info_update(pool, info["id"], value="updated-nonowner@example.com")

    assert result.get("status") != "pending_approval"
    assert result["value"] == "updated-nonowner@example.com"


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
    """contact_info_add rolls back the demote-primary UPDATE when the INSERT fails.

    Sets up a contact with an existing primary email, then injects a failure on the
    INSERT RETURNING fetchrow so only the demote UPDATE ran before the error.  Asserts
    that after the exception propagates the original primary row is still marked
    is_primary=True — demonstrating that the transaction wraps both operations atomically.
    """
    from butlers.tools.relationship import contact_create, contact_info_add
    from butlers.tools.relationship.contact_info import contact_info_add as _contact_info_add

    c = await contact_create(pool, "TxnRollbackAdd")
    existing = await contact_info_add(
        pool, c["id"], "email", "original@example.com", is_primary=True
    )

    # Verify baseline: original row is primary.
    row_before = await pool.fetchrow(
        "SELECT is_primary FROM public.contact_info WHERE id = $1", existing["id"]
    )
    assert row_before["is_primary"] is True

    failing_pool = _PoolWithFailingConn(pool, RuntimeError("simulated INSERT failure"))

    with pytest.raises(RuntimeError, match="simulated INSERT failure"):
        await _contact_info_add(
            failing_pool,  # type: ignore[arg-type]
            c["id"],
            "email",
            "new@example.com",
            is_primary=True,
        )

    # The demote UPDATE must have been rolled back — original row still primary.
    row_after = await pool.fetchrow(
        "SELECT is_primary FROM public.contact_info WHERE id = $1", existing["id"]
    )
    assert row_after["is_primary"] is True, (
        "Transaction rollback failed: demote UPDATE was not rolled back when INSERT raised"
    )


async def test_contact_info_update_rollback_on_update_failure(pool):
    """contact_info_update rolls back the demote-primary UPDATE when the main UPDATE fails.

    Sets up a contact with two emails — the first marked primary.  Injects a failure on
    the main UPDATE RETURNING fetchrow after the demote-primary execute has already run.
    Asserts that the original primary row is still marked is_primary=True.
    """
    from butlers.tools.relationship import contact_create, contact_info_add
    from butlers.tools.relationship.contact_info import contact_info_update

    c = await contact_create(pool, "TxnRollbackUpdate")
    first = await contact_info_add(pool, c["id"], "email", "first@example.com", is_primary=True)
    second = await contact_info_add(pool, c["id"], "email", "second@example.com", is_primary=False)

    # Verify baseline.
    row_before = await pool.fetchrow(
        "SELECT is_primary FROM public.contact_info WHERE id = $1", first["id"]
    )
    assert row_before["is_primary"] is True

    failing_pool = _PoolWithFailingConn(pool, RuntimeError("simulated UPDATE failure"))

    with pytest.raises(RuntimeError, match="simulated UPDATE failure"):
        await contact_info_update(
            failing_pool,  # type: ignore[arg-type]
            second["id"],
            is_primary=True,
        )

    # Demote of first must have been rolled back — first row still primary.
    row_after = await pool.fetchrow(
        "SELECT is_primary FROM public.contact_info WHERE id = $1", first["id"]
    )
    assert row_after["is_primary"] is True, (
        "Transaction rollback failed: demote UPDATE was not rolled back when main UPDATE raised"
    )
