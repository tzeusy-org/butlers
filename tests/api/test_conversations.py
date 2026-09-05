"""Tests for dashboard conversation API endpoints.

Condensed from 44 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list 200 + 503 combined, 422/404/400 error paths (parametrized), summary 200.

bu-mj2k2 adds coverage for the real Switchboard ingest wiring: pinned-target
routing, page_context propagation, unreachable/rejected submission handling,
and real session-metadata polling (previously fabricated stubs).

bu-p6ey8.1 replaces raw-session-completion polling with the conversation_reply
confirm-loop: the SSE poller now watches ``public.dashboard_messages`` for the
routed butler's deliberate reply instead of the spawned session's raw
transcript, adds sticky ``routed_butler`` stamping/follow-up pinning, and a
graceful ``SESSION_TIMEOUT`` event carrying a session link. See
tests/integration/test_conversation_reply_db.py for the real-Postgres
write-path coverage (mocked-pool-only green has previously hidden
search_path/schema bugs on main).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.conversations import (
    conversation_get_by_id_any_butler,
    conversation_get_or_create_by_thread,
    conversation_reply_create,
    conversation_search,
    conversation_set_routed_butler,
    message_create_idempotent,
    message_find_reply_since,
    message_set_session_id_if_null,
    resolve_resume_handle,
)
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.routers import conversations as conversations_router
from butlers.api.routers.conversations import (
    _SWITCHBOARD_BUTLER,
    _get_db_manager,
    _resolve_session_id,
    _stream_conversation_response,
    _submit_to_switchboard,
)
from butlers.core.dashboard_turns import DashboardTurnResult

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_CONV_ID = uuid4()
_BUTLER = "atlas"


def _make_conversation_row(**kw):
    defaults = {
        "id": _CONV_ID,
        "butler_name": _BUTLER,
        "title": "Hello world",
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
        "message_count": 2,
        "routed_butler": None,
    }
    defaults.update(kw)
    return defaults


def _app_with_mock_db(
    app: FastAPI,
    *,
    fetch_rows=None,
    fetchval_result=0,
    fetchrow_result=None,
    execute_result=None,
    db_raises=None,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if db_raises:
        mock_db.credential_shared_pool.side_effect = db_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# List conversations — 200 structure + 503 fallback
# ---------------------------------------------------------------------------


async def test_list_conversations_200_and_503(app):
    row = _make_conversation_row()
    _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert body["data"][0]["title"] == "Hello world"
    assert (
        not {
            "total_input_tokens",
            "total_output_tokens",
            "total_duration_ms",
        }
        & body["data"][0].keys()
    )

    # 503 when db unavailable
    _app_with_mock_db(app, db_raises=RuntimeError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get(f"/api/butlers/{_BUTLER}/conversations")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Search conversations — summary contract + snippet
# ---------------------------------------------------------------------------


async def test_search_conversations_returns_summary_fields_and_matching_snippet(app):
    latest_reply_at = _NOW + timedelta(minutes=1)
    row = _make_conversation_row(
        routed_butler="relationship",
        latest_assistant_reply_at=latest_reply_at,
        snippet="Alice is Bob's sister",
    )
    _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/search?q=Alice")

    assert resp.status_code == 200
    result = resp.json()["data"][0]
    assert set(result) == {
        "id",
        "butler_name",
        "title",
        "status",
        "created_at",
        "updated_at",
        "message_count",
        "routed_butler",
        "latest_assistant_reply_at",
        "snippet",
    }
    assert datetime.fromisoformat(result["latest_assistant_reply_at"]) == latest_reply_at
    assert result["snippet"] == "Alice is Bob's sister"


async def test_conversation_search_paginates_before_latest_reply_aggregate() -> None:
    """The assistant-reply lookup runs only for the final search page."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)

    await conversation_search(pool, butler_name=_BUTLER, query="Alice")

    query = pool.fetch.await_args.args[0]
    assert query.index("MAX(reply.created_at)") < query.index("FROM (")
    assert "reply.conversation_id = sub.id" in query


# ---------------------------------------------------------------------------
# Error paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,method,body,expected",
    [
        (f"/api/butlers/{_BUTLER}/conversations?status=invalid", "GET", None, 422),
        (f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}", "PATCH", {"title": "X"}, 404),
        (f"/api/butlers/{_BUTLER}/conversations/search", "GET", None, 400),
        (f"/api/butlers/{_BUTLER}/conversations/{uuid4()}/messages", "GET", None, 404),
    ],
    ids=["invalid-status-422", "patch-404", "search-no-query-400", "messages-conv-404"],
)
async def test_conversations_error_paths(app, path, method, body, expected):
    _app_with_mock_db(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if method == "GET":
            resp = await client.get(path)
        else:
            resp = await client.patch(path, json=body or {})
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------


async def test_conversation_summary_returns_stats(app):
    row = {
        "total_conversations": 5,
        "active_conversations": 3,
        "total_messages": 12,
    }
    _app_with_mock_db(app, fetchrow_result=row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/summary")
    assert resp.status_code == 200
    assert set(resp.json()) == {
        "total_conversations",
        "active_conversations",
        "total_messages",
    }


# ---------------------------------------------------------------------------
# build_dashboard_envelope — pinned_target / page_context (bu-mj2k2)
# ---------------------------------------------------------------------------


def test_build_dashboard_envelope_carries_pinned_target_and_page_context():
    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID,
        message_id=uuid4(),
        message_text="Alice is Bob's sister",
        pinned_target="relationship",
        page_context={
            "route": "/entities/concentration",
            "query_params": {"predicate": "child-of"},
        },
    )
    assert envelope["control"]["pinned_target"] == "relationship"
    assert envelope["payload"]["raw"]["page_context"] == {
        "route": "/entities/concentration",
        "query_params": {"predicate": "child-of"},
    }


def test_build_dashboard_envelope_omits_pinned_target_and_page_context_by_default():
    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hello"
    )
    assert "pinned_target" not in envelope["control"]
    assert "page_context" not in envelope["payload"]["raw"]


# ---------------------------------------------------------------------------
# _submit_to_switchboard — real MCP ingest dispatch (bu-mj2k2)
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMcpResult:
    def __init__(self, payload: dict, *, is_error: bool = False) -> None:
        self.content = [_FakeTextBlock(json.dumps(payload))]
        self.is_error = is_error


def _make_mcp_manager(mock_client: MagicMock) -> MagicMock:
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


async def test_submit_to_switchboard_calls_real_ingest_tool():
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "duplicate": False,
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi", pinned_target="finance"
    )

    result = await _submit_to_switchboard("finance", envelope, mcp_mgr=mgr)

    assert result == {
        "request_id": request_id,
        "status": "accepted",
        "duplicate": False,
        "triage_decision": "route_to",
        "triage_target": "finance",
    }
    mgr.get_client.assert_awaited_once_with(_SWITCHBOARD_BUTLER)
    mock_client.call_tool.assert_awaited_once_with("ingest", envelope)


