"""Integration tests — calendar-native reminder lifecycle.

These tests exercise CalendarModule native reminder methods directly against a
real PostgreSQL database (testcontainers), including provider mirroring to the
dedicated Butlers calendar.

[bu-hws35]
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.modules.calendar import (
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarProvider,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# SQL helpers — minimal native calendar schema for reminder lifecycle tests
# ---------------------------------------------------------------------------

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
    body TEXT,
    location TEXT,
    timezone TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    all_day BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'confirmed',
    visibility TEXT NOT NULL DEFAULT 'default',
    recurrence_rule TEXT,
    source_butler TEXT NOT NULL DEFAULT 'unknown',
    source_session_id TEXT,
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

_CREATE_CALENDAR_EVENT_ENTITIES_SQL = """
CREATE TABLE IF NOT EXISTS calendar_event_entities (
    event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL,
    PRIMARY KEY (event_id, entity_id)
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
    """Fresh DB with the native calendar projection tables."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_CALENDAR_SOURCES_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENTS_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENT_INSTANCES_SQL)
        await pool.execute(_CREATE_CALENDAR_EVENT_ENTITIES_SQL)
        await pool.execute(_CREATE_CALENDAR_SYNC_CURSORS_SQL)
        await pool.execute(_CREATE_CALENDAR_ACTION_LOG_SQL)
        yield pool


class _StubMCP:
    """Minimal MCP stub that captures registered tools by function name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def _make_module(pool, *, butler_name: str = "relationship") -> object:
    """Return a CalendarModule wired to a real pool, skipping startup."""
    from butlers.modules.calendar import CalendarModule

    mod = CalendarModule()
    db = SimpleNamespace(pool=pool, db_schema=butler_name, db_name="butlers")
    mod._db = db
    mod._butler_name = butler_name
    return mod


class _ReminderProviderDouble(CalendarProvider):
    """Stateful provider double for native reminder mirror reconciliation."""

    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self.create_calls: list[CalendarEventCreate] = []
        self.update_calls: list[tuple[str, CalendarEventUpdate]] = []
        self.delete_calls: list[str] = []

    @property
    def name(self) -> str:
        return "google"

    async def list_events(self, *, calendar_id, start_at=None, end_at=None, limit=50):
        return list(self.events.values())

    async def get_event(self, *, calendar_id, event_id):
        return self.events.get(event_id)

    async def create_event(self, *, calendar_id, payload):
        self.create_calls.append(payload)
        event_id = f"provider-{uuid.uuid4()}"
        event = CalendarEvent(
            event_id=event_id,
            title=payload.title,
            start_at=payload.start_at,
            end_at=payload.end_at,
            timezone=payload.timezone or "UTC",
            description=payload.description,
            location=payload.location,
            recurrence_rule=payload.recurrence_rule,
            butler_generated=True,
            butler_name=payload.private_metadata.get("butler_name"),
        )
        self.events[event_id] = event
        return event

    async def update_event(self, *, calendar_id, event_id, patch):
        self.update_calls.append((event_id, patch))
        existing = self.events[event_id]
        updated = existing.model_copy(
            update={
                key: value
                for key, value in {
                    "title": patch.title,
                    "start_at": patch.start_at,
                    "end_at": patch.end_at,
                    "timezone": patch.timezone,
                    "description": patch.description,
                    "location": patch.location,
                    "recurrence_rule": patch.recurrence_rule,
                }.items()
                if value is not None
            }
        )
        self.events[event_id] = updated
        return updated

    async def delete_event(self, *, calendar_id, event_id):
        self.delete_calls.append(event_id)
        self.events.pop(event_id, None)

    async def add_attendees(
        self, *, calendar_id, event_id, attendees, optional=False, send_updates="none"
    ):
        raise NotImplementedError

    async def remove_attendees(self, *, calendar_id, event_id, attendees, send_updates="none"):
        raise NotImplementedError

    async def get_free_busy(self, *, calendar_ids, start_at, end_at, timezone=None):
        return []

    async def find_conflicts(self, *, calendar_id, candidate):
        return []

    async def sync_incremental(self, *, calendar_id, sync_token, full_sync_window_days=30):
        return [], [], "token"

    async def shutdown(self):
        return None


async def test_calendar_native_butler_reminder_lifecycle(reminder_pool):
    """A native reminder can be resolved, updated, toggled, and deleted."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    end_at = start_at + timedelta(minutes=15)

    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Review renewal",
        body=None,
        description="Cancel if unused",
        location="Online",
        starts_at=start_at,
        ends_at=end_at,
        timezone="UTC",
        recurrence_rule=None,
        entity_ids=[],
    )

    assert await mod._find_native_reminder_target(str(event_id)) == event_id

    updated = await mod._update_native_reminder_event(
        reminder_id=event_id,
        title="Review subscription",
        body="Check the latest invoice",
        start_at=None,
        end_at=None,
        timezone=None,
        until_at=None,
        recurrence_rule=None,
        enabled=None,
    )
    assert updated["title"] == "Review subscription"
    assert updated["body"] == "Check the latest invoice"

    paused = await mod._toggle_native_reminder_event(event_id, enabled=False)
    assert paused["status"] == "cancelled"
    resumed = await mod._toggle_native_reminder_event(event_id, enabled=True)
    assert resumed["status"] == "confirmed"

    assert await mod._delete_native_reminder_event(event_id) is True
    assert await pool.fetchrow("SELECT id FROM calendar_events WHERE id = $1", event_id) is None


async def test_native_reminder_provider_mirror_is_durable_idempotent_and_orphan_safe(
    reminder_pool,
):
    """Native reminders create/update/delete one durable provider mirror."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    provider = _ReminderProviderDouble()
    mod._provider = provider
    mod._resolved_calendar_id = "butlers-calendar"

    starts_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Review insurance renewal",
        body="Compare the latest quote",
        description="Bring the renewal letter",
        location="Home office",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        timezone="Asia/Singapore",
        recurrence_rule="RRULE:FREQ=MONTHLY",
        entity_ids=[],
    )

    await mod._push_internal_events_to_provider()

    assert len(provider.create_calls) == 1
    created = provider.create_calls[0]
    assert created.description == "Bring the renewal letter"
    assert created.location == "Home office"
    assert created.recurrence_rule == "RRULE:FREQ=MONTHLY"
    provider_event_id = await pool.fetchval(
        "SELECT metadata->>'provider_event_id' FROM calendar_events WHERE id = $1",
        event_id,
    )
    assert provider_event_id is not None
    assert provider.delete_calls == []

    await mod._update_native_reminder_event(
        reminder_id=event_id,
        title="Review insurance options",
        body=None,
        start_at=None,
        end_at=None,
        timezone=None,
        until_at=None,
        recurrence_rule="RRULE:FREQ=YEARLY",
        enabled=None,
    )
    await mod._push_internal_events_to_provider()

    assert len(provider.create_calls) == 1
    assert len(provider.update_calls) == 1
    updated_id, patch = provider.update_calls[0]
    assert updated_id == provider_event_id
    assert patch.title == "Review insurance options"
    assert patch.description == "Bring the renewal letter"
    assert patch.location == "Home office"
    assert patch.recurrence_rule == "RRULE:FREQ=YEARLY"
    assert provider.delete_calls == []

    assert await mod._delete_native_reminder_event(event_id) is True
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM calendar_events WHERE id = $1",
            event_id,
        )
        == 0
    )
    assert provider.delete_calls == [provider_event_id]
    await mod._push_internal_events_to_provider()

    assert provider.delete_calls == [provider_event_id]


