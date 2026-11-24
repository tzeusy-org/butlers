"""Tests for contact_info tools — structured contact details for the relationship butler."""

from __future__ import annotations

import shutil
import sys
import uuid
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
async def rel_pool(db):
    """Core + memory + relationship + approvals chains, flat (owner gate parks actions)."""
    return await db("core", "memory", "relationship", "approvals")


@pytest.fixture
async def legacy_contact_info(rel_pool):
    """The retired ``public.contact_info`` table (dropped by core_115), for the legacy pins."""
    await rel_pool.execute("""
        CREATE TABLE public.contact_info (
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
    rel_pool,
    contact_id: uuid.UUID,
    type: str,
    value: str,
    *,
    label: str | None = None,
    is_primary: bool = False,
    context: str | None = None,
) -> dict[str, Any]:
    row = await rel_pool.fetchrow(
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
    rel_pool,
    entity_id: uuid.UUID,
    predicate: str,
    object_value: str,
    *,
    is_primary: bool = False,
) -> dict[str, Any]:
    """Insert a channel triple directly into relationship.entity_facts.

    Use this helper to seed test data for the migrated read paths
    (``channel_list``, ``channel_search``).  Mirrors what
    ``channel_add`` asserts via the central writer.
    """
    row = await rel_pool.fetchrow(
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


async def _fetch_contact_fact(rel_pool, entity_id: uuid.UUID, type: str, value: str):
    return await rel_pool.fetchrow(
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
# channel_add
# ------------------------------------------------------------------


async def test_channel_add_email(rel_pool):
    """channel_add stores an email triple."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "Alice")
    info = await channel_add(rel_pool, c["id"], "email", "alice@example.com")
    assert info["status"] == "asserted"
    assert info["type"] == "email"
    assert info["value"] == "alice@example.com"
    assert info["is_primary"] is False
    fact = await _fetch_contact_fact(rel_pool, c["entity_id"], "email", "alice@example.com")
    assert fact is not None
    assert fact["src"] == "relationship"


async def test_channel_add_with_label(rel_pool):
    """channel_add accepts legacy label input but writes a triple."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "Bob")
    info = await channel_add(rel_pool, c["id"], "phone", "+1-555-0100", label="Work")
    assert info["type"] == "phone"
    assert info["value"] == "+1-555-0100"
    assert "label" not in info
    fact = await _fetch_contact_fact(rel_pool, c["entity_id"], "phone", "+1-555-0100")
    assert fact is not None


async def test_channel_add_primary(rel_pool):
    """channel_add encodes primary on the triple."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "Charlie")
    info = await channel_add(rel_pool, c["id"], "email", "c@example.com", is_primary=True)
    assert info["is_primary"] is True
    fact = await _fetch_contact_fact(rel_pool, c["entity_id"], "email", "c@example.com")
    assert fact["primary"] is True


async def test_channel_add_primary_unsets_previous(rel_pool):
    """Primary is stored per asserted triple after the write-path cut-over."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "Diana")
    await channel_add(rel_pool, c["id"], "email", "d1@example.com", is_primary=True)
    await channel_add(rel_pool, c["id"], "email", "d2@example.com", is_primary=True)

    rows = await rel_pool.fetch(
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


async def test_channel_add_invalid_type(rel_pool):
    """channel_add rejects invalid info types."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "Eve")
    with pytest.raises(ValueError, match="Invalid contact info type"):
        await channel_add(rel_pool, c["id"], "fax", "555-0101")


async def test_channel_add_nonexistent_contact(rel_pool):
    """channel_add raises ValueError for nonexistent contact."""
    from butlers.tools.relationship import channel_add

    fake_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await channel_add(rel_pool, fake_id, "email", "nobody@example.com")


async def test_channel_add_all_types(rel_pool):
    """channel_add accepts all valid types and maps them to contact predicates."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "AllTypes")
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
        await channel_add(rel_pool, c["id"], t, v)

    facts = await rel_pool.fetch(
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
# channel_list
# ------------------------------------------------------------------


async def test_channel_list_all(rel_pool):
    """channel_list returns all info for a contact (reads entity_facts)."""
    from butlers.tools.relationship import channel_list, contact_create

    c = await contact_create(rel_pool, "ListAll")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "list@example.com")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-phone", "+1-555-0200")

    infos = await channel_list(rel_pool, c["id"])
    assert len(infos) == 2


async def test_channel_list_by_type(rel_pool):
    """channel_list filters by type when specified (reads entity_facts)."""
    from butlers.tools.relationship import channel_list, contact_create

    c = await contact_create(rel_pool, "ListByType")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "a@example.com")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "b@example.com")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-phone", "+1-555-0300")

    emails = await channel_list(rel_pool, c["id"], type="email")
    assert len(emails) == 2
    assert all(i["type"] == "email" for i in emails)

    phones = await channel_list(rel_pool, c["id"], type="phone")
    assert len(phones) == 1


async def test_channel_list_primary_first(rel_pool):
    """channel_list returns primary entries first (reads entity_facts)."""
    from butlers.tools.relationship import channel_list, contact_create

    c = await contact_create(rel_pool, "PrimaryFirst")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "secondary@example.com")
    await _insert_entity_fact(
        rel_pool, c["entity_id"], "has-email", "primary@example.com", is_primary=True
    )

    emails = await channel_list(rel_pool, c["id"], type="email")
    assert emails[0]["value"] == "primary@example.com"
    assert emails[0]["is_primary"] is True


async def test_channel_list_empty(rel_pool):
    """channel_list returns empty list when contact has no info."""
    from butlers.tools.relationship import channel_list, contact_create

    c = await contact_create(rel_pool, "NoInfo")
    infos = await channel_list(rel_pool, c["id"])
    assert infos == []


# ------------------------------------------------------------------
# channel_search (reverse lookup)
# ------------------------------------------------------------------


async def test_channel_search_exact(rel_pool):
    """channel_search finds a contact by exact email value (reads entity_facts)."""
    from butlers.tools.relationship import (
        channel_search,
        contact_create,
    )

    c = await contact_create(rel_pool, "SearchExact")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "searchexact@example.com")

    results = await channel_search(rel_pool, "searchexact@example.com")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


async def test_channel_search_partial(rel_pool):
    """channel_search supports partial matching (ILIKE) via entity_facts."""
    from butlers.tools.relationship import (
        channel_search,
        contact_create,
    )

    c = await contact_create(rel_pool, "SearchPartial")
    await _insert_entity_fact(
        rel_pool, c["entity_id"], "has-email", "partial_unique_xyz@example.com"
    )

    results = await channel_search(rel_pool, "partial_unique_xyz")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


async def test_channel_search_with_type_filter(rel_pool):
    """channel_search filters by type (predicate) when specified."""
    from butlers.tools.relationship import (
        channel_search,
        contact_create,
    )

    c = await contact_create(rel_pool, "SearchTyped")
    await _insert_entity_fact(
        rel_pool, c["entity_id"], "has-email", "typed_search_unique@example.com"
    )
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-phone", "+1-555-9999")

    # Search by email type only (maps to has-email)
    results = await channel_search(rel_pool, "typed_search_unique", type="email")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)

    # Search by phone type should not find email value
    results = await channel_search(rel_pool, "typed_search_unique", type="phone")
    assert not any(r["id"] == c["id"] for r in results)


async def test_channel_search_multiple_contacts(rel_pool):
    """channel_search finds multiple contacts sharing a domain (entity_facts)."""
    from butlers.tools.relationship import (
        channel_search,
        contact_create,
    )

    c1 = await contact_create(rel_pool, "Multi-A")
    c2 = await contact_create(rel_pool, "Multi-B")
    await _insert_entity_fact(rel_pool, c1["entity_id"], "has-email", "a@shareduniquedomain.com")
    await _insert_entity_fact(rel_pool, c2["entity_id"], "has-email", "b@shareduniquedomain.com")

    results = await channel_search(rel_pool, "shareduniquedomain.com")
    found_ids = {r["id"] for r in results}
    assert c1["id"] in found_ids
    assert c2["id"] in found_ids


# ------------------------------------------------------------------
# Legacy context metadata (ignored since the contact_info cut-over)
# ------------------------------------------------------------------


async def test_channel_add_work_domain_sets_context_work(rel_pool):
    """Legacy context heuristic is not written by channel_add after cut-over."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "WorkPerson")
    info = await channel_add(rel_pool, c["id"], "email", "owner@example.com")

    assert "context" not in info
    fact = await _fetch_contact_fact(rel_pool, c["entity_id"], "email", "owner@example.com")
    assert fact is not None


async def test_channel_add_personal_domain_leaves_context_null(rel_pool):
    """channel_add ignores legacy context metadata for non-work domains too."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "PersonalPerson")
    info = await channel_add(rel_pool, c["id"], "email", "bob@gmail.com")

    assert "context" not in info


async def test_channel_add_explicit_context_not_overridden(rel_pool):
    """Explicit legacy context is accepted for compatibility and ignored."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "ExplicitContext")
    info = await channel_add(rel_pool, c["id"], "email", "owner@example.com", context="personal")

    assert "context" not in info


async def test_channel_add_non_email_type_no_heuristic(rel_pool):
    """channel_add ignores legacy context metadata for non-email types."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "PhonePerson")
    info = await channel_add(rel_pool, c["id"], "phone", "+1-555-0200")

    assert "context" not in info


async def test_channel_add_invalid_context_raises(rel_pool):
    """channel_add accepts ignored legacy context values after cut-over."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "InvalidContextPerson")
    info = await channel_add(rel_pool, c["id"], "email", "x@example.com", context="bogus")
    assert info["status"] == "asserted"


async def test_channel_search_case_insensitive(rel_pool):
    """channel_search is case-insensitive (entity_facts ILIKE)."""
    from butlers.tools.relationship import (
        channel_search,
        contact_create,
    )

    c = await contact_create(rel_pool, "CaseTest")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "CaseUnique@Example.COM")

    results = await channel_search(rel_pool, "caseunique@example.com")
    assert any(r["id"] == c["id"] for r in results)


