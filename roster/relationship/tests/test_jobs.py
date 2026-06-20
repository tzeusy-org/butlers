"""Tests for the Relationship butler scheduled job handlers (insight-scan)."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _today() -> date:
    return datetime.now(UTC).date()


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Schema setup helpers
# ---------------------------------------------------------------------------

CREATE_CONTACTS_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT,
    last_name TEXT,
    nickname TEXT,
    company TEXT,
    entity_id UUID,
    stay_in_touch_days INT,
    listed BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
)
"""

CREATE_IMPORTANT_DATES_SQL = """
CREATE TABLE IF NOT EXISTS important_dates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    month INT NOT NULL,
    day INT NOT NULL,
    year INT,
    created_at TIMESTAMPTZ DEFAULT now()
)
"""

CREATE_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(384),
    search_vector tsvector,
    importance FLOAT NOT NULL DEFAULT 5.0,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    decay_rate FLOAT NOT NULL DEFAULT 0.008,
    permanence TEXT NOT NULL DEFAULT 'standard',
    source_butler TEXT,
    source_episode_id UUID,
    supersedes_id UUID,
    validity TEXT NOT NULL DEFAULT 'active',
    scope TEXT NOT NULL DEFAULT 'global',
    reference_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_referenced_at TIMESTAMPTZ,
    last_confirmed_at TIMESTAMPTZ,
    tags JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    entity_id UUID,
    object_entity_id UUID,
    valid_at TIMESTAMPTZ,
    tenant_id TEXT NOT NULL DEFAULT 'shared',
    request_id TEXT,
    retention_class TEXT NOT NULL DEFAULT 'operational',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    idempotency_key TEXT,
    observed_at TIMESTAMPTZ DEFAULT now(),
    invalid_at TIMESTAMPTZ,
    embedding_model_version TEXT DEFAULT 'unknown'
)
"""

CREATE_ENTITIES_SQL = """
CREATE TABLE IF NOT EXISTS public.entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name VARCHAR NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    entity_type VARCHAR NOT NULL DEFAULT 'other',
    aliases TEXT[] NOT NULL DEFAULT '{}',
    metadata JSONB DEFAULT '{}'::jsonb,
    roles TEXT[] NOT NULL DEFAULT '{}',
    listed BOOLEAN NOT NULL DEFAULT true,
    stay_in_touch_days INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# contact_entity_map (rel_029) — contact_id → entity_id bridge that dunbar reads
# instead of public.contacts (Phase 7.4e).
CREATE_CONTACT_ENTITY_MAP_SQL = """
CREATE TABLE IF NOT EXISTS contact_entity_map (
    contact_id  UUID NOT NULL,
    entity_id   UUID NOT NULL,
    CONSTRAINT contact_entity_map_pkey PRIMARY KEY (contact_id)
)
"""


async def _setup_relationship_schema(pool) -> None:
    """Create the relationship-related tables needed for insight scan tests."""
    # Install required extensions for vector embeddings and full-text search
    await pool.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
    await pool.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    await pool.execute(CREATE_ENTITIES_SQL)
    await pool.execute(CREATE_CONTACTS_SQL)
    await pool.execute(CREATE_CONTACT_ENTITY_MAP_SQL)
    await pool.execute(CREATE_IMPORTANT_DATES_SQL)
    await pool.execute(CREATE_FACTS_SQL)
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_subj_pred ON facts (subject, predicate)"
    )


async def _setup_insight_tables(pool) -> None:
    """Create insight_candidates and related tables."""
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await create_insight_tables(pool)


# ---------------------------------------------------------------------------
# Helper insert functions
# ---------------------------------------------------------------------------


async def _insert_contact(
    pool,
    *,
    first_name: str = "Alice",
    last_name: str = "Smith",
    listed: bool = True,
    stay_in_touch_days: int | None = None,
    entity_id: str | None = None,
) -> str:
    """Insert a contact, a matching public.entities row, and a contact_entity_map entry.

    Seeds entity-side fields (canonical_name, listed, stay_in_touch_days) directly so
    re-pointed queries that JOIN contact_entity_map → public.entities find the right values
    without depending on bu-0mb6j dual-write (may not yet be merged when tests run).
    """
    contact_id = str(uuid.uuid4())
    resolved_entity_id = uuid.UUID(entity_id) if entity_id else uuid.uuid4()

    # Compose canonical_name the same way the old CONCAT_WS did
    name_parts = [p for p in (first_name, last_name) if p]
    canonical_name = " ".join(name_parts) if name_parts else "Unknown"

    # Seed public.entities with listed + stay_in_touch_days (rel_031 columns)
    await pool.execute(
        """
        INSERT INTO public.entities (id, canonical_name, name, listed, stay_in_touch_days)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (id) DO NOTHING
        """,
        resolved_entity_id,
        canonical_name,
        canonical_name,
        listed,
        stay_in_touch_days,
    )

    # Seed contact_entity_map bridge (rel_029)
    await pool.execute(
        """
        INSERT INTO contact_entity_map (contact_id, entity_id)
        VALUES ($1::uuid, $2)
        ON CONFLICT (contact_id) DO NOTHING
        """,
        contact_id,
        resolved_entity_id,
    )

    # Keep contacts row so important_dates FK + _insert_interaction_fact entity_id lookup work
    await pool.execute(
        """
        INSERT INTO contacts (id, first_name, last_name, listed, stay_in_touch_days, entity_id)
        VALUES ($1::uuid, $2, $3, $4, $5, $6)
        """,
        contact_id,
        first_name,
        last_name,
        listed,
        stay_in_touch_days,
        resolved_entity_id,
    )
    # Seed the entity + contact_entity_map bridge so dunbar (which now reads via
    # contact_entity_map → public.entities, not public.contacts) sees this contact.
    await pool.execute(
        """
        INSERT INTO public.entities (id, name, canonical_name, listed, stay_in_touch_days)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (id) DO NOTHING
        """,
        resolved_entity_id,
        first_name,
        f"{first_name} {last_name}".strip(),
        listed,
        stay_in_touch_days,
    )
    await pool.execute(
        """
        INSERT INTO contact_entity_map (contact_id, entity_id)
        VALUES ($1::uuid, $2)
        ON CONFLICT (contact_id) DO NOTHING
        """,
        contact_id,
        resolved_entity_id,
    )
    return contact_id