async def test_public_native_reminder_update_replaces_entity_links(reminder_pool):
    """The public update tool writes entity links against the native event ID."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    mcp = _StubMCP()
    await mod.register_tools(
        mcp=mcp,
        config={"provider": "google"},
        db=mod._db,
        butler_name="finance",
    )
    mod._prepare_workspace_mutation = AsyncMock(return_value=("test-key", None))
    mod._refresh_butler_projection = AsyncMock(return_value={"available": True})
    mod._finalize_workspace_mutation = AsyncMock()

    old_entity_id = uuid.uuid4()
    new_entity_id = uuid.uuid4()
    start_at = datetime.now(UTC) + timedelta(days=1)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Review renewal",
        body=None,
        starts_at=start_at,
        ends_at=start_at + timedelta(minutes=15),
        timezone="UTC",
        recurrence_rule=None,
        entity_ids=[old_entity_id],
    )

    result = await mcp.tools["calendar_update_butler_event"](
        event_id=str(event_id),
        title="Review subscription",
        entity_ids=[new_entity_id],
        source_hint="butler_reminder",
    )

    assert result["status"] == "updated"
    rows = await pool.fetch(
        """
        SELECT entity_id
        FROM calendar_event_entities
        WHERE event_id = $1
        ORDER BY entity_id
        """,
        event_id,
    )
    assert [row["entity_id"] for row in rows] == [new_entity_id]


async def test_native_recurring_reminder_refreshes_rolling_window(reminder_pool):
    """Create and periodic refresh both materialize multiple future instances."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime.now(UTC) + timedelta(days=1)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Weekly renewal review",
        body=None,
        starts_at=start_at,
        ends_at=start_at + timedelta(minutes=15),
        timezone="UTC",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        entity_ids=[],
    )

    initial_rows = await pool.fetch(
        """
        SELECT source_id, timezone
        FROM calendar_event_instances
        WHERE event_id = $1
        ORDER BY starts_at
        """,
        event_id,
    )
    assert len(initial_rows) >= 10
    assert all(row["source_id"] is not None for row in initial_rows)
    assert all(row["timezone"] == "UTC" for row in initial_rows)

    await pool.execute("DELETE FROM calendar_event_instances WHERE event_id = $1", event_id)
    mod._projection_tables_available_cache = True
    await mod._project_native_reminder_instances()

    refreshed_count = await pool.fetchval(
        "SELECT count(*) FROM calendar_event_instances WHERE event_id = $1",
        event_id,
    )
    assert refreshed_count >= 10