async def test_channel_search_excludes_archived(rel_pool):
    """channel_search excludes archived contacts.

    contact_archive() now sets entities.listed=false (bu-5nlh6), so
    channel_search (which filters on e.listed = true) correctly excludes
    archived contacts without a separate manual entity update.
    """
    from butlers.tools.relationship import (
        channel_search,
        contact_archive,
        contact_create,
    )

    c = await contact_create(rel_pool, "ArchivedSearch")
    await _insert_entity_fact(
        rel_pool, c["entity_id"], "has-email", "archivedsearch_unique@example.com"
    )
    await contact_archive(rel_pool, c["id"])
    # contact_archive now propagates the archive to entities.listed (bu-5nlh6);
    # no manual entity update needed here.

    results = await channel_search(rel_pool, "archivedsearch_unique@example.com")
    assert not any(r["id"] == c["id"] for r in results)


async def test_channel_search_no_results(rel_pool):
    """channel_search returns empty list when nothing matches."""
    from butlers.tools.relationship import channel_search

    results = await channel_search(rel_pool, "nonexistent_unique_value_xyz")
    assert results == []


async def test_channel_search_phone(rel_pool):
    """channel_search works for phone lookups (entity_facts)."""
    from butlers.tools.relationship import (
        channel_search,
        contact_create,
    )

    c = await contact_create(rel_pool, "PhoneLookup")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-phone", "+1-555-7777-unique")

    results = await channel_search(rel_pool, "+1-555-7777-unique", type="phone")
    assert len(results) >= 1
    assert any(r["id"] == c["id"] for r in results)