async def test_submit_to_switchboard_unreachable_returns_none():
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect=ButlerUnreachableError("switchboard", cause=ConnectionRefusedError())
    )
    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi"
    )

    result = await _submit_to_switchboard("finance", envelope, mcp_mgr=mgr)

    assert result is None


async def test_submit_to_switchboard_raises_on_deterministic_rejection():
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "status": "error",
                "error": "pinned_target 'ghost' is not a registered, routable butler",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi", pinned_target="ghost"
    )

    with pytest.raises(ValueError, match="not a registered, routable butler"):
        await _submit_to_switchboard("ghost", envelope, mcp_mgr=mgr)


# ---------------------------------------------------------------------------
# conversations.py DB layer — conversation_reply_create / conversation_set_routed_butler
# / message_find_reply_since (mocked pool; see tests/integration/test_conversation_reply_db.py
# for the real-Postgres write-path coverage)
# ---------------------------------------------------------------------------


async def test_conversation_reply_create_persists_and_bumps_count():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)  # conversation exists

    result = await conversation_reply_create(pool, _CONV_ID, message="Recorded — correct?")

    assert result is not None
    assert result["role"] == "assistant"
    assert result["content"] == "Recorded — correct?"
    # message_create()'s INSERT, then the count-bump UPDATE.
    assert pool.execute.await_count == 2
    update_sql = pool.execute.await_args.args[0]
    assert "message_count = message_count + 1" in update_sql


async def test_conversation_reply_create_returns_none_for_missing_conversation():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)  # conversation does not exist

    result = await conversation_reply_create(pool, _CONV_ID, message="hello")

    assert result is None
    pool.execute.assert_not_awaited()


