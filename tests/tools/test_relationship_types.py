"""Tests for relationship type taxonomy — typed relationships with auto-reverse labels."""

from __future__ import annotations

import shutil
import uuid

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# Seed data matching the migration
_SEED_TYPES = [
    ("Love", "spouse", "spouse"),
    ("Love", "partner", "partner"),
    ("Love", "ex-partner", "ex-partner"),
    ("Family", "parent", "child"),
    ("Family", "sibling", "sibling"),
    ("Family", "grandparent", "grandchild"),
    ("Family", "uncle/aunt", "nephew/niece"),
    ("Family", "cousin", "cousin"),
    ("Family", "in-law", "in-law"),
    ("Friend", "friend", "friend"),
    ("Friend", "best friend", "best friend"),
    ("Work", "colleague", "colleague"),
    ("Work", "boss", "subordinate"),
    ("Work", "mentor", "protege"),
    ("Custom", "custom", "custom"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with relationship tables + relationship_types."""
    async with provisioned_postgres_pool() as p:
        # Create base tables (from 001_relationship_tables)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                details JSONB DEFAULT '{}',
                archived_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS activity_feed (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)

        # Create relationship_types table (from 002_relationship_types)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship_types (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                "group" VARCHAR NOT NULL,
                forward_label VARCHAR NOT NULL,
                reverse_label VARCHAR NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE (forward_label, reverse_label)
            )
        """)

        # Seed relationship types
        for group, forward, reverse in _SEED_TYPES:
            await p.execute(
                """
                INSERT INTO relationship_types ("group", forward_label, reverse_label)
                VALUES ($1, $2, $3)
                ON CONFLICT (forward_label, reverse_label) DO NOTHING
                """,
                group,
                forward,
                reverse,
            )

        # Create relationships table WITH relationship_type_id FK
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_a UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                contact_b UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                relationship_type_id UUID REFERENCES relationship_types(id) ON DELETE SET NULL,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)

        yield p


@pytest.fixture
async def two_contacts(pool):
    """Create two contacts and return their dicts."""
    from butlers.tools.relationship import contact_create

    alice = await contact_create(pool, "Alice")
    bob = await contact_create(pool, "Bob")
    return alice, bob


# ------------------------------------------------------------------
# relationship_types_list
# ------------------------------------------------------------------


