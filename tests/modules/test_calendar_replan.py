"""The calendar replan exception, domain half (RFC 0038 D13, bu-qlx055 q12 = B).

A broker-forwarded ``calendar_update_event`` whose assertion names the
``replan_move`` conditional moves only an owner-only block Butlers created, as
the provider reports it at execution. Anything else is declined before any
write so the broker parks it as it always has.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from butlers.core.capability_json import arguments_digest
from butlers.core.goal_lineage import VerifiedLineage, lineage_scope
from butlers.modules.calendar import (
    AttendeeInfo,
    CalendarAuthError,
    CalendarModule,
    CalendarRequestError,
)
from butlers.modules.calendar.replan import (
    REPLAN_DECLINED,
    REPLAN_MOVE,
    movable,
    move_only,
    undo_receipt,
)
from butlers.testing.mcp import tool_fn
from tests.modules.test_module_calendar import _make_event, _ProviderDouble

pytestmark = pytest.mark.unit

NEW_START = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)
NEW_END = datetime(2026, 2, 20, 17, 0, tzinfo=UTC)
MOVE = {"event_id": "evt-1", "start_at": NEW_START, "end_at": NEW_END}


def _block(**overrides):
    values = {"title": "BUTLER: Gym", "butler_generated": True, "butler_name": "general"}
    values.update(overrides)
    return _make_event(**values)


def _lineage(args, *, conditional=REPLAN_MOVE) -> VerifiedLineage:
    wire = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in args.items()}
    return VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=0,
        origin="owner",
        exposed=False,
        capability="calendar_update_event",
        args_digest=arguments_digest(wire),
        call_id=uuid.uuid4(),
        effect_id=uuid.uuid4(),
        conditional=conditional,
    )


class _FailingGet(_ProviderDouble):
    async def get_event(self, *, calendar_id, event_id):
        raise CalendarAuthError("provider unavailable")


class _FailingConflictCheck(_ProviderDouble):
    async def find_conflicts(self, *, calendar_id, candidate):
        raise CalendarAuthError("free/busy unavailable")

    async def get_free_busy(self, *, calendar_ids, start_at, end_at, timezone=None):
        raise CalendarAuthError("free/busy unavailable")


class _FailingWrite(_ProviderDouble):
    def __init__(self, error: Exception, **kwargs):
        super().__init__(**kwargs)
        self._error = error

    async def update_event(self, *, calendar_id, event_id, patch):
        self.update_calls.append({"calendar_id": calendar_id, "event_id": event_id})
        raise self._error


async def _module(provider):
    mcp = FastMCP("calendar-replan-tests")
    mod = CalendarModule()
    mod._provider = provider
    mod._resolved_calendar_id = "primary"
    await mod.register_tools(
        mcp=mcp,
        config={"provider": "google", "calendar_id": "primary"},
        db=SimpleNamespace(db_name="butlers", db_schema="general"),
        butler_name="general",
    )
    return mod, mcp


async def _move(provider, args=MOVE, *, lineage=True):
    mod, mcp = await _module(provider)
    with (
        patch(
            "butlers.modules.calendar.mutations.require_permission",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
        patch.object(mod, "_project_provider_mutation", new_callable=AsyncMock),
        patch.object(mod, "_refresh_user_projection", new_callable=AsyncMock),
        lineage_scope(_lineage(args) if lineage else None),
    ):
        return await (await tool_fn(mcp, "calendar_update_event"))(**args)


# -- pure checks ----------------------------------------------------------------


def test_move_only_accepts_a_pure_time_change():
    assert move_only({"event_id": "e", "start_at": "x"})
    assert move_only({"event_id": "e", "end_at": "x", "timezone": "UTC", "calendar_id": "c"})


@pytest.mark.parametrize(
    "args",
    [
        {"event_id": "e"},  # moves nothing
        {"event_id": " ", "start_at": "x"},
        {"start_at": "x"},
        {"event_id": "e", "start_at": "x", "title": "renamed"},
        {"event_id": "e", "start_at": "x", "attendees": []},
        {"event_id": "e", "start_at": "x", "recurrence_scope": "this"},
        # A declined move must leave no receipt a later parked call could replay.
        {"event_id": "e", "start_at": "x", "request_id": "r"},
        {"event_id": "e", "start_at": None, "end_at": None},
    ],
)
def test_move_only_refuses_anything_else(args):
    assert not move_only(args)


def test_movable_needs_the_marker_and_no_attendees():
    assert movable(_block()) is None
    assert movable(None) == "unreadable"
    # Owner-made, or made before Butlers wrote the marker: both carry none.
    assert movable(_block(butler_generated=False)) == "not_butler_created"
    assert movable(_block(attendees=[AttendeeInfo(email="a@example.com")])) == "has_attendees"
    assert movable(_block(recurrence_rule="RRULE:FREQ=WEEKLY")) == "recurring"
    assert movable(_block(all_day=True)) == "all_day"


def test_undo_receipt_is_the_inverse_move():
    pre = {
        "event_id": "evt-1",
        "calendar_id": "primary",
        "start_at": "2026-02-20T14:00:00+00:00",
        "end_at": "2026-02-20T15:00:00+00:00",
        "timezone": "UTC",
        "title": "BUTLER: Gym",
    }
    undo = undo_receipt(pre)
    assert undo["tool"] == "calendar_update_event"
    assert move_only(undo["arguments"])
    assert undo["arguments"]["start_at"] == pre["start_at"]


# -- the tool under a conditional lineage --------------------------------------


async def test_a_marked_attendee_free_block_moves_with_an_undo_receipt():
    existing = _block()
    provider = _ProviderDouble(event=existing)
    result = await _move(provider)
    assert result["status"] == "updated"
    assert len(provider.update_calls) == 1
    # Bound to the etag the check read: a change in between fails the write.
    assert provider.update_calls[0]["patch"].etag == existing.etag
    assert result["pre_state"]["start_at"] == existing.start_at.isoformat()
    assert result["undo"] == undo_receipt(result["pre_state"])


@pytest.mark.parametrize(
    ("existing", "reason"),
    [
        (_block(butler_generated=False), "not_butler_created"),
        (_block(attendees=[AttendeeInfo(email="a@example.com")]), "has_attendees"),
        (_block(recurrence_rule="RRULE:FREQ=WEEKLY"), "recurring"),
        (None, "unreadable"),
    ],
)
async def test_anything_else_is_declined_before_any_write(existing, reason):
    provider = _ProviderDouble(event=existing)
    result = await _move(provider)
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == reason
    assert provider.update_calls == []


async def test_an_unreadable_event_is_declined_not_an_error():
    provider = _FailingGet(event=_block())
    result = await _move(provider)
    assert result == {
        "status": REPLAN_DECLINED,
        "reason": "unreadable",
        "event_id": "evt-1",
        "calendar_id": "primary",
    }
    assert provider.update_calls == []


async def test_a_move_onto_another_event_is_declined():
    other = _make_event(event_id="evt-2", title="Dentist", start_at=NEW_START, end_at=NEW_END)
    provider = _ProviderDouble(event=_block(), conflicts=[other])
    result = await _move(provider)
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflicts"
    assert provider.update_calls == []


async def test_a_conditional_call_that_is_not_a_pure_move_is_declined():
    provider = _ProviderDouble(event=_block())
    result = await _move(provider, {**MOVE, "title": "Renamed"})
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "not_a_move"
    assert provider.update_calls == []
    assert provider.get_calls == []


async def test_without_the_conditional_the_update_is_unchanged():
    provider = _ProviderDouble(event=_block(attendees=[AttendeeInfo(email="a@example.com")]))
    mod, mcp = await _module(provider)
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
        result = await (await tool_fn(mcp, "calendar_update_event"))(**MOVE)
    assert result["status"] != REPLAN_DECLINED
    assert "undo" not in result


async def test_calendar_respond_stays_always_per_event():
    mod = CalendarModule()
    with patch.object(
        mod, "canonical_approval_preparers", return_value={"calendar_respond": object()}
    ):
        meta = mod.tool_metadata()["calendar_respond"]
    assert meta.approval_policy == {"mode": "always_per_event"}


# -- provider errors after the fetch (bu-4c7nvp.1.13) ----------------------------


async def test_a_failed_conflict_check_declines():
    provider = _FailingConflictCheck(event=_block())
    result = await _move(provider)
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "conflict_check_unavailable"
    assert provider.update_calls == []


async def test_a_failed_precondition_on_the_write_declines():
    # The provider refused the etag-bound write: the event changed since the check.
    error = CalendarRequestError(status_code=412, message="Precondition Failed")
    provider = _FailingWrite(error, event=_block())
    result = await _move(provider)
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "changed_since_check"


@pytest.mark.parametrize(
    "error",
    [
        CalendarRequestError(status_code=500, message="backend error"),
        CalendarRequestError(status_code=403, message="forbidden"),
        CalendarAuthError("connection reset after send"),
    ],
)
async def test_any_other_write_error_is_a_tool_error_never_a_result(error):
    # A plain result would be recorded as a committed effect; an error result is not.
    provider = _FailingWrite(error, event=_block())
    with pytest.raises(CalendarAuthError):
        await _move(provider)


async def test_without_the_condition_a_write_error_is_still_the_structured_result():
    error = CalendarRequestError(status_code=500, message="backend error")
    provider = _FailingWrite(error, event=_block())
    result = await _move(provider, lineage=False)
    assert result["status"] == "error"