async def test_conversation_reply_create_persists_session_id_and_tool_calls():
    """bu-0ynlk.5: conversation_reply's ambient session id and this turn's
    tool calls flow through into the persisted message row."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)  # conversation exists
    session_id = uuid4()
    tool_calls = [{"name": "finance.get_budget"}]

    await conversation_reply_create(
        pool,
        _CONV_ID,
        message="You spent $312.",
        session_id=session_id,
        tool_calls=tool_calls,
    )

    insert_sql, *insert_args = pool.execute.await_args_list[0].args
    assert "INSERT INTO public.dashboard_messages" in insert_sql
    assert session_id in insert_args
    assert tool_calls in insert_args


async def test_conversation_reply_create_defaults_session_id_and_tool_calls_to_none():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)

    await conversation_reply_create(pool, _CONV_ID, message="Recorded — correct?")

    _insert_sql, *insert_args = pool.execute.await_args_list[0].args
    assert None in insert_args  # session_id / tool_calls both absent


async def test_message_set_session_id_if_null_updates_only_when_null():
    pool = AsyncMock()
    message_id = uuid4()
    session_id = uuid4()

    await message_set_session_id_if_null(pool, message_id, session_id=session_id)

    pool.execute.assert_awaited_once()
    sql, *args = pool.execute.await_args.args
    assert "session_id = $2" in sql
    assert "session_id IS NULL" in sql
    assert args == [message_id, session_id]


async def test_message_create_idempotent_returns_existing_message_without_incrementing():
    message_id = uuid4()
    existing = {
        "id": message_id,
        "conversation_id": _CONV_ID,
        "role": "user",
        "content": "Retry me",
        "created_at": _NOW,
        "session_id": None,
        "model_name": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "tool_calls": None,
        "error": None,
        "request_id": None,
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, existing])

    message, is_new = await message_create_idempotent(
        pool,
        message_id=message_id,
        conversation_id=_CONV_ID,
        role="user",
        content="Retry me",
    )

    assert is_new is False
    assert message == existing
    assert pool.fetchrow.await_count == 2


async def test_conversation_set_routed_butler_scopes_to_null_column():
    pool = AsyncMock()

    await conversation_set_routed_butler(pool, _CONV_ID, routed_butler="finance")

    pool.execute.assert_awaited_once()
    sql = pool.execute.await_args.args[0]
    assert "routed_butler IS NULL" in sql
    assert pool.execute.await_args.args[1:] == (_CONV_ID, "finance")


async def test_message_find_reply_since_returns_none_when_no_row():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    result = await message_find_reply_since(pool, _CONV_ID, since=_NOW)

    assert result is None


async def test_message_find_reply_since_deserializes_tool_calls_json_string():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "id": uuid4(),
            "content": "Recorded — correct?",
            "created_at": _NOW,
            "session_id": None,
            "model_name": None,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
            "tool_calls": '[{"name": "conversation_reply"}]',
            "error": None,
            "request_id": None,
        }
    )

    result = await message_find_reply_since(pool, _CONV_ID, since=_NOW)

    assert result["tool_calls"] == [{"name": "conversation_reply"}]


# ---------------------------------------------------------------------------
# bu-0ynlk.5: conversation_get_by_id_any_butler (dashboard-channel routing fix)
# ---------------------------------------------------------------------------


async def test_conversation_get_by_id_any_butler_returns_row_regardless_of_owner():
    """id-only lookup — no butler_name filter, since classification may route
    a dashboard turn to a different butler than the one the row was created
    under."""
    row = _make_conversation_row(butler_name="switchboard")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    result = await conversation_get_by_id_any_butler(pool, _CONV_ID)

    assert result == row
    sql, *args = pool.fetchrow.await_args.args
    assert "butler_name" not in sql.split("WHERE")[-1]
    assert args == [_CONV_ID]


async def test_conversation_get_by_id_any_butler_returns_none_when_missing():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    result = await conversation_get_by_id_any_butler(pool, _CONV_ID)

    assert result is None


# ---------------------------------------------------------------------------
# bu-ep4ks.8: channel-agnostic conversation anchor + provider resume ledger
# ---------------------------------------------------------------------------


def _transactional_anchor_pool(*fetchrow_results):
    connection = AsyncMock()
    connection.fetchrow = AsyncMock(side_effect=fetchrow_results)

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction = MagicMock(return_value=transaction)

    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=connection)
    acquired.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquired
    return pool, connection


async def test_conversation_get_or_create_by_thread_new_row_inserts_full_shape():
    inserted_row = _make_conversation_row(source_channel="telegram", source_thread_identity="t:1")
    pool, connection = _transactional_anchor_pool(inserted_row)

    conv, is_new = await conversation_get_or_create_by_thread(
        pool,
        butler_name=_BUTLER,
        source_channel="telegram",
        source_thread_identity="t:1",
        first_message="hello",
    )

    assert is_new is True
    assert conv == inserted_row
    connection.execute.assert_awaited_once()
    assert "pg_advisory_xact_lock" in connection.execute.await_args.args[0]
    connection.transaction.assert_called_once_with(isolation="read_committed")
    connection.fetchrow.assert_awaited_once()
    sql = connection.fetchrow.await_args.args[0]
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql


async def test_conversation_get_or_create_by_thread_conflict_reuses_existing_row():
    existing_row = _make_conversation_row(source_channel="telegram", source_thread_identity="t:1")
    # INSERT ... ON CONFLICT DO NOTHING RETURNING yields no row on conflict;
    # the follow-up SELECT recovers the winner.
    pool, connection = _transactional_anchor_pool(None, existing_row)

    conv, is_new = await conversation_get_or_create_by_thread(
        pool,
        butler_name=_BUTLER,
        source_channel="telegram",
        source_thread_identity="t:1",
        first_message="a retried first message",
    )

    assert is_new is False
    assert conv == existing_row
    assert connection.fetchrow.await_count == 2
    assert pool.acquire.call_count == 1


async def test_conversation_get_or_create_by_thread_raises_if_row_vanishes():
    pool, _connection = _transactional_anchor_pool(None, None)

    with pytest.raises(RuntimeError, match="disappeared"):
        await conversation_get_or_create_by_thread(
            pool,
            butler_name=_BUTLER,
            source_channel="telegram",
            source_thread_identity="t:1",
            first_message="hello",
        )


class TestResolveResumeHandle:
    """Pure eviction/TTL logic for the provider resume ledger."""

    def test_none_provider_session_returns_none(self):
        assert resolve_resume_handle(None, runtime_type="claude") is None

    def test_missing_handle_returns_none(self):
        session = {
            "provider_session_id": None,
            "provider_runtime_type": "claude",
            "provider_session_updated_at": _NOW,
        }
        assert resolve_resume_handle(session, runtime_type="claude", now=_NOW) is None

    def test_runtime_type_mismatch_returns_none(self):
        session = {
            "provider_session_id": "abc",
            "provider_runtime_type": "codex",
            "provider_session_updated_at": _NOW,
        }
        assert resolve_resume_handle(session, runtime_type="claude", now=_NOW) is None

    def test_fresh_handle_is_usable(self):
        session = {
            "provider_session_id": "abc",
            "provider_runtime_type": "claude",
            "provider_session_updated_at": _NOW - timedelta(minutes=5),
        }
        assert resolve_resume_handle(session, runtime_type="claude", now=_NOW) == "abc"

    def test_expired_handle_returns_none(self):
        session = {
            "provider_session_id": "abc",
            "provider_runtime_type": "claude",
            "provider_session_updated_at": _NOW - timedelta(hours=25),
        }
        assert resolve_resume_handle(session, runtime_type="claude", now=_NOW) is None

    def test_custom_ttl_is_honored(self):
        session = {
            "provider_session_id": "abc",
            "provider_runtime_type": "claude",
            "provider_session_updated_at": _NOW - timedelta(minutes=10),
        }
        assert (
            resolve_resume_handle(session, runtime_type="claude", ttl_seconds=60, now=_NOW) is None
        )


# ---------------------------------------------------------------------------
# _resolve_session_id — best-effort request_id -> session_id lookup, shared
# by the SESSION_TIMEOUT session link and POST .../cancel (bu-ep4ks.2)
# ---------------------------------------------------------------------------


async def test_resolve_session_id_returns_none_without_request_id():
    mock_db = MagicMock(spec=DatabaseManager)

    result = await _resolve_session_id(db=mock_db, routed_butler="finance", request_id=None)

    assert result is None
    mock_db.pool.assert_not_called()


async def test_resolve_session_id_returns_none_when_pool_missing():
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("no pool")

    result = await _resolve_session_id(db=mock_db, routed_butler="ghost", request_id=str(uuid4()))

    assert result is None


async def test_resolve_session_id_returns_session_id():
    session_id = uuid4()
    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = butler_pool

    result = await _resolve_session_id(db=mock_db, routed_butler="finance", request_id=str(uuid4()))

    assert result == session_id


# ---------------------------------------------------------------------------
# End-to-end SSE: reply arrives, sticky routing, timeout (bu-p6ey8.1)
# ---------------------------------------------------------------------------


def _make_reply_row(**kw):
    defaults = {
        "id": uuid4(),
        "content": "Recorded: Alice child-of Bob — correct?",
        "created_at": _NOW,
        "session_id": None,
        "model_name": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "tool_calls": None,
        "error": None,
        "request_id": None,
    }
    defaults.update(kw)
    return defaults


def _app_with_mock_db_and_mcp(app: FastAPI, *, mcp_manager, reply_row=None, conversation_row=None):
    shared_pool = AsyncMock()

    def _turn_row(
        outcome: str,
        *,
        message_id: UUID,
        conversation_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> dict[str, object]:
        """Return the SQL control-plane shape used by the dashboard stream."""
        return {
            "outcome": outcome,
            "message_id": message_id,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "target_butler": None,
            "target_kind": None,
            "route_inbox_id": None,
            "cancel_requested_at": None,
            "cancel_confirmed_at": None,
            "terminal_state": None,
            "terminal_at": None,
        }

    async def fetchrow(sql: str, *args: object):
        # The stream opens a durable row before returning SSE, then claims
        # ingress immediately before it calls Switchboard. Keep this helper's
        # ordinary reply tests independent from the control-plane internals.
        if "dashboard_turn_open" in sql:
            return _turn_row("ready", message_id=args[0], conversation_id=args[1])
        if "dashboard_turn_claim_ingress" in sql:
            return _turn_row("dispatch", message_id=args[0])
        if "dashboard_turn_bind_ingress" in sql:
            return _turn_row("accepted", message_id=args[0], request_id=args[1])
        if "dashboard_turn_record_ingress_failure" in sql:
            return _turn_row("active", message_id=args[0])
        if conversation_row is not None and "FROM public.dashboard_conversations" in sql:
            return conversation_row
        return reply_row

    shared_pool.fetchrow = AsyncMock(side_effect=fetchrow)
    shared_pool.execute = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    return app, shared_pool


async def test_create_conversation_streams_conversation_reply_message(app):
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "duplicate": False,
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    reply_row = _make_reply_row()
    app, shared_pool = _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=reply_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "Alice is Bob's sister"},
        )

    assert resp.status_code == 200
    assert "message_complete" in resp.text
    assert "Recorded: Alice child-of Bob" in resp.text
    # model_name/tokens are null — persisted mid-session, before the routed
    # session's own accounting exists.
    assert '"model_name": null' in resp.text


async def test_create_conversation_backfills_session_id_when_reply_row_lacks_one(app):
    """bu-0ynlk.5: conversation_reply best-effort-stamps session_id at write
    time; when that ambient context was absent, the poller must resolve it
    via request_id -> sessions.id on the routed butler, persist it, and
    include it on the emitted message_complete event."""
    request_id = str(uuid4())
    resolved_session_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "duplicate": False,
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    reply_row = _make_reply_row()  # session_id is None — ambient context absent
    app, shared_pool = _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=reply_row)

    mock_db = app.dependency_overrides[_get_db_manager]()
    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=resolved_session_id)
    mock_db.pool = MagicMock(return_value=butler_pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "Alice is Bob's sister"},
        )

    assert resp.status_code == 200
    assert f'"session_id": "{resolved_session_id}"' in resp.text
    butler_pool.fetchval.assert_awaited_once()
    backfill_calls = [
        call
        for call in shared_pool.execute.await_args_list
        if "dashboard_messages" in call.args[0] and "session_id = $2" in call.args[0]
    ]
    assert len(backfill_calls) == 1
    assert backfill_calls[0].args[1:] == (reply_row["id"], resolved_session_id)


async def test_create_conversation_keeps_ambient_session_id_without_resolving(app):
    """When conversation_reply already stamped session_id, the poller must
    not attempt the request_id -> sessions.id fallback resolution at all."""
    request_id = str(uuid4())
    ambient_session_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "duplicate": False,
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    reply_row = _make_reply_row(session_id=ambient_session_id)
    app, shared_pool = _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=reply_row)

    mock_db = app.dependency_overrides[_get_db_manager]()
    mock_db.pool = MagicMock(side_effect=AssertionError("must not resolve when already stamped"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "Alice is Bob's sister"},
        )

    assert resp.status_code == 200
    assert f'"session_id": "{ambient_session_id}"' in resp.text


async def test_create_conversation_streams_sources_on_the_message_complete_event(app):
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "duplicate": False,
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    reply_row = _make_reply_row(
        content="You spent $412 on groceries this month.",
        sources=["finance:transactions (category=groceries, month=2026-09)"],
    )
    app, shared_pool = _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=reply_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "how much did I spend on groceries this month?"},
        )

    assert resp.status_code == 200
    assert "event: message_complete" in resp.text
    assert '"sources": ["finance:transactions (category=groceries, month=2026-09)"]' in resp.text


async def test_create_conversation_defaults_sources_to_empty_list_when_absent(app):
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "duplicate": False,
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    reply_row = _make_reply_row()
    app, shared_pool = _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=reply_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "Alice is Bob's sister"},
        )

    assert resp.status_code == 200
    assert '"sources": []' in resp.text


async def test_create_conversation_retry_reuses_original_conversation_for_message_id(app):
    """A lost initial SSE response retries against its original thread.

    The browser has not received ``conversation_created`` in this case, so it
    must retry ``POST /conversations`` rather than the follow-up endpoint.
    Reusing the client message UUID must therefore find the already-persisted
    user row before a second conversation is created.
    """
    message_id = uuid4()
    original_conversation_id = uuid4()
    original_conversation = _make_conversation_row(id=original_conversation_id)
    existing_user_message = {
        "id": message_id,
        "conversation_id": original_conversation_id,
        "role": "user",
        "content": "Retry me",
        "created_at": _NOW,
        "session_id": None,
        "model_name": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "tool_calls": None,
        "error": None,
        "request_id": None,
    }
    reply_row = _make_reply_row(created_at=_NOW + timedelta(seconds=1))

    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"request_id": request_id, "status": "accepted"})
    )
    app, shared_pool = _app_with_mock_db_and_mcp(
        app,
        mcp_manager=_make_mcp_manager(mock_client),
        reply_row=None,
    )

    def _turn(outcome: str, *, bound_request_id: UUID | None = None):
        return {
            "outcome": outcome,
            "message_id": message_id,
            "conversation_id": original_conversation_id,
            "request_id": bound_request_id,
            "target_butler": None,
            "target_kind": None,
            "route_inbox_id": None,
            "cancel_requested_at": None,
            "cancel_confirmed_at": None,
            "terminal_state": None,
            "terminal_at": None,
        }

    async def fetchrow(sql, *_args):
        if "dashboard_turn_open" in sql:
            return _turn("ready")
        if "dashboard_turn_claim_ingress" in sql:
            return _turn("dispatch")
        if "dashboard_turn_bind_ingress" in sql:
            return _turn("accepted", bound_request_id=UUID(request_id))
        if "FROM public.dashboard_messages" in sql and "WHERE id = $1" in sql:
            return existing_user_message
        if "FROM public.dashboard_conversations" in sql:
            return original_conversation
        if "FROM public.dashboard_messages" in sql:
            return reply_row
        raise AssertionError(f"Unexpected query: {sql}")

    shared_pool.fetchrow = AsyncMock(side_effect=fetchrow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "Retry me", "message_id": str(message_id)},
        )

    assert resp.status_code == 200
    assert str(original_conversation_id) in resp.text
    sent_envelope = mock_client.call_tool.call_args.args[1]
    assert sent_envelope["event"]["external_event_id"] == str(message_id)
    assert sent_envelope["source"]["endpoint_identity"] == (
        f"dashboard:web:{original_conversation_id}"
    )
    assert not any(
        "INSERT INTO public.dashboard_conversations" in call.args[0]
        for call in shared_pool.execute.await_args_list
    )


async def test_create_conversation_rejects_message_id_reused_for_different_content(app):
    message_id = uuid4()
    original_conversation_id = uuid4()
    existing_user_message = {
        "id": message_id,
        "conversation_id": original_conversation_id,
        "role": "user",
        "content": "Original message",
        "created_at": _NOW,
        "session_id": None,
        "model_name": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "tool_calls": None,
        "error": None,
        "request_id": None,
    }
    app, shared_pool = _app_with_mock_db_and_mcp(
        app,
        mcp_manager=MagicMock(spec=MCPClientManager),
        reply_row=None,
    )

    async def fetchrow(sql, *_args):
        if "FROM public.dashboard_messages" in sql:
            return existing_user_message
        if "FROM public.dashboard_conversations" in sql:
            return _make_conversation_row(id=original_conversation_id)
        raise AssertionError(f"Unexpected query: {sql}")

    shared_pool.fetchrow = AsyncMock(side_effect=fetchrow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "Different message", "message_id": str(message_id)},
        )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MESSAGE_ID_CONFLICT"
    assert not any(
        "INSERT INTO public.dashboard_conversations" in call.args[0]
        for call in shared_pool.execute.await_args_list
    )


async def test_create_conversation_stamps_routed_butler_on_first_route(app):
    """Switchboard-addressed (classification-routed) conversations stamp
    routed_butler on the first route_to decision."""
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)
    app, shared_pool = _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=_make_reply_row())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations",
            json={"message": "The concentration chart is empty"},
        )

    assert resp.status_code == 200
    stamp_calls = [
        call
        for call in shared_pool.execute.await_args_list
        if "routed_butler" in call.args[0] and "IS NULL" in call.args[0]
    ]
    assert len(stamp_calls) == 1
    assert stamp_calls[0].args[-1] == "finance"


async def test_send_message_pins_to_routed_butler_when_already_routed(app):
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"request_id": request_id, "status": "accepted"})
    )
    mgr = _make_mcp_manager(mock_client)
    conv_row = _make_conversation_row(butler_name=_SWITCHBOARD_BUTLER, routed_butler="finance")
    app, shared_pool = _app_with_mock_db_and_mcp(
        app,
        mcp_manager=mgr,
        conversation_row=conv_row,
        reply_row=_make_reply_row(),
    )
    shared_pool.fetch = AsyncMock(return_value=[])
    shared_pool.fetchval = AsyncMock(return_value=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{_CONV_ID}/messages",
            json={"message": "Actually it's March 3rd"},
        )

    assert resp.status_code == 200
    sent_envelope = mock_client.call_tool.call_args.args[1]
    assert sent_envelope["control"]["pinned_target"] == "finance"


async def test_send_message_does_not_pin_when_not_yet_routed(app):
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"request_id": request_id, "status": "accepted"})
    )
    mgr = _make_mcp_manager(mock_client)
    conv_row = _make_conversation_row(butler_name=_SWITCHBOARD_BUTLER, routed_butler=None)
    app, shared_pool = _app_with_mock_db_and_mcp(
        app,
        mcp_manager=mgr,
        conversation_row=conv_row,
        reply_row=_make_reply_row(),
    )
    shared_pool.fetch = AsyncMock(return_value=[])
    shared_pool.fetchval = AsyncMock(return_value=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{_CONV_ID}/messages",
            json={"message": "The concentration chart is empty"},
        )

    assert resp.status_code == 200
    sent_envelope = mock_client.call_tool.call_args.args[1]
    assert "pinned_target" not in sent_envelope["control"]


async def test_create_conversation_switchboard_offline_emits_retry_error(app):
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect=ButlerUnreachableError("switchboard", cause=ConnectionRefusedError())
    )
    _app_with_mock_db_and_mcp(app, mcp_manager=mgr, reply_row=_make_reply_row())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={"message": "hello"},
        )

    assert resp.status_code == 200
    assert "SWITCHBOARD_UNAVAILABLE" in resp.text
    assert "Switchboard offline" in resp.text


# ---------------------------------------------------------------------------
# Timeout: no conversation_reply arrives within the poll window (bu-p6ey8.1)
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for starlette.requests.Request — never disconnects."""

    async def is_disconnected(self) -> bool:
        return False


