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
    return date.today()


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
    content TEXT NOT NULL DEFAULT '',
    embedding TEXT,
    validity TEXT NOT NULL DEFAULT 'active',
    scope TEXT NOT NULL DEFAULT 'global',
    entity_id UUID,
    valid_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    permanence TEXT NOT NULL DEFAULT 'standard',
    importance FLOAT NOT NULL DEFAULT 5.0,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    decay_rate FLOAT NOT NULL DEFAULT 0.008,
    source_butler TEXT,
    source_episode_id UUID,
    supersedes_id UUID,
    reference_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_referenced_at TIMESTAMPTZ,
    last_confirmed_at TIMESTAMPTZ,
    tags JSONB DEFAULT '[]'::jsonb,
    tenant_id TEXT NOT NULL DEFAULT 'owner',
    request_id TEXT,
    idempotency_key TEXT,
    observed_at TIMESTAMPTZ DEFAULT now(),
    invalid_at TIMESTAMPTZ,
    retention_class TEXT NOT NULL DEFAULT 'operational',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    search_vector tsvector
)
"""

CREATE_ENTITIES_SQL = """
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


async def _setup_relationship_schema(pool) -> None:
    """Create the relationship-related tables needed for insight scan tests."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS shared")
    await pool.execute(CREATE_ENTITIES_SQL)
    await pool.execute(CREATE_CONTACTS_SQL)
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
    """Insert a contact and return its UUID string."""
    contact_id = str(uuid.uuid4())
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
        uuid.UUID(entity_id) if entity_id else None,
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
) -> str:
    """Insert an interaction fact for a contact."""
    if occurred_at is None:
        occurred_at = _utcnow()
    fact_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO facts (id, subject, predicate, content, scope, validity, valid_at)
        VALUES ($1::uuid, $2, 'interaction', 'had a chat', 'relationship', 'active', $3)
        """,
        fact_id,
        f"contact:{contact_id}",
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
    from roster.relationship.tools.gifts import _slug

    fact_id = str(uuid.uuid4())
    subject = f"contact:{contact_id}:gift:{_slug(description)}"
    meta: dict = {"status": status}
    if occasion:
        meta["occasion"] = occasion
    await pool.execute(
        """
        INSERT INTO facts (id, subject, predicate, content, scope, validity, valid_at, metadata)
        VALUES ($1::uuid, $2, 'gift', $3, 'relationship', 'active', NULL, $4::jsonb)
        """,
        fact_id,
        subject,
        description,
        json.dumps(meta),
    )
    return fact_id


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — no data / no-op paths
# ---------------------------------------------------------------------------


async def test_insight_scan_no_contacts_no_op(provisioned_postgres_pool):
    """No-op: returns zeros when no contacts exist."""
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
    from roster.relationship.jobs.relationship_jobs import run_insight_scan

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
