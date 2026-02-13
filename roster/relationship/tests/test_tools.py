"""Tests for butlers.tools.relationship — personal CRM tools."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with relationship tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create relationship tables (mirrors Alembic relationship migration)
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
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts (first_name, last_name)
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_a UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                contact_b UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
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
                title TEXT,
                body TEXT NOT NULL,
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
                created_at TIMESTAMPTZ DEFAULT now(),
                direction VARCHAR(10),
                duration_minutes INTEGER,
                metadata JSONB
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_interactions_contact_occurred
                ON interactions (contact_id, occurred_at)
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'one_time',
                next_trigger_at TIMESTAMPTZ,
                last_triggered_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS gifts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                occasion TEXT,
                status TEXT NOT NULL DEFAULT 'idea'
                    CHECK (status IN ('idea', 'purchased', 'wrapped', 'given', 'thanked')),
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS loans (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                lender_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                borrower_contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                amount_cents INT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                loaned_at TIMESTAMPTZ,
                settled BOOLEAN DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT now(),
                settled_at TIMESTAMPTZ
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL UNIQUE,
                type TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                role TEXT,
                PRIMARY KEY (group_id, contact_id)
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS labels (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL UNIQUE,
                color TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contact_labels (
                label_id UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                PRIMARY KEY (label_id, contact_id)
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS quick_facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE (contact_id, key)
            )
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

        await p.execute("""
            CREATE TABLE IF NOT EXISTS addresses (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                label VARCHAR NOT NULL DEFAULT 'Home',
                line_1 TEXT NOT NULL,
                line_2 TEXT,
                city VARCHAR,
                province VARCHAR,
                postal_code VARCHAR,
                country VARCHAR(2),
                is_current BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_addresses_contact_id
                ON addresses (contact_id)
        """)
        # Life event tables (migration 002)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS life_event_categories (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS life_event_types (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                category_id UUID NOT NULL REFERENCES life_event_categories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE (category_id, name)
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_life_event_types_category
                ON life_event_types (category_id)
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS life_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                life_event_type_id UUID NOT NULL REFERENCES life_event_types(id),
                summary TEXT NOT NULL,
                description TEXT,
                happened_at DATE,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                title VARCHAR NOT NULL,
                description TEXT,
                completed BOOLEAN DEFAULT false,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_life_events_contact_happened
                ON life_events (contact_id, happened_at)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_life_events_type
                ON life_events (life_event_type_id)
        """)
        # Seed categories
        await p.execute("""
            INSERT INTO life_event_categories (name) VALUES
                ('Career'), ('Personal'), ('Social')
        """)
        # Seed Career types
        await p.execute("""
            INSERT INTO life_event_types (category_id, name)
            SELECT id, type_name FROM life_event_categories
            CROSS JOIN (VALUES
                ('new job'), ('promotion'), ('quit'), ('retired'), ('graduated')
            ) AS t(type_name)
            WHERE name = 'Career'
        """)
        # Seed Personal types
        await p.execute("""
            INSERT INTO life_event_types (category_id, name)
            SELECT id, type_name FROM life_event_categories
            CROSS JOIN (VALUES
                ('married'), ('divorced'), ('had a child'), ('moved'), ('passed away')
            ) AS t(type_name)
            WHERE name = 'Personal'
        """)
        # Seed Social types
        await p.execute("""
            INSERT INTO life_event_types (category_id, name)
            SELECT id, type_name FROM life_event_categories
            CROSS JOIN (VALUES
                ('met for first time'), ('reconnected')
            ) AS t(type_name)
            WHERE name = 'Social'
        """)

        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_contact_id ON tasks (contact_id)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks (completed)
        """)
        yield p


# ------------------------------------------------------------------
# Contact CRUD
# ------------------------------------------------------------------


async def test_contact_create(pool):
    """contact_create inserts a new contact and returns its dict."""
    from butlers.tools.relationship import contact_create

    result = await contact_create(
        pool,
        first_name="Alice",
        metadata={"email": "alice@example.com"},
    )
    assert result["first_name"] == "Alice"
    assert isinstance(result["id"], uuid.UUID)
    assert result["metadata"] == {"email": "alice@example.com"}


async def test_contact_create_default_details(pool):
    """contact_create uses empty dict for metadata when not provided."""
    from butlers.tools.relationship import contact_create

    result = await contact_create(pool, "Bob")
    assert result["metadata"] == {}


async def test_contact_update(pool):
    """contact_update changes fields on an existing contact."""
    from butlers.tools.relationship import contact_create, contact_update

    c = await contact_create(pool, "Carol")
    updated = await contact_update(pool, c["id"], first_name="Caroline", metadata={"age": 30})
    assert updated["first_name"] == "Caroline"
    assert updated["metadata"] == {"age": 30}


async def test_contact_update_not_found(pool):
    """contact_update raises ValueError for non-existent contact."""
    from butlers.tools.relationship import contact_update

    with pytest.raises(ValueError, match="not found"):
        await contact_update(pool, uuid.uuid4(), first_name="Nobody")


async def test_contact_get(pool):
    """contact_get returns the contact by ID."""
    from butlers.tools.relationship import contact_create, contact_get

    c = await contact_create(pool, "Dave")
    fetched = await contact_get(pool, c["id"])
    assert fetched["first_name"] == "Dave"
    assert fetched["id"] == c["id"]


async def test_contact_get_not_found(pool):
    """contact_get raises ValueError for non-existent contact by default."""
    from butlers.tools.relationship import contact_get

    with pytest.raises(ValueError, match="not found"):
        await contact_get(pool, uuid.uuid4())


async def test_contact_search(pool):
    """contact_search finds contacts by name ILIKE."""
    from butlers.tools.relationship import contact_create, contact_search

    await contact_create(pool, "Eve Johnson")
    await contact_create(pool, "Frank Miller")

    results = await contact_search(pool, "john")
    names = [r["first_name"] for r in results]
    assert "Eve" in names
    assert "Frank" not in names


async def test_contact_search_by_company(pool):
    """contact_search finds contacts by company match."""
    from butlers.tools.relationship import contact_create, contact_search

    await contact_create(pool, first_name="Grace", company="Acme Corp")

    results = await contact_search(pool, "Acme")
    names = [r["first_name"] for r in results]
    assert "Grace" in names


async def test_contact_archive(pool):
    """contact_archive sets listed=false and excludes from search."""
    from butlers.tools.relationship import contact_archive, contact_create, contact_search

    c = await contact_create(pool, "Hank Archived")
    archived = await contact_archive(pool, c["id"])
    assert archived["listed"] is False

    # Should not appear in search
    results = await contact_search(pool, "Hank Archived")
    assert len(results) == 0


async def test_contact_archive_not_found(pool):
    """contact_archive raises ValueError for non-existent contact."""
    from butlers.tools.relationship import contact_archive

    with pytest.raises(ValueError, match="not found"):
        await contact_archive(pool, uuid.uuid4())


# ------------------------------------------------------------------
# Bidirectional relationships
# ------------------------------------------------------------------


async def test_relationship_add_creates_two_rows(pool):
    """relationship_add creates two rows for bidirectional link."""
    from butlers.tools.relationship import contact_create, relationship_add

    a = await contact_create(pool, "Rel-A")
    b = await contact_create(pool, "Rel-B")

    result = await relationship_add(pool, a["id"], b["id"], "friend", notes="college")
    assert result["type"] == "friend"

    # Check two rows exist
    count = await pool.fetchval(
        """
        SELECT count(*) FROM relationships
        WHERE (contact_a = $1 AND contact_b = $2)
           OR (contact_a = $2 AND contact_b = $1)
        """,
        a["id"],
        b["id"],
    )
    assert count == 2


async def test_relationship_list_both_directions(pool):
    """relationship_list returns relationships from both sides."""
    from butlers.tools.relationship import contact_create, relationship_add, relationship_list

    a = await contact_create(pool, "Dir-A")
    b = await contact_create(pool, "Dir-B")
    await relationship_add(pool, a["id"], b["id"], "sibling")

    list_a = await relationship_list(pool, a["id"])
    list_b = await relationship_list(pool, b["id"])

    assert len(list_a) >= 1
    assert len(list_b) >= 1
    assert any(r["contact_b"] == b["id"] for r in list_a)
    assert any(r["contact_b"] == a["id"] for r in list_b)


async def test_relationship_remove(pool):
    """relationship_remove deletes both directions."""
    from butlers.tools.relationship import (
        contact_create,
        relationship_add,
        relationship_list,
        relationship_remove,
    )

    a = await contact_create(pool, "Rem-A")
    b = await contact_create(pool, "Rem-B")
    await relationship_add(pool, a["id"], b["id"], "colleague")

    await relationship_remove(pool, a["id"], b["id"])

    list_a = await relationship_list(pool, a["id"])
    list_b = await relationship_list(pool, b["id"])
    assert not any(r["contact_b"] == b["id"] for r in list_a)
    assert not any(r["contact_b"] == a["id"] for r in list_b)


# ------------------------------------------------------------------
# Dates
# ------------------------------------------------------------------


async def test_date_add(pool):
    """date_add creates an important date."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Date-Person")
    d = await date_add(pool, c["id"], "birthday", 3, 15, 1990)
    assert d["label"] == "birthday"
    assert d["month"] == 3
    assert d["day"] == 15
    assert d["year"] == 1990