def _dashboard_turn_result(
    outcome: str,
    *,
    message_id: UUID,
    request_id: UUID | None = None,
    target_kind: str | None = None,
    target_butler: str | None = None,
) -> DashboardTurnResult:
    return DashboardTurnResult(
        outcome=outcome,
        message_id=message_id,
        conversation_id=_CONV_ID,
        request_id=request_id,
        target_butler=target_butler,
        target_kind=target_kind,
        route_inbox_id=None,
        cancel_requested_at=None,
        cancel_confirmed_at=None,
        terminal_state=None,
        terminal_at=None,
    )


def _dispatch_receipts(events: list[str]) -> list[dict[str, str | None]]:
    return [
        json.loads(event.split("data: ", 1)[1])
        for event in events
        if "event: dispatch_accepted" in event
    ]


async def test_stream_receipt_stays_targetless_until_a_durable_route_exists(monkeypatch):
    """A pre-routing classification target is not a receipt-worthy route."""
    message_id = uuid4()
    request_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "relationship",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "accepted", message_id=message_id, request_id=request_id
            )
        ),
    )
    monkeypatch.setattr(
        conversations_router,
        "dispatch_status",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "active", message_id=message_id, request_id=request_id
            )
        ),
    )

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == [{"routed_butler": None}]


async def test_stream_receipt_uses_only_the_durable_route_target(monkeypatch):
    """A later receipt names only the committed target, never a triage proposal."""
    message_id = uuid4()
    request_id = uuid4()
    durable_route = _dashboard_turn_result(
        "active",
        message_id=message_id,
        request_id=request_id,
        target_kind="route",
        target_butler="finance",
    )
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "relationship",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "accepted", message_id=message_id, request_id=request_id
            )
        ),
    )
    status = AsyncMock(side_effect=[durable_route, durable_route])
    monkeypatch.setattr(conversations_router, "dispatch_status", status)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    # Even when the first safe observation has a route, the initial receipt
    # stays targetless. A second authoritative observation may upgrade it.
    assert _dispatch_receipts(events) == [
        {"routed_butler": None},
        {"routed_butler": "finance"},
    ]
    assert status.await_count == 2


