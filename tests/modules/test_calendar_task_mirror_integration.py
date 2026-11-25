"""Integration: scheduled-task Google mirror keeps its truth (bu-5ii4gj.4 slice B).

Real PostgreSQL. A butler-authored task keeps its link id apart from the provider id, a failed
push or delete is recorded on the row and retried, and an unchanged task sends nothing.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from butlers.core.scheduler.crud import schedule_create, schedule_delete, schedule_list
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
        event_id = payload.event_id or f"g{uuid.uuid4().hex[:24]}"
        if event_id in self.events:
            raise CalendarRequestError(status_code=409, message="duplicate")
        self.creates += 1
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


async def _age(pool, task_id, *, seconds: int = 7200) -> None:
    """Move the last attempt into the past so the retry backoff has elapsed."""
    await pool.execute(
        "UPDATE scheduled_tasks SET mirror_attempted_at = now() - make_interval(secs => $2) "
        "WHERE id = $1",
        task_id,
        float(seconds),
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
    await _age(db_pool, task_id)
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
    await _age(db_pool, task_id)
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
    assert row["calendar_event_id"] in provider.events and row["mirror_state"] == "mirrored"


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
    await _age(db_pool, task_id)
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


async def _exists(pool, task_id) -> bool:
    return await pool.fetchval("SELECT count(*) FROM scheduled_tasks WHERE id = $1", task_id) == 1


# ---------------------------------------------------------------------------
# Deleting a mirrored task retires it instead of stranding a provider event
# ---------------------------------------------------------------------------


async def test_deleting_a_mirrored_task_retires_it_then_the_mirror_purges_it(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    provider_id = (await _row(db_pool, task_id))["calendar_event_id"]

    await schedule_delete(db_pool, task_id)

    row = await _row(db_pool, task_id)
    assert row["mirror_state"] == "retiring" and row["enabled"] is False
    assert provider_id in provider.events, "the provider copy is deleted by the mirror, not here"
    assert task_id not in [t["id"] for t in await schedule_list(db_pool)]

    await mod._push_internal_events_to_provider()
    assert provider_id not in provider.events
    assert not await _exists(db_pool, task_id)


async def test_a_failed_provider_delete_keeps_the_retired_row_until_it_succeeds(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    provider_id = (await _row(db_pool, task_id))["calendar_event_id"]
    await schedule_delete(db_pool, task_id)

    provider.fail_delete = RuntimeError("503")
    await mod._push_internal_events_to_provider()
    assert (await _row(db_pool, task_id))["mirror_state"] == "retiring"
    assert (await read_mirror_health(db_pool))["retiring"] == 1

    provider.fail_delete = CalendarRequestError(status_code=410, message="gone")
    await _age(db_pool, task_id)
    await mod._push_internal_events_to_provider()
    assert not await _exists(db_pool, task_id), "410 means the copy is already gone"
    assert provider_id in provider.events  # the double kept it; the row is purged regardless


async def test_a_task_that_was_never_mirrored_is_deleted_outright(db_pool):
    task_id = await _task(db_pool)
    await schedule_delete(db_pool, task_id)
    assert not await _exists(db_pool, task_id)


async def test_a_retired_task_gives_its_name_back(db_pool):
    task_id = await _task(db_pool)
    name = await db_pool.fetchval("SELECT name FROM scheduled_tasks WHERE id = $1", task_id)
    provider = _Provider()
    await _module(db_pool, provider)._push_internal_events_to_provider()
    await schedule_delete(db_pool, task_id)
    start = datetime.now(UTC) + timedelta(days=2)
    await schedule_create(db_pool, name, "0 9 * * *", "Again", start_at=start, timezone="UTC")


async def test_a_deadline_with_a_mirror_is_retired_not_dropped(db_pool):
    from butlers.core.temporal.deadlines_db import deadline_delete

    task_id = await _task(db_pool)
    await db_pool.execute(
        "UPDATE scheduled_tasks SET task_type = 'deadline' WHERE id = $1", task_id
    )
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    assert (await _row(db_pool, task_id))["calendar_event_id"]

    await deadline_delete(db_pool, task_id)

    assert (await _row(db_pool, task_id))["mirror_state"] == "retiring"
    await mod._push_internal_events_to_provider()
    assert not await _exists(db_pool, task_id) and provider.events == {}


async def test_a_task_deleted_while_its_create_is_in_flight_leaves_no_ghost(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    real_create = provider.create_event

    async def create_then_owner_deletes(**kw):
        event = await real_create(**kw)
        await schedule_delete(db_pool, task_id)  # attempted_at is stamped: retired, not purged
        return event

    provider.create_event = create_then_owner_deletes
    await mod._push_internal_events_to_provider()
    await mod._push_internal_events_to_provider()
    assert not await _exists(db_pool, task_id) and provider.events == {}


# ---------------------------------------------------------------------------
# Idempotent creates
# ---------------------------------------------------------------------------


async def test_overlapping_passes_create_one_provider_event(db_pool):
    await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)

    await asyncio.gather(
        mod._push_internal_events_to_provider(), mod._push_internal_events_to_provider()
    )

    assert provider.creates == 1 and len(provider.events) == 1


async def test_the_provider_id_is_derived_from_the_task(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    await _module(db_pool, provider)._push_internal_events_to_provider()
    assert (await _row(db_pool, task_id))["calendar_event_id"] == task_id.hex


async def test_a_crash_after_the_create_adopts_the_event_instead_of_duplicating(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    real_done = mod._mirror_done

    async def crash(*_a, **_kw):
        raise RuntimeError("process died before the row was written")

    mod._mirror_done = crash
    await mod._push_internal_events_to_provider()
    assert provider.creates == 1 and (await _row(db_pool, task_id))["calendar_event_id"] is None

    mod._mirror_done = real_done
    await mod._push_internal_events_to_provider()

    row = await _row(db_pool, task_id)
    assert provider.creates == 1, "the retry got a 409 and adopted the existing event"
    assert row["calendar_event_id"] == task_id.hex and row["mirror_state"] == "mirrored"
    assert len(provider.events) == 1


async def test_one_row_failing_to_record_does_not_stop_the_others(db_pool):
    first = await _task(db_pool)
    second = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    real_done = mod._mirror_done
    calls = 0

    async def flaky(pool, task_id, *rest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("write failed")
        return await real_done(pool, task_id, *rest)

    mod._mirror_done = flaky
    await mod._push_internal_events_to_provider()

    states = {(await _row(db_pool, t))["mirror_state"] for t in (first, second)}
    assert states == {"pending", "mirrored"}


# ---------------------------------------------------------------------------
# Backoff, parking and log volume
# ---------------------------------------------------------------------------


async def test_a_failing_row_is_not_retried_before_its_backoff_elapses(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    provider.fail_create = RuntimeError("quota")
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    attempts_seen = (await _row(db_pool, task_id))["mirror_attempts"]

    attempts = 0
    real_create = provider.create_event

    async def counting(**kw):
        nonlocal attempts
        attempts += 1
        return await real_create(**kw)

    provider.create_event = counting
    await mod._push_internal_events_to_provider()
    assert attempts == 0 and (await _row(db_pool, task_id))["mirror_attempts"] == attempts_seen

    await _age(db_pool, task_id)
    await mod._push_internal_events_to_provider()
    assert attempts == 1  # retried once the wait had passed


async def test_the_backoff_grows_and_is_capped(db_pool):
    wait = _module(db_pool, _Provider())._mirror_backoff_wait
    assert wait(1) < wait(2) < wait(3)
    assert wait(50) == wait(40) == timedelta(hours=1)


async def test_a_row_parks_as_failed_after_repeated_attempts_until_what_is_sent_changes(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    provider.fail_create = RuntimeError("boom")
    mod = _module(db_pool, provider)
    for _ in range(8):
        await mod._push_internal_events_to_provider()
        await _age(db_pool, task_id)

    provider.fail_create = None
    await mod._push_internal_events_to_provider()
    assert provider.creates == 0, "parked rows are not retried"
    assert (await read_mirror_health(db_pool))["failed"] == 1, "but they still count as failed"

    # A dispatch run bumps updated_at on every tick; that is not an edit and must not unpark.
    await db_pool.execute("UPDATE scheduled_tasks SET updated_at = now() WHERE id = $1", task_id)
    await mod._push_internal_events_to_provider()
    assert provider.creates == 0

    await db_pool.execute(
        "UPDATE scheduled_tasks SET display_title = 'Edited' WHERE id = $1", task_id
    )
    await mod._push_internal_events_to_provider()
    assert provider.creates == 1


async def test_a_repeating_failure_warns_once_not_every_pass(db_pool, caplog):
    task_id = await _task(db_pool)
    provider = _Provider()
    provider.fail_create = RuntimeError("quota")
    mod = _module(db_pool, provider)
    with caplog.at_level(logging.DEBUG, logger="butlers.modules.calendar"):
        await mod._push_internal_events_to_provider()
        for _ in range(3):
            await _age(db_pool, task_id)
            await mod._push_internal_events_to_provider()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "mirror" in r.message]
    assert len(warnings) == 1


async def test_a_gone_provider_copy_on_a_disabled_task_counts_as_deleted(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    await db_pool.execute("UPDATE scheduled_tasks SET enabled = false WHERE id = $1", task_id)
    provider.fail_delete = CalendarRequestError(status_code=410, message="gone")

    await mod._push_internal_events_to_provider()

    row = await _row(db_pool, task_id)
    assert row["calendar_event_id"] is None and row["mirror_state"] == "pending"


async def test_the_dashboard_source_freshness_reports_failing_mirrors(db_pool):

    from butlers.api.models.calendar_workspace import CalendarWorkspaceSourceFreshness
    from butlers.api.routers.calendar_workspace import _with_mirror_health

    await _task(db_pool)
    provider = _Provider()
    provider.fail_create = RuntimeError("boom")
    await _module(db_pool, provider)._push_internal_events_to_provider()

    class _Db:
        def butlers_with_module(self, _name):
            return ["general"]

        async def fan_out_with_status(self, sql, args=(), butler_names=None):
            return {"general": await db_pool.fetch(sql, *args)}, []

    item = CalendarWorkspaceSourceFreshness(
        source_id=uuid.uuid4(),
        source_key="internal:sched",
        source_kind="internal_scheduler",
        lane="butler",
        butler_name="general",
        sync_state="fresh",
    )
    (shown,) = await _with_mirror_health(_Db(), [item], butlers=None)

    assert shown.sync_state == "failed" and shown.mirror_failed == 1


async def test_a_retired_task_is_not_projected_as_an_event(db_pool):
    task_id = await _task(db_pool)
    # Without a display title the projection falls back to the task name.
    await db_pool.execute("UPDATE scheduled_tasks SET display_title = NULL WHERE id = $1", task_id)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    await mod._project_internal_sources(emit_event=False)
    assert await db_pool.fetchval("SELECT count(*) FROM calendar_events") >= 1

    await schedule_delete(db_pool, task_id)
    await mod._project_internal_sources(emit_event=False)

    assert (
        await db_pool.fetchval("SELECT count(*) FROM calendar_events WHERE title ILIKE '%deleted%'")
        == 0
    ), "the retired row's renamed task must not surface as an event"


async def test_deleting_a_mirrored_event_through_the_tool_retires_then_purges(db_pool, monkeypatch):
    from fastmcp import FastMCP

    from butlers.testing.mcp import tool_fn

    # The delete is a high-impact action that normally runs once approved; model that execution.
    monkeypatch.setattr(
        "butlers.modules.calendar.recurrence.get_approval_execution_context",
        lambda **_kw: object(),
    )
    task_id = await _task(db_pool, link=str(uuid.uuid4()))
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    provider_id = (await _row(db_pool, task_id))["calendar_event_id"]
    mcp = FastMCP("calendar-delete-e2e")
    await mod.register_tools(
        mcp=mcp,
        config={"provider": "google", "calendar_id": "butlers-calendar"},
        db=mod._db,
        butler_name="general",
    )
    mod._provider = provider
    mod._resolved_calendar_id = "butlers-calendar"
    provider.fail_delete = RuntimeError("503")  # the tool's own refresh pass cannot delete yet

    result = await (await tool_fn(mcp, "calendar_delete_butler_event"))(
        event_id=str(task_id), source_hint="scheduled_task"
    )

    assert result["status"] == "deleted", result
    row = await _row(db_pool, task_id)
    assert row["mirror_state"] == "retiring" and provider_id in provider.events

    provider.fail_delete = None
    await _age(db_pool, task_id)
    await mod._push_internal_events_to_provider()
    assert provider_id not in provider.events
    assert not await _exists(db_pool, task_id)


async def test_a_parked_row_that_is_then_disabled_still_gets_its_provider_copy_deleted(db_pool):
    task_id = await _task(db_pool)
    provider = _Provider()
    mod = _module(db_pool, provider)
    await mod._push_internal_events_to_provider()
    provider_id = (await _row(db_pool, task_id))["calendar_event_id"]
    assert provider_id in provider.events
    await db_pool.execute(
        "UPDATE scheduled_tasks SET mirror_state = 'failed', mirror_attempts = 8, "
        "mirror_error = 'boom', mirror_attempt_fingerprint = 'stale', "
        "mirror_attempted_at = now() - interval '1 day' WHERE id = $1",
        task_id,
    )

    await db_pool.execute("UPDATE scheduled_tasks SET enabled = false WHERE id = $1", task_id)
    await mod._push_internal_events_to_provider()

    assert provider_id not in provider.events, "a parked row must not strand its ghost event"
    assert (await _row(db_pool, task_id))["calendar_event_id"] is None


async def test_a_cron_task_stays_parked_as_its_next_occurrence_moves(db_pool, monkeypatch):
    task_id = await schedule_create(
        db_pool, f"cron-{uuid.uuid4().hex[:8]}", "0 9 * * *", "Do the thing", timezone="UTC"
    )
    provider = _Provider()
    provider.fail_create = RuntimeError("boom")
    mod = _module(db_pool, provider)
    occurrence = [datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)]
    monkeypatch.setattr(
        "butlers.modules.calendar.internal_projection._cron_next_occurrence",
        lambda *_a, **_kw: occurrence[0],
    )
    for _ in range(8):
        await mod._push_internal_events_to_provider()
        await _age(db_pool, task_id)

    provider.fail_create = None
    for _ in range(3):
        occurrence[0] += timedelta(days=1)  # each run derives a later next occurrence
        await mod._push_internal_events_to_provider()
        await _age(db_pool, task_id)
    assert provider.creates == 0, "a moving occurrence is not an edit"
    assert (await read_mirror_health(db_pool))["failed"] == 1

    await db_pool.execute("UPDATE scheduled_tasks SET cron = '0 10 * * *' WHERE id = $1", task_id)
    await mod._push_internal_events_to_provider()
    assert provider.creates == 1
