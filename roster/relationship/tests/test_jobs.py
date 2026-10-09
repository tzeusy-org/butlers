"""Tests for the Relationship butler scheduled job handlers (insight-scan)."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.testing.migrated_templates import MigrationStage
from butlers.testing.migration import migrated_pool

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


# contact_entity_map (rel_029) — contact_id → entity_id bridge that dunbar reads
# instead of public.contacts (Phase 7.4e).


async def _setup_relationship_schema(pool) -> None:
    """Admit only test case actions; complete real chains already own all DDL."""
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled=true WHERE singleton"
    )


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
    posture: str = "active",
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
        INSERT INTO public.entities (id, canonical_name, aliases, listed, stay_in_touch_days, posture)
        VALUES ($1, $2, ARRAY[$3::text], $4, $5, $6)
        ON CONFLICT (id) DO NOTHING
        """,
        resolved_entity_id,
        canonical_name,
        canonical_name,
        listed,
        stay_in_touch_days,
        posture,
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
        INSERT INTO contacts (id, name, first_name, last_name, listed, stay_in_touch_days, entity_id)
        VALUES ($1::uuid, concat_ws(' ', $2::text, $3::text), $2, $3, $4, $5, $6)
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
        INSERT INTO public.entities (id, aliases, canonical_name, listed, stay_in_touch_days)
        VALUES ($1, ARRAY[$2::text], $3, $4, $5)
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


async def test_insight_scan_no_contacts_no_op(postgres_container):
    """No-op: returns zeros when no contacts exist."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0
        assert result["candidates_accepted"] == 0
        assert result["candidates_filtered"] == 0
        assert result["candidates_errored"] == 0
        assert result["early_exit"] is False


async def test_insight_scan_unlisted_contact_excluded(postgres_container):
    """Unlisted contacts are excluded from all insight categories."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


@pytest.mark.parametrize("posture", ["memorial", "quiet", "no_contact"])
async def test_insight_scan_non_active_posture_excluded(postgres_container, posture):
    """A person the owner marked memorial/quiet/no_contact is no insight's subject."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

        contact_id = await _insert_contact(
            pool, first_name="Person", last_name="Synthetic", posture=posture
        )
        today = _today()
        await _insert_important_date(
            pool, contact_id=contact_id, label="birthday", month=today.month, day=today.day
        )

        result = await run_insight_scan(pool)
        assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — upcoming date insights
# ---------------------------------------------------------------------------


async def test_insight_scan_upcoming_birthday_today_priority_95(postgres_container):
    """Birthday today gets priority 95 (time-critical)."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_upcoming_birthday_3_days_priority_80(postgres_container):
    """Birthday in 3 days gets priority 80."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_upcoming_birthday_7_days_priority_70(postgres_container):
    """Birthday in 5-7 days gets priority 70."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_birthday_beyond_window_excluded(postgres_container):
    """Birthdays beyond 7 days are excluded."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_anniversary_dedup_key_format(postgres_container):
    """Anniversary dedup_key follows anniversary:{entity-id}:{year} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_birthday_dedup_key_format(postgres_container):
    """Birthday dedup_key follows birthday:{entity-id}:{year} format when entity_id exists."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_birthday_cooldown_days(postgres_container):
    """Birthday cooldown_days matches priority tier."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_birthday_message_includes_contact_name(postgres_container):
    """Birthday message includes the contact name."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


@pytest.mark.pg_clock
async def test_insight_scan_contact_candidates_include_entity_and_event_metadata(
    postgres_container,
    monkeypatch,
):
    """Relationship candidates preserve the contact entity and real occasion date."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)
        entity_id = str(uuid.uuid4())
        contact_id = await _insert_contact(
            pool,
            first_name="Cora",
            entity_id=entity_id,
            stay_in_touch_days=7,
        )
        upcoming_date = _today() + timedelta(days=3)
        await _insert_important_date(
            pool,
            contact_id=contact_id,
            label="birthday",
            month=upcoming_date.month,
            day=upcoming_date.day,
        )
        await _insert_gift_fact(pool, contact_id=contact_id, description="tea", status="idea")
        for offset in range(10):
            await _insert_interaction_fact(
                pool,
                contact_id=contact_id,
                occurred_at=_utcnow() - timedelta(days=30 + offset),
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT category, metadata FROM insight_candidates WHERE category = ANY($1::text[])",
            ["birthday", "stale-contact", "pending-gift", "milestone"],
        )
        metadata_by_category = {row["category"]: row["metadata"] for row in rows}

        assert metadata_by_category["birthday"] == {
            "entity_id": entity_id,
            "event_date": upcoming_date.isoformat(),
        }
        assert metadata_by_category["stale-contact"] == {"entity_id": entity_id}
        assert metadata_by_category["pending-gift"] == {
            "entity_id": entity_id,
            "event_date": upcoming_date.isoformat(),
        }
        assert metadata_by_category["milestone"] == {"entity_id": entity_id}


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — stale contact insights
# ---------------------------------------------------------------------------