async def test_date_add_partial(pool):
    """date_add works without a year (partial date)."""
    from butlers.tools.relationship import contact_create, date_add

    c = await contact_create(pool, "Partial-Date")
    d = await date_add(pool, c["id"], "anniversary", 7, 4)
    assert d["year"] is None
    assert d["month"] == 7
    assert d["day"] == 4


async def test_date_list(pool):
    """date_list returns dates ordered by month/day."""
    from butlers.tools.relationship import contact_create, date_add, date_list

    c = await contact_create(pool, "Multi-Date")
    await date_add(pool, c["id"], "birthday", 12, 25)
    await date_add(pool, c["id"], "anniversary", 1, 1)

    dates = await date_list(pool, c["id"])
    assert len(dates) == 2
    assert dates[0]["month"] <= dates[1]["month"]


async def test_upcoming_dates(pool):
    """upcoming_dates returns dates within the specified window."""
    from butlers.tools.relationship import contact_create, date_add, upcoming_dates

    c = await contact_create(pool, "Upcoming-Person")
    now = datetime.now(UTC)

    # Add a date that's tomorrow (should be upcoming)
    from datetime import timedelta

    tomorrow = now + timedelta(days=1)
    await date_add(pool, c["id"], "test-date", tomorrow.month, tomorrow.day)

    results = await upcoming_dates(pool, days_ahead=7)
    assert any(r["contact_id"] == c["id"] for r in results)


# ------------------------------------------------------------------
# Notes
# ------------------------------------------------------------------


async def test_note_create(pool):
    """note_create stores a note with optional emotion."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Note-Person")
    n = await note_create(pool, c["id"], "Met at conference", emotion="happy")
    assert n["body"] == "Met at conference"
    assert n["emotion"] == "happy"


async def test_note_create_no_emotion(pool):
    """note_create works without emotion."""
    from butlers.tools.relationship import contact_create, note_create

    c = await contact_create(pool, "Note-NoEmo")
    n = await note_create(pool, c["id"], "Just a regular note")
    assert n["emotion"] is None


async def test_note_list(pool):
    """note_list returns notes for a contact."""
    from butlers.tools.relationship import contact_create, note_create, note_list

    c = await contact_create(pool, "Note-List")
    await note_create(pool, c["id"], "First note")
    await note_create(pool, c["id"], "Second note")

    notes = await note_list(pool, c["id"])
    assert len(notes) == 2


async def test_note_search(pool):
    """note_search finds notes by body/title ILIKE."""
    from butlers.tools.relationship import contact_create, note_create, note_search

    c = await contact_create(pool, "Note-Search")
    await note_create(pool, c["id"], "Loves playing tennis on weekends")
    await note_create(pool, c["id"], "Allergic to peanuts")

    results = await note_search(pool, "tennis")
    assert len(results) >= 1
    assert any("tennis" in r["body"] for r in results)


# ------------------------------------------------------------------
# Interactions
# ------------------------------------------------------------------


async def test_interaction_log(pool):
    """interaction_log creates an interaction record."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Person")
    i = await interaction_log(pool, c["id"], "call", summary="Caught up on the phone")
    assert i["type"] == "call"
    assert i["summary"] == "Caught up on the phone"


async def test_interaction_log_custom_time(pool):
    """interaction_log accepts a custom occurred_at."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Custom")
    ts = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
    i = await interaction_log(pool, c["id"], "meeting", occurred_at=ts)
    assert i["occurred_at"] == ts


async def test_interaction_list_with_limit(pool):
    """interaction_list respects the limit parameter."""
    from butlers.tools.relationship import contact_create, interaction_list, interaction_log

    c = await contact_create(pool, "Inter-Limit")
    types = ["call", "email", "text", "video", "chat"]
    for idx in range(5):
        await interaction_log(pool, c["id"], types[idx], summary=f"Chat {idx}")

    results = await interaction_list(pool, c["id"], limit=3)
    assert len(results) == 3


async def test_interaction_log_with_direction(pool):
    """interaction_log stores direction field."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Direction")
    i = await interaction_log(pool, c["id"], "call", direction="incoming")
    assert i["direction"] == "incoming"


async def test_interaction_log_with_duration(pool):
    """interaction_log stores duration_minutes field."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Duration")
    i = await interaction_log(pool, c["id"], "meeting", duration_minutes=45)
    assert i["duration_minutes"] == 45


async def test_interaction_log_with_metadata(pool):
    """interaction_log stores metadata JSONB field."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Metadata")
    meta = {"location": "coffee shop", "topic": "project planning"}
    i = await interaction_log(pool, c["id"], "meeting", metadata=meta)
    assert i["metadata"] == meta


async def test_interaction_log_all_new_fields(pool):
    """interaction_log accepts all new fields together."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-AllNew")
    meta = {"via": "zoom", "recording": True}
    i = await interaction_log(
        pool,
        c["id"],
        "video_call",
        summary="Quarterly review",
        direction="mutual",
        duration_minutes=60,
        metadata=meta,
    )
    assert i["type"] == "video_call"
    assert i["summary"] == "Quarterly review"
    assert i["direction"] == "mutual"
    assert i["duration_minutes"] == 60
    assert i["metadata"] == meta


async def test_interaction_log_invalid_direction(pool):
    """interaction_log rejects invalid direction values."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-BadDir")
    with pytest.raises(ValueError, match="Invalid direction"):
        await interaction_log(pool, c["id"], "call", direction="sideways")


