"""Integration: scheduled-task Google mirror keeps its truth (bu-5ii4gj.4 slice B).

Real PostgreSQL. A butler-authored task keeps its link id apart from the provider id, a failed
push or delete is recorded on the row and retried, and an unchanged task sends nothing.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from butlers.core.scheduler.crud import schedule_create
from butlers.modules.calendar import CalendarEvent, CalendarEventCreate, CalendarProvider
from butlers.modules.calendar.errors import CalendarRequestError
from butlers.modules.calendar.sync import read_mirror_health

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


class _Provider(CalendarProvider):
    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self.creates = 0
        self.updates: list[str] = []
        self.deletes: list[str] = []
        self.fail_create: Exception | None = None
        self.fail_update: Exception | None = None
        self.fail_delete: Exception | None = None

    @property
    def name(self) -> str:
        return "google"

    async def create_event(self, *, calendar_id, payload: CalendarEventCreate):
        if self.fail_create is not None:
            raise self.fail_create
        self.creates += 1
        event_id = f"g{uuid.uuid4().hex[:24]}"
        event = CalendarEvent(
            event_id=event_id,
            title=payload.title,
            start_at=payload.start_at,
            end_at=payload.end_at,
            timezone=payload.timezone or "UTC",
        )
        self.events[event_id] = event
        return event

    async def update_event(self, *, calendar_id, event_id, patch):
        if self.fail_update is not None:
            raise self.fail_update
        self.updates.append(event_id)
        return self.events[event_id]

    async def delete_event(self, *, calendar_id, event_id):
        if self.fail_delete is not None:
            raise self.fail_delete
        self.deletes.append(event_id)
        self.events.pop(event_id, None)

    async def list_events(self, **_kw):
        return list(self.events.values())

    async def get_event(self, *, calendar_id, event_id):
        return self.events.get(event_id)

    async def add_attendees(self, **_kw):
        raise NotImplementedError

    async def remove_attendees(self, **_kw):
        raise NotImplementedError

    async def get_free_busy(self, **_kw):
        return []

    async def find_conflicts(self, **_kw):
        return []

    async def sync_incremental(self, **_kw):
        return [], [], "token"

    async def shutdown(self):
        return None


def _module(pool, provider):
    from butlers.modules.calendar import CalendarModule

    mod = CalendarModule()
    mod._db = SimpleNamespace(pool=pool, db_schema="general", db_name="butlers")
    mod._butler_name = "general"
    mod._provider = provider
    mod._resolved_calendar_id = "butlers-calendar"
    return mod


async def _task(pool, *, link: str | None = None) -> uuid.UUID:
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    return await schedule_create(
        pool,
        f"task-{uuid.uuid4().hex[:8]}",
        "0 9 * * *",
        "Do the thing",
        start_at=start,
        end_at=start + timedelta(minutes=30),
        timezone="UTC",
        display_title="Synthetic task",
        calendar_link_id=link,
    )


async def _row(pool, task_id):
    return await pool.fetchrow(
        "SELECT calendar_event_id, calendar_link_id, mirror_state, mirror_error, mirror_attempts, "
        "enabled FROM scheduled_tasks WHERE id = $1",
        task_id,
    )


async def test_a_butler_authored_task_is_mirrored_and_keeps_its_link(db_pool):
    link = str(uuid.uuid4())
    task_id = await _task(db_pool, link=link)
    provider = _Provider()
    mod = _module(db_pool, provider)

    await mod._push_internal_events_to_provider()

    row = await _row(db_pool, task_id)
    assert provider.creates == 1, "the local link id must not be mistaken for a provider id"
    assert row["calendar_event_id"] in provider.events
    assert row["calendar_link_id"] == link
    assert row["mirror_state"] == "mirrored"
    # The caller's handle and the provider id both still resolve the task.
    assert await mod._find_scheduled_task_target(link) == task_id
    assert await mod._find_scheduled_task_target(row["calendar_event_id"]) == task_id


async def test_a_failed_create_is_recorded_then_retried_not_erased(db_pool):
    task_id = await _task(db_pool, link=str(uuid.uuid4()))
    provider = _Provider()
    provider.fail_create = RuntimeError("quota exceeded")
    mod = _module(db_pool, provider)

    await mod._push_internal_events_to_provider()
    row = await _row(db_pool, task_id)
    assert row["mirror_state"] == "failed"
    assert "quota exceeded" in row["mirror_error"]
    assert row["mirror_attempts"] == 1
    assert row["calendar_event_id"] is None
    health = await read_mirror_health(db_pool)
    assert health["failed"] == 1 and health["retiring"] == 0

    provider.fail_create = None
    await mod._push_internal_events_to_provider()
    row = await _row(db_pool, task_id)
    assert row["mirror_state"] == "mirrored"
    assert row["mirror_error"] is None and row["mirror_attempts"] == 0
    assert (await read_mirror_health(db_pool))["failed"] == 0


async def test_an_unchanged_mirror_sends_no_update_and_a_change_sends_one(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    await mod._push_internal_events_to_provider()
    assert provider.updates == []

    await db_pool.execute(
        "UPDATE scheduled_tasks SET display_title = 'Renamed' WHERE id = $1", task_id
    )
    await mod._push_internal_events_to_provider()
    assert len(provider.updates) == 1


async def test_a_failed_update_is_recorded_and_the_provider_id_kept(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    provider_id = (await _row(db_pool, task_id))["calendar_event_id"]
    await db_pool.execute(
        "UPDATE scheduled_tasks SET display_title = 'Renamed' WHERE id = $1", task_id
    )

    provider.fail_update = RuntimeError("503")
    await mod._push_internal_events_to_provider()
    row = await _row(db_pool, task_id)
    assert row["mirror_state"] == "failed" and row["calendar_event_id"] == provider_id

    provider.fail_update = None
    await mod._push_internal_events_to_provider()
    assert (await _row(db_pool, task_id))["mirror_state"] == "mirrored"


async def test_a_missing_provider_copy_is_created_again(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    gone = (await _row(db_pool, task_id))["calendar_event_id"]
    provider.events.pop(gone)
    await db_pool.execute(
        "UPDATE scheduled_tasks SET display_title = 'Renamed' WHERE id = $1", task_id
    )
    provider.fail_update = CalendarRequestError(status_code=404, message="not found")

    await mod._push_internal_events_to_provider()

    row = await _row(db_pool, task_id)
    assert provider.creates == 2
    assert row["calendar_event_id"] != gone and row["mirror_state"] == "mirrored"


async def test_a_failed_delete_of_a_disabled_task_keeps_the_link_and_retries(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    provider_id = (await _row(db_pool, task_id))["calendar_event_id"]
    await db_pool.execute("UPDATE scheduled_tasks SET enabled = false WHERE id = $1", task_id)

    provider.fail_delete = RuntimeError("503")
    await mod._push_internal_events_to_provider()
    row = await _row(db_pool, task_id)
    assert row["calendar_event_id"] == provider_id, "the ghost must stay addressable"
    assert row["mirror_state"] == "retiring"
    assert provider_id in provider.events
    assert (await read_mirror_health(db_pool))["retiring"] == 1

    provider.fail_delete = None
    await mod._push_internal_events_to_provider()
    row = await _row(db_pool, task_id)
    assert row["calendar_event_id"] is None and row["mirror_state"] == "pending"
    assert provider_id not in provider.events


async def test_failing_mirrors_make_the_internal_source_report_failed(db_pool):
    await _task(db_pool)
    provider = _Provider()
    provider.fail_create = RuntimeError("boom")
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    source_id = await db_pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, metadata) VALUES ('internal:sched', 'internal_scheduler', "
        "'butler', 'internal', 'internal', 'Scheduled tasks', false, '{}') RETURNING id"
    )
    assert source_id is not None
    freshness = await mod._projection_freshness_metadata()
    internal = [s for s in freshness["sources"] if s["source_kind"] == "internal_scheduler"]
    assert internal and internal[0]["sync_state"] == "failed"
    assert "calendar mirror(s) failing" in internal[0]["last_error"]
    assert freshness["mirror"]["failed"] == 1
