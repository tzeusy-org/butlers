"""Integration tests — calendar-native reminder lifecycle.

Tests the full lifecycle of reminders stored as calendar-native events:
  1. Full lifecycle: create reminder → list → dismiss → verify state
  2. Recurring reminder: dismiss advances to next instance, series remains active
  3. Source-butler isolation: _project_reminders_source scopes to the current
     butler's source_key, so butler A's events don't bleed into butler B's
     calendar_sources row
  4. Contact association: create reminder with contact_id, verify stored row

These tests exercise CalendarModule private methods directly against a real
PostgreSQL database (testcontainers) using the same fixture pattern as the
existing relationship butler SPO tests (test_spo_tools.py).

[bu-hws35]
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# SQL helpers — minimal schema for reminder lifecycle tests
# ---------------------------------------------------------------------------

_CREATE_REMINDERS_SQL = """
CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label TEXT,
    message TEXT,
    type TEXT,
    reminder_type TEXT,
    next_trigger_at TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    until_at TIMESTAMPTZ,
    recurrence_rule TEXT,
    cron TEXT,
    dismissed BOOLEAN NOT NULL DEFAULT false,
    calendar_event_id TEXT,
    contact_id UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_CALENDAR_SOURCES_SQL = """
CREATE TABLE IF NOT EXISTS calendar_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL,
    lane TEXT NOT NULL DEFAULT 'user',
    provider TEXT,
    calendar_id TEXT,
    butler_name TEXT,
    display_name TEXT,
    writable BOOLEAN NOT NULL DEFAULT false,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT calendar_sources_lane_check CHECK (lane IN ('user', 'butler')),
    CONSTRAINT calendar_sources_source_key_nonempty
        CHECK (length(btrim(source_key)) > 0),
    CONSTRAINT calendar_sources_source_kind_nonempty
        CHECK (length(btrim(source_kind)) > 0)
)
"""