async def test_interaction_log_backward_compat(pool):
    """interaction_log works without new fields (backward compat)."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Compat")
    i = await interaction_log(pool, c["id"], "email", summary="Quick question")
    assert i["type"] == "email"
    assert i["summary"] == "Quick question"
    assert i["direction"] is None
    assert i["duration_minutes"] is None
    assert i["metadata"] is None


async def test_interaction_log_same_day_without_occurred_at_is_not_deduplicated(pool):
    """Ad-hoc logs without occurred_at should not trigger date-based deduplication."""
    from butlers.tools.relationship import contact_create, interaction_log

    c = await contact_create(pool, "Inter-Adhoc")
    first = await interaction_log(pool, c["id"], "call")
    second = await interaction_log(pool, c["id"], "call")

    assert "skipped" not in first
    assert "skipped" not in second
    assert second["id"] != first["id"]


async def test_interaction_list_filter_by_direction(pool):
    """interaction_list filters by direction."""
    from butlers.tools.relationship import contact_create, interaction_list, interaction_log

    c = await contact_create(pool, "Inter-FilterDir")
    await interaction_log(pool, c["id"], "call", direction="incoming")
    await interaction_log(pool, c["id"], "call", direction="outgoing")
    await interaction_log(pool, c["id"], "email", direction="incoming")

    incoming = await interaction_list(pool, c["id"], direction="incoming")
    assert len(incoming) == 2
    assert all(r["direction"] == "incoming" for r in incoming)

    outgoing = await interaction_list(pool, c["id"], direction="outgoing")
    assert len(outgoing) == 1
    assert outgoing[0]["direction"] == "outgoing"


async def test_interaction_list_filter_by_type(pool):
    """interaction_list filters by type."""
    from butlers.tools.relationship import contact_create, interaction_list, interaction_log

    c = await contact_create(pool, "Inter-FilterType")
    await interaction_log(pool, c["id"], "call")
    await interaction_log(pool, c["id"], "email")
    await interaction_log(pool, c["id"], "call")

    calls = await interaction_list(pool, c["id"], type="call")
    assert len(calls) == 2
    assert all(r["type"] == "call" for r in calls)

    emails = await interaction_list(pool, c["id"], type="email")
    assert len(emails) == 1


async def test_interaction_list_filter_by_direction_and_type(pool):
    """interaction_list supports combined direction + type filters."""
    from butlers.tools.relationship import contact_create, interaction_list, interaction_log

    c = await contact_create(pool, "Inter-CombinedFilter")
    await interaction_log(pool, c["id"], "call", direction="incoming")
    await interaction_log(pool, c["id"], "call", direction="outgoing")
    await interaction_log(pool, c["id"], "email", direction="incoming")
    await interaction_log(pool, c["id"], "email", direction="outgoing")

    results = await interaction_list(pool, c["id"], direction="incoming", type="call")
    assert len(results) == 1
    assert results[0]["type"] == "call"
    assert results[0]["direction"] == "incoming"


async def test_interaction_list_no_filters_returns_all(pool):
    """interaction_list without filters returns all interactions."""
    from butlers.tools.relationship import contact_create, interaction_list, interaction_log

    c = await contact_create(pool, "Inter-NoFilter")
    await interaction_log(pool, c["id"], "call", direction="incoming")
    await interaction_log(pool, c["id"], "email")  # no direction

    all_results = await interaction_list(pool, c["id"])
    assert len(all_results) == 2


async def test_interaction_feed_includes_direction(pool):
    """Activity feed entry includes direction when present."""
    from butlers.tools.relationship import contact_create, feed_get, interaction_log

    c = await contact_create(pool, "Inter-FeedDir")
    await interaction_log(pool, c["id"], "call", direction="outgoing")

    feed = await feed_get(pool, contact_id=c["id"])
    interaction_entries = [f for f in feed if f["action"] == "interaction_logged"]
    assert len(interaction_entries) >= 1
    assert "(outgoing)" in interaction_entries[0]["summary"]


async def test_interaction_feed_no_direction(pool):
    """Activity feed entry omits direction when not present."""
    from butlers.tools.relationship import contact_create, feed_get, interaction_log

    c = await contact_create(pool, "Inter-FeedNoDir")
    await interaction_log(pool, c["id"], "email")

    feed = await feed_get(pool, contact_id=c["id"])
    interaction_entries = [f for f in feed if f["action"] == "interaction_logged"]
    assert len(interaction_entries) >= 1
    # Should not contain parenthetical direction
    assert "(incoming)" not in interaction_entries[0]["summary"]
    assert "(outgoing)" not in interaction_entries[0]["summary"]
    assert "(mutual)" not in interaction_entries[0]["summary"]


# ------------------------------------------------------------------
# Reminders
# ------------------------------------------------------------------


async def test_reminder_create_one_time(pool):
    """reminder_create stores a one_time reminder."""
    from butlers.tools.relationship import contact_create, reminder_create

    c = await contact_create(pool, "Remind-Once")
    due = datetime(2025, 12, 25, 0, 0, 0, tzinfo=UTC)
    r = await reminder_create(
        pool,
        contact_id=c["id"],
        message="Buy gift",
        reminder_type="one_time",
        next_trigger_at=due,
    )
    assert r["type"] == "one_time"
    assert r["next_trigger_at"] == due
    assert r["last_triggered_at"] is None


async def test_reminder_create_recurring(pool):
    """reminder_create stores a recurring reminder."""
    from butlers.tools.relationship import contact_create, reminder_create

    c = await contact_create(pool, "Remind-Recur")
    r = await reminder_create(
        pool,
        contact_id=c["id"],
        message="Monthly check-in",
        reminder_type="recurring_monthly",
    )
    assert r["type"] == "recurring_monthly"


async def test_reminder_list(pool):
    """reminder_list returns reminders for a contact."""
    from butlers.tools.relationship import contact_create, reminder_create, reminder_list

    c = await contact_create(pool, "Remind-List")
    now = datetime.now(UTC)
    await reminder_create(
        pool,
        contact_id=c["id"],
        message="Reminder 1",
        reminder_type="one_time",
        next_trigger_at=now,
    )
    await reminder_create(
        pool,
        contact_id=c["id"],
        message="Reminder 2",
        reminder_type="one_time",
        next_trigger_at=now,
    )

    reminders = await reminder_list(pool, contact_id=c["id"])
    assert len(reminders) == 2


async def test_reminder_dismiss(pool):
    """reminder_dismiss clears next_trigger_at for one-time reminders."""
    from butlers.tools.relationship import contact_create, reminder_create, reminder_dismiss

    c = await contact_create(pool, "Remind-Dismiss")
    r = await reminder_create(
        pool,
        contact_id=c["id"],
        message="Do something",
        reminder_type="one_time",
        next_trigger_at=datetime.now(UTC),
    )

    dismissed = await reminder_dismiss(pool, r["id"])
    assert dismissed["next_trigger_at"] is None
    assert dismissed["last_triggered_at"] is not None


async def test_reminder_dismiss_not_found(pool):
    """reminder_dismiss raises ValueError for non-existent reminder."""
    from butlers.tools.relationship import reminder_dismiss

    with pytest.raises(ValueError, match="not found"):
        await reminder_dismiss(pool, uuid.uuid4())


# ------------------------------------------------------------------
# Gifts (pipeline validation)
# ------------------------------------------------------------------


async def test_gift_add(pool):
    """gift_add creates a gift idea."""
    from butlers.tools.relationship import contact_create, gift_add

    c = await contact_create(pool, "Gift-Person")
    g = await gift_add(pool, c["id"], "Fancy pen", occasion="birthday")
    assert g["description"] == "Fancy pen"
    assert g["occasion"] == "birthday"
    assert g["status"] == "idea"


async def test_gift_update_status_pipeline(pool):
    """gift_update_status follows the pipeline order."""
    from butlers.tools.relationship import contact_create, gift_add, gift_update_status

    c = await contact_create(pool, "Gift-Pipeline")
    g = await gift_add(pool, c["id"], "Book")

    g = await gift_update_status(pool, g["id"], "purchased")
    assert g["status"] == "purchased"

    g = await gift_update_status(pool, g["id"], "wrapped")
    assert g["status"] == "wrapped"

    g = await gift_update_status(pool, g["id"], "given")
    assert g["status"] == "given"

    g = await gift_update_status(pool, g["id"], "thanked")
    assert g["status"] == "thanked"


async def test_gift_update_status_rejects_backward(pool):
    """gift_update_status rejects backward transitions."""
    from butlers.tools.relationship import contact_create, gift_add, gift_update_status

    c = await contact_create(pool, "Gift-Backward")
    g = await gift_add(pool, c["id"], "Watch")
    await gift_update_status(pool, g["id"], "purchased")

    with pytest.raises(ValueError, match="Cannot move"):
        await gift_update_status(pool, g["id"], "idea")


async def test_gift_update_status_rejects_same(pool):
    """gift_update_status rejects same status transition."""
    from butlers.tools.relationship import contact_create, gift_add, gift_update_status

    c = await contact_create(pool, "Gift-Same")
    g = await gift_add(pool, c["id"], "Scarf")

    with pytest.raises(ValueError, match="Cannot move"):
        await gift_update_status(pool, g["id"], "idea")


async def test_gift_update_status_invalid(pool):
    """gift_update_status rejects invalid status values."""
    from butlers.tools.relationship import contact_create, gift_add, gift_update_status

    c = await contact_create(pool, "Gift-Invalid")
    g = await gift_add(pool, c["id"], "Mug")

    with pytest.raises(ValueError, match="Invalid status"):
        await gift_update_status(pool, g["id"], "destroyed")


async def test_gift_update_status_not_found(pool):
    """gift_update_status raises ValueError for non-existent gift."""
    from butlers.tools.relationship import gift_update_status

    with pytest.raises(ValueError, match="not found"):
        await gift_update_status(pool, uuid.uuid4(), "purchased")


async def test_gift_list(pool):
    """gift_list returns gifts for a contact."""
    from butlers.tools.relationship import contact_create, gift_add, gift_list

    c = await contact_create(pool, "Gift-List")
    await gift_add(pool, c["id"], "Gift A")
    await gift_add(pool, c["id"], "Gift B")

    gifts = await gift_list(pool, c["id"])
    assert len(gifts) == 2


async def test_gift_list_filtered_by_status(pool):
    """gift_list filters by status when provided."""
    from butlers.tools.relationship import contact_create, gift_add, gift_list, gift_update_status

    c = await contact_create(pool, "Gift-Filter")
    g1 = await gift_add(pool, c["id"], "Filter Gift A")
    await gift_add(pool, c["id"], "Filter Gift B")
    await gift_update_status(pool, g1["id"], "purchased")

    ideas = await gift_list(pool, c["id"], status="idea")
    assert len(ideas) == 1
    assert ideas[0]["description"] == "Filter Gift B"

    purchased = await gift_list(pool, c["id"], status="purchased")
    assert len(purchased) == 1
    assert purchased[0]["description"] == "Filter Gift A"


# ------------------------------------------------------------------
# Loans
# ------------------------------------------------------------------


async def test_loan_create(pool):
    """loan_create stores a loan record."""
    from butlers.tools.relationship import contact_create, loan_create

    lender = await contact_create(pool, "Loan-Lender")
    borrower = await contact_create(pool, "Loan-Borrower")
    loan = await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Lunch money",
        amount_cents=5000,
    )
    assert loan["amount_cents"] == 5000
    assert loan["lender_contact_id"] == lender["id"]
    assert loan["borrower_contact_id"] == borrower["id"]
    assert loan["currency"] == "USD"
    assert loan["settled"] is False


async def test_loan_settle(pool):
    """loan_settle marks a loan as settled."""
    from butlers.tools.relationship import contact_create, loan_create, loan_settle

    lender = await contact_create(pool, "Loan-Settle-Lender")
    borrower = await contact_create(pool, "Loan-Settle-Borrower")
    loan = await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Settle Test",
        amount_cents=10000,
    )
    settled = await loan_settle(pool, loan["id"])
    assert settled["settled"] is True
    assert settled["settled_at"] is not None


async def test_loan_settle_not_found(pool):
    """loan_settle raises ValueError for non-existent loan."""
    from butlers.tools.relationship import loan_settle

    with pytest.raises(ValueError, match="not found"):
        await loan_settle(pool, uuid.uuid4())


async def test_loan_list(pool):
    """loan_list returns loans for a contact."""
    from butlers.tools.relationship import contact_create, loan_create, loan_list

    lender = await contact_create(pool, "Loan-List-Lender")
    borrower = await contact_create(pool, "Loan-List-Borrower")
    await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="First",
        amount_cents=2500,
    )
    await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Second",
        amount_cents=7500,
    )

    loans = await loan_list(pool, lender["id"])
    assert len(loans) == 2


# ------------------------------------------------------------------
# Groups
# ------------------------------------------------------------------


async def test_group_create(pool):
    """group_create creates a named group."""
    from butlers.tools.relationship import group_create

    g = await group_create(pool, "Family")
    assert g["name"] == "Family"
    assert isinstance(g["id"], uuid.UUID)


async def test_group_add_member(pool):
    """group_add_member adds a contact to a group."""
    from butlers.tools.relationship import contact_create, group_add_member, group_create

    g = await group_create(pool, "Work Team")
    c = await contact_create(pool, "Group-Member")
    result = await group_add_member(pool, g["id"], c["id"])
    assert result["group_id"] == g["id"]
    assert result["contact_id"] == c["id"]


async def test_group_list(pool):
    """group_list returns all groups."""
    from butlers.tools.relationship import group_create, group_list

    await group_create(pool, "Sports")
    groups = await group_list(pool)
    names = [g["name"] for g in groups]
    assert "Sports" in names


async def test_group_members(pool):
    """group_members returns contacts in a group."""
    from butlers.tools.relationship import (
        contact_create,
        group_add_member,
        group_create,
        group_members,
    )

    g = await group_create(pool, "Book Club")
    c1 = await contact_create(pool, "Member-A")
    c2 = await contact_create(pool, "Member-B")
    await group_add_member(pool, g["id"], c1["id"])
    await group_add_member(pool, g["id"], c2["id"])

    members = await group_members(pool, g["id"])
    member_names = [m["first_name"] for m in members]
    assert "Member-A" in member_names
    assert "Member-B" in member_names


# ------------------------------------------------------------------
# Labels
# ------------------------------------------------------------------


async def test_label_create(pool):
    """label_create creates a label with optional color."""
    from butlers.tools.relationship import label_create

    lbl = await label_create(pool, "vip", color="#ff0000")
    assert lbl["name"] == "vip"
    assert lbl["color"] == "#ff0000"


async def test_label_create_no_color(pool):
    """label_create works without a color."""
    from butlers.tools.relationship import label_create

    lbl = await label_create(pool, "casual")
    assert lbl["color"] is None


async def test_label_assign(pool):
    """label_assign assigns a label to a contact."""
    from butlers.tools.relationship import contact_create, label_assign, label_create

    lbl = await label_create(pool, "important")
    c = await contact_create(pool, "Label-Person")
    result = await label_assign(pool, lbl["id"], c["id"])
    assert result["label_id"] == lbl["id"]
    assert result["contact_id"] == c["id"]


async def test_contact_search_by_label(pool):
    """contact_search_by_label finds contacts with a specific label."""
    from butlers.tools.relationship import (
        contact_create,
        contact_search_by_label,
        label_assign,
        label_create,
    )

    lbl = await label_create(pool, "priority")
    c1 = await contact_create(pool, "Priority-A")
    c2 = await contact_create(pool, "Priority-B")
    await contact_create(pool, "Normal-C")
    await label_assign(pool, lbl["id"], c1["id"])
    await label_assign(pool, lbl["id"], c2["id"])

    results = await contact_search_by_label(pool, "priority")
    names = [r["first_name"] for r in results]
    assert "Priority-A" in names
    assert "Priority-B" in names
    assert "Normal-C" not in names


# ------------------------------------------------------------------
# Quick facts
# ------------------------------------------------------------------


async def test_fact_set(pool):
    """fact_set stores a key-value fact."""
    from butlers.tools.relationship import contact_create, fact_set

    c = await contact_create(pool, "Fact-Person")
    f = await fact_set(pool, c["id"], "favorite_color", "blue")
    assert f["key"] == "favorite_color"
    assert f["value"] == "blue"


async def test_fact_set_upsert(pool):
    """fact_set updates an existing fact (UPSERT)."""
    from butlers.tools.relationship import contact_create, fact_list, fact_set

    c = await contact_create(pool, "Fact-Upsert")
    await fact_set(pool, c["id"], "pet", "dog")
    await fact_set(pool, c["id"], "pet", "cat")

    facts = await fact_list(pool, c["id"])
    pet_facts = [f for f in facts if f["key"] == "pet"]
    assert len(pet_facts) == 1
    assert pet_facts[0]["value"] == "cat"


async def test_fact_list(pool):
    """fact_list returns all facts for a contact ordered by key."""
    from butlers.tools.relationship import contact_create, fact_list, fact_set

    c = await contact_create(pool, "Fact-List")
    await fact_set(pool, c["id"], "zodiac", "leo")
    await fact_set(pool, c["id"], "allergy", "gluten")

    facts = await fact_list(pool, c["id"])
    keys = [f["key"] for f in facts]
    assert "allergy" in keys
    assert "zodiac" in keys
    # Should be alphabetically ordered
    assert keys.index("allergy") < keys.index("zodiac")


# ------------------------------------------------------------------
# Activity feed
# ------------------------------------------------------------------


async def test_activity_feed_auto_populated(pool):
    """Mutating tools automatically populate the activity feed."""
    from butlers.tools.relationship import contact_create, feed_get, note_create

    c = await contact_create(pool, "Feed-Person")
    await note_create(pool, c["id"], "Test note for feed")

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "contact_created" in actions
    assert "note_created" in actions


async def test_activity_feed_filter_by_contact(pool):
    """feed_get filters by contact_id."""
    from butlers.tools.relationship import contact_create, feed_get, note_create

    c1 = await contact_create(pool, "Feed-A")
    c2 = await contact_create(pool, "Feed-B")
    await note_create(pool, c1["id"], "Note for A")
    await note_create(pool, c2["id"], "Note for B")

    feed_a = await feed_get(pool, contact_id=c1["id"])
    feed_b = await feed_get(pool, contact_id=c2["id"])

    # All entries in feed_a should be for c1
    assert all(f["contact_id"] == c1["id"] for f in feed_a)
    assert all(f["contact_id"] == c2["id"] for f in feed_b)


async def test_activity_feed_global(pool):
    """feed_get without contact_id returns all entries."""
    from butlers.tools.relationship import contact_create, feed_get

    await contact_create(pool, "Feed-Global")

    feed = await feed_get(pool)
    assert isinstance(feed, list)
    assert len(feed) > 0


async def test_activity_feed_limit(pool):
    """feed_get respects the limit parameter."""
    from butlers.tools.relationship import contact_create, feed_get, note_create

    c = await contact_create(pool, "Feed-Limit")
    for i in range(5):
        await note_create(pool, c["id"], f"Note {i}")

    feed = await feed_get(pool, contact_id=c["id"], limit=3)
    assert len(feed) <= 3


# ------------------------------------------------------------------
# Addresses
# ------------------------------------------------------------------


async def test_address_add(pool):
    """address_add creates an address for a contact."""
    from butlers.tools.relationship import address_add, contact_create

    c = await contact_create(pool, "Address-Person")
    addr = await address_add(
        pool,
        c["id"],
        line_1="123 Main St",
        label="Home",
        city="Springfield",
        province="IL",
        postal_code="62704",
        country="US",
    )
    assert addr["contact_id"] == c["id"]
    assert addr["line_1"] == "123 Main St"
    assert addr["label"] == "Home"
    assert addr["city"] == "Springfield"
    assert addr["province"] == "IL"
    assert addr["postal_code"] == "62704"
    assert addr["country"] == "US"
    assert addr["is_current"] is False
    assert addr["id"] is not None


async def test_address_add_minimal(pool):
    """address_add works with only required fields."""
    from butlers.tools.relationship import address_add, contact_create

    c = await contact_create(pool, "Address-Minimal")
    addr = await address_add(pool, c["id"], line_1="PO Box 42")
    assert addr["line_1"] == "PO Box 42"
    assert addr["label"] == "Home"  # default
    assert addr["city"] is None
    assert addr["country"] is None
    assert addr["is_current"] is False


async def test_address_add_with_line_2(pool):
    """address_add stores line_2 (apartment/suite)."""
    from butlers.tools.relationship import address_add, contact_create

    c = await contact_create(pool, "Address-Line2")
    addr = await address_add(pool, c["id"], line_1="456 Oak Ave", line_2="Apt 7B")
    assert addr["line_2"] == "Apt 7B"


async def test_address_add_is_current(pool):
    """address_add with is_current=True sets the flag."""
    from butlers.tools.relationship import address_add, contact_create

    c = await contact_create(pool, "Address-Current")
    addr = await address_add(pool, c["id"], line_1="1 Current Rd", is_current=True)
    assert addr["is_current"] is True


async def test_address_add_current_clears_others(pool):
    """Adding a new current address clears is_current on existing addresses."""
    from butlers.tools.relationship import address_add, address_list, contact_create

    c = await contact_create(pool, "Address-ClearCurrent")
    await address_add(pool, c["id"], line_1="Old Current", is_current=True)
    addr2 = await address_add(pool, c["id"], line_1="New Current", is_current=True)

    addrs = await address_list(pool, c["id"])
    current_addrs = [a for a in addrs if a["is_current"]]
    assert len(current_addrs) == 1
    assert current_addrs[0]["id"] == addr2["id"]


async def test_address_add_invalid_country(pool):
    """address_add rejects country codes that are not 2 characters."""
    from butlers.tools.relationship import address_add, contact_create

    c = await contact_create(pool, "Address-BadCountry")
    with pytest.raises(ValueError, match="2-letter ISO 3166-1"):
        await address_add(pool, c["id"], line_1="1 Bad St", country="USA")


async def test_address_list(pool):
    """address_list returns all addresses for a contact."""
    from butlers.tools.relationship import address_add, address_list, contact_create

    c = await contact_create(pool, "Address-ListPerson")
    await address_add(pool, c["id"], line_1="Home Address", label="Home")
    await address_add(pool, c["id"], line_1="Work Address", label="Work")

    addrs = await address_list(pool, c["id"])
    assert len(addrs) == 2
    lines = [a["line_1"] for a in addrs]
    assert "Home Address" in lines
    assert "Work Address" in lines


async def test_address_list_current_first(pool):
    """address_list returns the current address first."""
    from butlers.tools.relationship import address_add, address_list, contact_create

    c = await contact_create(pool, "Address-OrderPerson")
    await address_add(pool, c["id"], line_1="Not Current", label="Other")
    await address_add(pool, c["id"], line_1="Is Current", label="Home", is_current=True)

    addrs = await address_list(pool, c["id"])
    assert addrs[0]["line_1"] == "Is Current"
    assert addrs[0]["is_current"] is True


async def test_address_list_empty(pool):
    """address_list returns empty list for contact with no addresses."""
    from butlers.tools.relationship import address_list, contact_create

    c = await contact_create(pool, "Address-NoneYet")
    addrs = await address_list(pool, c["id"])
    assert addrs == []


async def test_address_update(pool):
    """address_update modifies address fields."""
    from butlers.tools.relationship import address_add, address_update, contact_create

    c = await contact_create(pool, "Address-UpdatePerson")
    addr = await address_add(pool, c["id"], line_1="Old Street", city="OldCity")

    updated = await address_update(
        pool, addr["id"], line_1="New Street", city="NewCity", province="CA"
    )
    assert updated["line_1"] == "New Street"
    assert updated["city"] == "NewCity"
    assert updated["province"] == "CA"
    assert updated["id"] == addr["id"]


async def test_address_update_set_current(pool):
    """address_update with is_current=True clears other current addresses."""
    from butlers.tools.relationship import (
        address_add,
        address_list,
        address_update,
        contact_create,
    )

    c = await contact_create(pool, "Address-UpdateCurrent")
    await address_add(pool, c["id"], line_1="First", is_current=True)
    addr2 = await address_add(pool, c["id"], line_1="Second")

    await address_update(pool, addr2["id"], is_current=True)

    addrs = await address_list(pool, c["id"])
    current_addrs = [a for a in addrs if a["is_current"]]
    assert len(current_addrs) == 1
    assert current_addrs[0]["id"] == addr2["id"]


async def test_address_update_not_found(pool):
    """address_update raises ValueError for nonexistent address."""
    from butlers.tools.relationship import address_update

    fake_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await address_update(pool, fake_id, line_1="Nope")


async def test_address_update_invalid_country(pool):
    """address_update rejects invalid country code."""
    from butlers.tools.relationship import address_add, address_update, contact_create

    c = await contact_create(pool, "Address-UpdateBadCountry")
    addr = await address_add(pool, c["id"], line_1="1 Good St", country="US")

    with pytest.raises(ValueError, match="2-letter ISO 3166-1"):
        await address_update(pool, addr["id"], country="United States")


async def test_address_remove(pool):
    """address_remove deletes the address."""
    from butlers.tools.relationship import (
        address_add,
        address_list,
        address_remove,
        contact_create,
    )

    c = await contact_create(pool, "Address-RemovePerson")
    addr = await address_add(pool, c["id"], line_1="Temporary St")

    await address_remove(pool, addr["id"])

    addrs = await address_list(pool, c["id"])
    assert len(addrs) == 0


async def test_address_remove_not_found(pool):
    """address_remove raises ValueError for nonexistent address."""
    from butlers.tools.relationship import address_remove

    fake_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await address_remove(pool, fake_id)


async def test_address_multiple_per_contact(pool):
    """A contact can have multiple addresses with different labels."""
    from butlers.tools.relationship import address_add, address_list, contact_create

    c = await contact_create(pool, "Address-Multi")
    await address_add(pool, c["id"], line_1="Home St", label="Home")
    await address_add(pool, c["id"], line_1="Work Blvd", label="Work")
    await address_add(pool, c["id"], line_1="Other Ln", label="Other")

    addrs = await address_list(pool, c["id"])
    assert len(addrs) == 3
    labels = {a["label"] for a in addrs}
    assert labels == {"Home", "Work", "Other"}


async def test_address_activity_feed_add(pool):
    """address_add logs to the activity feed."""
    from butlers.tools.relationship import address_add, contact_create, feed_get

    c = await contact_create(pool, "Address-FeedAdd")
    await address_add(pool, c["id"], line_1="Feed St", city="FeedCity", country="US")

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "address_added" in actions
    addr_entry = next(f for f in feed if f["action"] == "address_added")
    assert "Feed St" in addr_entry["summary"]
    assert "FeedCity" in addr_entry["summary"]


async def test_address_activity_feed_update(pool):
    """address_update logs to the activity feed."""
    from butlers.tools.relationship import (
        address_add,
        address_update,
        contact_create,
        feed_get,
    )

    c = await contact_create(pool, "Address-FeedUpdate")
    addr = await address_add(pool, c["id"], line_1="Before St")
    await address_update(pool, addr["id"], line_1="After St")

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "address_updated" in actions


async def test_address_activity_feed_remove(pool):
    """address_remove logs to the activity feed."""
    from butlers.tools.relationship import (
        address_add,
        address_remove,
        contact_create,
        feed_get,
    )

    c = await contact_create(pool, "Address-FeedRemove")
    addr = await address_add(pool, c["id"], line_1="Gone St", label="Work")
    await address_remove(pool, addr["id"])

    feed = await feed_get(pool, contact_id=c["id"])
    actions = [f["action"] for f in feed]
    assert "address_removed" in actions
    remove_entry = next(f for f in feed if f["action"] == "address_removed")
    assert "Work" in remove_entry["summary"]


async def test_address_cascade_on_contact_delete(pool):
    """Addresses are deleted when the parent contact is deleted."""
    from butlers.tools.relationship import address_add, contact_create

    c = await contact_create(pool, "Address-Cascade")
    await address_add(pool, c["id"], line_1="Cascade St")

    # Direct delete (not using archive, which is soft-delete)
    await pool.execute("DELETE FROM contacts WHERE id = $1", c["id"])

    count = await pool.fetchval("SELECT COUNT(*) FROM addresses WHERE contact_id = $1", c["id"])
    assert count == 0


# ------------------------------------------------------------------
# Life Events Tests
# ------------------------------------------------------------------


async def test_life_event_types_list(pool):
    """Test listing all life event types."""
    from butlers.tools.relationship import life_event_types_list

    types = await life_event_types_list(pool)
    assert len(types) > 0

    # Check that we have the seeded categories
    categories = {t["category"] for t in types}
    assert "Career" in categories
    assert "Personal" in categories
    assert "Social" in categories

    # Check some specific types
    type_names = {t["name"] for t in types}
    assert "promotion" in type_names
    assert "married" in type_names
    assert "met for first time" in type_names


async def test_life_event_log_basic(pool):
    """Test logging a life event."""
    from butlers.tools.relationship import contact_create, life_event_log

    contact = await contact_create(pool, "Alice")
    event = await life_event_log(
        pool,
        contact["id"],
        "promotion",
        "Got promoted to Senior Engineer",
        description="Alice received a well-deserved promotion after 3 years of great work.",
        happened_at="2026-01-15",
    )

    assert event["contact_id"] == contact["id"]
    assert event["summary"] == "Got promoted to Senior Engineer"
    assert event["description"] == (
        "Alice received a well-deserved promotion after 3 years of great work."
    )
    assert str(event["happened_at"]) == "2026-01-15"


async def test_life_event_log_invalid_type(pool):
    """Test logging with an invalid life event type."""
    from butlers.tools.relationship import contact_create, life_event_log

    contact = await contact_create(pool, "Bob")

    with pytest.raises(ValueError, match="Unknown life event type"):
        await life_event_log(
            pool,
            contact["id"],
            "invalid_type",
            "Something happened",
        )


async def test_life_event_log_without_date(pool):
    """Test logging a life event without specifying a date."""
    from butlers.tools.relationship import contact_create, life_event_log

    contact = await contact_create(pool, "Charlie")
    event = await life_event_log(
        pool,
        contact["id"],
        "new job",
        "Started at TechCorp",
    )

    assert event["contact_id"] == contact["id"]
    assert event["summary"] == "Started at TechCorp"
    assert event["happened_at"] is None


async def test_life_event_log_uses_occurred_at_when_happened_at_omitted(pool):
    """occurred_at should backfill happened_at for taxonomy-backed life events."""
    from butlers.tools.relationship import contact_create, life_event_log

    contact = await contact_create(pool, "Dana")
    occurred_at = datetime(2026, 2, 1, 15, 30, 0, tzinfo=UTC)
    event = await life_event_log(
        pool,
        contact["id"],
        "promotion",
        "Promoted to lead",
        occurred_at=occurred_at,
    )

    assert str(event["happened_at"]) == "2026-02-01"


async def test_life_event_list_all(pool):
    """Test listing all life events."""
    from butlers.tools.relationship import contact_create, life_event_list, life_event_log

    alice = await contact_create(pool, "Alice")
    bob = await contact_create(pool, "Bob")

    await life_event_log(pool, alice["id"], "promotion", "Promoted to manager")
    await life_event_log(pool, bob["id"], "married", "Got married")
    await life_event_log(pool, alice["id"], "moved", "Moved to London")

    events = await life_event_list(pool)
    assert len(events) >= 3

    # Check that contact names are included
    contact_names = {e["contact_name"] for e in events}
    assert "Alice" in contact_names
    assert "Bob" in contact_names


async def test_life_event_list_by_contact(pool):
    """Test filtering life events by contact."""
    from butlers.tools.relationship import contact_create, life_event_list, life_event_log

    alice = await contact_create(pool, "Alice")
    bob = await contact_create(pool, "Bob")

    await life_event_log(pool, alice["id"], "promotion", "Promoted to manager")
    await life_event_log(pool, bob["id"], "married", "Got married")
    await life_event_log(pool, alice["id"], "moved", "Moved to London")

    alice_events = await life_event_list(pool, contact_id=alice["id"])
    assert len(alice_events) == 2
    assert all(e["contact_id"] == alice["id"] for e in alice_events)


async def test_life_event_list_by_type(pool):
    """Test filtering life events by type."""
    from butlers.tools.relationship import contact_create, life_event_list, life_event_log

    alice = await contact_create(pool, "Alice")
    bob = await contact_create(pool, "Bob")

    await life_event_log(pool, alice["id"], "promotion", "Promoted to manager")
    await life_event_log(pool, bob["id"], "promotion", "Promoted to director")
    await life_event_log(pool, alice["id"], "moved", "Moved to London")

    promotion_events = await life_event_list(pool, type_name="promotion")
    assert len(promotion_events) == 2
    assert all(e["type_name"] == "promotion" for e in promotion_events)


async def test_life_event_list_by_contact_and_type(pool):
    """Test filtering life events by both contact and type."""
    from butlers.tools.relationship import contact_create, life_event_list, life_event_log

    alice = await contact_create(pool, "Alice")
    bob = await contact_create(pool, "Bob")

    await life_event_log(pool, alice["id"], "promotion", "Promoted to manager")
    await life_event_log(pool, bob["id"], "promotion", "Promoted to director")
    await life_event_log(pool, alice["id"], "moved", "Moved to London")

    alice_promotions = await life_event_list(pool, contact_id=alice["id"], type_name="promotion")
    assert len(alice_promotions) == 1
    assert alice_promotions[0]["contact_id"] == alice["id"]
    assert alice_promotions[0]["type_name"] == "promotion"


async def test_life_event_list_ordering(pool):
    """Test that life events are ordered by happened_at (most recent first)."""
    from butlers.tools.relationship import contact_create, life_event_list, life_event_log

    alice = await contact_create(pool, "Alice")

    await life_event_log(
        pool, alice["id"], "new job", "Started at Company A", happened_at="2024-01-01"
    )
    await life_event_log(pool, alice["id"], "promotion", "Promoted", happened_at="2025-06-15")
    await life_event_log(pool, alice["id"], "quit", "Left Company A", happened_at="2026-01-01")

    events = await life_event_list(pool, contact_id=alice["id"])
    assert len(events) == 3
    # Most recent first
    assert str(events[0]["happened_at"]) == "2026-01-01"
    assert str(events[1]["happened_at"]) == "2025-06-15"
    assert str(events[2]["happened_at"]) == "2024-01-01"


async def test_life_event_activity_feed_integration(pool):
    """Test that life events are logged to activity feed."""
    from butlers.tools.relationship import contact_create, feed_get, life_event_log

    alice = await contact_create(pool, "Alice")
    await life_event_log(pool, alice["id"], "graduated", "Graduated from MIT")

    feed = await feed_get(pool, contact_id=alice["id"])

    # Should have two activities: contact_created and life_event_logged
    assert len(feed) >= 2

    # Find the life event activity
    life_event_activity = next((a for a in feed if a["action"] == "life_event_logged"), None)
    assert life_event_activity is not None
    assert "graduated" in life_event_activity["summary"]
    assert "Graduated from MIT" in life_event_activity["summary"]


async def test_life_event_all_seeded_types(pool):
    """Test that all expected types are seeded."""
    from butlers.tools.relationship import life_event_types_list

    types = await life_event_types_list(pool)
    type_dict = {(t["category"], t["name"]) for t in types}

    # Career types
    assert ("Career", "new job") in type_dict
    assert ("Career", "promotion") in type_dict
    assert ("Career", "quit") in type_dict
    assert ("Career", "retired") in type_dict
    assert ("Career", "graduated") in type_dict

    # Personal types
    assert ("Personal", "married") in type_dict
    assert ("Personal", "divorced") in type_dict
    assert ("Personal", "had a child") in type_dict
    assert ("Personal", "moved") in type_dict
    assert ("Personal", "passed away") in type_dict

    # Social types
    assert ("Social", "met for first time") in type_dict
    assert ("Social", "reconnected") in type_dict


# ------------------------------------------------------------------
# Contact resolution
# ------------------------------------------------------------------


async def test_contact_resolve_exact_match(pool):
    """contact_resolve returns HIGH confidence for an exact case-insensitive match."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c = await contact_create(pool, "Sarah Connor")
    result = await contact_resolve(pool, "sarah connor")

    assert result["contact_id"] == c["id"]
    assert result["confidence"] == "high"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["contact_id"] == c["id"]
    assert result["candidates"][0]["score"] == 100


