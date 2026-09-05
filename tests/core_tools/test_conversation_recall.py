"""Tests for the ``conversation_recall``/``conversation_thread_read`` core MCP tools.

Covers:
- Empty/blank query returns [] without a DB round trip (never fabricates a
  recollection from no search term).
- Missing pool raises rather than silently degrading — recall has no safe
  "no memory" default the way memory_access does.
- Malformed since/until/conversation_id/around_message_id raise a clear
  ValueError the model can see and correct.
- Successful calls delegate to the api.conversations data layer and reshape
  UUID/datetime fields to JSON-safe strings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.core_tools._base import ToolContext

pytestmark = pytest.mark.unit


def _register_and_grab(pool=None, butler_name="finance"):
    import butlers.core_tools._conversation_recall as mod

    registered: dict = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(),
        pool=pool,
        spawner=None,
        butler_name=butler_name,
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    mcp = SimpleNamespace()
    mod.register_conversation_recall_tool(ctx, mcp, _core_tool)
    return registered["conversation_recall"], registered["conversation_thread_read"]


# ---------------------------------------------------------------------------
# conversation_recall
# ---------------------------------------------------------------------------


async def test_conversation_recall_empty_query_returns_empty_without_db_call():
    fake_pool = AsyncMock()
    recall, _ = _register_and_grab(pool=fake_pool)

    result = await recall(query="   ")

    assert result == []
    fake_pool.fetch.assert_not_called()


async def test_conversation_recall_raises_when_pool_unavailable():
    recall, _ = _register_and_grab(pool=None)

    with pytest.raises(RuntimeError, match="Database pool"):
        await recall(query="landlord")


async def test_conversation_recall_rejects_malformed_since():
    recall, _ = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="since"):
        await recall(query="landlord", since="not-a-timestamp")


async def test_conversation_recall_rejects_malformed_until():
    recall, _ = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="until"):
        await recall(query="landlord", until="not-a-timestamp")


async def test_conversation_recall_maps_hits_and_forwards_filters(monkeypatch):
    conv_id = uuid4()
    msg_id = uuid4()
    session_id = uuid4()
    created_at = datetime(2026, 8, 1, tzinfo=UTC)

    fake_search = AsyncMock(
        return_value={
            "items": [
                {
                    "conversation_id": conv_id,
                    "message_id": msg_id,
                    "role": "user",
                    "created_at": created_at,
                    "butler_name": "home",
                    "snippet": "the landlord called",
                    "session_id": session_id,
                    "deep_link": f"/sessions/{session_id}",
                }
            ],
            "next_cursor": None,
            "has_more": False,
        }
    )
    monkeypatch.setattr("butlers.api.conversations.message_search", fake_search)

    recall, _ = _register_and_grab(pool=AsyncMock())

    result = await recall(
        query="landlord",
        since="2026-07-25T00:00:00+00:00",
        until="2026-08-02T00:00:00+00:00",
        limit=5,
        channel="dashboard",
        butler="home",
    )

    assert result == [
        {
            "conversation_id": str(conv_id),
            "message_id": str(msg_id),
            "role": "user",
            "created_at": created_at.isoformat(),
            "butler_name": "home",
            "snippet": "the landlord called",
            "session_id": str(session_id),
            "deep_link": f"/sessions/{session_id}",
        }
    ]
    fake_search.assert_awaited_once()
    kwargs = fake_search.await_args.kwargs
    assert kwargs["query"] == "landlord"
    assert kwargs["since"] == datetime.fromisoformat("2026-07-25T00:00:00+00:00")
    assert kwargs["until"] == datetime.fromisoformat("2026-08-02T00:00:00+00:00")
    assert kwargs["limit"] == 5
    assert kwargs["channel"] == "dashboard"
    assert kwargs["butler"] == "home"


async def test_conversation_recall_no_hits_returns_empty_list(monkeypatch):
    fake_search = AsyncMock(return_value={"items": [], "next_cursor": None, "has_more": False})
    monkeypatch.setattr("butlers.api.conversations.message_search", fake_search)

    recall, _ = _register_and_grab(pool=AsyncMock())

    assert await recall(query="zzz-no-such-term") == []


# ---------------------------------------------------------------------------
# conversation_thread_read
# ---------------------------------------------------------------------------


async def test_conversation_thread_read_raises_when_pool_unavailable():
    _, thread_read = _register_and_grab(pool=None)

    with pytest.raises(RuntimeError, match="Database pool"):
        await thread_read(conversation_id=str(uuid4()))


async def test_conversation_thread_read_rejects_invalid_conversation_id():
    _, thread_read = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="conversation_id"):
        await thread_read(conversation_id="not-a-uuid")


async def test_conversation_thread_read_rejects_invalid_around_message_id():
    _, thread_read = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="around_message_id"):
        await thread_read(conversation_id=str(uuid4()), around_message_id="not-a-uuid")


async def test_conversation_thread_read_maps_messages(monkeypatch):
    conv_id = uuid4()
    msg_id = uuid4()
    session_id = uuid4()
    created_at = datetime(2026, 8, 1, tzinfo=UTC)

    fake_window = AsyncMock(
        return_value=[
            {
                "id": msg_id,
                "role": "assistant",
                "content": "The lease renews in March.",
                "created_at": created_at,
                "session_id": session_id,
                "model_name": "claude",
            }
        ]
    )
    monkeypatch.setattr("butlers.api.conversations.message_thread_window", fake_window)

    _, thread_read = _register_and_grab(pool=AsyncMock())

    result = await thread_read(conversation_id=str(conv_id))

    assert result == {
        "conversation_id": str(conv_id),
        "messages": [
            {
                "message_id": str(msg_id),
                "role": "assistant",
                "content": "The lease renews in March.",
                "created_at": created_at.isoformat(),
                "session_id": str(session_id),
            }
        ],
    }
    fake_window.assert_awaited_once()
    assert fake_window.await_args.args[1] == conv_id
    assert fake_window.await_args.kwargs["around_message_id"] is None


async def test_conversation_thread_read_no_messages_returns_empty_list(monkeypatch):
    fake_window = AsyncMock(return_value=[])
    monkeypatch.setattr("butlers.api.conversations.message_thread_window", fake_window)

    _, thread_read = _register_and_grab(pool=AsyncMock())

    result = await thread_read(conversation_id=str(uuid4()))

    assert result["messages"] == []