async def _insert_important_date(
    pool,
    *,
    contact_id: str,
    label: str = "birthday",
    month: int,
    day: int,
    year: int | None = None,
) -> str:
    """Insert an important date and return its UUID string."""
    date_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO important_dates (id, contact_id, label, month, day, year)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
        """,
        date_id,
        contact_id,
        label,
        month,
        day,
        year,
    )
    return date_id


async def _insert_interaction_fact(
    pool,
    *,
    contact_id: str,
    occurred_at: datetime | None = None,
    interaction_type: str = "call",
) -> str:
    """Insert an interaction fact for a contact.

    Looks up entity_id from the contacts table so the fact is stored with
    ``subject='entity:{entity_id}'`` and the entity_id column set.  The new
    reader queries join on ``f.entity_id = c.entity_id``, so facts without a
    populated entity_id would be silently dropped.
    """
    if occurred_at is None:
        occurred_at = _utcnow()
    entity_id = await pool.fetchval(
        "SELECT entity_id FROM contacts WHERE id = $1::uuid", contact_id
    )
    fact_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO facts (id, subject, predicate, content, scope, entity_id, validity, valid_at)
        VALUES ($1::uuid, $2, $3, 'had a chat', 'relationship', $4, 'active', $5)
        """,
        fact_id,
        f"entity:{entity_id}",
        f"interaction_{interaction_type}",
        entity_id,
        occurred_at,
    )
    return fact_id


async def _insert_gift_fact(
    pool,
    *,
    contact_id: str,
    description: str = "a book",
    status: str = "idea",
    occasion: str | None = None,
) -> str:
    """Insert a gift (facts-based) for a contact and return fact UUID string."""
    from butlers.tools.relationship.gifts import _slug

    fact_id = str(uuid.uuid4())
    subject = f"contact:{contact_id}:gift:{_slug(description)}"
    meta: dict = {"status": status}
    if occasion:
        meta["occasion"] = occasion
    await pool.execute(
        """
        INSERT INTO facts (id, subject, predicate, content, scope, validity, valid_at, metadata)
        VALUES ($1::uuid, $2, 'gift', $3, 'relationship', 'active', NULL, $4)
        """,
        fact_id,
        subject,
        description,
        meta,
    )
    return fact_id


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — no data / no-op paths
# ---------------------------------------------------------------------------


async def test_insight_scan_no_contacts_no_op(provisioned_postgres_pool):
    """No-op: returns zeros when no contacts exist."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0
        assert result["candidates_accepted"] == 0
        assert result["candidates_filtered"] == 0
        assert result["candidates_errored"] == 0
        assert result["early_exit"] is False


async def test_insight_scan_unlisted_contact_excluded(provisioned_postgres_pool):
    """Unlisted contacts are excluded from all insight categories."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        # Unlisted contact with upcoming birthday
        contact_id = await _insert_contact(pool, first_name="Bob", listed=False)
        today = _today()
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=today.month,
            day=today.day,
        )

        result = await run_insight_scan(pool)
        assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — upcoming date insights
# ---------------------------------------------------------------------------


async def test_insight_scan_upcoming_birthday_today_priority_95(provisioned_postgres_pool):
    """Birthday today gets priority 95 (time-critical)."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        contact_id = await _insert_contact(pool, first_name="Alice", last_name="Day")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=today.month,
            day=today.day,
        )

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] >= 1
        rows = await pool.fetch(
            "SELECT priority, category, dedup_key FROM insight_candidates"
            " WHERE category = 'birthday'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 95


async def test_insight_scan_upcoming_birthday_3_days_priority_80(provisioned_postgres_pool):
    """Birthday in 3 days gets priority 80."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=3)
        contact_id = await _insert_contact(pool, first_name="Carol")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority FROM insight_candidates WHERE category = 'birthday'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 80


async def test_insight_scan_upcoming_birthday_7_days_priority_70(provisioned_postgres_pool):
    """Birthday in 5-7 days gets priority 70."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=6)
        contact_id = await _insert_contact(pool, first_name="Dave")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority FROM insight_candidates WHERE category = 'birthday'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 70


async def test_insight_scan_birthday_beyond_window_excluded(provisioned_postgres_pool):
    """Birthdays beyond 7 days are excluded."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=10)
        contact_id = await _insert_contact(pool, first_name="Eve")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )

        result = await run_insight_scan(pool)
        assert result["candidates_proposed"] == 0


async def test_insight_scan_anniversary_dedup_key_format(provisioned_postgres_pool):
    """Anniversary dedup_key follows anniversary:{entity-id}:{year} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=2)
        entity_id = str(uuid.uuid4())
        contact_id = await _insert_contact(pool, first_name="Frank", entity_id=entity_id)
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="anniversary",
            month=target.month,
            day=target.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'anniversary'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key.startswith("anniversary:")
        assert entity_id in dedup_key
        assert str(target.year) in dedup_key


async def test_insight_scan_birthday_dedup_key_format(provisioned_postgres_pool):
    """Birthday dedup_key follows birthday:{entity-id}:{year} format when entity_id exists."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=4)
        entity_id = str(uuid.uuid4())
        contact_id = await _insert_contact(pool, first_name="Grace", entity_id=entity_id)
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'birthday'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key.startswith("birthday:")
        assert entity_id in dedup_key


async def test_insight_scan_birthday_cooldown_days(provisioned_postgres_pool):
    """Birthday cooldown_days matches priority tier."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        # within 1 day → cooldown 1
        target = _today() + timedelta(days=1)
        contact_id = await _insert_contact(pool, first_name="Hannah")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT cooldown_days FROM insight_candidates WHERE category = 'birthday'"
        )
        assert len(rows) == 1
        assert rows[0]["cooldown_days"] == 1


async def test_insight_scan_birthday_message_includes_contact_name(provisioned_postgres_pool):
    """Birthday message includes the contact name."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=3)
        contact_id = await _insert_contact(pool, first_name="Isabella", last_name="Clark")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT message FROM insight_candidates WHERE category = 'birthday'"
        )
        assert len(rows) == 1
        assert "Isabella" in rows[0]["message"]


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — stale contact insights
# ---------------------------------------------------------------------------


async def test_insight_scan_stale_contact_overdue_2x_cadence_priority_45(
    provisioned_postgres_pool,
):
    """Contact overdue by >2x cadence gets priority 45."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        # Contact with stay_in_touch_days=14, last interaction 35 days ago (>2x)
        contact_id = await _insert_contact(pool, first_name="Jack", stay_in_touch_days=14)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=35),
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority, category FROM insight_candidates WHERE category = 'stale-contact'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 45


async def test_insight_scan_stale_contact_overdue_1x_cadence_priority_35(
    provisioned_postgres_pool,
):
    """Contact overdue by 1-2x cadence gets priority 35."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        # Contact with stay_in_touch_days=14, last interaction 20 days ago (1-2x)
        contact_id = await _insert_contact(pool, first_name="Karen", stay_in_touch_days=14)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=20),
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority FROM insight_candidates WHERE category = 'stale-contact'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 35