async def test_stream_receipt_upgrades_once_when_a_durable_route_appears(monkeypatch):
    """A later route claim upgrades the targetless receipt exactly once."""
    message_id = uuid4()
    request_id = uuid4()
    initial_status = _dashboard_turn_result("active", message_id=message_id, request_id=request_id)
    durable_route = _dashboard_turn_result(
        "active",
        message_id=message_id,
        request_id=request_id,
        target_kind="route",
        target_butler="finance",
    )
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "relationship",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "accepted", message_id=message_id, request_id=request_id
            )
        ),
    )
    status = AsyncMock(side_effect=[initial_status, durable_route, durable_route])
    monkeypatch.setattr(conversations_router, "dispatch_status", status)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(side_effect=[None, _make_reply_row()])
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    monkeypatch.setattr(conversations_router.asyncio, "sleep", AsyncMock())

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == [
        {"routed_butler": None},
        {"routed_butler": "finance"},
    ]
    # A third identical durable-route observation cannot duplicate the upgrade.
    assert status.await_count == 3


async def test_stream_does_not_emit_a_receipt_while_bind_ingress_is_cancelling(monkeypatch):
    """A pending Stop after Switchboard acceptance is not a positive receipt."""
    message_id = uuid4()
    request_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "cancelling", message_id=message_id, request_id=request_id
            )
        ),
    )

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == []
    assert any("INGEST_IN_PROGRESS" in event for event in events)


async def test_stream_continues_reply_polling_when_bind_ingress_is_finished(monkeypatch):
    """A fast durable completion has no receipt, but its reply remains readable."""
    message_id = uuid4()
    request_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "finished",
                message_id=message_id,
                request_id=request_id,
                target_kind="route",
                target_butler="finance",
            )
        ),
    )
    status = AsyncMock(
        return_value=_dashboard_turn_result(
            "finished",
            message_id=message_id,
            request_id=request_id,
            target_kind="route",
            target_butler="finance",
        )
    )
    monkeypatch.setattr(conversations_router, "dispatch_status", status)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == []
    assert not any("TURN_OUTCOME_UNKNOWN" in event for event in events)
    assert any("event: message_complete" in event for event in events)
    # Initial status is observed for receipt safety, then polling continues
    # normally until the already-persisted reply is found.
    assert status.await_count == 2