def _mock_stale_contact_gate(monkeypatch: pytest.MonkeyPatch, *, is_overdue: bool) -> None:
    """Keep legacy priority tests focused on policy after producer admission."""
    from types import SimpleNamespace

    from butlers.tools.relationship import stale_contacts

    monkeypatch.setattr(
        stale_contacts,
        "evaluate_stale_contact_signal",
        AsyncMock(return_value=SimpleNamespace(is_overdue=is_overdue)),
    )


@pytest.mark.pg_clock
async def test_insight_scan_stale_contact_overdue_2x_cadence_priority_45(
    postgres_container,
    monkeypatch,
):
    """Contact overdue by >2x cadence gets priority 45."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


@pytest.mark.pg_clock
async def test_insight_scan_stale_contact_parks_a_prepared_reach_out(
    postgres_container,
    monkeypatch,
):
    """bu-2jtfw.11: the stale-contact candidate links a silently-parked prepared action."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

        contact_id = await _insert_contact(pool, first_name="Priya", stay_in_touch_days=14)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=35),
        )

        await run_insight_scan(pool)

        candidate = await pool.fetchrow(
            "SELECT prepared_action_id FROM insight_candidates WHERE category = 'stale-contact'"
        )
        assert candidate is not None
        assert candidate["prepared_action_id"] is not None

        action = await pool.fetchrow(
            "SELECT origin, status, tool_name, tool_args, deduplication_key "
            "FROM pending_actions WHERE id = $1",
            candidate["prepared_action_id"],
        )
        assert action is not None
        assert action["origin"] == "prepared"
        assert action["status"] == "pending"
        assert action["tool_name"] == "notify"
        tool_args = action["tool_args"]
        assert tool_args["intent"] == "send"
        assert "Priya" in tool_args["message"]
        assert action["deduplication_key"].startswith("relationship:prepared-reach-out:")

        # A same-week re-run must not double-park (approvals_013's active
        # deduplication_key uniqueness) -- it must reuse the same row.
        await run_insight_scan(pool)
        rows = await pool.fetch(
            "SELECT id FROM pending_actions WHERE deduplication_key = $1",
            action["deduplication_key"],
        )
        assert len(rows) == 1


@pytest.mark.pg_clock
async def test_insight_scan_stale_contact_concurrent_scans_park_one_prepared_action(
    postgres_container,
    monkeypatch,
):
    """bu-2jtfw.11: two concurrent scan ticks racing the same dedup key must not
    crash ``run_insight_scan`` -- the loser reuses the winner's row instead of
    surfacing an unhandled ``UniqueViolationError``.

    Unlike the sequential same-week re-run above (which never reaches the
    INSERT because the pre-check already finds a row), this forces both ticks
    past the pre-check simultaneously so the real conflict happens at the
    database's unique index, exercising the ``except asyncpg.UniqueViolationError``
    branch directly.
    """
    import asyncio
    import sys
    from unittest.mock import patch

    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    relationship_jobs = sys.modules["butlers.jobs._roster.relationship_jobs"]

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

        contact_id = await _insert_contact(pool, first_name="Rosa", stay_in_touch_days=14)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=35),
        )

        real_park = relationship_jobs.park_prepared_action
        both_reached_park = asyncio.Event()
        arrivals = 0
        arrivals_lock = asyncio.Lock()

        async def _synchronized_park(*args, **kwargs):
            nonlocal arrivals
            async with arrivals_lock:
                arrivals += 1
                if arrivals == 2:
                    both_reached_park.set()
            await asyncio.wait_for(both_reached_park.wait(), timeout=5)
            return await real_park(*args, **kwargs)

        with patch.object(relationship_jobs, "park_prepared_action", new=_synchronized_park):
            first, second = await asyncio.gather(
                run_insight_scan(pool),
                run_insight_scan(pool),
            )

        assert first.get("errors", 0) == 0
        assert second.get("errors", 0) == 0

        rows = await pool.fetch(
            "SELECT id FROM pending_actions WHERE deduplication_key LIKE "
            "'relationship:prepared-reach-out:%'"
        )
        assert len(rows) == 1

        candidates = await pool.fetch(
            "SELECT prepared_action_id FROM insight_candidates WHERE category = 'stale-contact'"
        )
        prepared_action_ids = {c["prepared_action_id"] for c in candidates}
        assert prepared_action_ids == {rows[0]["id"]}