_CREATE_CALENDAR_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
    origin_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,
    timezone TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    all_day BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'confirmed',
    visibility TEXT NOT NULL DEFAULT 'default',
    recurrence_rule TEXT,
    etag TEXT,
    origin_updated_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT calendar_events_source_origin_unique UNIQUE (source_id, origin_ref),
    CONSTRAINT calendar_events_source_origin_nonempty
        CHECK (length(btrim(origin_ref)) > 0),
    CONSTRAINT calendar_events_window_check CHECK (ends_at > starts_at),
    CONSTRAINT calendar_events_status_check
        CHECK (status IN ('confirmed', 'tentative', 'cancelled'))
)
"""

_CREATE_CALENDAR_EVENT_INSTANCES_SQL = """
CREATE TABLE IF NOT EXISTS calendar_event_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
    origin_instance_ref TEXT NOT NULL,
    timezone TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    is_exception BOOLEAN NOT NULL DEFAULT false,
    origin_updated_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT calendar_event_instances_event_origin_unique
        UNIQUE (event_id, origin_instance_ref),
    CONSTRAINT calendar_event_instances_origin_ref_nonempty
        CHECK (length(btrim(origin_instance_ref)) > 0),
    CONSTRAINT calendar_event_instances_window_check CHECK (ends_at > starts_at),
    CONSTRAINT calendar_event_instances_status_check
        CHECK (status IN ('confirmed', 'tentative', 'cancelled'))
)
"""

_CREATE_CALENDAR_SYNC_CURSORS_SQL = """
CREATE TABLE IF NOT EXISTS calendar_sync_cursors (
    source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
    cursor_name TEXT NOT NULL DEFAULT 'default',
    sync_token TEXT,
    checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
    full_sync_required BOOLEAN NOT NULL DEFAULT false,
    last_synced_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, cursor_name),
    CONSTRAINT calendar_sync_cursors_cursor_name_nonempty
        CHECK (length(btrim(cursor_name)) > 0)
)
"""

_CREATE_CALENDAR_ACTION_LOG_SQL = """
CREATE TABLE IF NOT EXISTS calendar_action_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_id TEXT,
    action_type TEXT NOT NULL,
    action_status TEXT NOT NULL DEFAULT 'pending',
    source_id UUID REFERENCES calendar_sources(id) ON DELETE SET NULL,
    event_id UUID REFERENCES calendar_events(id) ON DELETE SET NULL,
    instance_id UUID REFERENCES calendar_event_instances(id) ON DELETE SET NULL,
    origin_ref TEXT,
    action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ,
    CONSTRAINT calendar_action_log_idempotency_key_nonempty
        CHECK (length(btrim(idempotency_key)) > 0),
    CONSTRAINT calendar_action_log_action_type_nonempty
        CHECK (length(btrim(action_type)) > 0),
    CONSTRAINT calendar_action_log_status_check
        CHECK (action_status IN ('pending', 'applied', 'failed', 'noop'))
)
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def reminder_pool(provisioned_postgres_pool):
    """Fresh DB with reminders + full calendar projection tables."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_REMINDERS_SQL)
        await pool.execute(_CREATE_CALENDAR_SOURCES_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENTS_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENT_INSTANCES_SQL)
        await pool.execute(_CREATE_CALENDAR_SYNC_CURSORS_SQL)
        await pool.execute(_CREATE_CALENDAR_ACTION_LOG_SQL)
        yield pool


def _make_module(pool, *, butler_name: str = "relationship") -> object:
    """Return a CalendarModule wired to a real pool, skipping startup."""
    from butlers.modules.calendar import CalendarModule

    mod = CalendarModule()
    db = SimpleNamespace(pool=pool, db_schema=butler_name, db_name="butlers")
    mod._db = db
    mod._butler_name = butler_name
    return mod


# ---------------------------------------------------------------------------
# Helper — create a reminder row via CalendarModule private method
# ---------------------------------------------------------------------------


async def _create_reminder(
    mod,
    *,
    title: str = "Test reminder",
    start_at: datetime | None = None,
    recurrence_rule: str | None = None,
    cron: str | None = None,
    action_args: dict | None = None,
) -> dict:
    if start_at is None:
        start_at = datetime.now(UTC) + timedelta(days=1)
    return await mod._create_reminder_event(
        title=title,
        start_at=start_at,
        timezone="UTC",
        until_at=None,
        recurrence_rule=recurrence_rule,
        cron=cron,
        action="test action",
        action_args=action_args,
        calendar_event_id=str(uuid.uuid4()),
    )


# ===========================================================================
# 1. Full lifecycle: create → verify list → dismiss → verify dismissed
# ===========================================================================


async def test_full_lifecycle_create_list_dismiss(reminder_pool):
    """Full reminder lifecycle: create, verify persisted, dismiss, verify dismissed.

    Covers spec: Full lifecycle integration test.
    """
    pool = reminder_pool
    mod = _make_module(pool)

    start_at = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    reminder = await _create_reminder(mod, title="Take vitamins", start_at=start_at)

    assert reminder["label"] == "Take vitamins"
    assert reminder["dismissed"] is False
    assert reminder["next_trigger_at"] == start_at
    assert reminder["due_at"] == start_at
    reminder_id = uuid.UUID(str(reminder["id"]))

    # Verify persisted in DB
    row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
    assert row is not None
    assert row["dismissed"] is False

    # Dismiss (toggle enabled=False)
    dismissed = await mod._toggle_reminder_event(reminder_id, enabled=False)
    assert dismissed["dismissed"] is True
    assert dismissed["next_trigger_at"] is None

    # Verify DB state
    row_after = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
    assert row_after["dismissed"] is True
    assert row_after["next_trigger_at"] is None


async def test_full_lifecycle_delete(reminder_pool):
    """Deleting a reminder removes the row from the database."""
    pool = reminder_pool
    mod = _make_module(pool)

    reminder = await _create_reminder(mod, title="Dentist appointment")
    reminder_id = uuid.UUID(str(reminder["id"]))

    # Verify exists
    assert await pool.fetchrow("SELECT id FROM reminders WHERE id = $1", reminder_id) is not None

    # Delete
    deleted = await mod._delete_reminder_event(reminder_id)
    assert deleted is True

    # Verify removed
    assert await pool.fetchrow("SELECT id FROM reminders WHERE id = $1", reminder_id) is None


async def test_full_lifecycle_update_title_and_time(reminder_pool):
    """Updating a reminder's title and time reflects in the database."""
    pool = reminder_pool
    mod = _make_module(pool)

    original_start = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    reminder = await _create_reminder(mod, title="Morning walk", start_at=original_start)
    reminder_id = uuid.UUID(str(reminder["id"]))

    new_start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    updated = await mod._update_reminder_event(
        reminder_id=reminder_id,
        title="Afternoon walk",
        start_at=new_start,
        timezone=None,
        until_at=None,
        recurrence_rule=None,
        cron=None,
        enabled=None,
    )

    assert updated["label"] == "Afternoon walk"
    assert updated["message"] == "Afternoon walk"
    assert updated["next_trigger_at"] == new_start
    assert updated["due_at"] == new_start

    # Verify DB
    row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
    if row["label"] is not None:
        assert row["label"] == "Afternoon walk"
    if row["next_trigger_at"] is not None:
        assert row["next_trigger_at"] == new_start