@pytest.mark.parametrize(
    ("bind_outcome", "expected_code"),
    [
        ("cancelled", "SESSION_CANCELLED"),
        ("ambiguous", "TURN_OUTCOME_UNKNOWN"),
        ("conflict", "SWITCHBOARD_ERROR"),
    ],
)
async def test_stream_does_not_emit_a_receipt_for_terminal_or_conflicting_bind_outcomes(
    monkeypatch,
    bind_outcome: str,
    expected_code: str,
):
    """Only a durably active ingress can yield a current-turn receipt."""
    message_id = uuid4()
    request_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                bind_outcome,
                message_id=message_id,
                request_id=request_id,
            )
        ),
    )
    status = AsyncMock()
    monkeypatch.setattr(conversations_router, "dispatch_status", status)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == []
    assert any(expected_code in event for event in events)
    status.assert_not_awaited()


async def test_stream_does_not_emit_a_receipt_without_an_immutable_message_id():
    """Legacy streams cannot prove a durable current-turn receipt."""
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(uuid4()),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "relationship",
            }
        )
    )
    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=uuid4(),
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
        )
    ]

    assert _dispatch_receipts(events) == []


async def test_reused_accepted_ingress_emits_one_targetless_durable_receipt(monkeypatch):
    """An observer of accepted ingress reports only its durable target state."""
    message_id = uuid4()
    request_id = uuid4()
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "accepted",
                message_id=message_id,
                request_id=request_id,
                target_kind="route",
                target_butler="relationship",
            )
        ),
    )
    monkeypatch.setattr(
        conversations_router,
        "dispatch_status",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "active", message_id=message_id, request_id=request_id
            )
        ),
    )
    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock()

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=mgr,
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == [{"routed_butler": None}]
    mgr.get_client.assert_not_awaited()


async def test_stream_does_not_emit_a_receipt_when_durable_status_is_unavailable(monkeypatch):
    """A failed status read cannot support even a targetless receipt."""
    message_id = uuid4()
    request_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "relationship",
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "accepted", message_id=message_id, request_id=request_id
            )
        ),
    )
    monkeypatch.setattr(
        conversations_router, "dispatch_status", AsyncMock(side_effect=RuntimeError())
    )
    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == []


async def test_stream_does_not_emit_a_route_receipt_for_a_terminal_action(monkeypatch):
    """Non-route terminal actions have their own truthful surface, not a receipt."""
    message_id = uuid4()
    request_id = uuid4()
    terminal_action = _dashboard_turn_result(
        "external_action_in_progress",
        message_id=message_id,
        request_id=request_id,
        target_kind="bug_report",
    )
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(request_id),
                "status": "accepted",
                "triage_decision": "file_bug_report",
                "triage_target": None,
            }
        )
    )
    monkeypatch.setattr(
        conversations_router,
        "claim_ingress",
        AsyncMock(return_value=_dashboard_turn_result("dispatch", message_id=message_id)),
    )
    monkeypatch.setattr(
        conversations_router,
        "bind_ingress",
        AsyncMock(
            return_value=_dashboard_turn_result(
                "accepted", message_id=message_id, request_id=request_id
            )
        ),
    )
    monkeypatch.setattr(
        conversations_router, "dispatch_status", AsyncMock(return_value=terminal_action)
    )
    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    events = [
        chunk
        async for chunk in _stream_conversation_response(
            request=_FakeRequest(),
            butler_name=_SWITCHBOARD_BUTLER,
            conversation_id=_CONV_ID,
            message_created_at=_NOW - timedelta(seconds=1),
            envelope=build_dashboard_envelope(
                conversation_id=_CONV_ID,
                message_id=message_id,
                message_text="hi",
                pinned_target=None,
            ),
            db=mock_db,
            mcp_mgr=_make_mcp_manager(mock_client),
            message_id=message_id,
        )
    ]

    assert _dispatch_receipts(events) == []


async def test_stream_conversation_response_times_out_gracefully(monkeypatch):
    """No conversation_reply lands before the poll window closes: emits a
    graceful SESSION_TIMEOUT error carrying the routed session's id, and the
    stream still terminates with `done` (the thread stays open for a late
    reply — see tests/integration/test_conversation_reply_db.py)."""
    monkeypatch.setattr(conversations_router, "_SESSION_TIMEOUT_S", 0.05)
    monkeypatch.setattr(conversations_router, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(conversations_router, "_KEEPALIVE_INTERVAL_S", 1000.0)

    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=None)  # conversation_reply never arrives
    shared_pool.execute = AsyncMock(return_value=None)

    timed_out_session_id = uuid4()
    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=timed_out_session_id)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.return_value = butler_pool

    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi", pinned_target=None
    )

    events: list[str] = []
    async for chunk in _stream_conversation_response(
        request=_FakeRequest(),
        butler_name=_SWITCHBOARD_BUTLER,
        conversation_id=_CONV_ID,
        message_created_at=_NOW - timedelta(seconds=1),
        envelope=envelope,
        db=mock_db,
        mcp_mgr=mgr,
    ):
        events.append(chunk)

    full_stream = "".join(events)
    assert "SESSION_TIMEOUT" in full_stream
    assert str(timed_out_session_id) in full_stream
    assert "No reply yet" in full_stream
    # The stream still terminates with `done` after the graceful timeout.
    error_idx = full_stream.index("event: error")
    done_idx = full_stream.index("event: done")
    assert error_idx < done_idx


async def test_stream_conversation_response_emits_keepalives_then_late_reply(monkeypatch):
    """The poller must survive several no-reply-yet iterations — emitting a
    keepalive comment each time the interval elapses (conversations.py:364-367)
    — and still surface the reply once it lands late, after
    ``message_find_reply_since`` has returned ``None`` a few times
    (conversations.py:389-393).

    Every existing poller test either finds the reply on the very first poll
    (``test_create_conversation_streams_conversation_reply_message``) or never
    finds one at all (``test_stream_conversation_response_times_out_gracefully``).
    Neither exercises the loop actually looping — this pins the multi-iteration
    keepalive + late-arrival path specifically.
    """
    monkeypatch.setattr(conversations_router, "_SESSION_TIMEOUT_S", 10.0)
    monkeypatch.setattr(conversations_router, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(conversations_router, "_KEEPALIVE_INTERVAL_S", 0.02)

    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)

    reply_row = _make_reply_row()
    shared_pool = AsyncMock()
    # message_find_reply_since polls four times finding nothing (the poller
    # loop must survive all of them, emitting keepalives along the way)
    # before the real reply lands on the fifth poll.
    shared_pool.fetchrow = AsyncMock(side_effect=[None, None, None, None, reply_row])
    shared_pool.execute = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi", pinned_target=None
    )

    events: list[str] = []
    async for chunk in _stream_conversation_response(
        request=_FakeRequest(),
        butler_name=_SWITCHBOARD_BUTLER,
        conversation_id=_CONV_ID,
        message_created_at=_NOW - timedelta(seconds=1),
        envelope=envelope,
        db=mock_db,
        mcp_mgr=mgr,
    ):
        events.append(chunk)

    full_stream = "".join(events)
    # The late-arrival path: the poller looped past multiple None polls
    # before the reply landed.
    assert shared_pool.fetchrow.await_count == 5
    # The keepalive path: at least one keepalive comment fired while polling.
    assert full_stream.count(": keepalive") >= 1
    assert "event: token" in full_stream
    assert "Recorded: Alice child-of Bob" in full_stream
    assert "event: done" in full_stream
    # Keepalives must precede the eventual reply — proves they were emitted
    # during the wait, not after the fact.
    assert full_stream.index(": keepalive") < full_stream.index("event: token")