async def test_contact_resolve_exact_match_case_preserved(pool):
    """contact_resolve exact match works regardless of case."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c = await contact_create(pool, "JOHN DOE")
    result = await contact_resolve(pool, "John Doe")

    assert result["contact_id"] == c["id"]
    assert result["confidence"] == "high"


async def test_contact_resolve_no_match(pool):
    """contact_resolve returns null contact_id and empty candidates when no match."""
    from butlers.tools.relationship import contact_resolve

    result = await contact_resolve(pool, "Nonexistent Person XYZ123")

    assert result["contact_id"] is None
    assert result["confidence"] == "none"
    assert result["candidates"] == []


async def test_contact_resolve_empty_name(pool):
    """contact_resolve returns none for empty name."""
    from butlers.tools.relationship import contact_resolve

    result = await contact_resolve(pool, "")

    assert result["contact_id"] is None
    assert result["confidence"] == "none"
    assert result["candidates"] == []


async def test_contact_resolve_whitespace_name(pool):
    """contact_resolve returns none for whitespace-only name."""
    from butlers.tools.relationship import contact_resolve

    result = await contact_resolve(pool, "   ")

    assert result["contact_id"] is None
    assert result["confidence"] == "none"
    assert result["candidates"] == []


async def test_contact_resolve_partial_first_name(pool):
    """contact_resolve returns medium/high confidence for first-name-only match."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c = await contact_create(pool, "Resolve-Maria Garcia")
    result = await contact_resolve(pool, "Resolve-Maria")

    assert result["confidence"] in {"medium", "high"}
    assert len(result["candidates"]) >= 1
    ids = [cand["contact_id"] for cand in result["candidates"]]
    assert c["id"] in ids


