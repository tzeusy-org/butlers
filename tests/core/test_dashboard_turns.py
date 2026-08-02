"""Focused contracts for the durable dashboard-turn control helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


async def test_register_session_gate_publishes_pending_session_and_observes_cancel() -> None:
    """The pre-invoke gate is one durable operation, not a read-then-write race."""
    from butlers.core.dashboard_turns import register_session_and_check_cancel

    message_id = uuid4()
    session_id = uuid4()
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "outcome": "cancelled",
            "message_id": message_id,
            "conversation_id": uuid4(),
            "request_id": uuid4(),
            "target_butler": "finance",
            "target_kind": "route",
            "route_inbox_id": uuid4(),
            "cancel_requested_at": datetime.now(UTC),
            "terminal_state": None,
            "terminal_at": None,
        }
    )

    result = await register_session_and_check_cancel(
        pool,
        message_id=message_id,
        session_id=session_id,
        request_id=uuid4(),
        butler_name="finance",
        phase="route",
    )

    assert result.outcome == "cancelled"
    pool.fetchrow.assert_awaited_once()
    query, *args = pool.fetchrow.await_args.args
    assert "dashboard_turn_register_session" in query
    assert args == [message_id, session_id, args[2], "finance", "route"]


async def test_claim_target_is_distinct_from_route_inbox_enqueue() -> None:
    """The target code can keep claim and enqueue in its one DB transaction."""
    from butlers.core.dashboard_turns import claim_target, mark_route_enqueued

    message_id = uuid4()
    request_id = uuid4()
    inbox_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {
                "outcome": "active",
                "message_id": message_id,
                "conversation_id": uuid4(),
                "request_id": request_id,
                "target_butler": "finance",
                "target_kind": "route",
                "route_inbox_id": None,
                "cancel_requested_at": None,
                "terminal_state": None,
                "terminal_at": None,
            },
            {
                "outcome": "active",
                "message_id": message_id,
                "conversation_id": uuid4(),
                "request_id": request_id,
                "target_butler": "finance",
                "target_kind": "route",
                "route_inbox_id": inbox_id,
                "cancel_requested_at": None,
                "terminal_state": None,
                "terminal_at": None,
            },
        ]
    )

    claim = await claim_target(
        conn,
        message_id=message_id,
        request_id=request_id,
        target_butler="finance",
    )
    enqueued = await mark_route_enqueued(conn, message_id=message_id, route_inbox_id=inbox_id)

    assert claim.outcome == "active"
    assert enqueued.route_inbox_id == inbox_id
    assert conn.fetchrow.await_count == 2
    assert "dashboard_turn_claim_target" in conn.fetchrow.await_args_list[0].args[0]
    assert "dashboard_turn_mark_route_enqueued" in conn.fetchrow.await_args_list[1].args[0]