@pytest.mark.pg_clock
async def test_insight_scan_stale_contact_overdue_1x_cadence_priority_35(
    postgres_container,
    monkeypatch,
):
    """Contact overdue by 1-2x cadence gets priority 35."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_stale_contact_not_yet_overdue_excluded(postgres_container, monkeypatch):
    """Contact not yet overdue is excluded from stale-contact insights."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=False)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_unmeasurable_stale_contact_suppresses_candidate(
    postgres_container, monkeypatch
):
    """Elapsed cadence cannot emit when producer admission is unmeasurable."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=False)
    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)
        contact_id = await _insert_contact(pool, first_name="Instrument", stay_in_touch_days=7)
        await _insert_interaction_fact(
            pool,
            contact_id=contact_id,
            occurred_at=_utcnow() - timedelta(days=30),
        )

        await run_insight_scan(pool)

        assert (
            await pool.fetchval(
                "SELECT count(*) FROM insight_candidates WHERE category = 'stale-contact'"
            )
            == 0
        )


@pytest.mark.pg_clock
async def test_insight_scan_stale_contact_dedup_key_weekly_granularity(
    postgres_container,
    monkeypatch,
):
    """Stale contact dedup_key uses relationship:stale-contact:{id}:{year-week} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


@pytest.mark.pg_clock
async def test_insight_scan_stale_contact_expires_7_days_from_now(postgres_container, monkeypatch):
    """Stale contact candidate expires 7 days from generation."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    _mock_stale_contact_gate(monkeypatch, is_overdue=True)

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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
    postgres_container,
):
    """Pending gift (idea/purchased) with upcoming date gets priority 60."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_pending_gift_no_upcoming_date_excluded(postgres_container):
    """Pending gift without upcoming contact date is not surfaced."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

        contact_id = await _insert_contact(pool, first_name="Paul")
        # No upcoming dates for this contact
        await _insert_gift_fact(pool, contact_id=contact_id, description="wine", status="idea")

        await run_insight_scan(pool)
        rows = await pool.fetch("SELECT id FROM insight_candidates WHERE category = 'pending-gift'")
        assert len(rows) == 0


async def test_insight_scan_pending_gift_dedup_key_format(postgres_container):
    """Pending gift dedup_key follows relationship:pending-gift:{gift-id} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_gift_given_status_excluded(postgres_container):
    """Gifts with status 'given' or 'thanked' are excluded."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_pending_gift_expires_at_upcoming_date(postgres_container):
    """Pending gift candidate expires_at matches the associated upcoming date."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_milestone_100th_interaction(postgres_container):
    """100th interaction with a contact generates a milestone insight."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_milestone_dedup_key_format(postgres_container):
    """Milestone dedup_key follows relationship:milestone:{contact-id}:{milestone-type} format."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_milestone_non_notable_count_excluded(postgres_container):
    """Non-notable interaction counts (e.g., 7) do not generate milestones."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_first_interaction_anniversary(postgres_container):
    """1-year anniversary of first interaction generates a milestone insight."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_early_exit_verbosity_off(postgres_container):
    """Early exit when verbosity=off: job returns early_exit=True."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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


async def test_insight_scan_stats_keys_present(postgres_container):
    """Result dict always contains all expected statistics keys."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

        result = await run_insight_scan(pool)

        assert "candidates_proposed" in result
        assert "candidates_accepted" in result
        assert "candidates_filtered" in result
        assert "candidates_errored" in result
        assert "early_exit" in result


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — origin_butler tagging
# ---------------------------------------------------------------------------


async def test_insight_scan_origin_butler_is_relationship(postgres_container):
    """All generated candidates are tagged with origin_butler='relationship'."""
    from butlers.jobs._roster.relationship_jobs import run_insight_scan

    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
    ) as pool:
        await _setup_relationship_schema(pool)

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
# Synthetic data for real migrated interaction-sync fixtures
# ---------------------------------------------------------------------------


async def _insert_contact_anchor(
    pool, *, first_name: str = "Alice", last_name: str = "Smith", entity_id: str | None = None
) -> str:
    """Create the canonical entity/contact bridge without retired public contacts."""
    from roster.relationship.tests.calendar_projection import make_contact_anchor

    if entity_id is None:
        entity_id = await _insert_public_entity(pool)
    return str(await make_contact_anchor(pool, uuid.UUID(entity_id)))


async def _insert_public_entity(pool, *, roles: list[str] | None = None) -> str:
    from roster.relationship.tests.calendar_projection import make_entity

    return str(await make_entity(pool, roles=roles))


async def _insert_contact_info(
    pool, *, contact_id: str, ci_type: str, value: str, is_primary: bool = True
) -> str:
    """Write canonical identity evidence with the real central fact writer."""
    from roster.relationship.tests.calendar_projection import link_identity

    predicates = {
        "telegram_chat_id": "has-handle",
        "whatsapp_jid": "has-handle",
        "email": "has-email",
        "phone": "has-phone",
    }
    entity_id = await pool.fetchval(
        "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
        contact_id,
    )
    assert entity_id is not None
    await link_identity(pool, entity_id=entity_id, predicate=predicates[ci_type], value=value)
    return str(uuid.uuid4())