async def test_contact_resolve_partial_last_name(pool):
    """contact_resolve returns medium/high confidence for last-name-only match."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c = await contact_create(pool, "Resolve-Alex Petrosyan")
    result = await contact_resolve(pool, "Petrosyan")

    assert result["confidence"] in {"medium", "high"}
    ids = [cand["contact_id"] for cand in result["candidates"]]
    assert c["id"] in ids


async def test_contact_resolve_ambiguous_multiple_matches(pool):
    """contact_resolve returns MEDIUM confidence with multiple candidates for ambiguous names."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c1 = await contact_create(pool, "Resolve-Amb Sarah Smith")
    c2 = await contact_create(pool, "Resolve-Amb Sarah Johnson")
    result = await contact_resolve(pool, "Resolve-Amb Sarah")

    assert result["confidence"] == "medium"
    assert len(result["candidates"]) >= 2
    ids = [cand["contact_id"] for cand in result["candidates"]]
    assert c1["id"] in ids
    assert c2["id"] in ids


async def test_contact_resolve_context_disambiguates(pool):
    """context parameter helps disambiguate between multiple exact-name matches."""
    from butlers.tools.relationship import contact_create, contact_resolve, note_create

    c1 = await contact_create(pool, first_name="Resolve-Ctx Mike", company="Acme")
    await contact_create(pool, first_name="Resolve-Ctx Mike", company="Globex")
    await note_create(pool, c1["id"], "Works at the Acme office downtown")

    result = await contact_resolve(pool, "Resolve-Ctx Mike", context="from Acme")

    assert result["confidence"] == "high"
    assert result["contact_id"] == c1["id"]
    # c1 should be ranked higher due to context match
    assert result["candidates"][0]["contact_id"] == c1["id"]


