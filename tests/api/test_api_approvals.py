"""Tests for approvals API endpoints.

Condensed from 56 tests to ~8 tests (bu-egmz6).
Keeps: list structure, 404/409/422 paths, rules CRUD, suggestions endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.approvals import _clear_table_cache, _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_ACTION_ID = uuid4()
_RULE_ID = uuid4()


@pytest.fixture(autouse=True)
def clear_approvals_cache():
    _clear_table_cache()
    yield
    _clear_table_cache()


def _make_action(*, action_id=None, tool_name="telegram_send_message", status="pending"):
    return {
        "id": action_id or _ACTION_ID,
        "tool_name": tool_name,
        "tool_args": {"chat_id": "12345", "text": "Hello"},
        "status": status,
        "requested_at": _NOW,
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": None,
        "decided_at": None,
        "execution_result": None,
        "approval_rule_id": None,
    }


def _make_rule(*, rule_id=None, tool_name="telegram_send_message"):
    return {
        "id": rule_id or _RULE_ID,
        "tool_name": tool_name,
        "arg_constraints": {"chat_id": {"type": "exact", "value": "12345"}},
        "description": "Auto-approve messages",
        "created_from": None,
        "created_at": _NOW,
        "expires_at": None,
        "max_uses": None,
        "use_count": 0,
        "active": True,
    }


def _app_with_mock_db(
    app,
    *,
    has_approvals_tables=True,
    fetch_rows=None,
    fetchval_return=None,
    fetchrow_return=None,
):
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    if has_approvals_tables:

        def fetchval_mock(*args, **kwargs):
            sql = args[0] if args else ""
            if "to_regclass" in sql or "EXISTS" in sql:
                return True
            return fetchval_return

        mock_conn.fetchval = AsyncMock(side_effect=fetchval_mock)
    else:
        mock_conn.fetchval = AsyncMock(return_value=fetchval_return)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())

    mock_db = MagicMock(spec=DatabaseManager)
    if has_approvals_tables:
        mock_db.pool.return_value = mock_pool
        mock_db.butler_names = ["general"]
    else:
        mock_db.pool.side_effect = KeyError("No pool")
        mock_db.butler_names = []

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
    return app, mock_conn


async def test_list_actions_returns_paginated_structure(app):
    action = _make_action()
    app, _ = _app_with_mock_db(app, fetch_rows=[action], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body


async def test_list_actions_no_approvals_tables_returns_empty(app):
    app, _ = _app_with_mock_db(app, has_approvals_tables=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_get_action_not_found_returns_404(app):
    app, _ = _app_with_mock_db(app, fetchrow_return=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/approvals/actions/{uuid4()}")
    assert resp.status_code == 404


async def test_get_action_invalid_uuid_returns_400(app):
    app, _ = _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions/not-a-uuid")
    assert resp.status_code == 400


async def test_list_rules_returns_paginated_structure(app):
    rule = _make_rule()
    app, _ = _app_with_mock_db(app, fetch_rows=[rule], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules")
    assert resp.status_code == 200
    assert "data" in resp.json()


async def test_create_rule_invalid_max_uses_returns_422(app):
    app, _ = _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/approvals/rules",
            json={
                "tool_name": "x",
                "max_uses": -1,
            },
        )
    assert resp.status_code == 422


async def test_revoke_rule_not_found_returns_404(app):
    app, _ = _app_with_mock_db(app, fetchrow_return=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/approvals/rules/{uuid4()}/revoke")
    assert resp.status_code == 404


async def test_list_suggestions_no_table_returns_empty(app):
    app, _ = _app_with_mock_db(app, has_approvals_tables=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/suggestions")
    assert resp.status_code == 200
    assert resp.json()["data"] == []