# ---------------------------------------------------------------------------
# _ACTIVE_TURNS registration lifecycle + POST .../cancel (bu-ep4ks.2)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_active_turns():
    """_ACTIVE_TURNS is process-local module state — never leak between tests."""
    conversations_router._ACTIVE_TURNS.clear()
    yield
    conversations_router._ACTIVE_TURNS.clear()


async def test_stream_conversation_response_registers_and_clears_active_turn():
    """The turn is cancellable while streaming and gone once the reply lands."""
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "triage_decision": "route_to",
                "triage_target": "finance",
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)

    reply_row = _make_reply_row()
    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=reply_row)
    shared_pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi", pinned_target=None
    )

    assert _CONV_ID not in conversations_router._ACTIVE_TURNS
    gen = _stream_conversation_response(
        request=_FakeRequest(),
        butler_name=_SWITCHBOARD_BUTLER,
        conversation_id=_CONV_ID,
        message_created_at=_NOW - timedelta(seconds=1),
        envelope=envelope,
        db=mock_db,
        mcp_mgr=mgr,
    )
    await anext(gen)  # advance past the Switchboard submission step
    assert conversations_router._ACTIVE_TURNS[_CONV_ID] == {
        "routed_butler": "finance",
        "request_id": request_id,
    }

    async for _ in gen:
        pass

    assert _CONV_ID not in conversations_router._ACTIVE_TURNS


async def test_stream_conversation_response_registers_switchboard_for_pass_through_turn():
    """A normal unpinned turn starts cancellable at the classifier boundary."""
    request_id = str(uuid4())
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": request_id,
                "status": "accepted",
                "triage_decision": "pass_through",
                "triage_target": None,
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool

    envelope = build_dashboard_envelope(
        conversation_id=_CONV_ID, message_id=uuid4(), message_text="hi", pinned_target=None
    )
    gen = _stream_conversation_response(
        request=_FakeRequest(),
        butler_name=_SWITCHBOARD_BUTLER,
        conversation_id=_CONV_ID,
        message_created_at=_NOW - timedelta(seconds=1),
        envelope=envelope,
        db=mock_db,
        mcp_mgr=mgr,
    )

    await anext(gen)

    assert conversations_router._ACTIVE_TURNS[_CONV_ID] == {
        "routed_butler": _SWITCHBOARD_BUTLER,
        "request_id": request_id,
    }

    async for _ in gen:
        pass


async def test_cancel_with_no_active_turn_is_benign_noop(app):
    """Stop after the turn already finished must never claim it stopped anything."""
    app.dependency_overrides[_get_db_manager] = lambda: MagicMock(spec=DatabaseManager)
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock(spec=MCPClientManager)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/butlers/{_BUTLER}/conversations/{uuid4()}/cancel")

    assert resp.status_code == 200
    assert resp.json() == {
        "cancelled": False,
        "already_finished": True,
        "conversation_id": None,
        "session_id": None,
        "message": None,
    }


async def test_cancel_uses_switchboard_before_classifier_handoff(app):
    """Stop still cancels the live classifier when no domain target exists yet."""
    conversation_id = uuid4()
    request_id = str(uuid4())
    switchboard_session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": _SWITCHBOARD_BUTLER,
        "request_id": request_id,
    }

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(
        return_value=_make_conversation_row(
            id=conversation_id,
            butler_name=_SWITCHBOARD_BUTLER,
            routed_butler=None,
        )
    )
    switchboard_pool = AsyncMock()
    switchboard_pool.fetchval = AsyncMock(return_value=switchboard_session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.return_value = switchboard_pool

    switchboard_client = MagicMock()
    switchboard_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(switchboard_session_id)})
    )
    mgr = _make_mcp_manager(switchboard_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{conversation_id}/cancel"
        )

    assert response.status_code == 200
    assert response.json() == {
        "cancelled": True,
        "already_finished": False,
        "conversation_id": None,
        "session_id": str(switchboard_session_id),
        "message": None,
    }
    mock_db.pool.assert_called_once_with(_SWITCHBOARD_BUTLER)
    switchboard_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(switchboard_session_id)}
    )


async def test_cancel_prefers_post_classification_target_over_switchboard(app):
    """Stop must kill the domain session after an LLM classifier handoff."""
    conversation_id = uuid4()
    request_id = str(uuid4())
    switchboard_session_id = uuid4()
    finance_session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": _SWITCHBOARD_BUTLER,
        "request_id": request_id,
    }

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(
        return_value=_make_conversation_row(
            id=conversation_id,
            butler_name=_SWITCHBOARD_BUTLER,
            routed_butler="finance",
        )
    )
    switchboard_pool = AsyncMock()
    switchboard_pool.fetchval = AsyncMock(return_value=switchboard_session_id)
    finance_pool = AsyncMock()
    finance_pool.fetchval = AsyncMock(return_value=finance_session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.side_effect = {
        _SWITCHBOARD_BUTLER: switchboard_pool,
        "finance": finance_pool,
    }.__getitem__

    switchboard_client = MagicMock()
    switchboard_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(switchboard_session_id)})
    )
    finance_client = MagicMock()
    finance_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(finance_session_id)})
    )
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect={
            _SWITCHBOARD_BUTLER: switchboard_client,
            "finance": finance_client,
        }.__getitem__
    )

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{conversation_id}/cancel"
        )

    assert response.status_code == 200
    assert response.json() == {
        "cancelled": True,
        "already_finished": False,
        "conversation_id": None,
        "session_id": str(finance_session_id),
        "message": None,
    }
    mock_db.pool.assert_called_once_with("finance")
    mgr.get_client.assert_awaited_once_with("finance")
    finance_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(finance_session_id)}
    )
    switchboard_client.call_tool.assert_not_awaited()