async def _insert_message_inbox(
    pool,
    *,
    sender_identity: str,
    source_channel: str,
    received_at: datetime | None = None,
    direction: str = "inbound",
    source_endpoint_identity: str | None = None,
    source_thread_identity: str | None = None,
    external_conversation_id: str | None = None,
) -> None:
    """Insert a message_inbox row for testing."""
    if received_at is None:
        received_at = datetime.now(UTC) - timedelta(hours=1)
    request_context = {
        "source_sender_identity": sender_identity,
        "source_channel": source_channel,
    }
    if source_endpoint_identity is not None:
        request_context["source_endpoint_identity"] = source_endpoint_identity
    if source_thread_identity is not None:
        request_context["source_thread_identity"] = source_thread_identity
    if external_conversation_id is not None:
        request_context["external_conversation_id"] = external_conversation_id
    from roster.relationship.tests.calendar_projection import insert_message

    await insert_message(
        pool, received_at=received_at, request_context=request_context, direction=direction
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
    """Use the existing local CalendarModule writer, including source and instance."""
    from roster.relationship.tests.calendar_projection import project_event

    return str(
        await project_event(
            pool,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            status=status,
            attendees=attendees,
        )
    )


# ---------------------------------------------------------------------------
# Tests: run_interaction_sync
# ---------------------------------------------------------------------------


async def test_interaction_sync_no_messages_returns_zeros(interaction_sync_pool):
    """No-op: returns zeros when message_inbox is empty."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        result = await run_interaction_sync(pool)

        assert result["processed"] == 0
        assert result["logged"] == 0
        assert result["skipped_unresolved"] == 0
        assert result["skipped_owner"] == 0
        assert result["errors"] == 0


async def test_interaction_sync_returns_expected_stats_keys(interaction_sync_pool):
    """Result dict always contains all expected statistics keys."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        result = await run_interaction_sync(pool)

        assert {
            "scan_window_start",
            "scan_window_end",
            "processed",
            "logged",
            "skipped_unresolved",
            "skipped_owner",
            "skipped_ineligible",
            "skipped_group_too_large",
            "calendar_events_scanned",
            "co_attended_edges_minted",
            "knows_edges_minted",
            "resolution_degraded",
            "errors",
        }.issubset(result)


async def test_interaction_sync_unresolved_sender_skipped(interaction_sync_pool):
    """Senders with no matching contact_info entry are counted as skipped_unresolved."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
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


async def test_interaction_sync_outbound_messages_ignored(interaction_sync_pool):
    """Outbound messages are not processed (only inbound are synced)."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Bob")
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


async def test_interaction_sync_owner_contact_skipped(interaction_sync_pool):
    """Owner contacts are skipped even when their sender identity is resolved."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        # Create an owner entity and link it to the contact
        entity_id = await _insert_public_entity(pool, roles=["owner"])
        contact_id = await _insert_contact_anchor(pool, first_name="Owner", entity_id=entity_id)
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


async def test_interaction_sync_logs_telegram_interaction(interaction_sync_pool):
    """A resolved telegram_user_client message is logged as an interaction fact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Carol")
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
            "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
            contact_id,
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


async def test_interaction_sync_groups_chat_by_conversation_not_reply_target(
    interaction_sync_pool,
):
    """Per-message reply targets in one chat aggregate into one chat group (bu-7exe4.2)."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Grace")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="telegram_chat_id", value="777002"
        )
        observed_at = datetime.now(UTC) - timedelta(hours=1)
        for message_id in (1, 2):
            await _insert_message_inbox(
                pool,
                sender_identity="777002",
                source_channel="telegram_user_client",
                source_thread_identity=f"777002:{message_id}",
                external_conversation_id="telegram:777002",
                received_at=observed_at + timedelta(minutes=message_id),
            )

        result = await run_interaction_sync(pool)

        assert result["processed"] == 1
        assert result["logged"] == 1
        message_count = await pool.fetchval(
            """
            SELECT (metadata -> 'extra_metadata' ->> 'message_count')::int FROM facts
            WHERE predicate = 'interaction_telegram_user_client'
            """
        )
        assert message_count == 2


async def test_interaction_sync_keeps_sibling_endpoints_separate(
    interaction_sync_pool,
):
    """One shared thread/date cannot collapse two exact producer endpoints."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Endpoint")
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
            contact_id,
        )
        await _insert_contact_info(
            pool,
            contact_id=contact_id,
            ci_type="email",
            value="endpoint@example.com",
        )
        observed_at = datetime.now(UTC) - timedelta(hours=1)
        for endpoint in ("gmail:account-a", "gmail:account-b"):
            await _insert_message_inbox(
                pool,
                sender_identity="endpoint@example.com",
                source_channel="email",
                source_endpoint_identity=endpoint,
                source_thread_identity="shared-thread",
                received_at=observed_at,
            )

        result = await run_interaction_sync(pool)

        assert result["logged"] == 2
        rows = await pool.fetch(
            """
            SELECT metadata->'expected_signal_source' AS source
            FROM facts
            WHERE entity_id = $1
              AND predicate = 'interaction_email'
            ORDER BY metadata #>> '{expected_signal_source,source_endpoint_identity}'
            """,
            entity_id,
        )
        assert [row["source"]["source_endpoint_identity"] for row in rows] == [
            "gmail:account-a",
            "gmail:account-b",
        ]
        assert all(row["source"]["producer"] == "connector:gmail" for row in rows)
        assert result["errors"] == 0


