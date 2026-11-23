"""A write is overlap-checked against all owner time (bu-5ii4gj.4 slice A).

A Butlers block lands on the Butlers calendar, but the owner's meetings live on the primary
calendar, so a check of the target calendar alone reports "no conflict" over a real meeting.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


def _pool(owner_ids: list[str], *, error: Exception | None = None):
    pool = MagicMock()
    if error is not None:
        pool.fetch = AsyncMock(side_effect=error)
    else:
        pool.fetch = AsyncMock(return_value=[{"calendar_id": cid} for cid in owner_ids])
    return pool


def _wire(mod: CalendarModule, pool) -> None:
    mod._resolved_calendar_id = BUTLERS
    mod._primary_calendar_id = PRIMARY
    mod._all_provider_calendar_ids = [BUTLERS, PRIMARY, SHARED]
    mod._db = SimpleNamespace(pool=pool)


async def _create(provider, pool, **extra):
    mcp = FastMCP("owner-busy-create")
    mod = CalendarModule()
    mod._provider = provider
    await mod.register_tools(
        mcp=mcp,
        config={"provider": "google", "calendar_id": BUTLERS},
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
            title="Focus", start_at=START, end_at=END, **extra
        )


async def test_a_create_over_a_primary_calendar_meeting_reports_the_conflict():
    provider = _ByCalendarProvider({PRIMARY: [_meeting()]}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY]))
    assert result["status"] == "conflict"
    assert result["conflicts"], "the primary-calendar meeting must be named"
    assert provider.create_calls == []
    assert PRIMARY in provider.checked and BUTLERS in provider.checked


async def test_another_persons_shared_calendar_is_never_checked():
    provider = _ByCalendarProvider({SHARED: [_meeting()]}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY]))
    assert result.get("status") != "conflict"
    assert SHARED not in provider.checked


async def test_a_failed_free_busy_refuses_the_write_instead_of_reporting_clear():
    provider = _ByCalendarProvider({}, fail={PRIMARY}, event=_block())
    result = await _create(provider, _pool([BUTLERS, PRIMARY]))
    assert result["status"] == "error"
    assert "conflict check unavailable" in result["error"]
    assert provider.create_calls == []


async def test_an_unreadable_role_ledger_refuses_the_write():
    provider = _ByCalendarProvider({}, event=_block())
    result = await _create(provider, _pool([], error=RuntimeError("ledger down")))
    assert result["status"] == "error"
    assert "conflict check unavailable" in result["error"]
    assert provider.create_calls == []


async def test_a_no_park_move_onto_a_primary_calendar_meeting_is_declined():
    provider = _ByCalendarProvider({PRIMARY: [_meeting()]}, event=_block())
    result = await _move(provider, setup=lambda mod: _wire(mod, _pool([BUTLERS, PRIMARY])))
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflicts"
    assert provider.update_calls == []


async def test_a_no_park_move_declines_when_the_owner_busy_set_cannot_be_read():
    provider = _ByCalendarProvider({}, fail={PRIMARY}, event=_block())
    result = await _move(provider, setup=lambda mod: _wire(mod, _pool([BUTLERS, PRIMARY])))
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflict_check_unavailable"
    assert provider.update_calls == []


async def test_identical_windows_from_two_calendars_are_reported_once():
    same = [_meeting("a")]
    provider = _ByCalendarProvider({BUTLERS: same, PRIMARY: [_meeting("b")]}, event=_block())
    mod = CalendarModule()
    mod._provider = provider
    _wire(mod, _pool([BUTLERS, PRIMARY]))
    from butlers.modules.calendar.models import CalendarEventCreate

    candidate = CalendarEventCreate(title="x", start_at=START, end_at=END)
    result = await mod._check_conflicts(
        provider=provider, calendar_id=BUTLERS, candidate=candidate, conflict_policy="suggest"
    )
    assert len(result["conflicts"]) == 1


def test_the_refusal_is_a_calendar_auth_error_subclass():
    from butlers.modules.calendar.errors import ConflictCheckUnavailable

    assert issubclass(ConflictCheckUnavailable, CalendarAuthError)


_ = Any


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_role_ledger_query_returns_owner_and_butler_calendars_only(db):
    import shutil

    if not shutil.which("docker"):
        pytest.skip("Docker not available")
    pool = await db("core", "relationship", schemas={"relationship": "relationship"})
    for key, cal, role in (
        ("a", BUTLERS, "butler"),
        ("b", PRIMARY, "owner"),
        ("c", SHARED, "other_person"),
    ):
        await pool.execute(
            "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
            "display_name, writable, metadata, time_role) VALUES ($1, 'provider_event', 'user', "
            "'double', $2, $2, true, '{}', $3)",
            key,
            cal,
            role,
        )
    mod = CalendarModule()
    mod._provider = _ProviderDouble()
    _wire(mod, pool)
    assert await mod._owner_busy_calendar_ids(BUTLERS) == [BUTLERS, PRIMARY]