async def test_cancel_with_post_classification_target_pending_session_stays_retryable(app):
    """A known target without a session is still active routing, not a completed turn."""
    conversation_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": _SWITCHBOARD_BUTLER,
        "request_id": str(uuid4()),
    }

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(
        return_value=_make_conversation_row(
            id=conversation_id,
            butler_name=_SWITCHBOARD_BUTLER,
            routed_butler="finance",
        )
    )
    switchboard_pool = AsyncMock()
    switchboard_pool.fetchval = AsyncMock(return_value=None)
    finance_pool = AsyncMock()
    finance_pool.fetchval = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.side_effect = {
        _SWITCHBOARD_BUTLER: switchboard_pool,
        "finance": finance_pool,
    }.__getitem__
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock()

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{conversation_id}/cancel"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is False
    assert body["already_finished"] is False
    assert body["session_id"] is None
    assert body["message"]
    mock_db.pool.assert_called_once_with("finance")
    mgr.get_client.assert_not_awaited()


@pytest.mark.parametrize(
    ("pool_side_effect", "fetchval_side_effect", "expected_message"),
    [
        (KeyError("missing pool"), None, "Could not locate finance"),
        (None, RuntimeError("database unavailable"), "Could not inspect finance"),
    ],
)
async def test_cancel_surfaces_target_session_lookup_failures(
    app,
    pool_side_effect,
    fetchval_side_effect,
    expected_message,
):
    """Lookup infrastructure failures are not mislabeled as routing progress."""
    conversation_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": "finance",
        "request_id": str(uuid4()),
    }

    finance_pool = AsyncMock()
    finance_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = finance_pool
    if pool_side_effect is not None:
        mock_db.pool.side_effect = pool_side_effect
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock()

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/{_BUTLER}/conversations/{conversation_id}/cancel"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is False
    assert body["already_finished"] is False
    assert body["session_id"] is None
    assert expected_message in body["message"]
    mgr.get_client.assert_not_awaited()


async def test_cancel_rechecks_handoff_after_switchboard_has_finished(app):
    """Stop must not miss a target stamped while the classifier session finished."""
    conversation_id = uuid4()
    request_id = str(uuid4())
    switchboard_session_id = uuid4()
    finance_session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": _SWITCHBOARD_BUTLER,
        "request_id": request_id,
    }

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(
        side_effect=[
            _make_conversation_row(
                id=conversation_id,
                butler_name=_SWITCHBOARD_BUTLER,
                routed_butler=None,
            ),
            _make_conversation_row(
                id=conversation_id,
                butler_name=_SWITCHBOARD_BUTLER,
                routed_butler="finance",
            ),
        ]
    )
    switchboard_pool = AsyncMock()
    switchboard_pool.fetchval = AsyncMock(return_value=switchboard_session_id)
    finance_pool = AsyncMock()
    finance_pool.fetchval = AsyncMock(return_value=finance_session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.side_effect = {
        _SWITCHBOARD_BUTLER: switchboard_pool,
        "finance": finance_pool,
    }.__getitem__

    switchboard_client = MagicMock()
    switchboard_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": False, "session_id": str(switchboard_session_id)})
    )
    finance_client = MagicMock()
    finance_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(finance_session_id)})
    )
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect={
            _SWITCHBOARD_BUTLER: switchboard_client,
            "finance": finance_client,
        }.__getitem__
    )

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{conversation_id}/cancel"
        )

    assert response.status_code == 200
    assert response.json() == {
        "cancelled": True,
        "already_finished": False,
        "conversation_id": None,
        "session_id": str(finance_session_id),
        "message": None,
    }
    mgr.get_client.assert_has_awaits(
        [
            ((_SWITCHBOARD_BUTLER,), {}),
            (("finance",), {}),
        ]
    )
    finance_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(finance_session_id)}
    )


async def test_cancel_rechecks_handoff_after_switchboard_cancel_succeeds(app):
    """Stop must not report success before checking a target stamped during cancellation."""
    conversation_id = uuid4()
    request_id = str(uuid4())
    switchboard_session_id = uuid4()
    finance_session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": _SWITCHBOARD_BUTLER,
        "request_id": request_id,
    }

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(
        side_effect=[
            _make_conversation_row(
                id=conversation_id,
                butler_name=_SWITCHBOARD_BUTLER,
                routed_butler=None,
            ),
            _make_conversation_row(
                id=conversation_id,
                butler_name=_SWITCHBOARD_BUTLER,
                routed_butler="finance",
            ),
        ]
    )
    switchboard_pool = AsyncMock()
    switchboard_pool.fetchval = AsyncMock(return_value=switchboard_session_id)
    finance_pool = AsyncMock()
    finance_pool.fetchval = AsyncMock(return_value=finance_session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.side_effect = {
        _SWITCHBOARD_BUTLER: switchboard_pool,
        "finance": finance_pool,
    }.__getitem__

    switchboard_client = MagicMock()
    switchboard_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(switchboard_session_id)})
    )
    finance_client = MagicMock()
    finance_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(finance_session_id)})
    )
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect={
            _SWITCHBOARD_BUTLER: switchboard_client,
            "finance": finance_client,
        }.__getitem__
    )

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations/{conversation_id}/cancel"
        )

    assert response.status_code == 200
    assert response.json() == {
        "cancelled": True,
        "already_finished": False,
        "conversation_id": None,
        "session_id": str(finance_session_id),
        "message": None,
    }
    switchboard_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(switchboard_session_id)}
    )
    finance_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(finance_session_id)}
    )


async def test_cancel_confirmed_by_routed_butler_kills_the_session(app):
    """A genuinely in-flight session is killed and the response says so honestly."""
    conversation_id = uuid4()
    session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": "finance",
        "request_id": str(uuid4()),
    }

    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = butler_pool

    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": True, "session_id": str(session_id)})
    )
    mgr = _make_mcp_manager(mock_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/butlers/{_BUTLER}/conversations/{conversation_id}/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is True
    assert body["already_finished"] is False
    assert body["session_id"] == str(session_id)
    mock_client.call_tool.assert_awaited_once_with(
        "cancel_session", {"session_id": str(session_id)}
    )


async def test_cancel_when_session_already_finished_is_not_rendered_as_stopped(app):
    """The routed butler reports the session already completed -- honest no-op, not a claim of success."""
    conversation_id = uuid4()
    session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": "finance",
        "request_id": str(uuid4()),
    }

    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = butler_pool

    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"cancelled": False, "session_id": str(session_id)})
    )
    mgr = _make_mcp_manager(mock_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/butlers/{_BUTLER}/conversations/{conversation_id}/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is False
    assert body["already_finished"] is True


async def test_cancel_surfaces_honest_failure_when_butler_unreachable(app):
    """A failed cancel must surface as failed -- never fabricated calm."""
    conversation_id = uuid4()
    session_id = uuid4()
    conversations_router._ACTIVE_TURNS[conversation_id] = {
        "routed_butler": "finance",
        "request_id": str(uuid4()),
    }

    butler_pool = AsyncMock()
    butler_pool.fetchval = AsyncMock(return_value=session_id)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = butler_pool

    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(side_effect=ButlerUnreachableError("finance"))

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/butlers/{_BUTLER}/conversations/{conversation_id}/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is False
    assert body["already_finished"] is False
    assert body["message"]