async def test_contact_resolve_context_boosts_partial(pool):
    """context parameter boosts partial match scores using details."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c1 = await contact_create(
        pool,
        first_name="Resolve-CtxP David Lee",
        metadata={"hobby": "tennis"},
    )
    await contact_create(pool, first_name="Resolve-CtxP David Kim", metadata={"hobby": "chess"})

    result = await contact_resolve(pool, "Resolve-CtxP David", context="tennis match")

    assert result["confidence"] == "medium"
    assert len(result["candidates"]) >= 2
    # c1 should rank higher due to context match on "tennis"
    assert result["candidates"][0]["contact_id"] == c1["id"]


async def test_contact_resolve_excludes_archived(pool):
    """contact_resolve does not return archived contacts."""
    from butlers.tools.relationship import contact_archive, contact_create, contact_resolve

    c = await contact_create(pool, "Resolve-Archived Person XYZ")
    await contact_archive(pool, c["id"])

    result = await contact_resolve(pool, "Resolve-Archived Person XYZ")
    assert result["contact_id"] is None
    assert result["confidence"] == "none"
    assert result["candidates"] == []


async def test_contact_resolve_single_partial_returns_contact_id(pool):
    """When only one partial match exists, contact_id is returned with MEDIUM confidence."""
    from butlers.tools.relationship import contact_create, contact_resolve

    c = await contact_create(pool, "Resolve-Unique Ximenez")
    result = await contact_resolve(pool, "Ximenez")

    assert result["contact_id"] == c["id"]
    assert result["confidence"] == "medium"
    assert len(result["candidates"]) == 1


async def test_contact_resolve_candidates_sorted_by_score(pool):
    """Candidates are returned sorted by score descending."""
    from butlers.tools.relationship import contact_create, contact_resolve

    await contact_create(pool, "Resolve-Sort Taylor Swift")
    await contact_create(pool, "Resolve-Sort Taylor Jones")

    result = await contact_resolve(pool, "Resolve-Sort Taylor")

    scores = [c["score"] for c in result["candidates"]]
    assert scores == sorted(scores, reverse=True)


async def test_contact_resolve_context_with_interactions(pool):
    """Context matching works against interaction summaries."""
    from butlers.tools.relationship import (
        contact_create,
        contact_resolve,
        interaction_log,
    )

    c1 = await contact_create(pool, "Resolve-IntCtx Emma Brown")
    c2 = await contact_create(pool, "Resolve-IntCtx Emma Davis")
    await interaction_log(pool, c1["id"], "meeting", "Discussed yoga class schedule")

    result = await contact_resolve(pool, "Resolve-IntCtx Emma", context="yoga class")

    assert result["confidence"] == "medium"
    assert len(result["candidates"]) >= 2
    ids = [cand["contact_id"] for cand in result["candidates"]]
    assert c1["id"] in ids
    assert c2["id"] in ids
    # c1 should be ranked first due to interaction mentioning yoga
    assert result["candidates"][0]["contact_id"] == c1["id"]


# Tasks / To-dos
# ------------------------------------------------------------------


async def test_task_create(pool):
    """task_create inserts a task and returns its dict."""
    from butlers.tools.relationship import contact_create, task_create

    c = await contact_create(pool, "Task Contact")
    result = await task_create(pool, c["id"], "Buy groceries", "Milk, eggs, bread")
    assert result["title"] == "Buy groceries"
    assert result["description"] == "Milk, eggs, bread"
    assert result["completed"] is False
    assert result["completed_at"] is None
    assert isinstance(result["id"], uuid.UUID)
    assert result["contact_id"] == c["id"]


async def test_task_create_no_description(pool):
    """task_create works without optional description."""
    from butlers.tools.relationship import contact_create, task_create

    c = await contact_create(pool, "Task Contact Minimal")
    result = await task_create(pool, c["id"], "Call dentist")
    assert result["title"] == "Call dentist"
    assert result["description"] is None
    assert result["completed"] is False


async def test_task_create_feed_entry(pool):
    """task_create logs an activity feed entry."""
    from butlers.tools.relationship import contact_create, feed_get, task_create

    c = await contact_create(pool, "Task Feed Contact")
    await task_create(pool, c["id"], "Send report")
    feed = await feed_get(pool, c["id"])
    task_entries = [e for e in feed if e["action"] == "task_created"]
    assert len(task_entries) >= 1
    assert "Send report" in task_entries[0]["summary"]


async def test_task_list_by_contact(pool):
    """task_list filters tasks by contact_id."""
    from butlers.tools.relationship import contact_create, task_create, task_list

    c1 = await contact_create(pool, "Task List Contact A")
    c2 = await contact_create(pool, "Task List Contact B")
    await task_create(pool, c1["id"], "Task for A")
    await task_create(pool, c2["id"], "Task for B")

    tasks_a = await task_list(pool, contact_id=c1["id"])
    assert any(t["title"] == "Task for A" for t in tasks_a)
    assert not any(t["title"] == "Task for B" for t in tasks_a)

    tasks_b = await task_list(pool, contact_id=c2["id"])
    assert any(t["title"] == "Task for B" for t in tasks_b)
    assert not any(t["title"] == "Task for A" for t in tasks_b)


async def test_task_list_all(pool):
    """task_list without contact_id returns tasks for all contacts."""
    from butlers.tools.relationship import contact_create, task_create, task_list

    c = await contact_create(pool, "Task List All Contact")
    await task_create(pool, c["id"], "Global task unique")

    all_tasks = await task_list(pool)
    assert any(t["title"] == "Global task unique" for t in all_tasks)


async def test_task_list_excludes_completed_by_default(pool):
    """task_list excludes completed tasks by default."""
    from butlers.tools.relationship import (
        contact_create,
        task_complete,
        task_create,
        task_list,
    )

    c = await contact_create(pool, "Task Filter Contact")
    await task_create(pool, c["id"], "Incomplete task xyz")
    t2 = await task_create(pool, c["id"], "Completed task xyz")
    await task_complete(pool, t2["id"])

    tasks = await task_list(pool, contact_id=c["id"])
    titles = [t["title"] for t in tasks]
    assert "Incomplete task xyz" in titles
    assert "Completed task xyz" not in titles


async def test_task_list_include_completed(pool):
    """task_list with include_completed=True returns all tasks."""
    from butlers.tools.relationship import (
        contact_create,
        task_complete,
        task_create,
        task_list,
    )

    c = await contact_create(pool, "Task Include Completed Contact")
    await task_create(pool, c["id"], "Still open zzz")
    t2 = await task_create(pool, c["id"], "Already done zzz")
    await task_complete(pool, t2["id"])

    tasks = await task_list(pool, contact_id=c["id"], include_completed=True)
    titles = [t["title"] for t in tasks]
    assert "Still open zzz" in titles
    assert "Already done zzz" in titles


async def test_task_list_includes_contact_name(pool):
    """task_list results include contact_name from JOIN."""
    from butlers.tools.relationship import contact_create, task_create, task_list

    c = await contact_create(pool, "Named Contact For Tasks")
    await task_create(pool, c["id"], "Task with contact name")

    tasks = await task_list(pool, contact_id=c["id"])
    assert any(t["contact_name"] == "Named Contact For Tasks" for t in tasks)


async def test_task_complete(pool):
    """task_complete marks a task as completed with timestamp."""
    from butlers.tools.relationship import contact_create, task_complete, task_create

    c = await contact_create(pool, "Task Complete Contact")
    t = await task_create(pool, c["id"], "Finish report")
    completed = await task_complete(pool, t["id"])
    assert completed["completed"] is True
    assert completed["completed_at"] is not None
    assert isinstance(completed["completed_at"], datetime)


async def test_task_complete_feed_entry(pool):
    """task_complete logs an activity feed entry."""
    from butlers.tools.relationship import contact_create, feed_get, task_complete, task_create

    c = await contact_create(pool, "Task Complete Feed Contact")
    t = await task_create(pool, c["id"], "Review PR")
    await task_complete(pool, t["id"])

    feed = await feed_get(pool, c["id"])
    complete_entries = [e for e in feed if e["action"] == "task_completed"]
    assert len(complete_entries) >= 1
    assert "Review PR" in complete_entries[0]["summary"]


async def test_task_complete_not_found(pool):
    """task_complete raises ValueError for non-existent task."""
    from butlers.tools.relationship import task_complete

    with pytest.raises(ValueError, match="not found"):
        await task_complete(pool, uuid.uuid4())


async def test_task_delete(pool):
    """task_delete removes a task from the database."""
    from butlers.tools.relationship import contact_create, task_create, task_delete, task_list

    c = await contact_create(pool, "Task Delete Contact")
    t = await task_create(pool, c["id"], "Deletable task")

    await task_delete(pool, t["id"])

    tasks = await task_list(pool, contact_id=c["id"], include_completed=True)
    assert not any(task["title"] == "Deletable task" for task in tasks)


async def test_task_delete_feed_entry(pool):
    """task_delete logs an activity feed entry."""
    from butlers.tools.relationship import contact_create, feed_get, task_create, task_delete

    c = await contact_create(pool, "Task Delete Feed Contact")
    t = await task_create(pool, c["id"], "Task to delete")
    await task_delete(pool, t["id"])

    feed = await feed_get(pool, c["id"])
    delete_entries = [e for e in feed if e["action"] == "task_deleted"]
    assert len(delete_entries) >= 1
    assert "Task to delete" in delete_entries[0]["summary"]


async def test_task_delete_not_found(pool):
    """task_delete raises ValueError for non-existent task."""
    from butlers.tools.relationship import task_delete

    with pytest.raises(ValueError, match="not found"):
        await task_delete(pool, uuid.uuid4())


# Stay-in-touch cadence
# ------------------------------------------------------------------


@pytest.fixture
async def pool_with_cadence(pool):
    """Extend the base pool fixture with the stay_in_touch_days column."""
    await pool.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS stay_in_touch_days INTEGER
    """)
    yield pool