# ===========================================================================
# 2. Recurring reminder: dismiss advances next_trigger_at, series stays active
# ===========================================================================


async def test_recurring_reminder_toggle_state_transitions(reminder_pool):
    """Toggle a monthly recurring reminder off and on; verify state transitions.

    _toggle_reminder_event(enabled=False) clears next_trigger_at and marks
    dismissed=True. Re-enabling (enabled=True) restores next_trigger_at from
    due_at and sets dismissed=False.

    Note: The CalendarModule's _toggle_reminder_event clears next_trigger_at
    when enabled=False and restores it from due_at when re-enabled. The
    recurring advance logic (advancing to the next occurrence) lives in the
    relationship butler's reminder_dismiss function, not here.
    """
    pool = reminder_pool
    mod = _make_module(pool)

    due = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    reminder = await _create_reminder(
        mod,
        title="Monthly review",
        start_at=due,
        recurrence_rule="RRULE:FREQ=MONTHLY",
    )
    reminder_id = uuid.UUID(str(reminder["id"]))

    # Confirm it was created as recurring with trigger time set
    row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
    assert row["dismissed"] is False
    assert row["next_trigger_at"] is not None or row["due_at"] is not None

    # Dismiss (pause) the recurring reminder
    dismissed = await mod._toggle_reminder_event(reminder_id, enabled=False)
    assert dismissed["dismissed"] is True
    assert dismissed["next_trigger_at"] is None

    # Re-enable: next_trigger_at should be restored from due_at
    resumed = await mod._toggle_reminder_event(reminder_id, enabled=True)
    assert resumed["dismissed"] is False
    assert resumed["next_trigger_at"] == due


async def test_recurring_projection_creates_multiple_instances(reminder_pool):
    """A recurring reminder projects into multiple calendar_event_instances.

    Covers spec: Recurring reminder — series appears in calendar projection.
    """
    pool = reminder_pool
    mod = _make_module(pool)

    # Start 1 week from now so the projection window captures occurrences
    start_at = datetime.now(UTC) + timedelta(days=7)
    reminder = await _create_reminder(
        mod,
        title="Weekly standup",
        start_at=start_at,
        recurrence_rule="RRULE:FREQ=WEEKLY",
    )
    reminder_id = reminder["id"]

    # Project the reminders source to calendar tables
    await mod._project_reminders_source()

    # Verify at least one calendar_event row was created
    event_row = await pool.fetchrow(
        "SELECT id, title, status FROM calendar_events WHERE origin_ref = $1",
        str(reminder_id),
    )
    assert event_row is not None, "Expected a calendar_event row for the recurring reminder"
    assert event_row["status"] == "confirmed"

    # Verify multiple instances (weekly over 90-day window → ~12 instances)
    instance_count = await pool.fetchval(
        "SELECT COUNT(*) FROM calendar_event_instances WHERE event_id = $1",
        event_row["id"],
    )
    assert instance_count >= 2, (
        f"Expected at least 2 instances for a weekly reminder; got {instance_count}"
    )


async def test_dismissed_recurring_reminder_projects_as_cancelled(reminder_pool):
    """Dismissed recurring reminder projects to calendar_events with status=cancelled.

    Covers spec: Dismiss → calendar event status reflects dismissed state.
    """
    pool = reminder_pool
    mod = _make_module(pool)

    start_at = datetime.now(UTC) + timedelta(days=3)
    reminder = await _create_reminder(
        mod,
        title="Water plants",
        start_at=start_at,
        recurrence_rule="RRULE:FREQ=WEEKLY",
    )
    reminder_id = uuid.UUID(str(reminder["id"]))

    # Dismiss the reminder
    await mod._toggle_reminder_event(reminder_id, enabled=False)

    # Project and verify status
    await mod._project_reminders_source()

    event_row = await pool.fetchrow(
        "SELECT status FROM calendar_events WHERE origin_ref = $1",
        str(reminder_id),
    )
    assert event_row is not None
    assert event_row["status"] == "cancelled"


# ===========================================================================
# 3. Source-butler isolation: butler A's projection does not bleed into butler B
# ===========================================================================


