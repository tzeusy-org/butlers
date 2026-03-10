"""Tests for relationship butler tools writing/reading SPO facts.

These tests verify that when a `facts` table is present, the tools
write to SPO facts and read back from them correctly.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Embedding engine mock — prevents loading sentence_transformers in tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def mock_embedding_engine():
    """Session-scoped autouse fixture that patches the embedding engine.

    Relationship tools lazy-load an EmbeddingEngine via get_embedding_engine().
    In tests we don't have sentence_transformers available, so we return a
    deterministic fake that produces a list of floats.  The facts table stores
    embedding as TEXT so the stringified list is compatible.
    """
    import sys

    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384

    with patch("butlers.modules.memory.tools.get_embedding_engine", return_value=engine):
        # Reset module-level _embedding_engine cache in each relationship tool
        for mod_name in (
            "butlers.tools.relationship.facts",
            "butlers.tools.relationship.gifts",
            "butlers.tools.relationship.tasks",
            "butlers.tools.relationship.loans",
            "butlers.tools.relationship.reminders",
            "butlers.tools.relationship.interactions",
            "butlers.tools.relationship.life_events",
        ):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


# ---------------------------------------------------------------------------
# Pool fixture — relationship tables + facts table (no pgvector)
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with relationship tables + facts table."""
    async with provisioned_postgres_pool() as p:
        # Contacts table (minimal — includes entity_id for resolve tests)
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
                entity_id UUID,
                listed BOOLEAN NOT NULL DEFAULT true,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # Activity feed (needed by _log_activity)
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
        # Life event taxonomy tables (needed by _validate_life_event_type)
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
        # Seed one life event category and type for validation tests
        await p.execute("""
            INSERT INTO life_event_categories (name) VALUES ('career') ON CONFLICT DO NOTHING
        """)
        await p.execute("""
            INSERT INTO life_event_types (category_id, name)
            SELECT c.id, 'promotion' FROM life_event_categories c WHERE c.name = 'career'
            ON CONFLICT DO NOTHING
        """)
        await p.execute("""
            INSERT INTO life_event_types (category_id, name)
            SELECT c.id, 'new_job' FROM life_event_categories c WHERE c.name = 'career'
            ON CONFLICT DO NOTHING
        """)
        # Facts table — embedding stored as TEXT (no pgvector required in tests)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT,
                search_vector tsvector,
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                permanence TEXT NOT NULL DEFAULT 'standard',
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID REFERENCES facts(id) ON DELETE SET NULL,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                entity_id UUID,
                object_entity_id UUID,
                valid_at TIMESTAMPTZ,
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_referenced_at TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{}'::jsonb,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal'
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
            ON facts (subject, predicate)
        """)
        # memory_links table — needed by store_fact() supersession
        await p.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                source_type TEXT NOT NULL,
                source_id UUID NOT NULL,
                target_type TEXT NOT NULL,
                target_id UUID NOT NULL,
                relation TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (source_type, source_id, target_type, target_id),
                CONSTRAINT chk_memory_links_relation CHECK (
                    relation IN (
                        'derived_from', 'supports', 'contradicts',
                        'supersedes', 'related_to'
                    )
                )
            )
        """)

        yield p


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_contact(pool, first_name: str) -> dict:
    row = await pool.fetchrow(
        "INSERT INTO contacts (first_name) VALUES ($1) RETURNING id, first_name",
        first_name,
    )
    return dict(row)


# ===========================================================================
# Quick facts (property facts)
# ===========================================================================


async def test_fact_set_reads_from_facts_table(pool):
    """fact_set writes to facts table and fact_list reads from it."""
    from butlers.tools.relationship.facts import fact_list, fact_set

    contact = await _make_contact(pool, "Alice")
    cid = contact["id"]

    result = await fact_set(pool, cid, "color", "blue")
    assert result["key"] == "color"
    assert result["value"] == "blue"
    assert result["contact_id"] == cid

    items = await fact_list(pool, cid)
    assert len(items) == 1
    assert items[0]["key"] == "color"
    assert items[0]["value"] == "blue"