async def test_insight_scan_stale_contact_not_yet_overdue_excluded(provisioned_postgres_pool):
    """Contact not yet overdue is excluded from stale-contact insights."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        # Contact with cadence 14 days, last interaction 10 days ago
        contact_id = await _insert_contact(pool, first_name="Leo", stay_in_touch_days=14)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=10),
        )

        await run_insight_scan(pool)
        # No stale-contact candidates
        rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'stale-contact'"
        )
        assert len(rows) == 0


async def test_insight_scan_stale_contact_dedup_key_weekly_granularity(
    provisioned_postgres_pool,
):
    """Stale contact dedup_key uses relationship:stale-contact:{id}:{year-week} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        contact_id = await _insert_contact(pool, first_name="Mia", stay_in_touch_days=7)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=30),
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'stale-contact'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key.startswith("relationship:stale-contact:")
        assert contact_id in dedup_key
        # Should contain year-week pattern
        iso_year, iso_week, _ = _today().isocalendar()
        assert f"{iso_year}-W{iso_week:02d}" in dedup_key


async def test_insight_scan_stale_contact_expires_7_days_from_now(provisioned_postgres_pool):
    """Stale contact candidate expires 7 days from generation."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        contact_id = await _insert_contact(pool, first_name="Noah", stay_in_touch_days=7)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=30),
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT expires_at FROM insight_candidates WHERE category = 'stale-contact'"
        )
        assert len(rows) == 1
        expires_at = rows[0]["expires_at"]
        days_until_expiry = (expires_at.date() - _today()).days
        # Allow 6-8 days to handle edge cases around midnight
        assert 6 <= days_until_expiry <= 8


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — pending gift insights
# ---------------------------------------------------------------------------


async def test_insight_scan_pending_gift_with_upcoming_date_priority_60(
    provisioned_postgres_pool,
):
    """Pending gift (idea/purchased) with upcoming date gets priority 60."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=5)
        contact_id = await _insert_contact(pool, first_name="Olivia")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )
        await _insert_gift_fact(pool, contact_id=contact_id, description="flowers", status="idea")

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority, category, dedup_key FROM insight_candidates"
            " WHERE category = 'pending-gift'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 60


async def test_insight_scan_pending_gift_no_upcoming_date_excluded(provisioned_postgres_pool):
    """Pending gift without upcoming contact date is not surfaced."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        contact_id = await _insert_contact(pool, first_name="Paul")
        # No upcoming dates for this contact
        await _insert_gift_fact(pool, contact_id=contact_id, description="wine", status="idea")

        await run_insight_scan(pool)
        rows = await pool.fetch("SELECT id FROM insight_candidates WHERE category = 'pending-gift'")
        assert len(rows) == 0


async def test_insight_scan_pending_gift_dedup_key_format(provisioned_postgres_pool):
    """Pending gift dedup_key follows relationship:pending-gift:{gift-id} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=3)
        contact_id = await _insert_contact(pool, first_name="Quinn")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )
        gift_id = await _insert_gift_fact(
            pool, contact_id=contact_id, description="chocolate", status="purchased"
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'pending-gift'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key == f"relationship:pending-gift:{gift_id}"


async def test_insight_scan_gift_given_status_excluded(provisioned_postgres_pool):
    """Gifts with status 'given' or 'thanked' are excluded."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=3)
        contact_id = await _insert_contact(pool, first_name="Riley")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )
        # Given gift — should not generate insight
        await _insert_gift_fact(pool, contact_id=contact_id, description="book", status="given")

        await run_insight_scan(pool)
        rows = await pool.fetch("SELECT id FROM insight_candidates WHERE category = 'pending-gift'")
        assert len(rows) == 0


async def test_insight_scan_pending_gift_expires_at_upcoming_date(provisioned_postgres_pool):
    """Pending gift candidate expires_at matches the associated upcoming date."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        target = _today() + timedelta(days=7)
        contact_id = await _insert_contact(pool, first_name="Sam")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=target.month,
            day=target.day,
        )
        await _insert_gift_fact(pool, contact_id=contact_id, description="scarf", status="idea")

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT expires_at FROM insight_candidates WHERE category = 'pending-gift'"
        )
        assert len(rows) == 1
        expires_at = rows[0]["expires_at"]
        assert expires_at.date() == target


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — interaction milestone insights
# ---------------------------------------------------------------------------


async def test_insight_scan_milestone_100th_interaction(provisioned_postgres_pool):
    """100th interaction with a contact generates a milestone insight."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        contact_id = await _insert_contact(pool, first_name="Taylor")
        # Insert exactly 100 interaction facts
        for i in range(100):
            await _insert_interaction_fact(
                pool,
                contact_id=contact_id,
                occurred_at=_utcnow() - timedelta(days=i),
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority, dedup_key, cooldown_days FROM insight_candidates"
            " WHERE category = 'milestone'"
        )
        milestone_rows = [r for r in rows if "count-100" in r["dedup_key"]]
        assert len(milestone_rows) == 1
        assert milestone_rows[0]["priority"] == 30
        assert milestone_rows[0]["cooldown_days"] == 30


async def test_insight_scan_milestone_dedup_key_format(provisioned_postgres_pool):
    """Milestone dedup_key follows relationship:milestone:{contact-id}:{milestone-type} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        contact_id = await _insert_contact(pool, first_name="Uma")
        for i in range(10):
            await _insert_interaction_fact(
                pool,
                contact_id=contact_id,
                occurred_at=_utcnow() - timedelta(days=i),
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'milestone'"
        )
        count_rows = [r for r in rows if "count-10" in r["dedup_key"]]
        assert len(count_rows) == 1
        dedup_key = count_rows[0]["dedup_key"]
        assert dedup_key.startswith("relationship:milestone:")
        assert contact_id in dedup_key


