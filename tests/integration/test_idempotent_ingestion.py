"""Tests for idempotent ingestion guards on Relationship butler tools."""

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


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


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
    await p.execute("""
        CREATE TABLE IF NOT EXISTS activity_feed (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_activity_feed_contact_created
            ON activity_feed (contact_id, created_at)
    """)

    # SPO facts infrastructure (needed by store_fact called from _log_activity)
    await p.execute("CREATE SCHEMA IF NOT EXISTS shared")
    await p.execute("""
        CREATE TABLE IF NOT EXISTS shared.entities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL DEFAULT '',
            roles TEXT[] NOT NULL DEFAULT '{}'
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS predicate_registry (
            name TEXT PRIMARY KEY,
            is_temporal BOOLEAN NOT NULL DEFAULT false,
            description TEXT
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
            entity_id UUID REFERENCES shared.entities(id),
            object_entity_id UUID REFERENCES shared.entities(id),
            valid_at TIMESTAMPTZ DEFAULT NULL
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


@pytest.fixture(autouse=True, scope="session")
def patch_embedding_engine():
    """Patch get_embedding_engine so store_fact does not require a real ML model."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
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


async def test_interaction_log_first_call_inserts(pool):
    """First interaction_log call inserts normally."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Dedup-Interaction")
    ts = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    result = await interaction_log(pool, c["id"], "call", summary="Catch-up", occurred_at=ts)
    assert "id" in result
    assert result["type"] == "call"
    assert "skipped" not in result


async def test_interaction_log_duplicate_skips(pool):
    """Duplicate interaction_log (same contact+type+date) returns skip flag."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Dedup-Interaction-Dup")
    ts = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    first = await interaction_log(pool, c["id"], "call", summary="First", occurred_at=ts)
    assert "skipped" not in first

    # Same contact, same type, same date => skip
    second = await interaction_log(pool, c["id"], "call", summary="Second", occurred_at=ts)
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(first["id"])


async def test_interaction_log_different_type_not_skipped(pool):
    """Different interaction type on same date is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Dedup-Interaction-DiffType")
    ts = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    await interaction_log(pool, c["id"], "call", occurred_at=ts)
    result = await interaction_log(pool, c["id"], "email", occurred_at=ts)
    assert "skipped" not in result
    assert result["type"] == "email"


async def test_interaction_log_different_date_not_skipped(pool):
    """Same interaction type on different date is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Dedup-Interaction-DiffDate")
    ts1 = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    ts2 = datetime(2026, 3, 16, 10, 0, tzinfo=UTC)
    await interaction_log(pool, c["id"], "call", occurred_at=ts1)
    result = await interaction_log(pool, c["id"], "call", occurred_at=ts2)
    assert "skipped" not in result


# ------------------------------------------------------------------
# date_add dedup
# ------------------------------------------------------------------


async def test_date_add_first_call_inserts(pool):
    """First date_add call inserts normally."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Dedup-Date")
    result = await date_add(pool, c["id"], "birthday", 6, 15)
    assert "id" in result
    assert result["label"] == "birthday"
    assert "skipped" not in result


async def test_date_add_duplicate_skips(pool):
    """Duplicate date_add (same contact+label+month+day) returns skip flag."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Dedup-Date-Dup")
    first = await date_add(pool, c["id"], "birthday", 6, 15)
    assert "skipped" not in first

    second = await date_add(pool, c["id"], "birthday", 6, 15, year=1990)
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(first["id"])


async def test_date_add_different_label_not_skipped(pool):
    """Different label on same month/day is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Dedup-Date-DiffLabel")
    await date_add(pool, c["id"], "birthday", 6, 15)
    result = await date_add(pool, c["id"], "anniversary", 6, 15)
    assert "skipped" not in result
    assert result["label"] == "anniversary"


async def test_date_add_different_day_not_skipped(pool):
    """Same label on different day is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Dedup-Date-DiffDay")
    await date_add(pool, c["id"], "birthday", 6, 15)
    result = await date_add(pool, c["id"], "birthday", 6, 16)
    assert "skipped" not in result


# ------------------------------------------------------------------
# note_create dedup
# ------------------------------------------------------------------


async def test_note_create_first_call_inserts(pool):
    """First note_create call inserts normally."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Dedup-Note")
    result = await note_create(pool, c["id"], "Some important note")
    assert "id" in result
    assert result["content"] == "Some important note"
    assert "skipped" not in result


async def test_note_create_duplicate_within_hour_skips(pool):
    """Duplicate note_create (same contact+content within 1h) returns skip flag."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Dedup-Note-Dup")
    first = await note_create(pool, c["id"], "Repeated note content")
    assert "skipped" not in first

    second = await note_create(pool, c["id"], "Repeated note content")
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(first["id"])


async def test_note_create_different_content_not_skipped(pool):
    """Different content is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Dedup-Note-DiffContent")
    await note_create(pool, c["id"], "First note")
    result = await note_create(pool, c["id"], "Second note")
    assert "skipped" not in result
    assert result["content"] == "Second note"


async def test_note_create_same_content_after_hour_not_skipped(pool):
    """Same content after 1 hour window is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Dedup-Note-OldContent")
    # Insert a note, then backdate its created_at to 2 hours ago
    first = await note_create(pool, c["id"], "Old note content")
    assert "skipped" not in first

    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    await pool.execute(
        "UPDATE facts SET created_at = $1 WHERE id = $2",
        two_hours_ago,
        first["id"],
    )

    # Same content, but the old note is now outside the 1-hour window
    second = await note_create(pool, c["id"], "Old note content")
    assert "skipped" not in second
    assert "id" in second


# ------------------------------------------------------------------
# life_event_log dedup
# ------------------------------------------------------------------


async def test_life_event_log_first_call_inserts(pool):
    """First life_event_log call inserts normally."""
    from butlers.tools.relationship import contact_create, life_event_log

    c = await contact_create(pool, "Dedup-LifeEvent")
    ts = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    result = await life_event_log(
        pool, c["id"], "promotion", description="Got promoted", occurred_at=ts
    )
    assert "id" in result
    assert result["type_name"] == "promotion"
    assert "skipped" not in result


async def test_life_event_log_duplicate_skips(pool):
    """Duplicate life_event_log (same contact+type+date) returns skip flag."""
    from butlers.tools.relationship import contact_create, life_event_log

    c = await contact_create(pool, "Dedup-LifeEvent-Dup")
    ts = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    first = await life_event_log(pool, c["id"], "promotion", occurred_at=ts)
    assert "skipped" not in first

    second = await life_event_log(
        pool, c["id"], "promotion", description="Different desc", occurred_at=ts
    )
    assert second["skipped"] == "duplicate"
    assert second["existing_id"] == str(first["id"])


async def test_life_event_log_different_type_not_skipped(pool):
    """Different event type on same date is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, life_event_log

    c = await contact_create(pool, "Dedup-LifeEvent-DiffType")
    ts = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    await life_event_log(pool, c["id"], "promotion", occurred_at=ts)
    result = await life_event_log(pool, c["id"], "married", occurred_at=ts)
    assert "skipped" not in result
    assert result["type_name"] == "married"


async def test_life_event_log_different_date_not_skipped(pool):
    """Same event type on different date is NOT a duplicate."""
    from butlers.tools.relationship import contact_create, life_event_log

    c = await contact_create(pool, "Dedup-LifeEvent-DiffDate")
    ts1 = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    ts2 = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    await life_event_log(pool, c["id"], "promotion", occurred_at=ts1)
    result = await life_event_log(pool, c["id"], "promotion", occurred_at=ts2)
    assert "skipped" not in result
