"""RFC 0038 D6 read-back: conversation history and insight candidates (bu-pkuny2 AC4).

Both stores carry the core_313 taint stamp. The dashboard API, the conversation
history the dashboard prompt is built from, and the insight broker's delivery
read hand it back as additive nullable fields. Readers take it from the whole
row so a schema that predates core_313 reads null instead of failing (proved
against Postgres in ``tests/migrations/test_core_313_goal_taint.py``).
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.conversations import message_get_by_id, message_list
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.testing.records import Record

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_GOAL, _INVOCATION = uuid4(), uuid4()
_STAMPED = {"taint_goal_id": _GOAL, "taint_invocation_id": _INVOCATION}
_UNSTAMPED = {"taint_goal_id": None, "taint_invocation_id": None}


def _message(conversation_id, **taint):
    return {
        "id": uuid4(),
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": "Booked.",
        "created_at": _NOW,
        "session_id": None,
        "model_name": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "tool_calls": None,
        "error": None,
        "request_id": None,
        "sources": None,
        "citations": [],
        "routed_butler": "general",
        "page_context": None,
        "captured_at": None,
        **taint,
    }


def _reads_the_whole_row_stamp(sql: str) -> bool:
    sql = " ".join(sql.split())
    return "->> 'taint_goal_id'" in sql and "->> 'taint_invocation_id'" in sql


@pytest.mark.parametrize(
    ("taint", "expected"),
    [(_STAMPED, (str(_GOAL), str(_INVOCATION))), (_UNSTAMPED, (None, None))],
    ids=["stamped", "unstamped"],
)
async def test_conversation_history_returns_the_taint_stamp(app, taint, expected):
    conversation_id = uuid4()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_message(conversation_id, **taint)])
    pool.fetchval = AsyncMock(return_value=1)
    pool.fetchrow = AsyncMock(
        return_value={
            "id": conversation_id,
            "butler_name": "general",
            "title": "t",
            "status": "active",
            "created_at": _NOW,
            "updated_at": _NOW,
            "message_count": 1,
            "routed_butler": None,
        }
    )
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/butlers/general/conversations/{conversation_id}/messages"
        )

    assert response.status_code == 200
    [message] = response.json()["data"]
    assert (message["taint_goal_id"], message["taint_invocation_id"]) == expected
    assert _reads_the_whole_row_stamp(pool.fetch.await_args.args[0])


async def test_every_message_reader_selects_the_stamp():
    conversation_id = uuid4()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_message(conversation_id, **_STAMPED)])
    pool.fetchval = AsyncMock(return_value=1)
    pool.fetchrow = AsyncMock(return_value=_message(conversation_id, **_STAMPED))

    [listed], _ = await message_list(pool, conversation_id)
    fetched = await message_get_by_id(pool, listed["id"])

    for row in (listed, fetched):
        assert (row["taint_goal_id"], row["taint_invocation_id"]) == (_GOAL, _INVOCATION)
    assert _reads_the_whole_row_stamp(pool.fetch.await_args.args[0])
    assert _reads_the_whole_row_stamp(pool.fetchrow.await_args.args[0])


def _insight(**taint):
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "origin_butler": "finance",
        "priority": 80,
        "category": "bill",
        "dedup_key": "bill:water",
        "cooldown_days": 7,
        "expires_at": None,
        "message": "Water bill due soon",
        "channel": "telegram",
        "metadata": {},
        "created_at": _NOW,
        "status": "pending",
        "delivered_at": None,
        "delivery_attempt_count": 0,
        "prepared_action_id": None,
        **taint,
    }


@pytest.mark.parametrize(
    ("taint", "expected"),
    [(_STAMPED, (str(_GOAL), str(_INVOCATION))), (_UNSTAMPED, (None, None))],
    ids=["stamped", "unstamped"],
)
async def test_insight_candidates_return_the_taint_stamp(app, taint, expected):
    importlib.import_module("butlers.api._roster.switchboard.router")
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[Record(_insight(**taint))])
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/switchboard/insights")

    assert response.status_code == 200
    [item] = response.json()["data"]
    assert (item["taint_goal_id"], item["taint_invocation_id"]) == expected
    assert _reads_the_whole_row_stamp(pool.fetch.await_args.args[0])