async def test_insight_scan_milestone_non_notable_count_excluded(provisioned_postgres_pool):
    """Non-notable interaction counts (e.g., 7) do not generate milestones."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        contact_id = await _insert_contact(pool, first_name="Victor")
        for i in range(7):
            await _insert_interaction_fact(
                pool,
                contact_id=contact_id,
                occurred_at=_utcnow() - timedelta(days=i),
            )

        await run_insight_scan(pool)
        rows = await pool.fetch("SELECT id FROM insight_candidates WHERE category = 'milestone'")
        assert len(rows) == 0


async def test_insight_scan_first_interaction_anniversary(provisioned_postgres_pool):
    """1-year anniversary of first interaction generates a milestone insight."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        contact_id = await _insert_contact(pool, first_name="Wendy")
        # First interaction exactly 1 year ago (same month/day)
        one_year_ago = datetime(today.year - 1, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=one_year_ago,
        )
        # Add a few more recent interactions so count != notable milestone
        for i in range(1, 4):
            await _insert_interaction_fact(
                pool,
                contact_id=contact_id,
                occurred_at=_utcnow() - timedelta(days=i),
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key, message FROM insight_candidates WHERE category = 'milestone'"
        )
        anniversary_rows = [r for r in rows if "first-interaction-anniversary" in r["dedup_key"]]
        assert len(anniversary_rows) >= 1
        assert "anniversary" in anniversary_rows[0]["message"].lower()


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — early exit on verbosity=off
# ---------------------------------------------------------------------------


async def test_insight_scan_early_exit_verbosity_off(provisioned_postgres_pool):
    """Early exit when verbosity=off: job returns early_exit=True."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        # Set verbosity to 'off'
        await pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'off')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'off'
        """)

        today = _today()
        contact_id = await _insert_contact(pool, first_name="Xavier")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=today.month,
            day=today.day,
        )

        result = await run_insight_scan(pool)

        assert result["early_exit"] is True
        assert result["candidates_filtered"] >= 1
        assert result["candidates_accepted"] == 0


async def test_insight_scan_stats_keys_present(provisioned_postgres_pool):
    """Result dict always contains all expected statistics keys."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        assert "candidates_proposed" in result
        assert "candidates_accepted" in result
        assert "candidates_filtered" in result
        assert "candidates_errored" in result
        assert "early_exit" in result


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — origin_butler tagging
# ---------------------------------------------------------------------------


async def test_insight_scan_origin_butler_is_relationship(provisioned_postgres_pool):
    """All generated candidates are tagged with origin_butler='relationship'."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_relationship_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        contact_id = await _insert_contact(pool, first_name="Yara")
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=today.month,
            day=today.day,
        )

        await run_insight_scan(pool)

        rows = await pool.fetch("SELECT origin_butler FROM insight_candidates")
        assert len(rows) >= 1
        for row in rows:
            assert row["origin_butler"] == "relationship"


# ---------------------------------------------------------------------------
# Schema setup helpers for interaction sync
# ---------------------------------------------------------------------------

CREATE_PUBLIC_CONTACTS_SQL = """
CREATE TABLE IF NOT EXISTS public.contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT,
    last_name TEXT,
    nickname TEXT,
    name TEXT,
    company TEXT,
    entity_id UUID,
    stay_in_touch_days INT,
    listed BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
)
"""

CREATE_PUBLIC_CONTACT_INFO_SQL = """
CREATE TABLE IF NOT EXISTS public.contact_info (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
    type VARCHAR NOT NULL,
    value TEXT NOT NULL,
    label VARCHAR,
    is_primary BOOLEAN DEFAULT false,
    secured BOOLEAN NOT NULL DEFAULT false,
    parent_id UUID,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_test_contact_info_type_value UNIQUE (type, value)
)
"""

CREATE_PUBLIC_ENTITIES_SQL = """
CREATE TABLE IF NOT EXISTS public.entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL DEFAULT '',
    canonical_name VARCHAR NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    entity_type VARCHAR NOT NULL DEFAULT 'other',
    aliases TEXT[] NOT NULL DEFAULT '{}',
    metadata JSONB DEFAULT '{}'::jsonb,
    roles TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_SWITCHBOARD_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS switchboard"

CREATE_MESSAGE_INBOX_SQL = """
CREATE TABLE IF NOT EXISTS switchboard.message_inbox (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized_text TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
    schema_version TEXT NOT NULL DEFAULT 'message_inbox.v2',
    direction TEXT NOT NULL DEFAULT 'inbound',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (received_at, id)
)
"""

# Full facts schema for interaction sync tests — includes object_entity_id
# which store_fact() requires (not present in the legacy CREATE_FACTS_SQL above).
CREATE_FACTS_FULL_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    embedding TEXT,
    search_vector tsvector,
    importance FLOAT NOT NULL DEFAULT 5.0,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    decay_rate FLOAT NOT NULL DEFAULT 0.008,
    permanence TEXT NOT NULL DEFAULT 'standard',
    source_butler TEXT,
    source_episode_id UUID,
    supersedes_id UUID,
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
    idempotency_key TEXT,
    observed_at TIMESTAMPTZ DEFAULT now(),
    invalid_at TIMESTAMPTZ,
    retention_class TEXT NOT NULL DEFAULT 'operational',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    embedding_model_version TEXT DEFAULT 'unknown'
)
"""

CREATE_PREDICATE_REGISTRY_SQL = """
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
"""

CREATE_MEMORY_LINKS_SQL = """
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
"""

CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INTEGER NOT NULL DEFAULT 1
)
"""

# calendar_events table for calendar interaction sync tests.
# Simplified version of the real schema — omits FK to calendar_sources
# since tests don't need the full calendar projection.
CREATE_CALENDAR_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS public.calendar_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    all_day BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'confirmed',
    visibility TEXT NOT NULL DEFAULT 'default',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT test_cal_events_status_check
        CHECK (status IN ('confirmed', 'tentative', 'cancelled'))
)
"""

# relationship.entity_facts: triple-store for contact/relational predicates (migration rel_013).
# run_interaction_sync now resolves sender identities via this table instead of public.contact_info.
CREATE_RELATIONSHIP_ENTITY_FACTS_SQL = """
CREATE SCHEMA IF NOT EXISTS relationship;
CREATE TABLE IF NOT EXISTS relationship.entity_facts (
    id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    predicate   TEXT        NOT NULL,
    object      TEXT        NOT NULL,
    object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
    src         TEXT        NOT NULL DEFAULT 'test',
    conf        FLOAT       NOT NULL DEFAULT 1.0,
    last_seen   TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    weight      INT,
    verified    BOOL        NOT NULL DEFAULT false,
    "primary"   BOOL,
    validity    TEXT        NOT NULL DEFAULT 'active'
                    CHECK (validity IN ('active', 'retracted', 'superseded')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Unique partial index required by relationship_assert_fact's ON CONFLICT clause.
CREATE_RELATIONSHIP_ENTITY_FACTS_UNIQUE_IDX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
    ON relationship.entity_facts (subject, predicate, object)
    WHERE validity = 'active'
"""