# ------------------------------------------------------------------
# Multi-value support
# ------------------------------------------------------------------


async def test_multiple_emails_per_contact(rel_pool):
    """A contact can have multiple email addresses (entity_facts)."""
    from butlers.tools.relationship import channel_list, contact_create

    c = await contact_create(rel_pool, "MultiEmail")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "work@example.com")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "personal@example.com")

    emails = await channel_list(rel_pool, c["id"], type="email")
    assert len(emails) == 2
    values = {i["value"] for i in emails}
    assert values == {"work@example.com", "personal@example.com"}


async def test_multiple_types_per_contact(rel_pool):
    """A contact can have multiple types of contact info (entity_facts).

    Telegram stored as has-handle with 'telegram:<id>' prefix; the returned
    type is 'telegram_user_id' (numeric id), not 'telegram'.
    """
    from butlers.tools.relationship import channel_list, contact_create

    c = await contact_create(rel_pool, "MultiType")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-email", "multi@example.com")
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-phone", "+1-555-0400")
    # Telegram stored with prefix per the entity_facts convention
    await _insert_entity_fact(rel_pool, c["entity_id"], "has-handle", "telegram:12345678")

    infos = await channel_list(rel_pool, c["id"])
    assert len(infos) == 3
    types = {i["type"] for i in infos}
    # has-handle with telegram: prefix → "telegram_user_id"; email → "email"; phone → "phone"
    assert types == {"email", "phone", "telegram_user_id"}


# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Cascade delete (application-layer)
# ------------------------------------------------------------------


