"""An approved overlap-resolution move (bu-5ii4gj.11 R2b).

The card is parked by the dashboard route; approving it runs the exact ``calendar_update_event``
through the owning butler. The etag the owner saw lives outside the call's arguments: the move
declines, writing nothing, when the event's etag has moved, and is written at most once.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.approvals.execution_context import (
    ApprovalExecutionContext,
    approval_tool_args_digest,
    reset_approval_execution_context,
    set_approval_execution_context,
)
from butlers.modules.calendar import CalendarRequestError
from butlers.modules.calendar.resolution_move import (
    RESOLUTION_DECLINED,
    blast_radius_for,
    resolution_card_args,
    resolution_card_summary,
    resolution_refusal,
    resolution_request_id,
)
from butlers.modules.calendar_approval import retire_legacy_calendar_approval_args
from butlers.testing.mcp import tool_fn
from tests.modules.test_calendar_replan import _block, _module
from tests.modules.test_calendar_replan_undo import _MovingProvider

pytestmark = pytest.mark.unit

NEW_START = datetime(2026, 2, 20, 16, 0, tzinfo=UTC)
NEW_END = datetime(2026, 2, 20, 17, 0, tzinfo=UTC)
CALL = {
    "event_id": "evt-1",
    "start_at": NEW_START.isoformat(),
    "end_at": NEW_END.isoformat(),
    "calendar_id": "primary",
}


def _card(request_id: str = "resolve:abc", event_id: str = "evt-1") -> dict:
    return resolution_card_args({**CALL, "event_id": event_id}, request_id=request_id)


async def _approve(provider, *, offer, request_id="resolve:abc", event_id="evt-1"):
    """Run the parked card's call exactly as the approvals executor does."""
    args = retire_legacy_calendar_approval_args(
        "calendar_update_event", _card(request_id, event_id)
    )
    mod, mcp = await _module(provider)
    mod._db.pool = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    context = ApprovalExecutionContext(
        action_id=uuid.uuid4(),
        session_id=None,
        actor="owner",
        tool_name="calendar_update_event",
        tool_args_digest=approval_tool_args_digest(args),
        authorized_task=asyncio.current_task(),  # type: ignore[arg-type]
    )
    with _patches(mod, offer):
        token = set_approval_execution_context(context)
        try:
            return await (await tool_fn(mcp, "calendar_update_event"))(**args)
        finally:
            reset_approval_execution_context(token)


@contextmanager
def _patches(mod, offer):
    with (
        patch(
            "butlers.modules.calendar.mutations.require_permission",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
        patch.object(mod, "_project_provider_mutation", new_callable=AsyncMock),
        patch.object(mod, "_refresh_user_projection", new_callable=AsyncMock),
        patch.object(
            mod,
            "_prepare_workspace_mutation",
            new_callable=AsyncMock,
            return_value=("resolve-key", None),
        ),
        patch(
            "butlers.modules.calendar.resolution_move.offer_for_card",
            new_callable=AsyncMock,
            return_value=offer,
        ),
        patch(
            "butlers.modules.calendar.event_tools.gate_calendar_party_mutation",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield


async def test_an_approved_move_writes_once_bound_to_the_etag_it_was_checked_against():
    provider = _MovingProvider(event=_block(etag='"v0"'))

    result = await _approve(provider, offer={"action_id": "x", "etag": '"v0"'})

    assert result["status"] == "updated"
    (call,) = provider.update_calls
    assert call["patch"].etag == '"v0"' and call["patch"].single_attempt is True
    assert call["patch"].start_at == NEW_START


async def test_a_recurring_instance_is_written_and_bound_by_its_own_etag():
    instance_id = "evt-1_20260220T150000Z"
    # The instance's etag is what the radar read from the owner-role copy and what a get of the
    # instance id returns; the series master's etag is never the If-Match of a move.
    provider = _MovingProvider(event=_block(event_id=instance_id, etag='"instance-v0"'))

    result = await _approve(
        provider, offer={"action_id": "x", "etag": '"instance-v0"'}, event_id=instance_id
    )

    assert result["status"] == "updated"
    (call,) = provider.update_calls
    assert call["event_id"] == instance_id
    assert call["patch"].etag == '"instance-v0"' and call["patch"].single_attempt is True


async def test_the_move_declines_without_writing_when_the_event_changed_since_the_check():
    provider = _MovingProvider(event=_block(etag='"v0"'))
    provider.edit(location="Elsewhere")  # the owner's etag is now stale

    result = await _approve(provider, offer={"action_id": "x", "etag": '"v0"'})

    assert result["status"] == RESOLUTION_DECLINED and result["reason"] == "changed_since_check"
    assert result["error"]  # the approvals executor records a failed execution, not executed
    assert provider.update_calls == []


async def test_a_412_on_the_move_declines_and_is_not_retried():
    provider = _MovingProvider(event=_block(etag='"v0"'))
    provider.refuse = CalendarRequestError(status_code=412, message="Precondition Failed")

    result = await _approve(provider, offer={"action_id": "x", "etag": '"v0"'})

    assert result["status"] == RESOLUTION_DECLINED
    assert len(provider.update_calls) == 1


async def test_an_approved_resolution_with_no_binding_declines_and_never_writes():
    provider = _MovingProvider(event=_block(etag='"v0"'))

    result = await _approve(provider, offer=None)

    assert result["status"] == RESOLUTION_DECLINED and result["reason"] == "unbound"
    assert result["error"]
    assert provider.update_calls == []


async def test_an_approval_that_is_not_a_resolution_is_the_ordinary_update():
    provider = _MovingProvider(event=_block(etag='"v0"'))
    provider.edit(location="Elsewhere")

    result = await _approve(provider, offer=None, request_id="plain-request")

    assert result["status"] == "updated"
    assert provider.update_calls[-1]["patch"].single_attempt is False


def test_a_move_that_names_no_calendar_cannot_be_carded():
    with pytest.raises(ValueError, match="calendar"):
        resolution_card_args({k: v for k, v in CALL.items() if k != "calendar_id"}, request_id="r")


def test_an_event_with_no_etag_never_writes():
    event = SimpleNamespace(etag=None)
    assert resolution_refusal({"etag": '"v0"'}, event) == "changed_since_check"  # type: ignore[arg-type]
    assert resolution_refusal({"etag": None}, event) == "changed_since_check"  # type: ignore[arg-type]


def test_the_request_id_follows_the_digest_the_owner_saw():
    a = resolution_request_id("pair", "move_block:1", "d1")
    assert a == resolution_request_id("pair", "move_block:1", "d1")
    assert a != resolution_request_id("pair", "move_block:1", "d2")
    assert a.startswith("resolve:")


def test_the_materialised_card_args_are_exactly_the_declared_command_arguments():
    from butlers.modules.approvals.command_contracts import RESOLUTION_MOVE_COMMAND

    args = RESOLUTION_MOVE_COMMAND.materialize(_card())

    assert set(args) == set(RESOLUTION_MOVE_COMMAND.argument_names)


def test_the_card_names_its_times_in_the_owner_zone():
    from zoneinfo import ZoneInfo

    summary = resolution_card_summary(CALL, ZoneInfo("Asia/Singapore"))

    assert "00:00 +08" in summary or "00:00 SGT" in summary  # 16:00 UTC is midnight in Singapore
    assert "16:00" not in summary


def test_only_a_move_that_notifies_attendees_reaches_beyond_the_owner():
    assert blast_radius_for("parked_per_event") == "contact"
    assert blast_radius_for("no_park_undo") == "self"
    assert blast_radius_for("owner_submit") == "self"
