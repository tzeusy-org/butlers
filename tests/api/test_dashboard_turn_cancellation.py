"""Truthfulness contracts for durable dashboard-turn cancellation."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.core.dashboard_turns import DashboardTurnResult, DashboardTurnSession

pytestmark = pytest.mark.unit


def _turn(
    outcome: str,
    *,
    message_id: UUID | None = None,
    terminal_state: str | None = None,
) -> DashboardTurnResult:
    return DashboardTurnResult(
        outcome=outcome,
        message_id=message_id or uuid4(),
        conversation_id=uuid4(),
        request_id=uuid4(),
        target_butler="finance",
        target_kind="route",
        route_inbox_id=uuid4(),
        cancel_requested_at=datetime.now(UTC) if outcome in {"cancelled", "cancelling"} else None,
        cancel_confirmed_at=datetime.now(UTC) if outcome == "cancelled" else None,
        terminal_state=terminal_state,
        terminal_at=datetime.now(UTC) if terminal_state else None,
    )


async def test_durable_stop_succeeds_before_any_runtime_invokes(monkeypatch) -> None:
    """A durable pre-invoke cancellation needs no best-effort MCP round trip."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    request_cancel = AsyncMock(return_value=_turn("cancelled", message_id=message_id))
    monkeypatch.setattr(subject, "request_cancel", request_cancel)

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()
    mcp_mgr = MagicMock(spec=MCPClientManager)

    result = await subject._cancel_dashboard_message_turn(
        db=db,
        mcp_mgr=mcp_mgr,
        message_id=message_id,
    )

    assert result.cancelled is True
    assert result.already_finished is False
    mcp_mgr.get_client.assert_not_awaited()


async def test_durable_stop_reports_an_unprovable_runtime_as_unknown(monkeypatch) -> None:
    """A lost runtime lease cannot be represented as a confirmed Stop or completion."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    monkeypatch.setattr(
        subject,
        "request_cancel",
        AsyncMock(
            return_value=_turn("ambiguous", message_id=message_id, terminal_state="ambiguous")
        ),
    )

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()
    mcp_mgr = MagicMock(spec=MCPClientManager)

    result = await subject._cancel_dashboard_message_turn(
        db=db,
        mcp_mgr=mcp_mgr,
        message_id=message_id,
    )

    assert result.cancelled is False
    assert result.already_finished is False
    assert result.message is not None
    assert "outcome is unknown" in result.message
    mcp_mgr.get_client.assert_not_awaited()


async def test_durable_stop_attempts_exact_session_cancellation_for_ambiguous_turn(
    monkeypatch,
) -> None:
    """Ambiguity blocks replay, not a best-effort stop of the known predecessor."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    session_id = uuid4()
    session = DashboardTurnSession(
        message_id=message_id,
        session_id=session_id,
        butler_name="finance",
        phase="route",
        registered_at=datetime.now(UTC),
        invoke_claimed_at=datetime.now(UTC),
        invoke_active=True,
    )
    monkeypatch.setattr(subject, "request_cancel", AsyncMock(return_value=_turn("cancelling")))
    monkeypatch.setattr(subject, "live_sessions", AsyncMock(return_value=[session]))
    monkeypatch.setattr(
        subject,
        "confirm_cancel",
        AsyncMock(return_value=_turn("ambiguous", terminal_state="ambiguous")),
    )
    monkeypatch.setattr(
        subject,
        "dispatch_status",
        AsyncMock(return_value=_turn("ambiguous", terminal_state="ambiguous")),
    )

    client = MagicMock()
    client.call_tool = AsyncMock(
        return_value=MagicMock(
            is_error=False,
            content=[MagicMock(type="text", text='{"cancelled": true}')],
        )
    )
    mcp_mgr = MagicMock(spec=MCPClientManager)
    mcp_mgr.get_client = AsyncMock(return_value=client)
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()

    result = await subject._cancel_dashboard_message_turn(
        db=db,
        mcp_mgr=mcp_mgr,
        message_id=message_id,
    )

    client.call_tool.assert_awaited_once_with("cancel_session", {"session_id": str(session_id)})
    assert result.cancelled is False
    assert result.already_finished is False
    assert result.message is not None
    assert "outcome is unknown" in result.message