class TestRelationshipTypesList:
    async def test_returns_all_groups(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        assert "Love" in grouped
        assert "Family" in grouped
        assert "Friend" in grouped
        assert "Work" in grouped
        assert "Custom" in grouped

    async def test_love_group_contents(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        love_labels = {t["forward_label"] for t in grouped["Love"]}
        assert love_labels == {"spouse", "partner", "ex-partner"}

    async def test_family_group_contents(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        family_labels = {t["forward_label"] for t in grouped["Family"]}
        assert family_labels == {
            "parent",
            "sibling",
            "grandparent",
            "uncle/aunt",
            "cousin",
            "in-law",
        }

    async def test_work_group_contents(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        work_labels = {t["forward_label"] for t in grouped["Work"]}
        assert work_labels == {"colleague", "boss", "mentor"}

    async def test_friend_group_contents(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        friend_labels = {t["forward_label"] for t in grouped["Friend"]}
        assert friend_labels == {"friend", "best friend"}

    async def test_filter_by_group(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool, group="Work")
        assert list(grouped.keys()) == ["Work"]
        assert len(grouped["Work"]) == 3

    async def test_filter_by_nonexistent_group(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool, group="Nonexistent")
        assert grouped == {}

    async def test_each_type_has_id_and_labels(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        for group_name, types in grouped.items():
            for t in types:
                assert "id" in t
                assert "forward_label" in t
                assert "reverse_label" in t
                # group should have been popped from individual entries
                assert "group" not in t

    async def test_asymmetric_types_have_different_labels(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        # parent -> child is asymmetric
        parent_type = next(t for t in grouped["Family"] if t["forward_label"] == "parent")
        assert parent_type["reverse_label"] == "child"

        # boss -> subordinate is asymmetric
        boss_type = next(t for t in grouped["Work"] if t["forward_label"] == "boss")
        assert boss_type["reverse_label"] == "subordinate"

    async def test_symmetric_types_have_same_labels(self, pool):
        from butlers.tools.relationship import relationship_types_list

        grouped = await relationship_types_list(pool)
        # sibling -> sibling is symmetric
        sibling_type = next(t for t in grouped["Family"] if t["forward_label"] == "sibling")
        assert sibling_type["reverse_label"] == "sibling"

        # friend -> friend is symmetric
        friend_type = next(t for t in grouped["Friend"] if t["forward_label"] == "friend")
        assert friend_type["reverse_label"] == "friend"


# ------------------------------------------------------------------
# relationship_type_get
# ------------------------------------------------------------------


class TestRelationshipTypeGet:
    async def test_get_existing_type(self, pool):
        from butlers.tools.relationship import relationship_type_get, relationship_types_list

        grouped = await relationship_types_list(pool)
        first_type = grouped["Love"][0]
        result = await relationship_type_get(pool, first_type["id"])
        assert result is not None
        assert result["forward_label"] == first_type["forward_label"]

    async def test_get_nonexistent_type(self, pool):
        from butlers.tools.relationship import relationship_type_get

        result = await relationship_type_get(pool, uuid.uuid4())
        assert result is None


# ------------------------------------------------------------------
# relationship_add with type_id (typed relationships)
# ------------------------------------------------------------------


class TestRelationshipAddTyped:
    async def test_add_symmetric_relationship_by_type_id(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        friend_type = next(t for t in grouped["Friend"] if t["forward_label"] == "friend")

        result = await relationship_add(pool, alice["id"], bob["id"], type_id=friend_type["id"])
        assert result["type"] == "friend"
        assert result["relationship_type_id"] == friend_type["id"]

        # Check both directions have same label for symmetric type
        alice_rels = await relationship_list(pool, alice["id"])
        bob_rels = await relationship_list(pool, bob["id"])
        assert alice_rels[-1]["type"] == "friend"
        assert bob_rels[-1]["type"] == "friend"

    async def test_add_asymmetric_relationship_auto_reverse(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        parent_type = next(t for t in grouped["Family"] if t["forward_label"] == "parent")

        # Alice is parent of Bob
        result = await relationship_add(pool, alice["id"], bob["id"], type_id=parent_type["id"])
        assert result["type"] == "parent"

        # From Alice's perspective: she is "parent" of Bob
        alice_rels = await relationship_list(pool, alice["id"])
        parent_rel = [r for r in alice_rels if r["related_name"] == "Bob"]
        assert any(r["type"] == "parent" for r in parent_rel)

        # From Bob's perspective: Alice is his "child" -> wait, reverse should be "child"
        bob_rels = await relationship_list(pool, bob["id"])
        alice_rel = [r for r in bob_rels if r["related_name"] == "Alice"]
        assert any(r["type"] == "child" for r in alice_rel)

    async def test_add_boss_subordinate_auto_reverse(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        boss_type = next(t for t in grouped["Work"] if t["forward_label"] == "boss")

        # Alice is boss of Bob
        await relationship_add(pool, alice["id"], bob["id"], type_id=boss_type["id"])

        alice_rels = await relationship_list(pool, alice["id"])
        bob_rels = await relationship_list(pool, bob["id"])

        alice_to_bob = [r for r in alice_rels if r["related_name"] == "Bob"]
        bob_to_alice = [r for r in bob_rels if r["related_name"] == "Alice"]

        assert any(r["type"] == "boss" for r in alice_to_bob)
        assert any(r["type"] == "subordinate" for r in bob_to_alice)

    async def test_add_mentor_protege_auto_reverse(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        mentor_type = next(t for t in grouped["Work"] if t["forward_label"] == "mentor")

        await relationship_add(pool, alice["id"], bob["id"], type_id=mentor_type["id"])

        alice_rels = await relationship_list(pool, alice["id"])
        bob_rels = await relationship_list(pool, bob["id"])

        alice_to_bob = [r for r in alice_rels if r["related_name"] == "Bob"]
        bob_to_alice = [r for r in bob_rels if r["related_name"] == "Alice"]

        assert any(r["type"] == "mentor" for r in alice_to_bob)
        assert any(r["type"] == "protege" for r in bob_to_alice)

    async def test_add_grandparent_grandchild_auto_reverse(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        gp_type = next(t for t in grouped["Family"] if t["forward_label"] == "grandparent")

        await relationship_add(pool, alice["id"], bob["id"], type_id=gp_type["id"])

        alice_rels = await relationship_list(pool, alice["id"])
        bob_rels = await relationship_list(pool, bob["id"])

        alice_to_bob = [r for r in alice_rels if r["related_name"] == "Bob"]
        bob_to_alice = [r for r in bob_rels if r["related_name"] == "Alice"]

        assert any(r["type"] == "grandparent" for r in alice_to_bob)
        assert any(r["type"] == "grandchild" for r in bob_to_alice)

    async def test_invalid_type_id_raises(self, pool, two_contacts):
        from butlers.tools.relationship import relationship_add

        alice, bob = two_contacts
        with pytest.raises(ValueError, match="not found"):
            await relationship_add(pool, alice["id"], bob["id"], type_id=uuid.uuid4())

    async def test_relationship_type_id_stored_in_row(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        spouse_type = next(t for t in grouped["Love"] if t["forward_label"] == "spouse")

        result = await relationship_add(pool, alice["id"], bob["id"], type_id=spouse_type["id"])
        assert result["relationship_type_id"] == spouse_type["id"]

    async def test_notes_preserved_with_typed_relationship(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        colleague_type = next(t for t in grouped["Work"] if t["forward_label"] == "colleague")

        result = await relationship_add(
            pool,
            alice["id"],
            bob["id"],
            type_id=colleague_type["id"],
            notes="Same team since 2024",
        )
        assert result["notes"] == "Same team since 2024"

        bob_rels = await relationship_list(pool, bob["id"])
        alice_rel = [r for r in bob_rels if r["related_name"] == "Alice"]
        assert any(r["notes"] == "Same team since 2024" for r in alice_rel)


# ------------------------------------------------------------------
# Backward compatibility: freetext type parameter
# ------------------------------------------------------------------


class TestRelationshipAddBackwardCompat:
    async def test_freetext_exact_match_resolves_to_type(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
        )

        alice, bob = two_contacts
        # "friend" should match the Friend/friend type
        result = await relationship_add(pool, alice["id"], bob["id"], type="friend")
        assert result["type"] == "friend"
        assert result["relationship_type_id"] is not None

    async def test_freetext_case_insensitive_match(self, pool, two_contacts):
        from butlers.tools.relationship import relationship_add

        alice, bob = two_contacts
        result = await relationship_add(pool, alice["id"], bob["id"], type="BOSS")
        assert result["type"] == "boss"

    async def test_freetext_asymmetric_gets_correct_reverse(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
        )

        alice, bob = two_contacts
        # Using freetext "parent" should still auto-reverse to "child"
        await relationship_add(pool, alice["id"], bob["id"], type="parent")

        bob_rels = await relationship_list(pool, bob["id"])
        alice_rel = [r for r in bob_rels if r["related_name"] == "Alice"]
        assert any(r["type"] == "child" for r in alice_rel)

    async def test_freetext_unknown_falls_back_to_custom(self, pool, two_contacts):
        from butlers.tools.relationship import relationship_add

        alice, bob = two_contacts
        result = await relationship_add(pool, alice["id"], bob["id"], type="neighbor")
        # Falls back to custom type
        assert result["type"] == "custom"
        assert result["relationship_type_id"] is not None

    async def test_freetext_matches_reverse_label_too(self, pool, two_contacts):
        from butlers.tools.relationship import relationship_add

        alice, bob = two_contacts
        # "child" is a reverse_label, should still resolve
        result = await relationship_add(pool, alice["id"], bob["id"], type="child")
        # Should match the parent/child type
        assert result["relationship_type_id"] is not None

    async def test_neither_type_nor_type_id_raises(self, pool, two_contacts):
        from butlers.tools.relationship import relationship_add

        alice, bob = two_contacts
        with pytest.raises(ValueError, match="Either type_id or type"):
            await relationship_add(pool, alice["id"], bob["id"])


# ------------------------------------------------------------------
# relationship_remove still works with typed relationships
# ------------------------------------------------------------------


class TestRelationshipRemoveTyped:
    async def test_remove_typed_relationship(self, pool, two_contacts):
        from butlers.tools.relationship import (
            relationship_add,
            relationship_list,
            relationship_remove,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        friend_type = next(t for t in grouped["Friend"] if t["forward_label"] == "friend")

        await relationship_add(pool, alice["id"], bob["id"], type_id=friend_type["id"])

        # Verify exists
        alice_rels = await relationship_list(pool, alice["id"])
        assert any(r["related_name"] == "Bob" for r in alice_rels)

        # Remove
        await relationship_remove(pool, alice["id"], bob["id"])

        # Verify both directions removed
        alice_rels = await relationship_list(pool, alice["id"])
        bob_rels = await relationship_list(pool, bob["id"])
        assert not any(r["related_name"] == "Bob" for r in alice_rels)
        assert not any(r["related_name"] == "Alice" for r in bob_rels)


# ------------------------------------------------------------------
# Activity feed integration with typed relationships
# ------------------------------------------------------------------


class TestActivityFeedTyped:
    async def test_activity_feed_logs_typed_relationship(self, pool, two_contacts):
        from butlers.tools.relationship import (
            feed_get,
            relationship_add,
            relationship_types_list,
        )

        alice, bob = two_contacts
        grouped = await relationship_types_list(pool)
        spouse_type = next(t for t in grouped["Love"] if t["forward_label"] == "spouse")

        await relationship_add(pool, alice["id"], bob["id"], type_id=spouse_type["id"])

        alice_feed = await feed_get(pool, alice["id"])
        relationship_entries = [e for e in alice_feed if e["type"] == "relationship_added"]
        assert len(relationship_entries) >= 1
        assert "spouse" in relationship_entries[0]["description"]