@pytest.mark.pg_clock
async def test_native_refresh_retains_and_dispatches_overdue_unnotified_instance(
    reminder_pool,
):
    """Projection refresh cannot drop an occurrence before tick delivers it."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Overdue renewal review",
        body=None,
        starts_at=start_at,
        ends_at=start_at + timedelta(minutes=15),
        timezone="UTC",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        entity_ids=[],
    )
    due_before_refresh = await pool.fetchval(
        """
        SELECT count(*)
        FROM calendar_event_instances
        WHERE event_id = $1
          AND starts_at <= now()
          AND status = 'confirmed'
          AND metadata->>'notified_at' IS NULL
        """,
        event_id,
    )
    assert due_before_refresh == 1

    mod._projection_tables_available_cache = True
    await mod._project_native_reminder_instances()

    due_after_refresh = await pool.fetchval(
        """
        SELECT count(*)
        FROM calendar_event_instances
        WHERE event_id = $1
          AND starts_at <= now()
          AND status = 'confirmed'
          AND metadata->>'notified_at' IS NULL
        """,
        event_id,
    )
    assert due_after_refresh == 1

    notify = AsyncMock()
    assert await mod.tick("finance", notify_fn=notify) == 1
    notify.assert_awaited_once()
    assert await mod.tick("finance", notify_fn=notify) == 0
    notify.assert_awaited_once()


async def test_native_recurring_update_preserves_cancelled_and_notified_instances(
    reminder_pool,
):
    """Changing recurrence replaces only future undispatched occurrence rows."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    end_at = start_at + timedelta(minutes=15)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Weekly renewal review",
        body=None,
        starts_at=start_at,
        ends_at=end_at,
        timezone="UTC",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        entity_ids=[],
    )
    instances = await pool.fetch(
        """
        SELECT id, starts_at
        FROM calendar_event_instances
        WHERE event_id = $1
        ORDER BY starts_at
        LIMIT 3
        """,
        event_id,
    )
    assert len(instances) == 3
    cancelled, notified, replaceable = instances
    await pool.execute(
        """
        UPDATE calendar_event_instances
        SET status = 'cancelled'
        WHERE id = $1
        """,
        cancelled["id"],
    )
    await pool.execute(
        """
        UPDATE calendar_event_instances
        SET metadata = metadata || '{"notified_at":"2026-01-01T00:00:00Z"}'::jsonb
        WHERE id = $1
        """,
        notified["id"],
    )

    shifted_start = start_at + timedelta(hours=1)
    await mod._update_native_reminder_event(
        reminder_id=event_id,
        title=None,
        body=None,
        start_at=shifted_start,
        end_at=end_at + timedelta(hours=1),
        timezone=None,
        until_at=None,
        recurrence_rule="RRULE:FREQ=DAILY",
        enabled=None,
    )

    cancelled_after = await pool.fetchrow(
        """
        SELECT status
        FROM calendar_event_instances
        WHERE id = $1
        """,
        cancelled["id"],
    )
    notified_after = await pool.fetchrow(
        """
        SELECT metadata->>'notified_at' AS notified_at
        FROM calendar_event_instances
        WHERE id = $1
        """,
        notified["id"],
    )
    assert cancelled_after["status"] == "cancelled"
    assert notified_after["notified_at"] == "2026-01-01T00:00:00Z"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM calendar_event_instances WHERE id = $1",
            replaceable["id"],
        )
        == 0
    )
    assert (
        await pool.fetchval(
            """
            SELECT count(*)
            FROM calendar_event_instances
            WHERE event_id = $1
              AND starts_at = $2
              AND status = 'confirmed'
            """,
            event_id,
            shifted_start,
        )
        == 1
    )


