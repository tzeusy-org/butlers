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


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with relationship tables and return a pool."""
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    # Create relationship tables (mirrors Alembic relationship migrations)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            nickname TEXT,
            company TEXT,
            job_title TEXT,
            entity_id UUID,
            details JSONB DEFAULT '{}',
            metadata JSONB DEFAULT '{}',
            listed BOOLEAN NOT NULL DEFAULT true,
            archived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS important_dates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            month INT NOT NULL,
            day INT NOT NULL,
            year INT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            emotion TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            summary TEXT,
            occurred_at TIMESTAMPTZ DEFAULT now(),
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_interactions_contact_occurred
            ON interactions (contact_id, occurred_at)
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS life_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            description TEXT,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_life_events_contact_occurred
            ON life_events (contact_id, occurred_at)
    """)
    # SPO facts infrastructure (needed by store_fact)
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
            search_vector TSVECTOR,
            description_embedding TEXT,
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TIMESTAMPTZ,
            scope TEXT NOT NULL DEFAULT 'global',
            aliases TEXT[] NOT NULL DEFAULT '{}',
            inverse_of TEXT,
            is_symmetric BOOLEAN NOT NULL DEFAULT false,
            example_json JSONB
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
            retention_class TEXT NOT NULL DEFAULT 'operational',
            sensitivity TEXT NOT NULL DEFAULT 'normal',
            idempotency_key TEXT,
            observed_at TIMESTAMPTZ DEFAULT now(),
            invalid_at TIMESTAMPTZ,
            embedding_model_version TEXT DEFAULT 'unknown'
        )
    """)
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
    await p.close()


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


async def test_interaction_log_idempotency(pool):
    """interaction_log: first call inserts; same entity+type+date skips; different type/date inserts.

    interaction_log() now accepts entity_id (not contact_id).  contact_create()
    always sets entity_id on the returned contact dict, so we pass c["entity_id"].
    """
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Dedup-Interaction")
    entity_id = c["entity_id"]
    assert entity_id is not None, "contact_create must set entity_id"
    ts = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    ts2 = datetime(2026, 3, 16, 10, 0, tzinfo=UTC)

    # First call inserts
    first = await interaction_log(pool, entity_id, "call", summary="Catch-up", occurred_at=ts)
    assert "id" in first and first["type"] == "call" and "skipped" not in first

    # Duplicate → skip
    second = await interaction_log(pool, entity_id, "call", summary="Second", occurred_at=ts)
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(_extract_fact_id(first))

    # Different type → not a duplicate
    diff_type = await interaction_log(pool, entity_id, "email", occurred_at=ts)
    assert "skipped" not in diff_type and diff_type["type"] == "email"

    # Different date → not a duplicate
    diff_date = await interaction_log(pool, entity_id, "call", occurred_at=ts2)
    assert "skipped" not in diff_date


# ------------------------------------------------------------------
# date_add dedup
# ------------------------------------------------------------------


async def test_date_add_idempotency(pool):
    """date_add: first call inserts; same contact+label+month+day skips; different label/day inserts."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Dedup-Date")

    # First call inserts
    first = await date_add(pool, c["id"], "birthday", 6, 15)
    assert "id" in first and first["label"] == "birthday" and "skipped" not in first

    # Duplicate → skip
    second = await date_add(pool, c["id"], "birthday", 6, 15, year=1990)
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(first["id"])

    # Different label → not a duplicate
    diff_label = await date_add(pool, c["id"], "anniversary", 6, 15)
    assert "skipped" not in diff_label and diff_label["label"] == "anniversary"

    # Different day → not a duplicate
    diff_day = await date_add(pool, c["id"], "birthday", 6, 16)
    assert "skipped" not in diff_day


# ------------------------------------------------------------------
# note_create dedup
# ------------------------------------------------------------------


async def test_note_create_idempotency(pool):
    """note_create: first call inserts; same contact+content within 1h skips; different/old content inserts."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Dedup-Note")

    # First call inserts
    first = await note_create(pool, c["id"], "Some important note")
    assert "id" in first and first["content"] == "Some important note" and "skipped" not in first

    # Duplicate within window → skip
    second = await note_create(pool, c["id"], "Some important note")
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(_extract_fact_id(first))

    # Different content → not a duplicate
    diff = await note_create(pool, c["id"], "Different note")
    assert "skipped" not in diff and diff["content"] == "Different note"

    # Same content but outside 1-hour window → not a duplicate
    old = await note_create(pool, c["id"], "Old note content")
    assert "skipped" not in old
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    await pool.execute(
        "UPDATE facts SET created_at = $1 WHERE id = $2", two_hours_ago, _extract_fact_id(old)
    )
    after_window = await note_create(pool, c["id"], "Old note content")
    assert "skipped" not in after_window and "id" in after_window


# ------------------------------------------------------------------
# life_event_log dedup
# ------------------------------------------------------------------


async def test_life_event_log_idempotency(pool):
    """life_event_log: first call inserts; same contact+type+date skips; different type/date inserts."""
    from butlers.tools.relationship import contact_create, life_event_log

    c = await contact_create(pool, "Dedup-LifeEvent")
    ts = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    ts2 = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)

    # First call inserts
    first = await life_event_log(
        pool, c["id"], "promotion", description="Got promoted", occurred_at=ts
    )
    assert "id" in first and first["type_name"] == "promotion" and "skipped" not in first

    # Duplicate → skip
    second = await life_event_log(
        pool, c["id"], "promotion", description="Different desc", occurred_at=ts
    )
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(_extract_fact_id(first))

    # Different type → not a duplicate
    diff_type = await life_event_log(pool, c["id"], "married", occurred_at=ts)
    assert "skipped" not in diff_type and diff_type["type_name"] == "married"

    # Different date → not a duplicate
    diff_date = await life_event_log(pool, c["id"], "promotion", occurred_at=ts2)
    assert "skipped" not in diff_date