async def test_message_scoped_stop_endpoint_never_needs_a_live_api_process_map(
    app, monkeypatch
) -> None:
    """The widget addresses the persisted message, not a transient conversation handle."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    expected = subject.ConversationCancelResponse(cancelled=True, already_finished=False)
    cancel = AsyncMock(return_value=expected)
    monkeypatch.setattr(subject, "_cancel_dashboard_message_turn", cancel)

    db = MagicMock(spec=DatabaseManager)
    mcp_mgr = MagicMock(spec=MCPClientManager)
    app.dependency_overrides[subject._get_db_manager] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/switchboard/conversation-turns/{message_id}/cancel"
        )

    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    cancel.assert_awaited_once_with(db=db, mcp_mgr=mcp_mgr, message_id=message_id)


async def test_sse_generator_claims_ingress_after_stop_can_address_the_turn(monkeypatch) -> None:
    """A Stop between API row creation and SSE execution must skip Switchboard."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    claim = AsyncMock(return_value=_turn("cancelled", message_id=message_id))
    submit = AsyncMock()
    monkeypatch.setattr(subject, "claim_ingress", claim)
    monkeypatch.setattr(subject, "_submit_to_switchboard", submit)

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()
    mcp_mgr = MagicMock(spec=MCPClientManager)

    events = [
        event
        async for event in subject._stream_conversation_response(
            request=_Request(),
            butler_name="switchboard",
            conversation_id=uuid4(),
            message_created_at=datetime.now(UTC),
            envelope={},
            db=db,
            mcp_mgr=mcp_mgr,
            message_id=message_id,
        )
    ]

    assert "SESSION_CANCELLED" in "".join(events)
    claim.assert_awaited_once_with(db.credential_shared_pool.return_value, message_id=message_id)
    submit.assert_not_awaited()


async def test_sse_reports_confirmed_stop_when_settling_ingress_fails(monkeypatch) -> None:
    """A settled ingress error after Stop is cancellation, not a retry banner."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    monkeypatch.setattr(subject, "claim_ingress", AsyncMock(return_value=_turn("dispatch")))
    monkeypatch.setattr(subject, "_submit_to_switchboard", AsyncMock(return_value=None))
    record_failure = AsyncMock(return_value=_turn("cancelled", message_id=message_id))
    monkeypatch.setattr(subject, "_record_dashboard_ingress_failure", record_failure)

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()

    events = [
        event
        async for event in subject._stream_conversation_response(
            request=_Request(),
            butler_name="switchboard",
            conversation_id=uuid4(),
            message_created_at=datetime.now(UTC),
            envelope={},
            db=db,
            mcp_mgr=MagicMock(spec=MCPClientManager),
            message_id=message_id,
        )
    ]

    stream = "".join(events)
    assert "SESSION_CANCELLED" in stream
    assert "SWITCHBOARD_UNAVAILABLE" not in stream
    record_failure.assert_awaited_once()


async def test_sse_claim_ingress_surfaces_ambiguous_turn_without_retry(monkeypatch) -> None:
    """An idempotent ingress reconnect must retain the no-replay outcome."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    submit = AsyncMock()
    monkeypatch.setattr(
        subject,
        "claim_ingress",
        AsyncMock(
            return_value=_turn("ambiguous", message_id=message_id, terminal_state="ambiguous")
        ),
    )
    monkeypatch.setattr(subject, "_submit_to_switchboard", submit)

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()
    events = [
        event
        async for event in subject._stream_conversation_response(
            request=_Request(),
            butler_name="switchboard",
            conversation_id=uuid4(),
            message_created_at=datetime.now(UTC),
            envelope={},
            db=db,
            mcp_mgr=MagicMock(spec=MCPClientManager),
            message_id=message_id,
        )
    ]

    stream = "".join(events)
    assert "TURN_OUTCOME_UNKNOWN" in stream
    assert "SWITCHBOARD_ERROR" not in stream
    submit.assert_not_awaited()