async def test_native_recurring_update_rolls_back_event_and_instances_on_refresh_failure(
    reminder_pool,
):
    """Event and occurrence replacement commit atomically."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    end_at = start_at + timedelta(minutes=15)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Weekly renewal review",
        body=None,
        starts_at=start_at,
        ends_at=end_at,
        timezone="UTC",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        entity_ids=[],
    )
    initial_count = await pool.fetchval(
        "SELECT count(*) FROM calendar_event_instances WHERE event_id = $1",
        event_id,
    )
    mod._materialize_native_reminder_instances = AsyncMock(
        side_effect=RuntimeError("synthetic materialization failure")
    )

    with pytest.raises(RuntimeError, match="synthetic materialization failure"):
        await mod._update_native_reminder_event(
            reminder_id=event_id,
            title="Changed title",
            body=None,
            start_at=start_at + timedelta(hours=1),
            end_at=end_at + timedelta(hours=1),
            timezone=None,
            until_at=None,
            recurrence_rule="RRULE:FREQ=DAILY",
            enabled=None,
        )

    event = await pool.fetchrow(
        "SELECT title, starts_at, recurrence_rule FROM calendar_events WHERE id = $1",
        event_id,
    )
    assert event["title"] == "Weekly renewal review"
    assert event["starts_at"] == start_at
    assert event["recurrence_rule"] == "RRULE:FREQ=WEEKLY"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM calendar_event_instances WHERE event_id = $1",
            event_id,
        )
        == initial_count
    )


async def test_native_recurring_delete_supports_this_and_following_scopes(
    reminder_pool,
):
    """Occurrence deletion keeps the series row and preserves earlier instances."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Weekly renewal review",
        body=None,
        starts_at=start_at,
        ends_at=start_at + timedelta(minutes=15),
        timezone="UTC",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        entity_ids=[],
    )
    instances = await pool.fetch(
        """
        SELECT id, starts_at
        FROM calendar_event_instances
        WHERE event_id = $1
        ORDER BY starts_at
        LIMIT 4
        """,
        event_id,
    )
    assert len(instances) == 4

    assert await mod._delete_native_reminder_event(
        event_id,
        scope="this",
        instance_start_at=instances[0]["starts_at"],
    )
    first = await pool.fetchrow(
        "SELECT status, is_exception FROM calendar_event_instances WHERE id = $1",
        instances[0]["id"],
    )
    second = await pool.fetchrow(
        "SELECT status, is_exception FROM calendar_event_instances WHERE id = $1",
        instances[1]["id"],
    )
    assert dict(first) == {"status": "cancelled", "is_exception": True}
    assert dict(second) == {"status": "confirmed", "is_exception": False}

    boundary = instances[2]["starts_at"]
    assert await mod._delete_native_reminder_event(
        event_id,
        scope="following",
        instance_start_at=boundary,
    )
    event = await pool.fetchrow(
        "SELECT recurrence_rule FROM calendar_events WHERE id = $1",
        event_id,
    )
    before_boundary = await pool.fetchrow(
        "SELECT status, is_exception FROM calendar_event_instances WHERE id = $1",
        instances[1]["id"],
    )
    following = await pool.fetch(
        """
        SELECT status, is_exception
        FROM calendar_event_instances
        WHERE event_id = $1 AND starts_at >= $2
        """,
        event_id,
        boundary,
    )
    assert "UNTIL=" in event["recurrence_rule"]
    assert dict(before_boundary) == {"status": "confirmed", "is_exception": False}
    assert following
    assert all(row["status"] == "cancelled" and row["is_exception"] for row in following)


async def test_native_following_delete_rejects_non_occurrence_boundary_without_mutation(
    reminder_pool,
):
    """An arbitrary timestamp cannot silently truncate a recurring reminder."""
    pool = reminder_pool
    mod = _make_module(pool, butler_name="finance")
    start_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    event_id, _ = await mod._insert_reminder_to_calendar_events(
        title="Weekly renewal review",
        body=None,
        starts_at=start_at,
        ends_at=start_at + timedelta(minutes=15),
        timezone="UTC",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        entity_ids=[],
    )

    assert (
        await mod._delete_native_reminder_event(
            event_id,
            scope="following",
            instance_start_at=start_at + timedelta(days=2),
        )
        is False
    )
    event = await pool.fetchrow(
        "SELECT recurrence_rule FROM calendar_events WHERE id = $1",
        event_id,
    )
    assert event["recurrence_rule"] == "RRULE:FREQ=WEEKLY"
    assert (
        await pool.fetchval(
            """
            SELECT count(*)
            FROM calendar_event_instances
            WHERE event_id = $1 AND status = 'cancelled'
            """,
            event_id,
        )
        == 0
    )
