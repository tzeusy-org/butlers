"""Tests for relationship type taxonomy — typed relationships with auto-reverse labels."""

from __future__ import annotations

import shutil
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migrated_templates import MigrationStage
from butlers.testing.migration import migrated_pool

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
async def pool(postgres_container):
    """Full taxonomy/CRM/memory chains; the migration supplies seed types."""
    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
        pool_schema="relationship",
    ) as p:
        await p.execute(
            "INSERT INTO predicate_registry (name, is_temporal) VALUES ('activity', true) ON CONFLICT (name) DO NOTHING"
        )
        yield p


@pytest.fixture(autouse=True, scope="session")
def patch_embedding_engine():
    """Patch get_embedding_engine so store_fact does not require a real ML model."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"
    with patch("butlers.modules.memory.tools.get_embedding_engine", return_value=engine):
        for mod_name in ("butlers.tools.relationship.feed",):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


@pytest.fixture
async def two_contacts(pool):
    """Create two contacts and return their dicts."""
    from butlers.tools.relationship import contact_create

    alice = await contact_create(pool, "Alice")
    bob = await contact_create(pool, "Bob")
    return alice, bob


async def test_relationship_types_structure_and_filter(pool):
    """types_list returns all groups with correct structure; filter and get work correctly."""
    from butlers.tools.relationship import relationship_type_get, relationship_types_list

    grouped = await relationship_types_list(pool)
    assert {"Love", "Family", "Friend", "Work", "Custom"} <= grouped.keys()

    for types in grouped.values():
        for t in types:
            assert {"id", "forward_label", "reverse_label"} <= t.keys()
            assert "group" not in t

    # Asymmetric and symmetric label checks
    assert (
        next(t for t in grouped["Family"] if t["forward_label"] == "parent")["reverse_label"]
        == "child"
    )
    assert (
        next(t for t in grouped["Work"] if t["forward_label"] == "boss")["reverse_label"]
        == "subordinate"
    )
    assert (
        next(t for t in grouped["Family"] if t["forward_label"] == "sibling")["reverse_label"]
        == "sibling"
    )

    # Filter by group
    assert list((await relationship_types_list(pool, group="Work")).keys()) == ["Work"]
    assert await relationship_types_list(pool, group="Nonexistent") == {}

    # get by id
    first_type = grouped["Love"][0]
    result = await relationship_type_get(pool, first_type["id"])
    assert result["forward_label"] == first_type["forward_label"]
    assert await relationship_type_get(pool, uuid.uuid4()) is None


async def test_typed_relationship_auto_reverse(pool, two_contacts):
    """Typed relationship_add stores type_id and auto-reverses asymmetric/symmetric types."""
    from butlers.tools.relationship import (
        relationship_add,
        relationship_list,
        relationship_types_list,
    )

    alice, bob = two_contacts
    grouped = await relationship_types_list(pool)

    for forward_label, group, expected_reverse in [
        ("friend", "Friend", "friend"),
        ("parent", "Family", "child"),
        ("boss", "Work", "subordinate"),
    ]:
        rel_type = next(t for t in grouped[group] if t["forward_label"] == forward_label)
        result = await relationship_add(pool, alice["id"], bob["id"], type_id=rel_type["id"])
        assert result["type"] == forward_label
        assert result["relationship_type_id"] == rel_type["id"]
        bob_rels = await relationship_list(pool, bob["id"])
        assert any(
            r["type"] == expected_reverse for r in bob_rels if r["related_name"] == "Alice"
        ), f"Expected {expected_reverse} for {forward_label}"


async def test_relationship_add_validations_and_compat(pool, two_contacts):
    """Invalid type_id raises; notes preserved; freetext backward compat; remove works."""
    from butlers.tools.relationship import (
        relationship_add,
        relationship_list,
        relationship_remove,
        relationship_types_list,
    )

    alice, bob = two_contacts

    with pytest.raises(ValueError, match="not found"):
        await relationship_add(pool, alice["id"], bob["id"], type_id=uuid.uuid4())
    with pytest.raises(ValueError, match="Either type_id or type"):
        await relationship_add(pool, alice["id"], bob["id"])

    # Notes preserved
    grouped = await relationship_types_list(pool)
    colleague_type = next(t for t in grouped["Work"] if t["forward_label"] == "colleague")
    result = await relationship_add(
        pool, alice["id"], bob["id"], type_id=colleague_type["id"], notes="Same team"
    )
    assert result["notes"] == "Same team"

    # Freetext: exact match, case-insensitive, unknown → custom, reverse label resolves
    r1 = await relationship_add(pool, alice["id"], bob["id"], type="BOSS")
    assert r1["type"] == "boss"
    r2 = await relationship_add(pool, alice["id"], bob["id"], type="neighbor")
    assert r2["type"] == "custom"

    # Remove cleans both directions
    spouse_type = next(t for t in grouped["Love"] if t["forward_label"] == "spouse")
    await relationship_add(pool, alice["id"], bob["id"], type_id=spouse_type["id"])
    await relationship_remove(pool, alice["id"], bob["id"])
    assert not any(r["related_name"] == "Bob" for r in await relationship_list(pool, alice["id"]))
    assert not any(r["related_name"] == "Alice" for r in await relationship_list(pool, bob["id"]))