async def test_stay_in_touch_set(pool_with_cadence):
    """Setting cadence stores the frequency_days on the contact."""
    from butlers.tools.relationship import contact_create, stay_in_touch_set

    pool = pool_with_cadence
    contact = await contact_create(pool, "Alice")
    cid = contact["id"]

    updated = await stay_in_touch_set(pool, cid, 14)
    assert updated["stay_in_touch_days"] == 14


async def test_stay_in_touch_clear(pool_with_cadence):
    """Clearing cadence (None) sets stay_in_touch_days to NULL."""
    from butlers.tools.relationship import contact_create, stay_in_touch_set

    pool = pool_with_cadence
    contact = await contact_create(pool, "Bob")
    cid = contact["id"]

    await stay_in_touch_set(pool, cid, 7)
    cleared = await stay_in_touch_set(pool, cid, None)
    assert cleared["stay_in_touch_days"] is None


async def test_stay_in_touch_set_not_found(pool_with_cadence):
    """Setting cadence on a non-existent contact raises ValueError."""
    from butlers.tools.relationship import stay_in_touch_set

    pool = pool_with_cadence
    fake_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await stay_in_touch_set(pool, fake_id, 30)


async def test_contacts_overdue_with_stale_interaction(pool_with_cadence):
    """Contact with last interaction beyond cadence shows as overdue."""
    from butlers.tools.relationship import (
        contact_create,
        contacts_overdue,
        interaction_log,
        stay_in_touch_set,
    )

    pool = pool_with_cadence
    contact = await contact_create(pool, "Charlie")
    cid = contact["id"]

    await stay_in_touch_set(pool, cid, 7)
    # Log an interaction 10 days ago
    old_time = datetime.now(UTC).replace(microsecond=0) - __import__("datetime").timedelta(days=10)
    await interaction_log(pool, cid, "call", "Catch-up call", occurred_at=old_time)

    overdue = await contacts_overdue(pool)
    overdue_ids = [c["id"] for c in overdue]
    assert cid in overdue_ids
    # Verify staleness data is present
    match = [c for c in overdue if c["id"] == cid][0]
    assert match["days_since_last_interaction"] is not None
    assert match["days_since_last_interaction"] >= 10