async def test_fact_set_supersedes_previous(pool):
    """fact_set on same key supersedes the old property fact."""
    from butlers.tools.relationship.facts import fact_list, fact_set

    contact = await _make_contact(pool, "Bob")
    cid = contact["id"]

    await fact_set(pool, cid, "city", "London")
    await fact_set(pool, cid, "city", "Paris")

    items = await fact_list(pool, cid)
    city_items = [i for i in items if i["key"] == "city"]
    assert len(city_items) == 1
    assert city_items[0]["value"] == "Paris"

    # Old fact should be superseded in DB
    superseded = await pool.fetchval(
        "SELECT COUNT(*) FROM facts"
        " WHERE subject = $1 AND predicate = 'city' AND validity = 'superseded'",
        str(cid),
    )
    assert superseded == 1


async def test_fact_list_multiple_keys(pool):
    """fact_list returns all active property facts for a contact."""
    from butlers.tools.relationship.facts import fact_list, fact_set

    contact = await _make_contact(pool, "Carol")
    cid = contact["id"]

    await fact_set(pool, cid, "food", "sushi")
    await fact_set(pool, cid, "sport", "tennis")

    items = await fact_list(pool, cid)
    keys = {i["key"] for i in items}
    assert "food" in keys
    assert "sport" in keys


# ===========================================================================
# Gifts (temporal facts)
# ===========================================================================


async def test_gift_add_writes_to_facts(pool):
    """gift_add writes a temporal fact and gift_list reads from it."""
    from butlers.tools.relationship.gifts import gift_add, gift_list

    contact = await _make_contact(pool, "Dave")
    cid = contact["id"]

    gift = await gift_add(pool, cid, "book about Python", occasion="birthday")
    assert gift["description"] == "book about Python"
    assert gift["occasion"] == "birthday"
    assert gift["status"] == "idea"
    assert gift["contact_id"] == cid

    gifts = await gift_list(pool, cid)
    assert len(gifts) == 1
    assert gifts[0]["description"] == "book about Python"


async def test_gift_add_multiple_gifts_coexist(pool):
    """Multiple gifts for a contact coexist as separate temporal facts."""
    from butlers.tools.relationship.gifts import gift_add, gift_list

    contact = await _make_contact(pool, "Eve")
    cid = contact["id"]

    await gift_add(pool, cid, "wine")
    await gift_add(pool, cid, "flowers")

    gifts = await gift_list(pool, cid)
    assert len(gifts) == 2
    descs = {g["description"] for g in gifts}
    assert "wine" in descs
    assert "flowers" in descs


async def test_gift_update_status(pool):
    """gift_update_status supersedes old fact and creates new one."""
    from butlers.tools.relationship.gifts import gift_add, gift_update_status

    contact = await _make_contact(pool, "Frank")
    cid = contact["id"]

    gift = await gift_add(pool, cid, "chocolate box")
    gift_id = gift["id"]

    updated = await gift_update_status(pool, gift_id, "purchased")
    assert updated["status"] == "purchased"

    # Old fact superseded
    superseded = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE id = $1 AND validity = 'superseded'",
        gift_id,
    )
    assert superseded == 1


async def test_gift_update_status_invalid_regression(pool):
    """gift_update_status rejects backward status transitions."""
    from butlers.tools.relationship.gifts import gift_add, gift_update_status

    contact = await _make_contact(pool, "Grace")
    cid = contact["id"]

    gift = await gift_add(pool, cid, "scarf")
    gift_id = gift["id"]

    updated_gift = await gift_update_status(pool, gift_id, "purchased")

    with pytest.raises(ValueError, match="Cannot move"):
        await gift_update_status(pool, updated_gift["id"], "idea")


async def test_gift_list_filter_by_status(pool):
    """gift_list filters by status correctly."""
    from butlers.tools.relationship.gifts import gift_add, gift_list, gift_update_status

    contact = await _make_contact(pool, "Helen")
    cid = contact["id"]

    g1 = await gift_add(pool, cid, "hat")
    await gift_update_status(pool, g1["id"], "purchased")
    await gift_add(pool, cid, "gloves")

    idea_gifts = await gift_list(pool, cid, status="idea")
    assert len(idea_gifts) == 1
    assert idea_gifts[0]["description"] == "gloves"

    purchased_gifts = await gift_list(pool, cid, status="purchased")
    assert len(purchased_gifts) == 1
    assert purchased_gifts[0]["description"] == "hat"