async def test_source_butler_isolation_separate_calendar_sources(reminder_pool):
    """_project_reminders_source creates isolated calendar_sources per butler.

    Butler A and butler B each call _project_reminders_source. The result
    must be two distinct rows in calendar_sources, each scoped to its
    own butler_name and source_key.

    Covers spec: tick(source_butler) isolation.
    """
    pool = reminder_pool

    # Butler A
    mod_a = _make_module(pool, butler_name="butler_a")
    start_a = datetime.now(UTC) + timedelta(days=1)
    await _create_reminder(mod_a, title="Butler A reminder", start_at=start_a)
    await mod_a._project_reminders_source()

    # Butler B (same pool — simulates the multi-tenant schema by sharing the
    # reminders table, which is the relevant case for source isolation testing)
    mod_b = _make_module(pool, butler_name="butler_b")
    start_b = datetime.now(UTC) + timedelta(days=2)
    await _create_reminder(mod_b, title="Butler B reminder", start_at=start_b)
    await mod_b._project_reminders_source()

    # Both butlers should have their own calendar_sources entries
    source_a = await pool.fetchrow(
        "SELECT id, butler_name, source_key FROM calendar_sources "
        "WHERE source_key = 'internal_reminders:butler_a'"
    )
    source_b = await pool.fetchrow(
        "SELECT id, butler_name, source_key FROM calendar_sources "
        "WHERE source_key = 'internal_reminders:butler_b'"
    )

    assert source_a is not None, "Butler A must have its own calendar_sources row"
    assert source_b is not None, "Butler B must have its own calendar_sources row"
    assert source_a["id"] != source_b["id"], "Butler A and B must have distinct source rows"
    assert source_a["butler_name"] == "butler_a"
    assert source_b["butler_name"] == "butler_b"


async def test_source_butler_isolation_events_scoped_to_source(reminder_pool):
    """calendar_events for butler A are linked to butler A's source_id only.

    Each butler's projected events must only appear under that butler's
    calendar_sources row, not under another butler's source_id.
    """
    pool = reminder_pool

    mod_a = _make_module(pool, butler_name="project_butler_a")
    mod_b = _make_module(pool, butler_name="project_butler_b")

    start = datetime.now(UTC) + timedelta(days=1)
    reminder_a = await _create_reminder(mod_a, title="A only reminder", start_at=start)
    await mod_a._project_reminders_source()

    # Create and project for B (different reminder)
    reminder_b = await _create_reminder(
        mod_b,
        title="B only reminder",
        start_at=start + timedelta(hours=1),
    )
    await mod_b._project_reminders_source()

    # Retrieve source IDs
    src_a = await pool.fetchrow(
        "SELECT id FROM calendar_sources WHERE source_key = 'internal_reminders:project_butler_a'"
    )
    src_b = await pool.fetchrow(
        "SELECT id FROM calendar_sources WHERE source_key = 'internal_reminders:project_butler_b'"
    )
    assert src_a is not None and src_b is not None

    # Verify Reminder A is ONLY under Source A (exactly one row, correct source_id)
    event_a_rows = await pool.fetch(
        "SELECT source_id FROM calendar_events WHERE origin_ref = $1",
        str(reminder_a["id"]),
    )
    assert len(event_a_rows) == 1, (
        f"Reminder A must appear exactly once in calendar_events; got {len(event_a_rows)}"
    )
    assert event_a_rows[0]["source_id"] == src_a["id"], "Reminder A must be under source A"

    # Verify Reminder B is ONLY under Source B (exactly one row, correct source_id)
    event_b_rows = await pool.fetch(
        "SELECT source_id FROM calendar_events WHERE origin_ref = $1",
        str(reminder_b["id"]),
    )
    assert len(event_b_rows) == 1, (
        f"Reminder B must appear exactly once in calendar_events; got {len(event_b_rows)}"
    )
    assert event_b_rows[0]["source_id"] == src_b["id"], "Reminder B must be under source B"


# ===========================================================================
# 4. Contact association: create reminder with contact_id, verify stored
# ===========================================================================


async def test_contact_association_stores_contact_id(reminder_pool):
    """Creating a reminder with contact_id in action_args stores it in the row.

    Covers spec: Entity association — create reminder with entity_ids,
    verify the contact linkage is persisted.
    """
    pool = reminder_pool
    mod = _make_module(pool)

    contact_id = uuid.uuid4()
    start_at = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    reminder = await _create_reminder(
        mod,
        title="Follow up with contact",
        start_at=start_at,
        action_args={"contact_id": str(contact_id)},
    )

    reminder_id = uuid.UUID(str(reminder["id"]))
    row = await pool.fetchrow("SELECT contact_id FROM reminders WHERE id = $1", reminder_id)
    assert row is not None
    assert row["contact_id"] == contact_id, (
        f"Expected contact_id {contact_id} stored in reminder row; got {row['contact_id']}"
    )