async def test_contacts_overdue_no_interaction(pool_with_cadence):
    """Contact with cadence but no interactions is always overdue."""
    from butlers.tools.relationship import contact_create, contacts_overdue, stay_in_touch_set

    pool = pool_with_cadence
    contact = await contact_create(pool, "Diana")
    cid = contact["id"]

    await stay_in_touch_set(pool, cid, 30)

    overdue = await contacts_overdue(pool)
    overdue_ids = [c["id"] for c in overdue]
    assert cid in overdue_ids
    # last_interaction_at should be None
    match = [c for c in overdue if c["id"] == cid][0]
    assert match["last_interaction_at"] is None
    assert match["days_since_last_interaction"] is None


async def test_contacts_overdue_recent_interaction(pool_with_cadence):
    """Contact with recent interaction within cadence does NOT show as overdue."""
    from butlers.tools.relationship import (
        contact_create,
        contacts_overdue,
        interaction_log,
        stay_in_touch_set,
    )

    pool = pool_with_cadence
    contact = await contact_create(pool, "Eve")
    cid = contact["id"]

    await stay_in_touch_set(pool, cid, 30)
    # Log a recent interaction (now)
    await interaction_log(pool, cid, "coffee", "Coffee catch-up")

    overdue = await contacts_overdue(pool)
    overdue_ids = [c["id"] for c in overdue]
    assert cid not in overdue_ids


async def test_contacts_overdue_no_cadence_excluded(pool_with_cadence):
    """Contact without cadence (NULL) never appears in overdue list."""
    from butlers.tools.relationship import contact_create, contacts_overdue

    pool = pool_with_cadence
    contact = await contact_create(pool, "Frank")
    # No cadence set — stay_in_touch_days is NULL by default

    overdue = await contacts_overdue(pool)
    overdue_ids = [c["id"] for c in overdue]
    assert contact["id"] not in overdue_ids


async def test_contacts_overdue_cleared_cadence_excluded(pool_with_cadence):
    """Clearing cadence removes contact from overdue list."""
    from butlers.tools.relationship import contact_create, contacts_overdue, stay_in_touch_set

    pool = pool_with_cadence
    contact = await contact_create(pool, "Grace")
    cid = contact["id"]

    # Set cadence — should be overdue (no interactions)
    await stay_in_touch_set(pool, cid, 1)
    overdue = await contacts_overdue(pool)
    assert cid in [c["id"] for c in overdue]

    # Clear cadence — should no longer be overdue
    await stay_in_touch_set(pool, cid, None)
    overdue = await contacts_overdue(pool)
    assert cid not in [c["id"] for c in overdue]


async def test_contacts_overdue_archived_excluded(pool_with_cadence):
    """Archived contacts with cadence are excluded from overdue list."""
    from butlers.tools.relationship import (
        contact_archive,
        contact_create,
        contacts_overdue,
        stay_in_touch_set,
    )

    pool = pool_with_cadence
    contact = await contact_create(pool, "Hank")
    cid = contact["id"]

    await stay_in_touch_set(pool, cid, 1)
    await contact_archive(pool, cid)

    overdue = await contacts_overdue(pool)
    overdue_ids = [c["id"] for c in overdue]
    assert cid not in overdue_ids