# Predicate registry used by relationship_assert_fact for validation.
CREATE_RELATIONSHIP_PREDICATE_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
    predicate   TEXT        NOT NULL PRIMARY KEY,
    kind        TEXT        NOT NULL,
    object_kind TEXT        NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Seed just the predicates relevant to interaction_sync tests.
SEED_RELATIONSHIP_PREDICATES_SQL = """
INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind, description)
VALUES
    ('has-email',    'contact',    'literal', 'Email address'),
    ('has-handle',   'contact',    'literal', 'Messaging handle'),
    ('has-phone',    'contact',    'literal', 'Phone number'),
    ('co-attended',  'relational', 'entity',  'Both entities attended the same event or place.')
ON CONFLICT (predicate) DO NOTHING
"""


async def _setup_interaction_sync_schema(pool) -> None:
    """Create tables needed for interaction sync tests."""
    await pool.execute(CREATE_PUBLIC_ENTITIES_SQL)
    await pool.execute(CREATE_PUBLIC_CONTACTS_SQL)
    await pool.execute(CREATE_PUBLIC_CONTACT_INFO_SQL)
    await pool.execute(CREATE_SWITCHBOARD_SCHEMA_SQL)
    await pool.execute(CREATE_MESSAGE_INBOX_SQL)
    await pool.execute(CREATE_CALENDAR_EVENTS_SQL)
    # Use the full facts schema (includes object_entity_id required by store_fact)
    await pool.execute(CREATE_FACTS_FULL_SQL)
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_subj_pred_sync ON facts (subject, predicate)"
    )
    # run_interaction_sync resolves sender identities via relationship.entity_facts (rel_013).
    # relationship_assert_fact also writes co-attended edges here, so schema must match
    # production (observed_at column + unique index for ON CONFLICT dedup).
    await pool.execute(CREATE_RELATIONSHIP_ENTITY_FACTS_SQL)
    await pool.execute(CREATE_RELATIONSHIP_ENTITY_FACTS_UNIQUE_IDX_SQL)
    # relationship_assert_fact validates predicates against entity_predicate_registry.
    await pool.execute(CREATE_RELATIONSHIP_PREDICATE_REGISTRY_SQL)
    await pool.execute(SEED_RELATIONSHIP_PREDICATES_SQL)
    # store_fact() requires predicate_registry and memory_links
    await pool.execute(CREATE_PREDICATE_REGISTRY_SQL)
    await pool.execute(CREATE_MEMORY_LINKS_SQL)
    # state table for checkpoint persistence
    await pool.execute(CREATE_STATE_SQL)


async def _insert_public_contact(
    pool,
    *,
    first_name: str = "Alice",
    last_name: str = "Smith",
    entity_id: str | None = None,
) -> str:
    """Insert a public.contacts row (with auto-created entity) and return its UUID string.

    Each contact must have a linked entity_id since interaction_log requires it.
    If no entity_id is provided, a new entity is created and linked automatically.
    """
    if entity_id is None:
        entity_id = await _insert_public_entity(pool)
    contact_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO public.contacts (id, first_name, last_name, entity_id)
        VALUES ($1::uuid, $2, $3, $4)
        """,
        contact_id,
        first_name,
        last_name,
        uuid.UUID(entity_id),
    )
    return contact_id


async def _insert_public_entity(pool, *, roles: list[str] | None = None) -> str:
    """Insert a public.entities row and return its UUID string."""
    entity_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO public.entities (id, roles)
        VALUES ($1::uuid, $2::text[])
        """,
        entity_id,
        roles or [],
    )
    return entity_id


async def _insert_contact_info(
    pool,
    *,
    contact_id: str,
    ci_type: str,
    value: str,
    is_primary: bool = True,
) -> str:
    """Insert a public.contact_info row and a matching relationship.entity_facts triple.

    The interaction_sync job resolves sender identities via relationship.entity_facts
    (post bead-7 cut-over).  Each call here also inserts the corresponding triple so
    tests that rely on identity resolution continue to work.
    """
    ci_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO public.contact_info (id, contact_id, type, value, is_primary)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5)
        """,
        ci_id,
        contact_id,
        ci_type,
        value,
        is_primary,
    )
    # Also insert the corresponding entity_facts triple for bead-7 resolution.
    # Map contact_info type → entity_facts predicate per relationship_jobs.py channel map.
    _ci_type_to_predicate = {
        "telegram_chat_id": "has-handle",
        "whatsapp_jid": "has-handle",
        "email": "has-email",
        "phone": "has-phone",
    }
    predicate = _ci_type_to_predicate.get(ci_type, "has-handle")
    # Look up entity_id from public.contacts (set by _insert_public_contact).
    entity_id = await pool.fetchval(
        "SELECT entity_id FROM public.contacts WHERE id = $1::uuid",
        contact_id,
    )
    if entity_id is not None:
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, validity)
            VALUES ($1, $2, $3, 'literal', 'test', 'active')
            ON CONFLICT DO NOTHING
            """,
            entity_id,
            predicate,
            value,
        )
    return ci_id


async def _insert_message_inbox(
    pool,
    *,
    sender_identity: str,
    source_channel: str,
    received_at: datetime | None = None,
    direction: str = "inbound",
) -> None:
    """Insert a message_inbox row for testing."""
    if received_at is None:
        received_at = datetime.now(UTC) - timedelta(hours=1)
    request_context = {
        "source_sender_identity": sender_identity,
        "source_channel": source_channel,
    }
    await pool.execute(
        """
        INSERT INTO switchboard.message_inbox (received_at, request_context, direction)
        VALUES ($1, $2, $3)
        """,
        received_at,
        request_context,
        direction,
    )


async def _insert_calendar_event(
    pool,
    *,
    title: str = "Team Sync",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    status: str = "confirmed",
    attendees: list[dict] | None = None,
) -> str:
    """Insert a public.calendar_events row and return its UUID string."""
    if starts_at is None:
        starts_at = datetime.now(UTC) - timedelta(hours=2)
    if ends_at is None:
        ends_at = starts_at + timedelta(hours=1)
    metadata: dict = {}
    if attendees is not None:
        metadata["attendees"] = attendees
    event_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO public.calendar_events
            (id, title, timezone, starts_at, ends_at, status, metadata)
        VALUES ($1::uuid, $2, 'UTC', $3, $4, $5, $6)
        """,
        event_id,
        title,
        starts_at,
        ends_at,
        status,
        metadata,
    )
    return event_id


# ---------------------------------------------------------------------------
# Tests: run_interaction_sync
# ---------------------------------------------------------------------------


async def test_interaction_sync_no_messages_returns_zeros(provisioned_postgres_pool):
    """No-op: returns zeros when message_inbox is empty."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        assert result["processed"] == 0
        assert result["logged"] == 0
        assert result["skipped_unresolved"] == 0
        assert result["skipped_owner"] == 0
        assert result["errors"] == 0


