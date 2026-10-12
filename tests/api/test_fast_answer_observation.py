"""The mounted observer's lane window ends observation, never execution."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.api.routers import conversations as router
from butlers.core.dashboard_turns import DashboardTurnResult

pytestmark = pytest.mark.unit


async def test_lane_windows_use_original_observation_start_and_preserve_late_reply(monkeypatch):
    # REQ-dashboard-fast-answer-005: actual generator, availability and late reply.
    message, conversation, request = uuid4(), uuid4(), uuid4()
    turn = DashboardTurnResult(
        "accepted", message, conversation, request, None, None, None, None, None, None, None
    )
    monkeypatch.setattr(router, "claim_ingress", AsyncMock(return_value=turn))
    monkeypatch.setattr(router, "dispatch_status", AsyncMock(return_value=turn))
    monkeypatch.setattr(router, "open_chat_stream_listener", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_resolve_session_id", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_POLL_INTERVAL_S", 0)
    monkeypatch.setattr(router, "_KEEPALIVE_INTERVAL_S", 1000)
    assert router._SESSION_TIMEOUT_S == 300
    for lanes, deadline in (
        (["answer"], 45),
        (["non_answer"], 300),
        (["answer", "non_answer"], 45),
        ([None], 300),
        ([None, "answer"], 45),
        (["answer", RuntimeError("unavailable")], 45),
    ):
        clock = SimpleNamespace(value=0.0)
        monkeypatch.setattr(router, "time", SimpleNamespace(monotonic=lambda: clock.value))
        pool = SimpleNamespace(fetchval=AsyncMock(side_effect=[*lanes] + [lanes[-1]] * 30))
        db = SimpleNamespace(credential_shared_pool=lambda: pool)
        finder = AsyncMock(return_value=None)
        monkeypatch.setattr(router, "message_find_reply_since", finder)

        class Request:
            async def is_disconnected(self):
                clock.value += 30
                return False

        events = [
            chunk
            async for chunk in router._stream_conversation_response(
                request=Request(),
                butler_name="switchboard",
                conversation_id=conversation,
                message_created_at=datetime.now(UTC),
                envelope={},
                db=db,
                mcp_mgr=SimpleNamespace(),
                message_id=message,
            )
        ]
        assert "SESSION_TIMEOUT" in "".join(events)
        assert "execution may still be active" in "".join(events)
        assert deadline <= clock.value < deadline + 30
        assert conversation not in router._ACTIVE_TURNS
        assert not hasattr(pool, "execute")  # observer has no Stop/terminal/write surface
        assert router._resolve_session_id.await_args.kwargs["request_id"] == str(request)

    # A separately reconnected observation can still see an already-persisted late reply.
    clock.value = 0
    pool.fetchval = AsyncMock(return_value="answer")
    late = {
        "id": uuid4(),
        "conversation_id": conversation,
        "role": "assistant",
        "content": "The late grounded answer.",
        "created_at": datetime.now(UTC),
        "session_id": None,
        "sources": [],
        "citations": [],
    }
    monkeypatch.setattr(router, "message_find_reply_since", AsyncMock(return_value=late))
    events = [
        chunk
        async for chunk in router._stream_conversation_response(
            request=Request(),
            butler_name="switchboard",
            conversation_id=conversation,
            message_created_at=datetime.now(UTC),
            envelope={},
            db=db,
            mcp_mgr=SimpleNamespace(),
            message_id=message,
        )
    ]
    assert "The late grounded answer." in "".join(events)
    assert "SESSION_TIMEOUT" not in "".join(events)
    assert not hasattr(pool, "execute")
