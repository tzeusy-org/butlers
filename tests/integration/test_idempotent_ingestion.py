"""Tests for idempotent ingestion guards on Relationship butler tools.

Covers first-insert, duplicate-skips, and different-key-not-skipped for
interaction_log, date_add, note_create, and life_event_log.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def relationship_pool(db):
    """The real core, memory (facts, predicate registry) and relationship chains."""
    return await db("core", "memory", "relationship")


def _extract_fact_id(result: dict) -> uuid.UUID:
    """Extract UUID from a tool result whose 'id' may be a store_fact dict."""
    fact_id = result["id"]
    if isinstance(fact_id, dict):
        return fact_id["id"]
    return fact_id


@pytest.fixture(autouse=True, scope="session")
def patch_embedding_engine():
    """Patch get_embedding_engine so store_fact does not require a real ML model."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"
    with patch("butlers.modules.memory.tools.get_embedding_engine", return_value=engine):
        for mod_name in (
            "butlers.tools.relationship.feed",
            "butlers.tools.relationship.interactions",
            "butlers.tools.relationship.notes",
            "butlers.tools.relationship.life_events",
        ):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


# ------------------------------------------------------------------
# interaction_log dedup
# ------------------------------------------------------------------


async def test_interaction_log_idempotency(relationship_pool):
    """interaction_log: first call inserts; same entity+type+date skips; different type/date inserts.

    interaction_log() now accepts entity_id (not contact_id).  contact_create()
    always sets entity_id on the returned contact dict, so we pass c["entity_id"].
    """
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(relationship_pool, "Dedup-Interaction")
    entity_id = c["entity_id"]
    assert entity_id is not None, "contact_create must set entity_id"
    ts = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    ts2 = datetime(2026, 3, 16, 10, 0, tzinfo=UTC)

    # First call inserts
    first = await interaction_log(
        relationship_pool, entity_id, "call", summary="Catch-up", occurred_at=ts
    )
    assert "id" in first and first["type"] == "call" and "skipped" not in first

    # Duplicate → skip
    second = await interaction_log(
        relationship_pool, entity_id, "call", summary="Second", occurred_at=ts
    )
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(_extract_fact_id(first))

    # Different type → not a duplicate
    diff_type = await interaction_log(relationship_pool, entity_id, "email", occurred_at=ts)
    assert "skipped" not in diff_type and diff_type["type"] == "email"

    # Different date → not a duplicate
    diff_date = await interaction_log(relationship_pool, entity_id, "call", occurred_at=ts2)
    assert "skipped" not in diff_date


# ------------------------------------------------------------------
# date_add dedup
# ------------------------------------------------------------------


async def test_date_add_idempotency(relationship_pool):
    """date_add: first call inserts; same contact+label+month+day skips; different label/day inserts."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(relationship_pool, "Dedup-Date")

    # First call inserts
    first = await date_add(relationship_pool, c["id"], "birthday", 6, 15)
    assert "id" in first and first["label"] == "birthday" and "skipped" not in first

    # Duplicate → skip
    second = await date_add(relationship_pool, c["id"], "birthday", 6, 15, year=1990)
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(first["id"])

    # Different label → not a duplicate
    diff_label = await date_add(relationship_pool, c["id"], "anniversary", 6, 15)
    assert "skipped" not in diff_label and diff_label["label"] == "anniversary"

    # Different day → not a duplicate
    diff_day = await date_add(relationship_pool, c["id"], "birthday", 6, 16)
    assert "skipped" not in diff_day


# ------------------------------------------------------------------
# note_create dedup
# ------------------------------------------------------------------


async def test_note_create_idempotency(relationship_pool):
    """note_create: first call inserts; same contact+content within 1h skips; different/old content inserts."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(relationship_pool, "Dedup-Note")

    # First call inserts
    first = await note_create(relationship_pool, c["id"], "Some important note")
    assert "id" in first and first["content"] == "Some important note" and "skipped" not in first

    # Duplicate within window → skip
    second = await note_create(relationship_pool, c["id"], "Some important note")
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(_extract_fact_id(first))

    # Different content → not a duplicate
    diff = await note_create(relationship_pool, c["id"], "Different note")
    assert "skipped" not in diff and diff["content"] == "Different note"

    # Same content but outside 1-hour window → not a duplicate
    old = await note_create(relationship_pool, c["id"], "Old note content")
    assert "skipped" not in old
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    await relationship_pool.execute(
        "UPDATE facts SET created_at = $1 WHERE id = $2", two_hours_ago, _extract_fact_id(old)
    )
    after_window = await note_create(relationship_pool, c["id"], "Old note content")
    assert "skipped" not in after_window and "id" in after_window


# ------------------------------------------------------------------
# life_event_log dedup
# ------------------------------------------------------------------


async def test_life_event_log_idempotency(relationship_pool):
    """life_event_log: first call inserts; same contact+type+date skips; different type/date inserts."""
    from butlers.tools.relationship import contact_create, life_event_log

    c = await contact_create(relationship_pool, "Dedup-LifeEvent")
    ts = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    ts2 = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)

    # First call inserts
    first = await life_event_log(
        relationship_pool, c["id"], "promotion", description="Got promoted", occurred_at=ts
    )
    assert "id" in first and first["type_name"] == "promotion" and "skipped" not in first

    # Duplicate → skip
    second = await life_event_log(
        relationship_pool, c["id"], "promotion", description="Different desc", occurred_at=ts
    )
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(_extract_fact_id(first))

    # Different type → not a duplicate
    diff_type = await life_event_log(relationship_pool, c["id"], "married", occurred_at=ts)
    assert "skipped" not in diff_type and diff_type["type_name"] == "married"

    # Different date → not a duplicate
    diff_date = await life_event_log(relationship_pool, c["id"], "promotion", occurred_at=ts2)
    assert "skipped" not in diff_date