async def test_interaction_sync_deduplicates_same_sender_same_day(interaction_sync_pool):
    """Multiple messages from the same sender on the same day → one interaction fact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Dave")
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
    interaction_sync_pool,
):
    """Same contact via different channels (telegram + email) → two separate facts."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Eve")
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
            "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
            contact_id,
        )
        rows = await pool.fetch(
            "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
            f"entity:{entity_id}",
        )
        assert len(rows) == 2


async def test_interaction_sync_old_messages_excluded(interaction_sync_pool):
    """Messages older than the scan window are not processed."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Frank")
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


async def test_interaction_sync_unknown_channel_ignored(interaction_sync_pool):
    """Messages from unsupported channels (e.g. telegram_bot) are not processed."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Grace")
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


async def test_interaction_sync_idempotent_second_run(interaction_sync_pool):
    """Running interaction sync twice for the same messages does not create duplicate facts."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Henry")
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
            "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
            contact_id,
        )
        rows = await pool.fetch(
            "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
            f"entity:{entity_id}",
        )
        assert len(rows) == 1


async def test_interaction_sync_whatsapp_channel_resolved(interaction_sync_pool):
    """A resolved whatsapp_user_client message is logged via whatsapp_jid contact_info."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Ivan")
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


async def test_interaction_sync_email_channel_resolved(interaction_sync_pool):
    """A resolved email message is logged via email contact_info."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Jane")
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


async def test_interaction_sync_no_checkpoint_uses_30_day_default(interaction_sync_pool):
    """Without a checkpoint, scan_window_start defaults to ~30 days ago."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        result = await run_interaction_sync(pool)

        assert "scan_window_start" in result
        assert "scan_window_end" in result
        start = datetime.fromisoformat(result["scan_window_start"])
        end = datetime.fromisoformat(result["scan_window_end"])
        # Start should be roughly 30 days before end (±1 minute tolerance)
        window = end - start
        assert abs(window - timedelta(days=30)) <= timedelta(minutes=1)


async def test_interaction_sync_checkpoint_used_as_start(interaction_sync_pool):
    """When a checkpoint exists, it is used as scan_window_start."""
    from butlers.core.state import state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        # Store a checkpoint 5 days ago
        checkpoint = datetime.now(UTC) - timedelta(days=5)
        await state_set(pool, "interaction_sync.last_scan_at", checkpoint.isoformat())

        result = await run_interaction_sync(pool)

        start = datetime.fromisoformat(result["scan_window_start"])
        end = datetime.fromisoformat(result["scan_window_end"])
        diff_days = (end - start).total_seconds() / 86400
        # Should be ~5 days, not 30
        assert 4.9 < diff_days < 5.1


async def test_interaction_sync_checkpoint_capped_at_30_days(interaction_sync_pool):
    """A checkpoint older than 30 days is capped to 30 days ago."""
    from butlers.core.state import state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        # Store a checkpoint 60 days ago
        checkpoint = datetime.now(UTC) - timedelta(days=60)
        await state_set(pool, "interaction_sync.last_scan_at", checkpoint.isoformat())

        result = await run_interaction_sync(pool)

        start = datetime.fromisoformat(result["scan_window_start"])
        end = datetime.fromisoformat(result["scan_window_end"])
        diff_days = (end - start).total_seconds() / 86400
        # Capped: should be ~30 days, not 60 or a much smaller window
        assert 29.9 < diff_days < 30.1


async def test_interaction_sync_writes_checkpoint_on_success(interaction_sync_pool):
    """After a successful run, the state store contains scan_window_end as the new checkpoint."""
    from butlers.core.state import state_get
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        result = await run_interaction_sync(pool)

        stored = await state_get(pool, "interaction_sync.last_scan_at")
        assert stored is not None
        assert stored == result["scan_window_end"]


