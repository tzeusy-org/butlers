"""Tests for approvals API endpoints.

Condensed from 56 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list paginated structure, 404/422 error paths (parametrized),
       graceful empty when no approvals table.

Extended (bu-d3fhz): butler filter param + butler field on ApprovalAction.
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


@pytest.fixture(autouse=True)
def clear_approvals_cache():
    _clear_table_cache()
    yield
    _clear_table_cache()


def _make_action(*, tool_name="telegram_send_message", status="pending"):
    return {
        "id": uuid4(),
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


def _app_with_two_butlers(app, *, home_rows=None, general_rows=None, fetchval_return=0):
    """Set up a mock DB with two butlers (home, general) each having pending_actions."""
    home_rows = home_rows or []
    general_rows = general_rows or []

    def _make_conn(rows):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)

        def fetchval_mock(*args, **kwargs):
            sql = args[0] if args else ""
            if "to_regclass" in sql or "EXISTS" in sql:
                return True
            return len(rows)

        conn.fetchval = AsyncMock(side_effect=fetchval_mock)
        conn.fetchrow = AsyncMock(return_value=None)
        return conn

    home_conn = _make_conn(home_rows)
    general_conn = _make_conn(general_rows)

    class _MockAcquire:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *a):
            pass

    home_pool = AsyncMock()
    home_pool.acquire = MagicMock(side_effect=lambda: _MockAcquire(home_conn))

    general_pool = AsyncMock()
    general_pool.acquire = MagicMock(side_effect=lambda: _MockAcquire(general_conn))

    pools = {"home": home_pool, "general": general_pool}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["home", "general"]
    mock_db.pool = MagicMock(side_effect=lambda name: pools[name])

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
    return app


# ---------------------------------------------------------------------------
# Paginated list structure
# ---------------------------------------------------------------------------


async def test_list_actions_returns_paginated_structure(app):
    app, _ = _app_with_mock_db(app, fetch_rows=[_make_action()], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body


# ---------------------------------------------------------------------------
# Error paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,method,body,expected",
    [
        (f"/api/approvals/actions/{uuid4()}", "GET", None, 404),
        ("/api/approvals/actions/not-a-uuid", "GET", None, 400),
        (
            "/api/approvals/rules",
            "POST",
            {"tool_name": "x", "arg_constraints": {}, "description": "test", "max_uses": -1},
            400,
        ),
        (f"/api/approvals/rules/{uuid4()}/revoke", "POST", None, 404),
    ],
    ids=["action-404", "action-bad-uuid-400", "rule-invalid-max-uses-400", "revoke-rule-404"],
)
async def test_approvals_error_paths(app, path, method, body, expected):
    app, _ = _app_with_mock_db(app, fetchrow_return=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if method == "GET":
            resp = await client.get(path)
        else:
            resp = await client.post(path, json=body or {})
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# No-table graceful empty
# ---------------------------------------------------------------------------


async def test_no_approvals_tables_returns_empty(app):
    app, _ = _app_with_mock_db(app, has_approvals_tables=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r_actions = await client.get("/api/approvals/actions")
        r_suggestions = await client.get("/api/approvals/suggestions")
    assert r_actions.json()["data"] == []
    assert r_suggestions.json()["data"] == []


# ---------------------------------------------------------------------------
# butler filter param + butler field (bu-d3fhz)
# ---------------------------------------------------------------------------


async def test_list_actions_includes_butler_field(app):
    """Every ApprovalAction in the response must include the butler field."""
    app, _ = _app_with_mock_db(app, fetch_rows=[_make_action()], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    actions = resp.json()["data"]
    assert len(actions) == 1
    assert "butler" in actions[0]
    assert actions[0]["butler"] == "general"


async def test_list_actions_butler_filter_returns_only_that_butler(app):
    """?butler=home returns only home actions, not general actions."""
    home_action = _make_action(tool_name="notify")
    general_action = _make_action(tool_name="send_telegram")
    app = _app_with_two_butlers(app, home_rows=[home_action], general_rows=[general_action])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions?butler=home")
    assert resp.status_code == 200
    body = resp.json()
    actions = body["data"]
    # Only home's action should be present
    assert len(actions) == 1
    assert actions[0]["butler"] == "home"
    assert actions[0]["tool_name"] == "notify"


async def test_list_actions_no_butler_filter_aggregates_all(app):
    """Without ?butler=, actions from all butlers are aggregated."""
    home_action = _make_action(tool_name="notify")
    general_action = _make_action(tool_name="send_telegram")
    app = _app_with_two_butlers(app, home_rows=[home_action], general_rows=[general_action])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    actions = resp.json()["data"]
    assert len(actions) == 2
    butler_names = {a["butler"] for a in actions}
    assert butler_names == {"home", "general"}


async def test_list_actions_unknown_butler_returns_empty(app):
    """?butler=nonexistent returns empty list, not 404."""
    app, _ = _app_with_mock_db(app, fetch_rows=[_make_action()], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions?butler=nonexistent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
