"""``park_response`` is the gate's own preparer call, made in-process (bu-5ii4gj.11 P2b)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.modules.calendar.response_park import RESPONSE_CARD_TTL, park_response
from butlers.modules.calendar_response import CalendarResponseError

pytestmark = pytest.mark.unit

ARGS = {"entry_id": "e", "response_status": "declined", "request_id": "r", "send_updates": "none"}


def _module(result=None, error=None):
    prepare = AsyncMock(return_value=result, side_effect=error)
    return SimpleNamespace(
        _response_coordinator=SimpleNamespace(prepare_and_park=prepare),
        _butler_name="general",
        _db=None,
    ), prepare


async def test_it_parks_through_the_modules_canonical_preparer_with_a_typed_dossier():
    module, prepare = _module({"status": "pending_approval", "action_id": "a", "command_id": "a"})

    result = await park_response(module, ARGS, why="Plan declines this meeting.")

    assert result["status"] == "pending_approval"
    kwargs = prepare.await_args.kwargs
    assert kwargs["tool_args"] == ARGS
    assert kwargs["expires_at"] - kwargs["requested_at"] == RESPONSE_CARD_TTL
    assert kwargs["dossier"].why == "Plan declines this meeting."


async def test_a_refused_response_is_an_error_dict_never_an_exception():
    module, _ = _module(error=CalendarResponseError("response_capability_unavailable"))

    assert await park_response(module, ARGS, why="x") == {
        "error": "response_capability_unavailable"
    }


async def test_a_missing_reason_is_refused_before_the_preparer_runs():
    module, prepare = _module({"status": "pending_approval"})

    result = await park_response(module, ARGS, why="")

    assert "error" in result or result.get("status") == "error"
    prepare.assert_not_awaited()