async def test_interaction_sync_window_end_is_iso8601(interaction_sync_pool):
    """scan_window_start and scan_window_end are valid ISO8601 strings."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        result = await run_interaction_sync(pool)

        # Should not raise
        datetime.fromisoformat(result["scan_window_start"])
        datetime.fromisoformat(result["scan_window_end"])


async def test_interaction_sync_second_run_uses_first_checkpoint(interaction_sync_pool):
    """Second run uses the checkpoint written by the first run."""
    from butlers.core.state import state_get
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
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


async def test_interaction_sync_calendar_no_events_returns_zero(interaction_sync_pool):
    """No calendar events → calendar_events_scanned remains 0."""
    from butlers.core.state import state_get
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 0
        assert result["logged"] == 0
        assert result["errors"] == 0
        assert await state_get(pool, "interaction_sync.last_scan_at") == result["scan_window_end"]


@pytest.mark.pg_clock
@pytest.mark.parametrize(
    ("failure", "category"),
    [
        ("table", "UndefinedTableError"),
        ("column", "UndefinedColumnError"),
        ("privilege", "InsufficientPrivilegeError"),
    ],
)
async def test_interaction_sync_calendar_query_failure_preserves_checkpoint(
    interaction_sync_pool, caplog, failure, category
):
    """REQ-passive-interaction-sync-002: retain the original window and committed work."""
    from butlers.core.state import state_get, state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync
    from roster.relationship.tests.calendar_projection import (
        database_for,
        link_identity,
        make_entity,
    )

    async with interaction_sync_pool() as pool:
        anchor = await pool.fetchval("SELECT now()")
        checkpoint = anchor - timedelta(days=3)
        event_at = anchor - timedelta(days=2)
        message_at = anchor - timedelta(days=1)
        await state_set(pool, "interaction_sync.last_scan_at", checkpoint.isoformat())
        alice = await make_entity(pool)
        bob = await make_entity(pool)
        for entity_id, email in [(alice, "alice@example.com"), (bob, "bob@example.com")]:
            await link_identity(pool, entity_id=entity_id, predicate="has-email", value=email)
        await _insert_message_inbox(
            pool,
            sender_identity="alice@example.com",
            source_channel="email",
            received_at=message_at,
        )
        await _insert_calendar_event(
            pool,
            starts_at=event_at,
            attendees=[{"email": "alice@example.com"}, {"email": "bob@example.com"}],
        )
        faults = {
            "table": (
                "ALTER TABLE relationship.calendar_events RENAME TO calendar_events_unavailable",
                "ALTER TABLE relationship.calendar_events_unavailable RENAME TO calendar_events",
            ),
            "column": (
                "ALTER TABLE relationship.calendar_events RENAME COLUMN metadata TO metadata_unavailable",
                "ALTER TABLE relationship.calendar_events RENAME COLUMN metadata_unavailable TO metadata",
            ),
            "privilege": (
                "REVOKE SELECT ON relationship.calendar_events FROM butler_relationship_rw",
                "GRANT SELECT ON relationship.calendar_events TO butler_relationship_rw",
            ),
        }
        break_read, restore_read = faults[failure]
        async with database_for(pool).control() as control:
            await control.execute(break_read)
        try:
            with caplog.at_level(logging.WARNING):
                result = await run_interaction_sync(pool)
            assert result["errors"] == 1
            assert result["logged"] == 1
            assert result["calendar_events_scanned"] == 0
            assert await state_get(pool, "interaction_sync.last_scan_at") == checkpoint.isoformat()
            assert any(category in record.message for record in caplog.records)
            if failure == "table":
                assert any(
                    "UndefinedTableError" in r.message and r.levelno == logging.WARNING
                    for r in caplog.records
                )
            else:
                assert any(
                    r.exc_info and r.exc_info[0].__name__ == category for r in caplog.records
                )
            message_ids = {
                r["id"]
                for r in await pool.fetch(
                    "SELECT id FROM facts WHERE predicate = 'interaction_email'"
                )
            }
            assert len(message_ids) == 1
        finally:
            async with database_for(pool).control() as control:
                await control.execute(restore_read)

        recovered = await run_interaction_sync(pool)
        for stats in (result, recovered):
            start = datetime.fromisoformat(stats["scan_window_start"])
            end = datetime.fromisoformat(stats["scan_window_end"])
            assert start == checkpoint < event_at < end
            assert end - start < timedelta(days=30)
        assert recovered["errors"] == 0
        assert recovered["logged"] == 2
        assert recovered["co_attended_edges_minted"] == 2
        assert {
            r["id"]
            for r in await pool.fetch("SELECT id FROM facts WHERE predicate = 'interaction_email'")
        } == message_ids
        facts = {
            r["id"]
            for r in await pool.fetch("SELECT id FROM facts WHERE predicate LIKE 'interaction_%'")
        }
        edges = {
            r["id"]
            for r in await pool.fetch(
                "SELECT id FROM relationship.entity_facts WHERE predicate = 'co-attended'"
            )
        }
        assert len(facts) == 3 and len(edges) == 2
        assert (
            await state_get(pool, "interaction_sync.last_scan_at") == recovered["scan_window_end"]
        )

        # Test setup deliberately repeats the SAME bounded interval to exercise
        # real writer dedup; production has no new rewind/retry mechanism.
        await state_set(pool, "interaction_sync.last_scan_at", checkpoint.isoformat())
        repeated = await run_interaction_sync(pool)
        assert repeated["errors"] == repeated["logged"] == repeated["co_attended_edges_minted"] == 0
        assert {
            r["id"]
            for r in await pool.fetch("SELECT id FROM facts WHERE predicate LIKE 'interaction_%'")
        } == facts
        assert {
            r["id"]
            for r in await pool.fetch(
                "SELECT id FROM relationship.entity_facts WHERE predicate = 'co-attended'"
            )
        } == edges


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_logs_attendee_interaction(interaction_sync_pool):
    """REQ-passive-interaction-sync-001: real own-role local projection consumption.

    The causal control restores ONLY the historical public-table SQL literal,
    not the full historical job/error/checkpoint implementation.
    """
    from types import FunctionType

    from butlers.core.state import state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync
    from roster.relationship.tests.calendar_projection import (
        database_for,
        link_identity,
        make_entity,
        project_event,
    )

    async with interaction_sync_pool() as pool:
        people = {}
        for label in ("own-a", "own-b", "public-a", "public-b", "foreign-a", "foreign-b"):
            people[label] = await make_entity(pool)
            await link_identity(
                pool, entity_id=people[label], predicate="has-email", value=f"{label}@example.com"
            )
        event_at = await pool.fetchval("SELECT now() - interval '2 days'")
        own_event = await project_event(
            pool,
            title="Own projected event",
            starts_at=event_at,
            attendees=[{"email": "own-a@example.com"}, {"email": "own-b@example.com"}],
        )
        database = database_for(pool)
        await database.migrate_controls()
        control_events = {}
        for schema, prefix in [("public", "public"), ("health", "foreign")]:
            async with database.control(schema) as control:
                control_events[schema] = await project_event(
                    control,
                    title=f"{schema} sentinel",
                    starts_at=event_at,
                    attendees=[
                        {"email": f"{prefix}-a@example.com"},
                        {"email": f"{prefix}-b@example.com"},
                    ],
                )
                assert (
                    await control.fetchval(
                        f"SELECT id FROM {schema}.calendar_events WHERE id = $1",
                        control_events[schema],
                    )
                    == control_events[schema]
                )
        # Schema reachability is checked first; to_regclass itself would throw
        # on a foreign schema without USAGE. No transaction catches/continues.
        assert not await pool.fetchval(
            "SELECT COALESCE((SELECT has_schema_privilege(oid, 'USAGE') "
            "FROM pg_namespace WHERE nspname = 'health'), false)"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await pool.fetch("SELECT id FROM health.calendar_events")

        async def fact_ids(ids):
            return {
                r["id"]
                for r in await pool.fetch(
                    "SELECT id FROM relationship.facts WHERE predicate = 'interaction_calendar_event' "
                    "AND entity_id = ANY($1::uuid[])",
                    ids,
                )
            }

        async def edge_ids(ids):
            return {
                r["id"]
                for r in await pool.fetch(
                    "SELECT id FROM relationship.entity_facts WHERE predicate = 'co-attended' "
                    "AND validity = 'active' AND subject = ANY($1::uuid[])",
                    ids,
                )
            }

        own = [people["own-a"], people["own-b"]]
        wrong = [people["public-a"], people["public-b"]]
        foreign = [people["foreign-a"], people["foreign-b"]]
        constants = run_interaction_sync.__code__.co_consts
        matches = [
            i
            for i, value in enumerate(constants)
            if isinstance(value, str) and "FROM relationship.calendar_events" in value
        ]
        assert len(matches) == 1, (
            "Historical-query control requires exactly one Step 4 SQL constant"
        )
        index = matches[0]
        assert constants[index].count("FROM relationship.calendar_events") == 1
        legacy_constants = tuple(
            value.replace("FROM relationship.calendar_events", "FROM public.calendar_events")
            if i == index
            else value
            for i, value in enumerate(constants)
        )
        legacy_code = run_interaction_sync.__code__.replace(co_consts=legacy_constants)
        assert legacy_code.replace(co_consts=constants) == run_interaction_sync.__code__
        assert all(legacy_constants[i] is constants[i] for i in range(len(constants)) if i != index)
        legacy_query = FunctionType(
            legacy_code,
            run_interaction_sync.__globals__,
            run_interaction_sync.__name__,
            run_interaction_sync.__defaults__,
            run_interaction_sync.__closure__,
        )
        legacy_query.__kwdefaults__ = run_interaction_sync.__kwdefaults__
        assert legacy_query.__globals__ is run_interaction_sync.__globals__
        legacy = await legacy_query(pool)
        assert legacy["errors"] == 0
        assert legacy["logged"] == legacy["co_attended_edges_minted"] == 2
        assert await fact_ids(own) == await edge_ids(own) == set()
        wrong_facts, wrong_edges = await fact_ids(wrong), await edge_ids(wrong)
        assert len(wrong_facts) == len(wrong_edges) == 2
        assert not await fact_ids(foreign) and not await edge_ids(foreign)

        # Only synthetic checkpoint state is reset between these two phases.
        # Previously-minted wrong IDs are positive witnesses, not claimed to
        # have been minted by the corrected call.
        await state_set(
            pool, "interaction_sync.last_scan_at", (event_at - timedelta(days=1)).isoformat()
        )
        result = await run_interaction_sync(pool)
        assert result["calendar_events_scanned"] == 1
        assert result["logged"] == result["co_attended_edges_minted"] == 2
        assert result["errors"] == 0
        assert len(await fact_ids(own)) == len(await edge_ids(own)) == 2
        assert await fact_ids(wrong) == wrong_facts and await edge_ids(wrong) == wrong_edges
        assert not await fact_ids(foreign) and not await edge_ids(foreign)
        rows = await pool.fetch(
            "SELECT metadata FROM relationship.facts WHERE entity_id = ANY($1::uuid[]) "
            "AND predicate = 'interaction_calendar_event'",
            own,
        )
        for row in rows:
            meta = row["metadata"]
            assert meta["type"] == "calendar_event" and meta["direction"] == "mutual"
            assert meta["extra_metadata"]["source"] == "interaction_sync"
            assert meta["extra_metadata"]["event_id"] == str(own_event)
        evidence = await pool.fetchval(
            "SELECT count(*) FROM relationship.fact_coverage WHERE subject = ANY($1::uuid[]) "
            "AND predicate = 'co-attended'",
            own,
        )
        assert evidence == 2


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_unresolved_attendee_skipped(
    interaction_sync_pool,
):
    """Attendee email not in contact_info increments skipped_unresolved."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
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