async def test_interaction_sync_returns_expected_stats_keys(provisioned_postgres_pool):
    """Result dict always contains all expected statistics keys."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        assert "scan_window_start" in result
        assert "scan_window_end" in result
        assert "processed" in result
        assert "logged" in result
        assert "skipped_unresolved" in result
        assert "skipped_owner" in result
        assert "errors" in result


async def test_interaction_sync_unresolved_sender_skipped(provisioned_postgres_pool):
    """Senders with no matching contact_info entry are counted as skipped_unresolved."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        # Insert message with no matching contact_info
        await _insert_message_inbox(
            pool,
            sender_identity="999999999",
            source_channel="telegram_user_client",
        )

        result = await run_interaction_sync(pool)

        assert result["processed"] == 1
        assert result["skipped_unresolved"] == 1
        assert result["logged"] == 0


async def test_interaction_sync_outbound_messages_ignored(provisioned_postgres_pool):
    """Outbound messages are not processed (only inbound are synced)."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Bob")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="12345"
        )
        # Insert outbound message — should be ignored
        await _insert_message_inbox(
            pool,
            sender_identity="12345",
            source_channel="telegram_user_client",
            direction="outbound",
        )

        result = await run_interaction_sync(pool)

        assert result["processed"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_owner_contact_skipped(provisioned_postgres_pool):
    """Owner contacts are skipped even when their sender identity is resolved."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        # Create an owner entity and link it to the contact
        entity_id = await _insert_public_entity(pool, roles=["owner"])
        contact_id = await _insert_public_contact(pool, first_name="Owner", entity_id=entity_id)
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="owner123"
        )
        await _insert_message_inbox(
            pool,
            sender_identity="owner123",
            source_channel="telegram_user_client",
        )

        result = await run_interaction_sync(pool)

        assert result["processed"] == 1
        assert result["skipped_owner"] == 1
        assert result["logged"] == 0


async def test_interaction_sync_logs_telegram_interaction(provisioned_postgres_pool):
    """A resolved telegram_user_client message is logged as an interaction fact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Carol")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="777001"
        )
        await _insert_message_inbox(
            pool,
            sender_identity="777001",
            source_channel="telegram_user_client",
        )

        result = await run_interaction_sync(pool)

        assert result["logged"] == 1
        assert result["errors"] == 0
        # Verify a fact was created with the typed predicate.
        # Facts are now stored with subject='entity:{entity_id}'.
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE id = $1::uuid", contact_id
        )
        rows = await pool.fetch(
            """
            SELECT id, predicate, metadata FROM facts
            WHERE subject = $1
              AND predicate LIKE 'interaction_%'
              AND scope = 'relationship'
            """,
            f"entity:{entity_id}",
        )
        assert len(rows) == 1
        assert rows[0]["predicate"] == "interaction_telegram_user_client"
        import json as _json

        meta = rows[0]["metadata"]
        if isinstance(meta, str):
            meta = _json.loads(meta)
        assert meta.get("type") == "telegram_user_client"
        assert meta.get("direction") == "incoming"


async def test_interaction_sync_deduplicates_same_sender_same_day(provisioned_postgres_pool):
    """Multiple messages from the same sender on the same day → one interaction fact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Dave")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="888001"
        )

        today = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
        # Insert three messages today from the same sender
        for hour_offset in [0, 2, 4]:
            await _insert_message_inbox(
                pool,
                sender_identity="888001",
                source_channel="telegram_user_client",
                received_at=today + timedelta(hours=hour_offset),
            )

        result = await run_interaction_sync(pool)

        # Should be grouped to one row by (sender, channel, date)
        assert result["processed"] == 1
        assert result["logged"] == 1


async def test_interaction_sync_different_channels_logged_separately(
    provisioned_postgres_pool,
):
    """Same contact via different channels (telegram + email) → two separate facts."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Eve")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="tg_eve"
        )
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="eve@example.com"
        )

        await _insert_message_inbox(
            pool,
            sender_identity="tg_eve",
            source_channel="telegram_user_client",
        )
        await _insert_message_inbox(
            pool,
            sender_identity="eve@example.com",
            source_channel="email",
        )

        result = await run_interaction_sync(pool)

        assert result["logged"] == 2
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE id = $1::uuid", contact_id
        )
        rows = await pool.fetch(
            "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
            f"entity:{entity_id}",
        )
        assert len(rows) == 2


async def test_interaction_sync_old_messages_excluded(provisioned_postgres_pool):
    """Messages older than the scan window are not processed."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Frank")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="old_sender"
        )
        # Insert a message 35 days old (outside the 30-day default window)
        old_ts = datetime.now(UTC) - timedelta(days=35)
        await _insert_message_inbox(
            pool,
            sender_identity="old_sender",
            source_channel="telegram_user_client",
            received_at=old_ts,
        )

        result = await run_interaction_sync(pool)

        assert result["processed"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_unknown_channel_ignored(provisioned_postgres_pool):
    """Messages from unsupported channels (e.g. telegram_bot) are not processed."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Grace")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="bot_sender"
        )
        await _insert_message_inbox(
            pool,
            sender_identity="bot_sender",
            source_channel="telegram_bot",  # NOT in supported channels
        )

        result = await run_interaction_sync(pool)

        assert result["processed"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_idempotent_second_run(provisioned_postgres_pool):
    """Running interaction sync twice for the same messages does not create duplicate facts."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Henry")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="idem_tg"
        )
        await _insert_message_inbox(
            pool,
            sender_identity="idem_tg",
            source_channel="telegram_user_client",
        )

        # First run
        result1 = await run_interaction_sync(pool)
        assert result1["logged"] == 1

        # Second run — same messages still in inbox
        result2 = await run_interaction_sync(pool)
        # Should be skipped as duplicate (interaction_log idempotency guard)
        assert result2["logged"] == 0

        # Only one fact should exist.
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE id = $1::uuid", contact_id
        )
        rows = await pool.fetch(
            "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
            f"entity:{entity_id}",
        )
        assert len(rows) == 1


