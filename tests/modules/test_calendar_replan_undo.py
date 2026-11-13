"""The owner's undo for a calendar replan move (RFC 0038 D13, bu-4c7nvp.1.12).

Every replan move that might land leaves exactly one approvals card whose call is
the inverse move, parked before the write (fail-closed). The card's pre-image is
the earliest one the goal recorded for that event, so a retry after a
failed-but-committed move still puts the block back where it started. The
approved undo is bound to the move and declines, writing nothing, when the event
changed since (``changed_since_move``) or the move never landed (``not_moved``).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.goal_lineage import lineage_scope
from butlers.modules.approvals.execution_context import (
    ApprovalExecutionContext,
    approval_tool_args_digest,
    reset_approval_execution_context,
    set_approval_execution_context,
)
from butlers.modules.calendar import CalendarRequestError
from butlers.modules.calendar.replan import REPLAN_DECLINED
from butlers.modules.calendar.replan_undo import UNDO_DECLINED
from butlers.modules.calendar_approval import retire_legacy_calendar_approval_args
from butlers.testing.mcp import tool_fn
from tests.modules.test_calendar_replan import (
    MOVE,
    NEW_START,
    _block,
    _FakeLedger,
    _lineage,
    _module,
)
from tests.modules.test_module_calendar import _ProviderDouble

pytestmark = pytest.mark.unit

OLD_START = datetime(2026, 2, 20, 14, 0, tzinfo=UTC)
OLD_END = datetime(2026, 2, 20, 15, 0, tzinfo=UTC)


class _MovingProvider(_ProviderDouble):
    """A provider whose writes move its one event and change its etag."""

    def __init__(self, *, fail_after_write: Exception | None = None, **kwargs):
        super().__init__(**kwargs)
        self.fail_after_write = fail_after_write
        self.refuse: Exception | None = None
        self._version = 0
        self.ledger: _FakeLedger | None = None
        self.cards_at_write: list[int] = []

    def edit(self, **changes):
        """The owner (or anyone) changes the event outside Butlers."""
        self._version += 1
        self._event = self._event.model_copy(update={**changes, "etag": f'"v{self._version}"'})

    async def update_event(self, *, calendar_id, event_id, patch):
        self.update_calls.append({"calendar_id": calendar_id, "event_id": event_id, "patch": patch})
        if self.ledger is not None:
            self.cards_at_write.append(len(self.ledger.cards))
        if self.refuse is not None:
            raise self.refuse
        self.edit(start_at=patch.start_at, end_at=patch.end_at)
        if self.fail_after_write is not None:
            error, self.fail_after_write = self.fail_after_write, None
            raise error
        return self._event


@contextmanager
def _tool_patches(mod, ledger):
    with (
        patch(
            "butlers.modules.calendar.mutations.require_permission",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
        patch.object(mod, "_project_provider_mutation", new_callable=AsyncMock),
        patch.object(mod, "_refresh_user_projection", new_callable=AsyncMock),
        patch(
            "butlers.modules.calendar.event_tools.replan_undo_ledger",
            return_value=ledger,
        ),
    ):
        yield


async def _replan_move(provider, ledger, *, goal_id: uuid.UUID, args=MOVE):
    mod, mcp = await _module(provider)
    lineage = replace(_lineage(args), goal_id=goal_id)
    with _tool_patches(mod, ledger), lineage_scope(lineage):
        return await (await tool_fn(mcp, "calendar_update_event"))(**args)


async def _approve(provider, ledger, card_id: uuid.UUID):
    """Run the card's call exactly as the approvals executor does."""
    card = ledger.cards[card_id]
    dispatch = retire_legacy_calendar_approval_args("calendar_update_event", card["tool_args"])
    mod, mcp = await _module(provider)
    context = ApprovalExecutionContext(
        action_id=card_id,
        session_id=None,
        actor="owner",
        tool_name="calendar_update_event",
        tool_args_digest=approval_tool_args_digest(dispatch),
        authorized_task=asyncio.current_task(),  # type: ignore[arg-type]
    )
    with (
        _tool_patches(mod, ledger),
        patch.object(
            mod,
            "_prepare_workspace_mutation",
            new_callable=AsyncMock,
            return_value=("undo-key", None),
        ),
        # The party gate re-reads the approved row from the database; it is covered by
        # its own suites. The undo's binding is checked by the decline test below.
        patch(
            "butlers.modules.calendar.event_tools.gate_calendar_party_mutation",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        token = set_approval_execution_context(context)
        try:
            return await (await tool_fn(mcp, "calendar_update_event"))(**dispatch)
        finally:
            reset_approval_execution_context(token)


def _card(ledger) -> tuple[uuid.UUID, dict[str, Any]]:
    assert len(ledger.cards) == 1
    return next(iter(ledger.cards.items()))


# -- one receipt per committed move ------------------------------------------------


async def test_a_committed_move_parks_one_card_whose_call_is_the_inverse():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    result = await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert result["status"] == "updated"
    card_id, card = _card(ledger)
    assert result["undo"]["action_id"] == str(card_id)
    assert card["tool_args"]["start_at"] == OLD_START.isoformat()
    assert card["tool_args"]["end_at"] == OLD_END.isoformat()
    assert card["tool_args"]["calendar_id"] == "primary"
    assert "Approve to put it back" in card["summary"]


async def test_a_second_move_by_the_same_goal_keeps_the_one_card():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    goal = uuid.uuid4()
    await _replan_move(provider, ledger, goal_id=goal)
    later = {
        **MOVE,
        "start_at": datetime(2026, 2, 20, 18, 0, tzinfo=UTC),
        "end_at": datetime(2026, 2, 20, 19, 0, tzinfo=UTC),
    }
    await _replan_move(provider, ledger, goal_id=goal, args=later)
    _, card = _card(ledger)
    assert card["tool_args"]["start_at"] == OLD_START.isoformat()  # still where it started


async def test_without_the_undo_tables_the_move_is_declined_before_any_write():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    ledger.available = AsyncMock(return_value=False)  # type: ignore[method-assign]
    result = await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "undo_unavailable"
    assert provider.update_calls == []


# -- the earliest pre-image (#5767 review N3) --------------------------------------


async def test_a_retry_after_a_failed_but_committed_move_undoes_to_the_true_pre_image():
    error = CalendarRequestError(status_code=503, message="backend error")
    provider = _MovingProvider(event=_block(etag='"v0"'), fail_after_write=error)
    ledger, goal = _FakeLedger(), uuid.uuid4()
    with pytest.raises(CalendarRequestError):
        await _replan_move(provider, ledger, goal_id=goal)
    assert len(ledger.cards) == 1  # fail-closed: the card was parked before the write
    # The retry reads the already-moved event: its own pre-image is the new time.
    result = await _replan_move(provider, ledger, goal_id=goal)
    assert result["status"] == "updated"
    _, card = _card(ledger)
    assert card["tool_args"]["start_at"] == OLD_START.isoformat()
    assert result["undo"]["arguments"]["start_at"] == OLD_START.isoformat()


async def test_a_refused_write_leaves_no_pre_image_behind():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    provider.refuse = CalendarRequestError(status_code=412, message="Precondition Failed")
    result = await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert result["reason"] == "changed_since_check"
    assert ledger.offers == []


# -- fail-closed order: the card exists before the write ---------------------------


async def test_the_card_is_parked_before_the_write():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    provider.ledger = ledger
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert provider.cards_at_write == [1]


async def test_a_card_that_cannot_be_parked_means_no_write():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    ledger.park_error = RuntimeError("approvals admission unavailable")
    result = await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert result["status"] == REPLAN_DECLINED
    assert result["reason"] == "undo_unavailable"
    assert provider.update_calls == []
    assert ledger.cards == {} and ledger.offers == []


async def test_a_412_cancels_the_card_it_opened_and_says_nothing_happened():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    provider.refuse = CalendarRequestError(status_code=412, message="Precondition Failed")
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    _, card = _card(ledger)
    assert card["status"] == "cancelled"
    assert card["summary"].startswith("Nothing happened")


async def test_after_an_ambiguous_failure_that_landed_the_undo_works():
    error = CalendarRequestError(status_code=503, message="backend error")
    provider = _MovingProvider(event=_block(etag='"v0"'), fail_after_write=error)
    ledger = _FakeLedger()
    with pytest.raises(CalendarRequestError):
        await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert provider._event.start_at == NEW_START  # the write did land
    card_id, _ = _card(ledger)
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == "updated"
    assert provider._event.start_at == OLD_START
    assert provider.update_calls[-1]["patch"].single_attempt is True


async def test_after_an_ambiguous_failure_that_never_landed_the_undo_declines_not_moved():
    # Also the window case: the owner approves before the write lands.
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    provider.refuse = CalendarRequestError(status_code=503, message="backend error")
    with pytest.raises(CalendarRequestError):
        await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    provider.refuse = None
    writes = len(provider.update_calls)
    card_id, _ = _card(ledger)
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == UNDO_DECLINED
    assert result["reason"] == "not_moved"
    assert len(provider.update_calls) == writes


async def test_a_refused_retry_keeps_the_undo_of_the_move_that_landed():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    goal = uuid.uuid4()
    await _replan_move(provider, ledger, goal_id=goal)  # lands at NEW_START
    provider.refuse = CalendarRequestError(status_code=412, message="Precondition Failed")
    later = {
        **MOVE,
        "start_at": datetime(2026, 2, 20, 18, 0, tzinfo=UTC),
        "end_at": datetime(2026, 2, 20, 19, 0, tzinfo=UTC),
    }
    await _replan_move(provider, ledger, goal_id=goal, args=later)
    provider.refuse = None
    card_id, card = _card(ledger)
    assert card["status"] == "pending"  # the reused card is not cancelled
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == "updated"
    assert provider._event.start_at == OLD_START


async def test_a_card_the_owner_decided_before_the_412_still_declines_not_moved():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    provider.refuse = CalendarRequestError(status_code=412, message="Precondition Failed")
    provider.ledger = ledger

    original = provider.update_event

    async def approve_then_write(**kwargs):
        for card in ledger.cards.values():
            card["status"] = "approved"  # the owner approves inside the window
        return await original(**kwargs)

    provider.update_event = approve_then_write  # type: ignore[method-assign]
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    provider.refuse = None
    provider.update_event = original  # type: ignore[method-assign]
    writes = len(provider.update_calls)
    card_id, _ = _card(ledger)
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == UNDO_DECLINED
    assert result["reason"] == "not_moved"
    assert len(provider.update_calls) == writes


# -- the undo itself ---------------------------------------------------------------


async def test_the_approved_undo_puts_the_block_back_bound_to_the_moved_etag():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    moved_etag = provider._event.etag
    card_id, _ = _card(ledger)
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == "updated"
    undo_patch = provider.update_calls[-1]["patch"]
    assert undo_patch.start_at == OLD_START and undo_patch.end_at == OLD_END
    assert undo_patch.etag == moved_etag
    assert undo_patch.single_attempt is True


async def test_the_undo_declines_without_writing_when_the_event_changed_since_the_move():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    provider.edit(location="Gym B")  # changed after the move
    writes = len(provider.update_calls)
    card_id, _ = _card(ledger)
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == UNDO_DECLINED
    assert result["reason"] == "changed_since_move"
    assert len(provider.update_calls) == writes


async def test_a_412_on_the_undo_write_declines():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    provider.refuse = CalendarRequestError(status_code=412, message="Precondition Failed")
    card_id, _ = _card(ledger)
    result = await _approve(provider, ledger, card_id)
    assert result["status"] == UNDO_DECLINED
    assert result["reason"] == "changed_since_move"


async def test_any_other_approved_update_is_not_an_undo():
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    card_id, card = _card(ledger)
    ledger.cards[uuid.uuid4()] = {**card, "status": "pending"}  # an unrelated approval
    other = next(k for k in ledger.cards if k != card_id)
    provider.edit(location="Gym B")
    result = await _approve(provider, ledger, other)
    assert result["status"] == "updated"  # no move binding: the ordinary approved update
    assert provider.update_calls[-1]["patch"].single_attempt is False


async def test_new_start_is_where_the_move_left_it():
    # Guard for the fixtures above: the move really moved the block.
    provider, ledger = _MovingProvider(event=_block(etag='"v0"')), _FakeLedger()
    await _replan_move(provider, ledger, goal_id=uuid.uuid4())
    assert provider._event.start_at == NEW_START
