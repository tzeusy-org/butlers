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
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.conversations import (
    conversation_get_or_create_by_thread,
    conversation_reply_create,
    conversation_search,
    conversation_set_routed_butler,
    message_create_idempotent,
    message_find_reply_since,
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
# bu-ep4ks.8: channel-agnostic conversation anchor + provider resume ledger
# ---------------------------------------------------------------------------


async def test_conversation_get_or_create_by_thread_new_row_inserts_full_shape():
    pool = AsyncMock()
    inserted_row = _make_conversation_row(source_channel="telegram", source_thread_identity="t:1")
    pool.fetchrow = AsyncMock(return_value=inserted_row)

    conv, is_new = await conversation_get_or_create_by_thread(
        pool,
        butler_name=_BUTLER,
        source_channel="telegram",
        source_thread_identity="t:1",
        first_message="hello",
    )

    assert is_new is True
    assert conv == inserted_row
    pool.fetchrow.assert_awaited_once()
    sql = pool.fetchrow.await_args.args[0]
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql


async def test_conversation_get_or_create_by_thread_conflict_reuses_existing_row():
    pool = AsyncMock()
    existing_row = _make_conversation_row(source_channel="telegram", source_thread_identity="t:1")
    # INSERT ... ON CONFLICT DO NOTHING RETURNING yields no row on conflict;
    # the follow-up SELECT recovers the winner.
    pool.fetchrow = AsyncMock(side_effect=[None, existing_row])

    conv, is_new = await conversation_get_or_create_by_thread(
        pool,
        butler_name=_BUTLER,
        source_channel="telegram",
        source_thread_identity="t:1",
        first_message="a retried first message",
    )

    assert is_new is False
    assert conv == existing_row
    assert pool.fetchrow.await_count == 2


async def test_conversation_get_or_create_by_thread_raises_if_row_vanishes():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, None])

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
    if conversation_row is not None:
        # conversation_get() consumes the first fetchrow; the poller's
        # message_find_reply_since() consumes the second.
        shared_pool.fetchrow = AsyncMock(side_effect=[conversation_row, reply_row])
    else:
        shared_pool.fetchrow = AsyncMock(return_value=reply_row)
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

    async def fetchrow(sql, *_args):
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


@pytest.mark.parametrize(
    ("triage_decision", "triage_target", "expected_routed_butler"),
    [
        ("route_to", "relationship", "relationship"),
        ("file_bug_report", None, None),
    ],
)
async def test_stream_conversation_response_emits_dispatch_accepted_truthfully(
    triage_decision: str,
    triage_target: str | None,
    expected_routed_butler: str | None,
):
    """Accepted dispatches surface the actual domain route, if one exists.

    A Switchboard acceptance is not itself a domain route.  The receipt must
    therefore preserve the route target only for ``route_to`` decisions and
    explicitly send ``null`` for accepted, non-routing decisions.
    """
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult(
            {
                "request_id": str(uuid4()),
                "status": "accepted",
                "triage_decision": triage_decision,
                "triage_target": triage_target,
            }
        )
    )
    mgr = _make_mcp_manager(mock_client)

    shared_pool = AsyncMock()
    shared_pool.fetchrow = AsyncMock(return_value=_make_reply_row())
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

    receipt_index = next(
        index for index, event in enumerate(events) if "event: dispatch_accepted" in event
    )
    token_index = next(index for index, event in enumerate(events) if "event: token" in event)
    receipt = events[receipt_index]

    assert receipt_index < token_index
    assert f"data: {json.dumps({'routed_butler': expected_routed_butler})}" in receipt


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
        "session_id": None,
        "message": None,
    }


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