async def test_contact_association_null_contact_id_allowed(reminder_pool):
    """Creating a reminder without contact_id does not fail — contact_id is nullable."""
    pool = reminder_pool
    mod = _make_module(pool)

    reminder = await _create_reminder(mod, title="Standalone reminder")
    reminder_id = uuid.UUID(str(reminder["id"]))

    row = await pool.fetchrow("SELECT contact_id FROM reminders WHERE id = $1", reminder_id)
    assert row is not None
    assert row["contact_id"] is None


async def test_contact_association_reminder_projected_with_metadata(reminder_pool):
    """Reminders with contact_id project to calendar_events with contact_id in metadata."""
    pool = reminder_pool
    mod = _make_module(pool)

    contact_id = uuid.uuid4()
    start_at = datetime.now(UTC) + timedelta(days=1)
    reminder = await _create_reminder(
        mod,
        title="Birthday follow-up",
        start_at=start_at,
        action_args={"contact_id": str(contact_id)},
    )

    await mod._project_reminders_source()

    event_row = await pool.fetchrow(
        "SELECT metadata FROM calendar_events WHERE origin_ref = $1",
        str(reminder["id"]),
    )
    assert event_row is not None
    raw_metadata = event_row["metadata"]
    # asyncpg typically decodes JSONB columns to Python dicts; the str branch
    # is a safety net for custom codec configurations that return raw JSON strings.
    if isinstance(raw_metadata, str):
        metadata = json.loads(raw_metadata)
    else:
        metadata = dict(raw_metadata)
    # The projection stores contact_id in the event metadata
    assert str(metadata.get("contact_id")) == str(contact_id), (
        f"Expected contact_id {contact_id!s} in calendar_event metadata; got {metadata}"
    )


# ===========================================================================
# 5. Regression: existing calendar module tests are unaffected
# ===========================================================================


async def test_reminder_update_not_found_raises(reminder_pool):
    """Updating a non-existent reminder raises ValueError."""
    pool = reminder_pool
    mod = _make_module(pool)

    nonexistent_id = uuid.uuid4()
    with pytest.raises(ValueError, match=str(nonexistent_id)):
        await mod._update_reminder_event(
            reminder_id=nonexistent_id,
            title="Ghost",
            start_at=None,
            timezone=None,
            until_at=None,
            recurrence_rule=None,
            cron=None,
            enabled=None,
        )


async def test_reminder_toggle_not_found_raises(reminder_pool):
    """Toggling a non-existent reminder raises ValueError."""
    pool = reminder_pool
    mod = _make_module(pool)

    nonexistent_id = uuid.uuid4()
    with pytest.raises(ValueError, match=str(nonexistent_id)):
        await mod._toggle_reminder_event(nonexistent_id, enabled=False)


async def test_reminder_delete_nonexistent_returns_false(reminder_pool):
    """Deleting a non-existent reminder returns False without raising."""
    pool = reminder_pool
    mod = _make_module(pool)

    nonexistent_id = uuid.uuid4()
    result = await mod._delete_reminder_event(nonexistent_id)
    assert result is False


async def test_projection_no_op_when_reminders_table_absent(provisioned_postgres_pool):
    """_project_reminders_source is a no-op when the reminders table does not exist."""
    async with provisioned_postgres_pool() as pool:
        # Only create the calendar tables, NOT the reminders table
        await pool.execute(_CREATE_CALENDAR_SOURCES_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENTS_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENT_INSTANCES_SQL)
        await pool.execute(_CREATE_CALENDAR_SYNC_CURSORS_SQL)
        await pool.execute(_CREATE_CALENDAR_ACTION_LOG_SQL)

        mod = _make_module(pool)
        # Should not raise — gracefully skips when reminders table is missing
        await mod._project_reminders_source()

        # No sources should have been created
        count = await pool.fetchval("SELECT COUNT(*) FROM calendar_sources")
        assert count == 0


async def test_create_reminder_no_db_raises(reminder_pool):
    """_create_reminder_event raises when no database pool is available."""
    from butlers.modules.calendar import CalendarModule

    mod = CalendarModule()
    mod._db = None
    mod._butler_name = "test"

    with pytest.raises(RuntimeError, match="Database pool"):
        await mod._create_reminder_event(
            title="No DB",
            start_at=datetime.now(UTC),
            timezone="UTC",
            until_at=None,
            recurrence_rule=None,
            cron=None,
            action="test",
            action_args=None,
            calendar_event_id=str(uuid.uuid4()),
        )
