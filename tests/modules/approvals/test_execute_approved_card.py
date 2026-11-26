"""``ApprovalsModule.execute_approved_card``: re-run one already-approved card (bu-5ii4gj.11.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.approvals.module import ApprovalsModule

pytestmark = pytest.mark.unit


def _row(status="approved", execution_result=None):
    return {
        "id": uuid.uuid4(),
        "tool_name": "calendar_update_event",
        "tool_args": {"event_id": "e"},
        "status": status,
        "requested_at": datetime(2026, 12, 1, tzinfo=UTC),
        "execution_result": execution_result,
    }


def _module(row, executor=True):
    module = ApprovalsModule()
    module._db = AsyncMock()
    module._db.fetchrow = AsyncMock(return_value=row)
    module._butler_name = "general"
    if executor:
        module.set_tool_executor(AsyncMock(return_value={"status": "updated"}))
    return module


@pytest.mark.parametrize(
    "row",
    [None, _row("pending"), _row("rejected"), _row("expired"), _row("executed", {"success": True})],
)
async def test_only_an_approved_unexecuted_card_is_re_run(row):
    module = _module(row)
    with patch("butlers.modules.approvals.module.execute_approved_action") as run:
        assert await module.execute_approved_card(uuid.uuid4()) is False
    run.assert_not_called()


async def test_without_an_executor_nothing_runs():
    module = _module(_row(), executor=False)
    with patch("butlers.modules.approvals.module.execute_approved_action") as run:
        assert await module.execute_approved_card(uuid.uuid4()) is False
    run.assert_not_called()


async def test_an_approved_card_runs_its_stored_call_through_the_executor():
    row = _row()
    module = _module(row)
    with patch(
        "butlers.modules.approvals.module.execute_approved_action", new_callable=AsyncMock
    ) as run:
        assert await module.execute_approved_card(row["id"]) is True
    kwargs = run.await_args.kwargs
    assert kwargs["action_id"] == row["id"] and kwargs["tool_args"] == {"event_id": "e"}
    assert kwargs["tool_name"] == "calendar_update_event"