# ===========================================================================
# Tasks (temporal facts)
# ===========================================================================


async def test_task_create_writes_to_facts(pool):
    """task_create writes a temporal fact."""
    from butlers.tools.relationship.tasks import task_create

    contact = await _make_contact(pool, "Ivan")
    cid = contact["id"]

    task = await task_create(pool, cid, "Call back", description="About the meeting")
    assert task["title"] == "Call back"
    assert task["description"] == "About the meeting"
    assert task["completed"] is False
    assert task["contact_id"] == cid


async def test_task_list_returns_tasks(pool):
    """task_list reads tasks from facts."""
    from butlers.tools.relationship.tasks import task_create, task_list

    contact = await _make_contact(pool, "Julia")
    cid = contact["id"]

    await task_create(pool, cid, "Send email")
    await task_create(pool, cid, "Book flight")

    tasks = await task_list(pool, cid)
    assert len(tasks) == 2
    titles = {t["title"] for t in tasks}
    assert "Send email" in titles
    assert "Book flight" in titles


async def test_task_list_includes_contact_name(pool):
    """task_list includes contact_name field."""
    from butlers.tools.relationship.tasks import task_create, task_list

    contact = await _make_contact(pool, "Karl")
    cid = contact["id"]

    await task_create(pool, cid, "Follow up")
    tasks = await task_list(pool, cid)
    assert len(tasks) == 1
    assert tasks[0]["contact_name"] == "Karl"


async def test_task_complete_supersedes_old_fact(pool):
    """task_complete supersedes old task fact and creates completed version."""
    from butlers.tools.relationship.tasks import task_complete, task_create

    contact = await _make_contact(pool, "Linda")
    cid = contact["id"]

    task = await task_create(pool, cid, "Fix bug")
    task_id = task["id"]

    completed = await task_complete(pool, task_id)
    assert completed["completed"] is True
    assert completed["completed_at"] is not None

    # Old fact superseded
    superseded = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE id = $1 AND validity = 'superseded'",
        task_id,
    )
    assert superseded == 1


async def test_task_delete_retracts_fact(pool):
    """task_delete retracts the task fact."""
    from butlers.tools.relationship.tasks import task_create, task_delete, task_list

    contact = await _make_contact(pool, "Mike")
    cid = contact["id"]

    task = await task_create(pool, cid, "Buy groceries")
    task_id = task["id"]

    await task_delete(pool, task_id)

    tasks = await task_list(pool, cid)
    task_ids = {t["id"] for t in tasks}
    assert task_id not in task_ids


async def test_task_list_exclude_completed_by_default(pool):
    """task_list excludes completed tasks by default."""
    from butlers.tools.relationship.tasks import task_complete, task_create, task_list

    contact = await _make_contact(pool, "Nancy")
    cid = contact["id"]

    t1 = await task_create(pool, cid, "Active task")
    t2 = await task_create(pool, cid, "Done task")

    completed = await task_complete(pool, t2["id"])

    # Default: no completed tasks
    tasks = await task_list(pool, cid)
    task_ids = {t["id"] for t in tasks}
    assert t1["id"] in task_ids
    assert completed["id"] not in task_ids

    # Include completed
    tasks_all = await task_list(pool, cid, include_completed=True)
    all_ids = {t["id"] for t in tasks_all}
    assert t1["id"] in all_ids
    assert completed["id"] in all_ids


# ===========================================================================
# Reminders (temporal facts)
# ===========================================================================


async def test_reminder_create_writes_to_facts(pool):
    """reminder_create writes a temporal fact."""
    from butlers.tools.relationship.reminders import reminder_create

    contact = await _make_contact(pool, "Oscar")
    cid = contact["id"]
    due = datetime.now(UTC) + timedelta(days=7)

    reminder = await reminder_create(pool, cid, message="Follow up", next_trigger_at=due)
    assert reminder["message"] == "Follow up"
    assert reminder["contact_id"] == cid
    assert reminder["dismissed"] is False
    assert reminder["last_triggered_at"] is None