async def test_durable_stop_never_claims_success_when_an_invoked_runtime_wont_confirm(
    monkeypatch,
) -> None:
    """Intent alone is not a Stop confirmation once runtime.invoke has begun."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    session_id = uuid4()
    session = DashboardTurnSession(
        message_id=message_id,
        session_id=session_id,
        butler_name="finance",
        phase="route",
        registered_at=datetime.now(UTC),
        invoke_claimed_at=datetime.now(UTC),
        invoke_active=True,
    )
    monkeypatch.setattr(subject, "request_cancel", AsyncMock(return_value=_turn("cancelling")))
    monkeypatch.setattr(subject, "live_sessions", AsyncMock(return_value=[session]))
    monkeypatch.setattr(subject, "dispatch_status", AsyncMock(return_value=_turn("active")))
    confirm_cancel = AsyncMock(return_value=_turn("cancelling", message_id=message_id))
    monkeypatch.setattr(subject, "confirm_cancel", confirm_cancel)

    client = MagicMock()
    client.call_tool = AsyncMock(return_value=MagicMock(is_error=False, content=[]))
    mcp_mgr = MagicMock(spec=MCPClientManager)
    mcp_mgr.get_client = AsyncMock(return_value=client)
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()

    result = await subject._cancel_dashboard_message_turn(
        db=db,
        mcp_mgr=mcp_mgr,
        message_id=message_id,
    )

    assert result.cancelled is False
    assert result.already_finished is False
    assert result.session_id == session_id
    assert result.message
    # A failed MCP acknowledgement is not success, but a concurrent runtime
    # may have persisted its own durable outcome while this request was in
    # flight. Re-read that authority before returning an honest failure.
    confirm_cancel.assert_awaited_once_with(
        db.credential_shared_pool.return_value, message_id=message_id
    )


async def test_existing_sse_observes_a_stop_settled_by_another_client(monkeypatch) -> None:
    """A live stream must emit cancellation, not wait for its generic timeout."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    accepted = _turn("accepted", message_id=message_id)
    submit = AsyncMock()
    monkeypatch.setattr(subject, "claim_ingress", AsyncMock(return_value=accepted))
    monkeypatch.setattr(subject, "_submit_to_switchboard", submit)
    monkeypatch.setattr(
        subject,
        "dispatch_status",
        AsyncMock(
            side_effect=[
                _turn("active", message_id=message_id),
                _turn("cancelled", message_id=message_id),
            ]
        ),
    )
    monkeypatch.setattr(subject, "message_find_reply_since", AsyncMock(return_value=None))
    monkeypatch.setattr(subject.asyncio, "sleep", AsyncMock())

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()

    events = [
        event
        async for event in subject._stream_conversation_response(
            request=_Request(),
            butler_name="switchboard",
            conversation_id=uuid4(),
            message_created_at=datetime.now(UTC),
            envelope={},
            db=db,
            mcp_mgr=MagicMock(spec=MCPClientManager),
            message_id=message_id,
        )
    ]

    stream = "".join(events)
    assert "SESSION_CANCELLED" in stream
    assert "SESSION_TIMEOUT" not in stream
    submit.assert_not_awaited()


async def test_existing_sse_surfaces_an_unprovable_runtime_as_unknown(monkeypatch) -> None:
    """A persisted ambiguous terminal state is not a timeout and must not invite retry."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    accepted = _turn("accepted", message_id=message_id)
    monkeypatch.setattr(subject, "claim_ingress", AsyncMock(return_value=accepted))
    monkeypatch.setattr(
        subject,
        "dispatch_status",
        AsyncMock(
            return_value=_turn("ambiguous", message_id=message_id, terminal_state="ambiguous")
        ),
    )

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()

    events = [
        event
        async for event in subject._stream_conversation_response(
            request=_Request(),
            butler_name="switchboard",
            conversation_id=uuid4(),
            message_created_at=datetime.now(UTC),
            envelope={},
            db=db,
            mcp_mgr=MagicMock(spec=MCPClientManager),
            message_id=message_id,
        )
    ]

    stream = "".join(events)
    assert "TURN_OUTCOME_UNKNOWN" in stream
    assert "SESSION_TIMEOUT" not in stream


async def test_durable_stop_requires_every_invoked_session_then_confirms(monkeypatch) -> None:
    """Classifier and routed sessions are both exact cancellation obligations."""
    from butlers.api.routers import conversations as subject

    message_id = uuid4()
    switchboard_session = DashboardTurnSession(
        message_id=message_id,
        session_id=uuid4(),
        butler_name="switchboard",
        phase="classification",
        registered_at=datetime.now(UTC),
        invoke_claimed_at=datetime.now(UTC),
        invoke_active=True,
    )
    target_session = DashboardTurnSession(
        message_id=message_id,
        session_id=uuid4(),
        butler_name="finance",
        phase="route",
        registered_at=datetime.now(UTC),
        invoke_claimed_at=datetime.now(UTC),
        invoke_active=True,
    )
    monkeypatch.setattr(subject, "request_cancel", AsyncMock(return_value=_turn("cancelling")))
    monkeypatch.setattr(
        subject,
        "live_sessions",
        AsyncMock(return_value=[switchboard_session, target_session]),
    )
    monkeypatch.setattr(subject, "confirm_cancel", AsyncMock(return_value=_turn("cancelled")))

    def _result_for(session: DashboardTurnSession) -> MagicMock:
        return MagicMock(
            is_error=False,
            content=[MagicMock(type="text", text='{"cancelled": true}')],
        )

    switchboard_client = MagicMock()
    switchboard_client.call_tool = AsyncMock(return_value=_result_for(switchboard_session))
    finance_client = MagicMock()
    finance_client.call_tool = AsyncMock(return_value=_result_for(target_session))
    mcp_mgr = MagicMock(spec=MCPClientManager)
    mcp_mgr.get_client = AsyncMock(
        side_effect={"switchboard": switchboard_client, "finance": finance_client}.__getitem__
    )
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = AsyncMock()

    result = await subject._cancel_dashboard_message_turn(
        db=db,
        mcp_mgr=mcp_mgr,
        message_id=message_id,
    )

    assert result.cancelled is True
    assert result.already_finished is False
    assert result.session_id == switchboard_session.session_id
    switchboard_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(switchboard_session.session_id)}
    )
    finance_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(target_session.session_id)}
    )