async def test_interaction_sync_whatsapp_channel_resolved(provisioned_postgres_pool):
    """A resolved whatsapp_user_client message is logged via whatsapp_jid contact_info."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Ivan")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="whatsapp_jid", value="5591999@s.whatsapp.net"
        )
        await _insert_message_inbox(
            pool,
            sender_identity="5591999@s.whatsapp.net",
            source_channel="whatsapp_user_client",
        )

        result = await run_interaction_sync(pool)

        assert result["logged"] == 1
        assert result["errors"] == 0


async def test_interaction_sync_email_channel_resolved(provisioned_postgres_pool):
    """A resolved email message is logged via email contact_info."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Jane")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="jane@example.com"
        )
        await _insert_message_inbox(
            pool,
            sender_identity="jane@example.com",
            source_channel="email",
        )

        result = await run_interaction_sync(pool)

        assert result["logged"] == 1


# ---------------------------------------------------------------------------
# Tests: run_interaction_sync — checkpoint and scan window behavior
# ---------------------------------------------------------------------------


async def test_interaction_sync_no_checkpoint_uses_30_day_default(provisioned_postgres_pool):
    """Without a checkpoint, scan_window_start defaults to ~30 days ago."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        assert "scan_window_start" in result
        assert "scan_window_end" in result
        start = datetime.fromisoformat(result["scan_window_start"])
        end = datetime.fromisoformat(result["scan_window_end"])
        # Start should be roughly 30 days before end (±1 minute tolerance)
        window = end - start
        assert abs(window - timedelta(days=30)) <= timedelta(minutes=1)


async def test_interaction_sync_checkpoint_used_as_start(provisioned_postgres_pool):
    """When a checkpoint exists, it is used as scan_window_start."""
    from butlers.core.state import state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        # Store a checkpoint 5 days ago
        checkpoint = datetime.now(UTC) - timedelta(days=5)
        await state_set(pool, "interaction_sync.last_scan_at", checkpoint.isoformat())

        result = await run_interaction_sync(pool)

        start = datetime.fromisoformat(result["scan_window_start"])
        end = datetime.fromisoformat(result["scan_window_end"])
        diff_days = (end - start).total_seconds() / 86400
        # Should be ~5 days, not 30
        assert 4.9 < diff_days < 5.1


async def test_interaction_sync_checkpoint_capped_at_30_days(provisioned_postgres_pool):
    """A checkpoint older than 30 days is capped to 30 days ago."""
    from butlers.core.state import state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        # Store a checkpoint 60 days ago
        checkpoint = datetime.now(UTC) - timedelta(days=60)
        await state_set(pool, "interaction_sync.last_scan_at", checkpoint.isoformat())

        result = await run_interaction_sync(pool)

        start = datetime.fromisoformat(result["scan_window_start"])
        end = datetime.fromisoformat(result["scan_window_end"])
        diff_days = (end - start).total_seconds() / 86400
        # Capped: should be ~30 days, not 60 or a much smaller window
        assert 29.9 < diff_days < 30.1


async def test_interaction_sync_writes_checkpoint_on_success(provisioned_postgres_pool):
    """After a successful run, the state store contains scan_window_end as the new checkpoint."""
    from butlers.core.state import state_get
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        stored = await state_get(pool, "interaction_sync.last_scan_at")
        assert stored is not None
        assert stored == result["scan_window_end"]


async def test_interaction_sync_window_end_is_iso8601(provisioned_postgres_pool):
    """scan_window_start and scan_window_end are valid ISO8601 strings."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        # Should not raise
        datetime.fromisoformat(result["scan_window_start"])
        datetime.fromisoformat(result["scan_window_end"])


async def test_interaction_sync_second_run_uses_first_checkpoint(provisioned_postgres_pool):
    """Second run uses the checkpoint written by the first run."""
    from butlers.core.state import state_get
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result1 = await run_interaction_sync(pool)
        checkpoint_after_first = await state_get(pool, "interaction_sync.last_scan_at")
        assert checkpoint_after_first == result1["scan_window_end"]

        result2 = await run_interaction_sync(pool)

        # Second run's window_start should be very close to first run's window_end
        end1 = datetime.fromisoformat(result1["scan_window_end"])
        start2 = datetime.fromisoformat(result2["scan_window_start"])
        diff_seconds = abs((start2 - end1).total_seconds())
        assert diff_seconds < 1.0  # Should match within a second


# ---------------------------------------------------------------------------
# Tests: run_interaction_sync — calendar event detection
# ---------------------------------------------------------------------------


async def test_interaction_sync_calendar_stats_key_present(provisioned_postgres_pool):
    """calendar_events_scanned is always present in the return stats."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        assert "calendar_events_scanned" in result
        assert result["calendar_events_scanned"] == 0


async def test_interaction_sync_calendar_no_events_returns_zero(provisioned_postgres_pool):
    """No calendar events → calendar_events_scanned remains 0."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 0
        assert result["logged"] == 0
        assert result["errors"] == 0


