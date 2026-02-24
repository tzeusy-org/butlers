"""Tests for contact_info tools — structured contact details for the relationship butler."""

from __future__ import annotations

import shutil
import uuid

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with relationship + contact_info tables."""
    async with provisioned_postgres_pool() as p:
        # Create base relationship tables (from 001 migration)
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

        # Create shared schema and shared.contact_info
        # (moved from per-butler schema in contacts_002)
        await p.execute("CREATE SCHEMA IF NOT EXISTS shared")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS shared.contact_info (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL,
                type VARCHAR NOT NULL,
                value TEXT NOT NULL,
                label VARCHAR,
                is_primary BOOLEAN DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_contact_info_type_value
                ON shared.contact_info (type, value)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_contact_info_contact_id
                ON shared.contact_info (contact_id)
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
    """shared.contact_info rows persist after contact deletion (no DB-level FK cascade).

    shared.contact_info intentionally has no REFERENCES contacts(id) ON DELETE CASCADE,
    since it lives in the shared schema and must be accessible from multiple butler schemas.
    Referential integrity is enforced at the application layer.  This test verifies the
    current DB behaviour: contact_info rows are NOT automatically removed when the parent
    contact is hard-deleted.  Application code that performs hard contact deletes must
    explicitly clean up shared.contact_info rows.
    """
    from butlers.tools.relationship import contact_create, contact_info_add

    c = await contact_create(pool, "CascadeTest")
    await contact_info_add(pool, c["id"], "email", "cascade@example.com")

    # Hard delete the contact — no FK cascade, so shared.contact_info rows persist
    await pool.execute("DELETE FROM contacts WHERE id = $1", c["id"])

    # Rows still exist: application layer must clean them up explicitly
    rows = await pool.fetch("SELECT * FROM shared.contact_info WHERE contact_id = $1", c["id"])
    assert len(rows) == 1, (
        "shared.contact_info has no FK cascade; orphan rows persist after contact deletion"
    )
