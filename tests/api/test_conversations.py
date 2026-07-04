"""Tests for dashboard conversation API endpoints.

Condensed from 44 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list 200 + 503 combined, 422/404/400 error paths (parametrized), summary 200.

bu-mj2k2 adds coverage for the real Switchboard ingest wiring: pinned-target
routing, page_context propagation, unreachable/rejected submission handling,
and real session-metadata polling (previously fabricated stubs).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.routers.conversations import (
    _SWITCHBOARD_BUTLER,
    _get_db_manager,
    _poll_session_completion,
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
        "total_input_tokens": 100,
        "total_output_tokens": 200,
        "total_duration_ms": 1500,
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

    # 503 when db unavailable
    _app_with_mock_db(app, db_raises=RuntimeError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get(f"/api/butlers/{_BUTLER}/conversations")
    assert resp_503.status_code == 503


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
        "total_input_tokens": 1000,
        "total_output_tokens": 500,
        "total_duration_ms": 3000,
    }
    _app_with_mock_db(app, fetchrow_result=row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/summary")
    assert resp.status_code == 200


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
# _poll_session_completion — real session-row lookup (bu-mj2k2)
# ---------------------------------------------------------------------------


async def test_poll_session_completion_returns_real_session_metadata():
    session_id = uuid4()
    row = {
        "id": session_id,
        "result": "Recorded: Alice child-of Bob — correct?",
        "model": "claude-sonnet-5",
        "input_tokens": 120,
        "output_tokens": 45,
        "duration_ms": 4200,
        "tool_calls": [],
        "error": None,
    }
    butler_pool = AsyncMock()
    butler_pool.fetchrow = AsyncMock(return_value=row)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = butler_pool

    result = await _poll_session_completion(
        db=mock_db, butler_name="relationship", request_id=str(uuid4())
    )

    assert result["completed"] is True
    assert result["session_id"] == session_id
    assert result["model"] == "claude-sonnet-5"
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 45
    assert result["duration_ms"] == 4200


async def test_poll_session_completion_not_yet_completed_when_no_session_row():
    butler_pool = AsyncMock()
    butler_pool.fetchrow = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = butler_pool

    result = await _poll_session_completion(
        db=mock_db, butler_name="relationship", request_id=str(uuid4())
    )

    assert result == {"completed": False}


# ---------------------------------------------------------------------------
# End-to-end SSE: pinned routing, page_context, offline retry (bu-mj2k2)
# ---------------------------------------------------------------------------


def _make_completed_session_row(**kw):
    defaults = {
        "id": uuid4(),
        "result": "Recorded: Alice child-of Bob — correct?",
        "model": "claude-sonnet-5",
        "input_tokens": 120,
        "output_tokens": 45,
        "duration_ms": 4200,
        "tool_calls": [],
        "error": None,
    }
    defaults.update(kw)
    return defaults


def _app_with_mock_db_and_mcp(app: FastAPI, *, mcp_manager, session_row=None):
    shared_pool = AsyncMock()
    butler_pool = AsyncMock()
    butler_pool.fetchrow = AsyncMock(return_value=session_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.return_value = butler_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    return app


async def test_create_conversation_pins_target_and_streams_real_session_metadata(app):
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
    _app_with_mock_db_and_mcp(app, mcp_manager=mgr, session_row=_make_completed_session_row())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/finance/conversations",
            json={
                "message": "Alice is Bob's sister",
                "page_context": {"route": "/entities/concentration", "query_params": {}},
            },
        )

    assert resp.status_code == 200
    assert "message_complete" in resp.text
    assert "claude-sonnet-5" in resp.text

    sent_envelope = mock_client.call_tool.call_args.args[1]
    assert sent_envelope["control"]["pinned_target"] == "finance"
    assert sent_envelope["payload"]["raw"]["page_context"]["route"] == "/entities/concentration"


async def test_create_conversation_does_not_pin_switchboard_target(app):
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(
        return_value=_FakeMcpResult({"request_id": str(uuid4()), "status": "accepted"})
    )
    mgr = _make_mcp_manager(mock_client)
    _app_with_mock_db_and_mcp(app, mcp_manager=mgr, session_row=_make_completed_session_row())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/{_SWITCHBOARD_BUTLER}/conversations",
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
    _app_with_mock_db_and_mcp(app, mcp_manager=mgr)

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