async def test_contact_info_orphan_after_contact_delete(rel_pool, legacy_contact_info):
    """public.contact_info rows persist after contact deletion (no DB-level FK cascade).

    public.contact_info intentionally has no,
    since it lives in the shared schema and must be accessible from multiple butler schemas.
    Referential integrity is enforced at the application layer.  This test verifies the
    current DB behaviour: contact_info rows are NOT automatically removed when the parent
    contact is hard-deleted.  Application code that performs hard contact deletes must
    explicitly clean up public.contact_info rows.
    """
    from butlers.tools.relationship import contact_create

    c = await contact_create(rel_pool, "CascadeTest")
    await _insert_legacy_contact_info(rel_pool, c["id"], "email", "cascade@example.com")

    # Hard delete the contact — no FK cascade, so public.contact_info rows persist
    await rel_pool.execute("DELETE FROM contacts WHERE id = $1", c["id"])

    # Rows still exist: application layer must clean them up explicitly
    rows = await rel_pool.fetch("SELECT * FROM public.contact_info WHERE contact_id = $1", c["id"])
    assert len(rows) == 1, (
        "public.contact_info has no FK cascade; orphan rows persist after contact deletion"
    )


# ------------------------------------------------------------------
# Owner gate helpers
# ------------------------------------------------------------------


async def _make_owner_contact(rel_pool):
    """Create an owner entity + contact and return the contact row.

    Inserts a public.entities row with roles=['owner'], then inserts a contacts
    row referencing that entity, and populates contact_entity_map so that
    _entity_resolve can find the entity_id from the contact_id without reading
    public.contacts (contacts-schema retirement, bu-sf8l8).
    """
    entity_id = await rel_pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Owner', 'person', ARRAY['owner'])
        RETURNING id
        """
    )
    contact_id = await rel_pool.fetchval(
        """
        INSERT INTO contacts (name, first_name, listed, entity_id)
        VALUES ('Owner', 'Owner', true, $1)
        RETURNING id
        """,
        entity_id,
    )
    await rel_pool.execute(
        """
        INSERT INTO contact_entity_map (contact_id, entity_id)
        VALUES ($1, $2)
        ON CONFLICT (contact_id) DO NOTHING
        """,
        contact_id,
        entity_id,
    )
    return {"id": contact_id, "entity_id": entity_id}


# ------------------------------------------------------------------
# channel_add — owner gate
# ------------------------------------------------------------------


async def test_channel_add_owner_gate_parks_action(rel_pool, legacy_contact_info):
    """channel_add targeting the owner contact creates a pending_action instead of inserting.

    Replays the 2026-04-21 incident shape: runtime LLM calls channel_add
    with the owner's contact_id and a speculative work email address.
    Expected: no row in public.contact_info; one pending_actions row with status='pending'.
    """
    from butlers.tools.relationship.channel import channel_add

    owner = await _make_owner_contact(rel_pool)

    result = await channel_add(
        rel_pool,
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
    rows = await rel_pool.fetch(
        "SELECT * FROM public.contact_info WHERE contact_id = $1",
        owner["id"],
    )
    assert rows == [], "Owner-targeted channel_add must not write to public.contact_info"

    # pending_actions row created with correct shape
    action_id = result["action_id"]
    action = await rel_pool.fetchrow(
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


async def test_channel_add_non_owner_writes_immediately(rel_pool):
    """channel_add targeting a non-owner contact writes a fact immediately."""
    from butlers.tools.relationship import channel_add, contact_create

    c = await contact_create(rel_pool, "NonOwner")
    result = await channel_add(rel_pool, c["id"], "email", "nonowner@example.com")

    # Returns a real contact_info row, not a pending dict
    assert result.get("status") != "pending_approval"
    assert result["type"] == "email"
    assert result["value"] == "nonowner@example.com"

    fact = await _fetch_contact_fact(rel_pool, c["entity_id"], "email", "nonowner@example.com")
    assert fact is not None


# ------------------------------------------------------------------
# channel_add rollback test
# ------------------------------------------------------------------


async def test_channel_add_rollback_on_insert_failure(rel_pool, legacy_contact_info):
    """channel_add propagates central-writer failures without legacy writes."""
    from butlers.tools.relationship import contact_create
    from butlers.tools.relationship.channel import channel_add as _channel_add

    c = await contact_create(rel_pool, "TxnRollbackAdd")

    with (
        patch(
            "butlers.tools.relationship.channel.relationship_assert_fact",
            side_effect=RuntimeError("simulated central-writer failure"),
        ),
        pytest.raises(RuntimeError, match="simulated central-writer failure"),
    ):
        await _channel_add(
            rel_pool,
            c["id"],
            "email",
            "new@example.com",
            is_primary=True,
        )

    fact_count = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM relationship.entity_facts WHERE subject = $1",
        c["entity_id"],
    )
    legacy_count = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM public.contact_info WHERE contact_id = $1",
        c["id"],
    )
    assert fact_count == 0
    assert legacy_count == 0