async def test_interaction_sync_calendar_declined_event_excluded(interaction_sync_pool):
    """Events where the owner RSVP is declined are excluded (not counted)."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Bob")
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


async def test_interaction_sync_calendar_cancelled_event_excluded(interaction_sync_pool):
    """Events with status='cancelled' are excluded by the query filter."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Carol")
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


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_owner_attendee_excluded(interaction_sync_pool):
    """The owner's own attendee entry (self=True) is excluded from interaction logging."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entity_id = await _insert_public_entity(pool, roles=["owner"])
        owner_contact_id = await _insert_contact_anchor(
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


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_owner_contact_skipped(interaction_sync_pool):
    """Owner contact resolved via non-self email entry is counted as skipped_owner."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entity_id = await _insert_public_entity(pool, roles=["owner"])
        owner_contact_id = await _insert_contact_anchor(
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


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_case_insensitive_email_match(
    interaction_sync_pool,
):
    """Attendee email matching against contact_info is case-insensitive."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Dave")
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


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_multiple_attendees_same_event(
    interaction_sync_pool,
):
    """A single event with multiple resolved attendees creates one fact per contact."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_a = await _insert_contact_anchor(pool, first_name="Eve")
        await _insert_contact_info(
            pool, contact_id=contact_a, ci_type="email", value="eve@example.com"
        )

        contact_b = await _insert_contact_anchor(pool, first_name="Frank")
        await _insert_contact_info(
            pool, contact_id=contact_b, ci_type="email", value="frank@example.com"
        )

        await _insert_calendar_event(
            pool,
            title="Team Lunch",
            attendees=[
                {"email": "eve@example.com", "responseStatus": "declined"},
                {"email": " EVE@EXAMPLE.COM ", "responseStatus": "accepted"},
                {"email": "frank@example.com", "responseStatus": "tentative"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["logged"] == 2

        for cid in (contact_a, contact_b):
            eid = await pool.fetchval(
                "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
                cid,
            )
            rows = await pool.fetch(
                "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
                f"entity:{eid}",
            )
            assert len(rows) == 1


async def test_interaction_sync_calendar_event_outside_window_excluded(
    interaction_sync_pool,
):
    """Calendar events older than the lookback window are not processed."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Grace")
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


@pytest.mark.pg_clock
async def test_interaction_sync_calendar_idempotent_second_run(interaction_sync_pool):
    """Running interaction sync twice for the same event does not create duplicate facts."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Henry")
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
            "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
            contact_id,
        )
        rows = await pool.fetch(
            "SELECT id FROM facts WHERE subject = $1 AND predicate LIKE 'interaction_%'",
            f"entity:{entity_id}",
        )
        assert len(rows) == 1


async def test_interaction_sync_calendar_no_attendees_field_skipped(interaction_sync_pool):
    """Absent, non-array and malformed attendees never manufacture interactions."""
    from butlers.core.state import state_set
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        event_at = await pool.fetchval("SELECT now() - interval '1 hour'")
        event_id = await _insert_calendar_event(pool, title="Solo Event", starts_at=event_at)
        for metadata, scanned in [
            ({}, 0),
            ({"attendees": []}, 0),
            ({"attendees": {"email": "not-an-array@example.com"}}, 0),
            ({"attendees": [None, {}, {"email": 7}]}, 1),
        ]:
            # Genuine projected row, malformed persisted metadata: exercise the
            # consumer separately from the provider's typed input validator.
            await pool.execute(
                "UPDATE relationship.calendar_events SET metadata = $2 WHERE id = $1",
                uuid.UUID(event_id),
                metadata,
            )
            await state_set(
                pool, "interaction_sync.last_scan_at", (event_at - timedelta(days=1)).isoformat()
            )
            result = await run_interaction_sync(pool)
            assert result["calendar_events_scanned"] == scanned
            assert result["logged"] == result["errors"] == 0


@pytest.mark.pg_clock
async def test_interaction_sync_combined_messages_and_calendar(interaction_sync_pool):
    """Messages and calendar events are both processed in a single run."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        # Contact with telegram + email
        contact_id = await _insert_contact_anchor(pool, first_name="Iris")
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
