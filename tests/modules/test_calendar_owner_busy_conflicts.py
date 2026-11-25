"""A write is overlap-checked against all owner time (bu-5ii4gj.4 slice A).

A Butlers block lands on the Butlers calendar, but the owner's meetings live on the primary
calendar, so a check of the target calendar alone reports "no conflict" over a real meeting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP

from butlers.modules.calendar import CalendarModule
from butlers.modules.calendar.errors import CalendarAuthError
from butlers.modules.calendar.replan import REPLAN_DECLINED
from butlers.testing.mcp import tool_fn
from tests.modules.test_calendar_replan import _block, _move
from tests.modules.test_module_calendar import _make_event, _ProviderDouble

pytestmark = pytest.mark.unit

BUTLERS = "butlers@group.calendar.google.com"
PRIMARY = "owner@example.com"
SHARED = "partner@example.com"
START = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)
END = datetime(2026, 2, 20, 17, 0, tzinfo=UTC)


class _ByCalendarProvider(_ProviderDouble):
    """Busy windows keyed by calendar_id, as a real free/busy is."""

    def __init__(self, busy_by_calendar: dict[str, list], *, fail: set[str] | None = None, **kw):
        super().__init__(**kw)
        self.busy_by_calendar = busy_by_calendar
        self.fail = fail or set()
        self.checked: list[str] = []

    async def find_conflicts(self, *, calendar_id, candidate):
        self.checked.append(calendar_id)
        if calendar_id in self.fail:
            raise RuntimeError("freebusy down")
        return list(self.busy_by_calendar.get(calendar_id, []))


def _meeting(event_id="evt-meeting"):
    return _make_event(event_id=event_id, title="Dentist", start_at=START, end_at=END)


def _pool(
    owner_ids: list[str],
    *,
    error: Exception | None = None,
    stored: list[tuple[datetime, datetime]] | None = None,
    synced_ago: timedelta | None = timedelta(minutes=1),
    synced_calendars: list[str] | None = None,
    disabled: set[str] | None = None,
):
    """A pool answering the role ledger, the sync-freshness read and the stored busy read."""
    pool = MagicMock()

    async def fetch(sql, *args):
        if error is not None:
            raise error
        if "time_role" in sql:
            return [
                {
                    "calendar_id": cid,
                    "metadata": {"sync_enabled": False} if cid in (disabled or ()) else {},
                }
                for cid in owner_ids
            ]
        if "last_success_at" in sql:
            others = synced_calendars if synced_calendars is not None else list(args[1])
            when = None if synced_ago is None else datetime.now(UTC) - synced_ago
            return [{"calendar_id": cid, "last_success_at": when} for cid in others]
        return [{"starts_at": a, "ends_at": b} for a, b in (stored or [])]

    pool.fetch = AsyncMock(side_effect=fetch)
    return pool


def _stored_read_args(pool) -> tuple:
    for call in pool.fetch.await_args_list:
        if "calendar_event_instances" in call.args[0]:
            return call.args[1:]
    raise AssertionError("no stored busy read")


def _wire(mod: CalendarModule, pool) -> None:
    mod._resolved_calendar_id = BUTLERS
    mod._primary_calendar_id = PRIMARY
    mod._all_provider_calendar_ids = [BUTLERS, PRIMARY, SHARED]
    mod._db = SimpleNamespace(pool=pool)


async def _create(provider, pool, _config_extra=None, **extra):
    mcp = FastMCP("owner-busy-create")
    mod = CalendarModule()
    mod._provider = provider
    await mod.register_tools(
        mcp=mcp,
        config={"provider": "google", "calendar_id": BUTLERS, **(_config_extra or {})},
        db=SimpleNamespace(db_name="butlers", db_schema="general", pool=pool),
        butler_name="general",
    )
    _wire(mod, pool)
    with (
        patch(
            "butlers.modules.calendar.mutations.require_permission",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
        patch.object(mod, "_project_provider_mutation", new_callable=AsyncMock),
        patch.object(mod, "_refresh_user_projection", new_callable=AsyncMock),
    ):
        return await (await tool_fn(mcp, "calendar_create_event"))(
            title="Focus", **{"start_at": START, "end_at": END, **extra}
        )


MESSAGE = "Couldn't read your calendars to check for overlaps. Try again, or allow overlap"
_PRIMARY_MEETING = [(START, END)]


async def test_a_create_over_a_primary_calendar_meeting_reports_the_conflict():
    provider = _ByCalendarProvider({}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY], stored=_PRIMARY_MEETING))
    assert result["status"] == "conflict"
    assert result["conflicts"], "the primary-calendar meeting must be named"
    assert provider.create_calls == []
    # The target calendar goes through free/busy; the primary is read from the projection.
    assert provider.checked == [BUTLERS]


async def test_the_primary_is_included_even_without_a_source_row():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS], stored=_PRIMARY_MEETING)  # the ledger does not list the primary
    result = await _create(provider, pool)
    assert result["status"] == "conflict"


async def test_a_primary_with_no_sync_row_makes_the_check_unavailable():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS], synced_calendars=[])  # no calendar_sources row for the primary
    result = await _create(provider, pool)
    assert result["status"] == "error" and MESSAGE in result["error"]
    assert provider.create_calls == []


async def test_a_stale_primary_sync_makes_the_check_unavailable():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY], synced_ago=timedelta(minutes=11))
    result = await _create(provider, pool)
    assert result["status"] == "error" and MESSAGE in result["error"]
    assert provider.create_calls == []
    # 10 minutes is the sync deadman's own grace (2x the 5 minute poll): just inside is fine.
    ok = await _create(provider, _pool([BUTLERS, PRIMARY], synced_ago=timedelta(minutes=9)))
    assert ok.get("status") != "error"


async def test_a_never_synced_primary_makes_the_check_unavailable():
    provider = _ByCalendarProvider({}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY], synced_ago=None))
    assert result["status"] == "error" and MESSAGE in result["error"]


async def test_another_persons_shared_calendar_is_never_checked():
    provider = _ByCalendarProvider({SHARED: [_meeting()]}, event=_block())
    pool = _pool([BUTLERS, PRIMARY])
    result = await _create(provider, pool)
    assert result.get("status") != "conflict"
    assert SHARED not in provider.checked
    for call in pool.fetch.await_args_list:
        if "time_role" not in call.args[0]:  # the role ledger may list every calendar
            assert SHARED not in str(call.args[1:])


async def test_a_failed_target_free_busy_refuses_the_write():
    provider = _ByCalendarProvider({}, fail={BUTLERS}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY]))
    assert result["status"] == "error" and MESSAGE in result["error"]
    assert provider.create_calls == []


async def test_an_unreadable_role_ledger_refuses_the_write():
    provider = _ByCalendarProvider({}, event=_block())
    result = await _create(provider, _pool([], error=OSError("ledger down")))
    assert result["status"] == "error" and MESSAGE in result["error"]
    assert provider.create_calls == []


async def test_allow_overlap_does_not_refuse_when_the_check_cannot_run():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY], synced_ago=timedelta(hours=2))
    result = await _create(provider, pool, conflict_policy="allow_overlap")
    assert result.get("status") != "error"
    assert provider.create_calls, "the owner chose to allow overlap"


async def test_a_no_park_move_onto_a_primary_calendar_meeting_is_declined():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY], stored=_PRIMARY_MEETING)
    result = await _move(provider, setup=lambda mod: _wire(mod, pool))
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflicts"
    assert provider.update_calls == []


async def test_a_no_park_move_declines_when_the_owner_busy_set_cannot_be_read():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY], synced_ago=timedelta(hours=2))
    result = await _move(provider, setup=lambda mod: _wire(mod, pool))
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflict_check_unavailable"
    assert provider.update_calls == []


async def test_a_replan_move_is_declined_even_under_allow_overlap_when_blind():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY], synced_ago=timedelta(hours=2))

    def setup(mod):
        _wire(mod, pool)
        mod._config.conflicts.policy = "allow_overlap"

    result = await _move(provider, setup=setup)
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflict_check_unavailable"


def test_the_refusal_is_a_calendar_auth_error_subclass():
    from butlers.modules.calendar.errors import ConflictCheckUnavailable

    assert issubclass(ConflictCheckUnavailable, CalendarAuthError)
    assert MESSAGE in str(ConflictCheckUnavailable())


async def test_an_all_day_write_binds_instants_not_bare_dates():
    from datetime import date

    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY])
    result = await _create(
        provider, pool, start_at=date(2026, 2, 20), end_at=date(2026, 2, 21), timezone="UTC"
    )
    assert result.get("status") != "error"
    _, _, start, end, _ = _stored_read_args(pool)
    assert isinstance(start, datetime) and isinstance(end, datetime)
    assert start.tzinfo is not None


async def test_a_naive_datetime_is_read_in_the_candidate_timezone_not_utc():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY])
    await _create(
        provider,
        pool,
        start_at=datetime(2026, 2, 20, 11, 0),
        end_at=datetime(2026, 2, 20, 12, 0),
        timezone="America/New_York",
    )
    _, _, start, _, _ = _stored_read_args(pool)
    assert start == datetime(2026, 2, 20, 16, 0, tzinfo=UTC)


async def test_a_coding_error_is_not_reported_as_unavailable():
    provider = _ByCalendarProvider({}, event=_block())
    with pytest.raises(TypeError):
        await _create(provider, _pool([BUTLERS, PRIMARY], error=TypeError("bug")))


async def test_the_staleness_grace_follows_the_configured_sync_interval():
    provider = _ByCalendarProvider({}, event=_block())

    def configure(minutes):
        return {"sync": {"interval_minutes": minutes}}

    # 30 minute poll: a 40 minute old sync is inside the 60 minute grace, not refused.
    ok = await _create(
        provider,
        _pool([BUTLERS, PRIMARY], synced_ago=timedelta(minutes=40)),
        _config_extra=configure(30),
    )
    assert ok.get("status") != "error"
    # 1 minute poll: a 3 minute old sync is outside the 2 minute grace.
    stale = await _create(
        provider,
        _pool([BUTLERS, PRIMARY], synced_ago=timedelta(minutes=3)),
        _config_extra=configure(1),
    )
    assert stale["status"] == "error" and MESSAGE in stale["error"]


async def test_a_source_switched_off_is_left_out_with_one_warning(caplog):
    provider = _ByCalendarProvider({}, event=_block())
    # The primary is switched off and never syncs: it must not block every write forever.
    pool = _pool([BUTLERS, PRIMARY], synced_ago=None, disabled={PRIMARY})
    with caplog.at_level("WARNING"):
        result = await _create(provider, pool)
    assert result.get("status") != "error"
    assert provider.create_calls
    assert result["conflict_check"] == "partial"
    assert result["skipped_calendars"] == 1
    warnings = [r for r in caplog.records if "sync disabled" in r.getMessage()]
    assert len(warnings) == 1


async def test_a_complete_check_carries_no_partial_marker():
    provider = _ByCalendarProvider({}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY]))
    assert "conflict_check" not in result and "skipped_calendars" not in result


async def test_a_replan_move_is_declined_when_a_calendar_was_skipped():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY], synced_ago=None, disabled={PRIMARY})
    result = await _move(provider, setup=lambda mod: _wire(mod, pool))
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflict_check_unavailable"
    assert provider.update_calls == []


async def test_an_owner_declined_stored_row_is_excluded_in_the_query():
    provider = _ByCalendarProvider({}, event=_block())
    pool = _pool([BUTLERS, PRIMARY])
    await _create(provider, pool)
    sql = next(
        c.args[0] for c in pool.fetch.await_args_list if "calendar_event_instances" in c.args[0]
    )
    assert "'declined'" in sql and "me ->> 'self' = 'true'" in sql


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_stored_busy_excludes_quarantined_invitations_and_the_updated_event(db):
    import shutil

    if not shutil.which("docker"):
        pytest.skip("Docker not available")
    pool = await db("core", "relationship", schemas={"relationship": "relationship"})
    sources = {}
    for key, cal, role in (("a", BUTLERS, "butler"), ("b", PRIMARY, "owner")):
        sources[cal] = await pool.fetchval(
            "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
            "display_name, writable, metadata, time_role) VALUES ($1, 'provider_event', 'user', "
            "'double', $2, $2, true, '{}', $3) RETURNING id",
            key,
            cal,
            role,
        )
    await pool.execute(
        "INSERT INTO calendar_sync_cursors (source_id, cursor_name, last_synced_at, "
        "last_success_at) VALUES ($1, 'provider_sync', now(), now())",
        sources[PRIMARY],
    )

    async def event(origin_ref, metadata):
        event_id = await pool.fetchval(
            "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
            "ends_at, status, metadata, source_butler) VALUES ($1, $2, 'x', 'UTC', $3, $4, "
            "'confirmed', $5, 'general') RETURNING id",
            sources[PRIMARY],
            origin_ref,
            START,
            END,
            metadata,
        )
        await pool.execute(
            "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
            "timezone, starts_at, ends_at, status) VALUES ($1, $2, $3, 'UTC', $4, $5, 'confirmed')",
            event_id,
            sources[PRIMARY],
            origin_ref,
            START,
            END,
        )

    invite = {
        "organizer": "stranger@nowhere.test",
        "attendees": [{"email": "me@example.com", "self": True, "response_status": "needsAction"}],
    }
    await event("quarantined-invite", invite)
    mod = CalendarModule()
    mod._provider = _ProviderDouble()
    _wire(mod, pool)
    from butlers.modules.calendar.models import CalendarEventCreate

    candidate = CalendarEventCreate(title="x", start_at=START, end_at=END)
    # A quarantined needsAction invitation on the primary does not block.
    assert (await mod._stored_owner_busy(BUTLERS, candidate, ignore_event_id=None))[0] == []

    await event("real-meeting", {"organizer": "me@example.com"})
    busy = (await mod._stored_owner_busy(BUTLERS, candidate, ignore_event_id=None))[0]
    assert len(busy) == 1
    # The event being updated is excluded by id, not by its window.
    assert (await mod._stored_owner_busy(BUTLERS, candidate, ignore_event_id="real-meeting"))[
        0
    ] == []

    # An all-day write binds as instants: it conflicts over an all-day primary event and passes
    # on a free day (a bare date against timestamptz is rejected by asyncpg).
    all_day = datetime(2026, 3, 1, tzinfo=UTC)
    await pool.execute(
        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
        "ends_at, status, metadata, source_butler) VALUES ($1, 'all-day', 'x', 'UTC', $2, $3, "
        "'confirmed', '{}', 'general')",
        sources[PRIMARY],
        all_day,
        all_day + timedelta(days=1),
    )
    all_day_id = await pool.fetchval("SELECT id FROM calendar_events WHERE origin_ref='all-day'")
    await pool.execute(
        "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
        "timezone, starts_at, ends_at, status) VALUES ($1, $2, 'all-day', 'UTC', $3, $4, "
        "'confirmed')",
        all_day_id,
        sources[PRIMARY],
        all_day,
        all_day + timedelta(days=1),
    )
    from datetime import date

    over = CalendarEventCreate(
        title="x", start_at=date(2026, 3, 1), end_at=date(2026, 3, 2), timezone="UTC"
    )
    free = CalendarEventCreate(
        title="x", start_at=date(2026, 3, 5), end_at=date(2026, 3, 6), timezone="UTC"
    )
    assert len((await mod._stored_owner_busy(BUTLERS, over, ignore_event_id=None))[0]) == 1
    assert (await mod._stored_owner_busy(BUTLERS, free, ignore_event_id=None))[0] == []

    # A naive datetime is read in the candidate's timezone: 11:00 New York is 16:00 UTC.
    naive = CalendarEventCreate(
        title="x",
        start_at=datetime(2026, 2, 20, 11, 0),
        end_at=datetime(2026, 2, 20, 12, 0),
        timezone="America/New_York",
    )
    assert len((await mod._stored_owner_busy(BUTLERS, naive, ignore_event_id=None))[0]) == 1

    # A row the owner declined holds no time.
    await pool.execute(
        "UPDATE calendar_events SET metadata = $1 WHERE origin_ref = 'real-meeting'",
        {
            "organizer": "me@example.com",
            "attendees": [{"self": True, "response_status": "declined"}],
        },
    )
    assert (await mod._stored_owner_busy(BUTLERS, candidate, ignore_event_id=None))[0] == []


_ = Any


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_create_tool_path_over_a_stored_primary_meeting_conflicts_with_no_provider_create(
    db,
):
    """The real projection, role ledger and freshness reads feed the tool, not stubs of them."""
    import shutil

    if not shutil.which("docker"):
        pytest.skip("Docker not available")
    pool = await db("core", "relationship", schemas={"relationship": "relationship"})
    sources = {}
    for key, cal, role in (("a", BUTLERS, "butler"), ("b", PRIMARY, "owner")):
        sources[cal] = await pool.fetchval(
            "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
            "display_name, writable, metadata, time_role) VALUES ($1, 'provider_event', 'user', "
            "'double', $2, $2, true, '{}', $3) RETURNING id",
            key,
            cal,
            role,
        )
    await pool.execute(
        "INSERT INTO calendar_sync_cursors (source_id, cursor_name, last_synced_at, "
        "last_success_at) VALUES ($1, 'provider_sync', now(), now())",
        sources[PRIMARY],
    )
    event_id = await pool.fetchval(
        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
        "ends_at, status, metadata, source_butler) VALUES ($1, 'real-meeting', 'Dentist', 'UTC', "
        "$2, $3, 'confirmed', $4, 'general') RETURNING id",
        sources[PRIMARY],
        START,
        END,
        {"organizer": "me@example.com"},
    )
    await pool.execute(
        "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
        "timezone, starts_at, ends_at, status) VALUES ($1, $2, 'real-meeting', 'UTC', $3, $4, "
        "'confirmed')",
        event_id,
        sources[PRIMARY],
        START,
        END,
    )
    provider = _ByCalendarProvider({}, event=_block())

    result = await _create(provider, pool)

    assert result["status"] == "conflict"
    assert result["conflicts"], "the primary-calendar meeting must be named"
    assert provider.create_calls == []
    # A free slot on the same pool still creates: the refusal is the meeting, not the harness.
    free = await _create(
        provider, pool, start_at=START + timedelta(hours=5), end_at=END + timedelta(hours=5)
    )
    assert free.get("status") != "conflict"
    assert len(provider.create_calls) == 1