async def test_reminder_create_one_time(pool):
    """reminder_create handles one_time type."""
    from butlers.tools.relationship.reminders import reminder_create

    contact = await _make_contact(pool, "Pam")
    cid = contact["id"]
    due = datetime.now(UTC) + timedelta(days=3)

    reminder = await reminder_create(
        pool, cid, message="Birthday call", reminder_type="one_time", next_trigger_at=due
    )
    assert reminder["type"] == "one_time"
    assert reminder["last_triggered_at"] is None


async def test_reminder_list_active(pool):
    """reminder_list returns active reminders."""
    from butlers.tools.relationship.reminders import reminder_create, reminder_list

    contact = await _make_contact(pool, "Quinn")
    cid = contact["id"]
    due = datetime.now(UTC) + timedelta(days=1)

    await reminder_create(pool, cid, message="Check in", next_trigger_at=due)

    reminders = await reminder_list(pool, cid)
    assert len(reminders) >= 1
    msgs = {r["message"] for r in reminders}
    assert "Check in" in msgs


async def test_reminder_dismiss_one_time(pool):
    """reminder_dismiss marks one_time reminder as dismissed (next_trigger_at=None)."""
    from butlers.tools.relationship.reminders import reminder_create, reminder_dismiss

    contact = await _make_contact(pool, "Rita")
    cid = contact["id"]
    due = datetime.now(UTC) + timedelta(days=2)

    reminder = await reminder_create(
        pool, cid, message="Ping", reminder_type="one_time", next_trigger_at=due
    )
    rid = reminder["id"]

    dismissed = await reminder_dismiss(pool, rid)
    assert dismissed["next_trigger_at"] is None
    assert dismissed["last_triggered_at"] is not None


async def test_reminder_dismiss_recurring_monthly(pool):
    """reminder_dismiss on recurring_monthly advances next_trigger_at by 1 month."""
    from butlers.tools.relationship.reminders import reminder_create, reminder_dismiss

    contact = await _make_contact(pool, "Sam")
    cid = contact["id"]
    due = datetime(2026, 3, 15, tzinfo=UTC)

    reminder = await reminder_create(
        pool,
        cid,
        message="Monthly check",
        reminder_type="recurring_monthly",
        next_trigger_at=due,
    )
    rid = reminder["id"]

    dismissed = await reminder_dismiss(pool, rid)
    assert dismissed["next_trigger_at"] is not None
    new_next = dismissed["next_trigger_at"]
    # Should be April 15
    assert new_next.month == 4
    assert new_next.day == 15


# ===========================================================================
# Loans (temporal facts)
# ===========================================================================


async def test_loan_create_writes_to_facts(pool):
    """loan_create writes a temporal fact."""
    from butlers.tools.relationship.loans import loan_create

    contact = await _make_contact(pool, "Tom")
    cid = contact["id"]

    loan = await loan_create(
        pool,
        contact_id=cid,
        amount=Decimal("50.00"),
        direction="lent",
        description="Coffee money",
        currency="USD",
    )
    assert loan["description"] == "Coffee money"
    assert loan["settled"] is False
    assert loan["lender_contact_id"] == cid


async def test_loan_settle_updates_fact(pool):
    """loan_settle supersedes old loan fact and marks as settled."""
    from butlers.tools.relationship.loans import loan_create, loan_settle

    contact = await _make_contact(pool, "Uma")
    cid = contact["id"]

    loan = await loan_create(
        pool, contact_id=cid, amount=Decimal("100.00"), direction="borrowed", description="Taxi"
    )
    loan_id = loan["id"]

    settled = await loan_settle(pool, loan_id)
    assert settled["settled"] is True
    assert settled["settled_at"] is not None

    # Old fact superseded
    superseded = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE id = $1 AND validity = 'superseded'",
        loan_id,
    )
    assert superseded == 1


async def test_loan_list_returns_loans(pool):
    """loan_list reads loans from facts."""
    from butlers.tools.relationship.loans import loan_create, loan_list

    contact = await _make_contact(pool, "Victor")
    cid = contact["id"]

    await loan_create(
        pool, contact_id=cid, amount=Decimal("20.00"), direction="lent", description="Lunch"
    )
    await loan_create(
        pool, contact_id=cid, amount=Decimal("30.00"), direction="lent", description="Dinner"
    )

    loans = await loan_list(pool, cid)
    assert len(loans) == 2