async def test_interaction_sync_missing_calendar_table_skips_calendar_scan(
    provisioned_postgres_pool,
):
    """Missing public.calendar_events degrades to a no-op instead of an error."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)
        await pool.execute("DROP TABLE public.calendar_events")

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 0
        assert result["errors"] == 0


async def test_interaction_sync_calendar_logs_attendee_interaction(provisioned_postgres_pool):
    """A calendar event with a resolved attendee email logs an interaction fact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Alice")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="alice@example.com"
        )

        await _insert_calendar_event(
            pool,
            title="Coffee Chat",
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["logged"] == 1
        assert result["errors"] == 0

        # Verify the fact was written correctly with typed predicate.
        # Facts are stored with subject='entity:{entity_id}' since rel_018.
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE id = $1::uuid", contact_id
        )
        rows = await pool.fetch(
            """
            SELECT id, predicate, metadata FROM facts
            WHERE subject = $1
              AND predicate LIKE 'interaction_%'
              AND scope = 'relationship'
            """,
            f"entity:{entity_id}",
        )
        assert len(rows) == 1
        assert rows[0]["predicate"] == "interaction_calendar_event"
        meta = rows[0]["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("type") == "calendar_event"
        assert meta.get("direction") == "mutual"
        extra = meta.get("extra_metadata") or {}
        assert extra.get("source") == "interaction_sync"
        assert "event_id" in extra


async def test_interaction_sync_calendar_unresolved_attendee_skipped(
    provisioned_postgres_pool,
):
    """Attendee email not in contact_info increments skipped_unresolved."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        await _insert_calendar_event(
            pool,
            title="Mystery Meeting",
            attendees=[
                {"email": "unknown@nobody.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["skipped_unresolved"] == 1
        assert result["logged"] == 0


async def test_interaction_sync_calendar_declined_event_excluded(provisioned_postgres_pool):
    """Events where the owner RSVP is declined are excluded (not counted)."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Bob")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="bob@example.com"
        )

        # Owner declined this event
        await _insert_calendar_event(
            pool,
            title="Declined Event",
            attendees=[
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {
                    "email": "me@owner.com",
                    "responseStatus": "declined",
                    "self": True,
                },
            ],
        )

        result = await run_interaction_sync(pool)

        # Declined events are skipped and not counted
        assert result["calendar_events_scanned"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_calendar_cancelled_event_excluded(provisioned_postgres_pool):
    """Events with status='cancelled' are excluded by the query filter."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Carol")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="carol@example.com"
        )

        await _insert_calendar_event(
            pool,
            title="Cancelled Meeting",
            status="cancelled",
            attendees=[
                {"email": "carol@example.com", "responseStatus": "accepted"},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_calendar_owner_attendee_excluded(provisioned_postgres_pool):
    """The owner's own attendee entry (self=True) is excluded from interaction logging."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        entity_id = await _insert_public_entity(pool, roles=["owner"])
        owner_contact_id = await _insert_public_contact(
            pool, first_name="Owner", entity_id=entity_id
        )
        await _insert_contact_info(
            pool, contact_id=owner_contact_id, ci_type="email", value="me@owner.com"
        )

        # Event where the only attendee is the owner themselves (self=True)
        await _insert_calendar_event(
            pool,
            title="Solo Block",
            attendees=[
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["logged"] == 0
        assert result["skipped_owner"] == 0  # excluded before owner check (self=True filter)


async def test_interaction_sync_calendar_owner_contact_skipped(provisioned_postgres_pool):
    """Owner contact resolved via non-self email entry is counted as skipped_owner."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        entity_id = await _insert_public_entity(pool, roles=["owner"])
        owner_contact_id = await _insert_public_contact(
            pool, first_name="Owner", entity_id=entity_id
        )
        # Register owner's email in contact_info (resolves as owner contact)
        await _insert_contact_info(
            pool, contact_id=owner_contact_id, ci_type="email", value="owner@company.com"
        )

        # Event where owner appears as a regular attendee (no self=True)
        await _insert_calendar_event(
            pool,
            title="Work Meeting",
            attendees=[
                {"email": "owner@company.com", "responseStatus": "accepted"},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["skipped_owner"] == 1
        assert result["logged"] == 0


async def test_interaction_sync_calendar_case_insensitive_email_match(
    provisioned_postgres_pool,
):
    """Attendee email matching against contact_info is case-insensitive."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Dave")
        # Stored as lowercase in contact_info
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="dave@example.com"
        )

        # Event attendee email is mixed case
        await _insert_calendar_event(
            pool,
            title="Strategy Session",
            attendees=[
                {"email": "Dave@Example.COM", "responseStatus": "accepted"},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["logged"] == 1


async def test_interaction_sync_calendar_multiple_attendees_same_event(
    provisioned_postgres_pool,
):
    """A single event with multiple resolved attendees creates one fact per contact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_a = await _insert_public_contact(pool, first_name="Eve")
        await _insert_contact_info(
            pool, contact_id=contact_a, ci_type="email", value="eve@example.com"
        )

        contact_b = await _insert_public_contact(pool, first_name="Frank")
        await _insert_contact_info(
            pool, contact_id=contact_b, ci_type="email", value="frank@example.com"
        )

        await _insert_calendar_event(
            pool,
            title="Team Lunch",
            attendees=[
                {"email": "eve@example.com", "responseStatus": "accepted"},
                {"email": "frank@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["logged"] == 2

        for cid in (contact_a, contact_b):
            eid = await pool.fetchval(
                "SELECT entity_id FROM public.contacts WHERE id = $1::uuid", cid
            )
            rows = await pool.fetch(
                "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
                f"entity:{eid}",
            )
            assert len(rows) == 1


async def test_interaction_sync_calendar_event_outside_window_excluded(
    provisioned_postgres_pool,
):
    """Calendar events older than the lookback window are not processed."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Grace")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="grace@example.com"
        )

        # Default scan window is 30 days; use 35 days to be clearly outside it.
        old_ts = datetime.now(UTC) - timedelta(days=35)
        await _insert_calendar_event(
            pool,
            title="Old Event",
            starts_at=old_ts,
            ends_at=old_ts + timedelta(hours=1),
            attendees=[
                {"email": "grace@example.com", "responseStatus": "accepted"},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_calendar_idempotent_second_run(provisioned_postgres_pool):
    """Running interaction sync twice for the same event does not create duplicate facts."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        contact_id = await _insert_public_contact(pool, first_name="Henry")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="henry@example.com"
        )

        await _insert_calendar_event(
            pool,
            title="Recurring Sync",
            attendees=[
                {"email": "henry@example.com", "responseStatus": "accepted"},
            ],
        )

        result1 = await run_interaction_sync(pool)
        assert result1["logged"] == 1

        result2 = await run_interaction_sync(pool)
        assert result2["logged"] == 0  # duplicate skipped

        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE id = $1::uuid", contact_id
        )
        rows = await pool.fetch(
            "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
            f"entity:{entity_id}",
        )
        assert len(rows) == 1


async def test_interaction_sync_calendar_no_attendees_field_skipped(
    provisioned_postgres_pool,
):
    """Events with no attendees field in metadata are silently skipped."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        # No attendees key in metadata
        await _insert_calendar_event(
            pool,
            title="Solo Event",
            attendees=None,  # _insert_calendar_event omits the key when None
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 0
        assert result["logged"] == 0


async def test_interaction_sync_combined_messages_and_calendar(provisioned_postgres_pool):
    """Messages and calendar events are both processed in a single run."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with provisioned_postgres_pool() as pool:
        await _setup_interaction_sync_schema(pool)

        # Contact with telegram + email
        contact_id = await _insert_public_contact(pool, first_name="Iris")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="tg_iris"
        )
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="iris@example.com"
        )

        # Message interaction
        await _insert_message_inbox(
            pool,
            sender_identity="tg_iris",
            source_channel="telegram_user_client",
        )

        # Calendar interaction (different time so occurred_at differs)
        await _insert_calendar_event(
            pool,
            title="Weekly Check-in",
            starts_at=datetime.now(UTC) - timedelta(hours=3),
            attendees=[
                {"email": "iris@example.com", "responseStatus": "accepted"},
            ],
        )

        result = await run_interaction_sync(pool)

        # Both a message interaction and a calendar interaction should be logged
        assert result["logged"] == 2
        assert result["calendar_events_scanned"] == 1
        assert result["errors"] == 0