# ===========================================================================
# Interactions (temporal facts)
# ===========================================================================


async def test_interaction_log_writes_to_facts(pool):
    """interaction_log writes a temporal fact."""
    from butlers.tools.relationship.interactions import interaction_log

    contact = await _make_contact(pool, "Wendy")
    cid = contact["id"]
    occurred = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

    result = await interaction_log(pool, cid, "call", summary="Caught up", occurred_at=occurred)
    assert result["type"] == "call"
    assert result["summary"] == "Caught up"
    assert result["contact_id"] == cid


async def test_interaction_log_with_metadata(pool):
    """interaction_log stores user-provided metadata."""
    from butlers.tools.relationship.interactions import interaction_log

    contact = await _make_contact(pool, "Xavier")
    cid = contact["id"]
    meta = {"topic": "project update"}
    occurred = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)

    result = await interaction_log(
        pool, cid, "meeting", summary="Project sync", occurred_at=occurred, metadata=meta
    )
    assert result["metadata"] == meta


async def test_interaction_list_returns_interactions(pool):
    """interaction_list reads interactions from facts."""
    from butlers.tools.relationship.interactions import interaction_list, interaction_log

    contact = await _make_contact(pool, "Yara")
    cid = contact["id"]

    await interaction_log(
        pool, cid, "email", summary="Hi!", occurred_at=datetime(2026, 3, 1, tzinfo=UTC)
    )
    await interaction_log(
        pool, cid, "call", summary="Chat", occurred_at=datetime(2026, 3, 2, tzinfo=UTC)
    )

    items = await interaction_list(pool, cid)
    assert len(items) == 2
    types = {i["type"] for i in items}
    assert "email" in types
    assert "call" in types


# ===========================================================================
# Life events (temporal facts)
# ===========================================================================


async def test_life_event_log_basic(pool):
    """life_event_log writes a temporal fact."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(pool, "Zara")
    cid = contact["id"]

    result = await life_event_log(pool, cid, "promotion", summary="Got promoted!")
    assert result["type_name"] == "promotion"
    assert result["summary"] == "Got promoted!"
    assert result["contact_id"] == cid


async def test_life_event_log_without_date(pool):
    """life_event_log with no date leaves happened_at as None in return."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(pool, "Andy")
    cid = contact["id"]

    result = await life_event_log(pool, cid, "new_job", summary="New role")
    # happened_at should be None since no date was provided
    assert result.get("happened_at") is None


async def test_life_event_log_with_date(pool):
    """life_event_log with happened_at sets the date."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(pool, "Beth")
    cid = contact["id"]

    result = await life_event_log(
        pool, cid, "promotion", summary="Promoted", happened_at="2026-01-15"
    )
    from datetime import date

    assert result["happened_at"] == date(2026, 1, 15)


async def test_life_event_list_returns_events(pool):
    """life_event_list reads events from facts table."""
    from butlers.tools.relationship.life_events import life_event_list, life_event_log

    contact = await _make_contact(pool, "Chad")
    cid = contact["id"]

    await life_event_log(pool, cid, "promotion", summary="Promoted to VP")
    await life_event_log(pool, cid, "new_job", summary="Started at Acme")

    events = await life_event_list(pool, cid)
    assert len(events) == 2
    type_names = {e["type_name"] for e in events}
    assert "promotion" in type_names
    assert "new_job" in type_names


async def test_life_event_list_filter_by_type(pool):
    """life_event_list filters by type_name correctly."""
    from butlers.tools.relationship.life_events import life_event_list, life_event_log

    contact = await _make_contact(pool, "Dana")
    cid = contact["id"]

    await life_event_log(pool, cid, "promotion", summary="Promoted")
    await life_event_log(pool, cid, "new_job", summary="New job")

    events = await life_event_list(pool, cid, type_name="promotion")
    assert len(events) == 1
    assert events[0]["type_name"] == "promotion"


async def test_life_event_log_invalid_type_raises(pool):
    """life_event_log raises ValueError for unknown event type."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(pool, "Evan")
    cid = contact["id"]

    with pytest.raises(ValueError, match="Unknown life event type"):
        await life_event_log(pool, cid, "alien_abduction", summary="Weird")
