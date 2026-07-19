"""Tests for approvals API endpoints.

Condensed from 56 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list paginated structure, 404/422 error paths (parametrized),
       graceful empty when no approvals table.

Extended (bu-d3fhz): butler filter param + butler field on ApprovalAction.
Extended (bu-5xiu9): defer bounds, policy round-trip, audit.append on verbs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.approvals import (
    _clear_table_cache,
    _get_db_manager,
    _row_to_autonomy_suggestion,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


class _NullTxCtx:
    """No-op async context manager standing in for ``conn.transaction()``.

    ``mock_conn = AsyncMock()`` auto-mocks ``.transaction`` as an AsyncMock
    too, whose call returns a coroutine rather than an async context manager
    — incompatible with ``async with conn.transaction():``. Assign
    ``mock_conn.transaction = MagicMock(return_value=_NullTxCtx())`` wherever
    a route now wraps its writes in a transaction (approve/deny/defer/policy).
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # never suppress — let exceptions propagate/rollback


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


def test_v2_suggestion_api_explains_its_safety_critical_scope():
    action_id = uuid4()
    suggestion = _row_to_autonomy_suggestion(
        {
            "id": uuid4(),
            "action_id": action_id,
            "suggestion_type": "promotion",
            "pattern_fingerprint": "fingerprint",
            "fingerprint_version": 2,
            "tool_name": "send_telegram",
            "representative_args": {"chat_id": "mom_123"},
            "status": "pending",
            "approval_count_at_creation": 5,
            "created_at": _NOW,
        }
    )

    assert suggestion.fingerprint_version == 2
    assert suggestion.action_id == str(action_id)
    assert suggestion.representative_args == {"chat_id": "mom_123"}
    assert "shown arguments are exactly pinned" in suggestion.scope_description
    assert "omitted arguments may vary" in suggestion.scope_description


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
# GET /api/approvals (flat) and /api/approvals/history --
# sources_degraded contract (bu-qvnce.1)
# ---------------------------------------------------------------------------


def _app_with_one_healthy_one_raising_butler(app, *, healthy_rows=None):
    """Two named approvals pools: 'general' answers normally, 'home' raises
    mid-query (table exists per to_regclass, but the actual SELECT fails).
    """
    healthy_rows = healthy_rows or []

    healthy_conn = AsyncMock()
    healthy_conn.fetch = AsyncMock(return_value=healthy_rows)
    healthy_conn.fetchval = AsyncMock(
        side_effect=lambda *a, **k: True if ("to_regclass" in a[0] or "EXISTS" in a[0]) else 0
    )

    raising_conn = AsyncMock()
    raising_conn.fetch = AsyncMock(side_effect=RuntimeError("connection reset by peer"))
    raising_conn.fetchval = AsyncMock(
        side_effect=lambda *a, **k: True if ("to_regclass" in a[0] or "EXISTS" in a[0]) else 0
    )

    class _MockAcquire:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *a):
            pass

    healthy_pool = AsyncMock()
    healthy_pool.acquire = MagicMock(side_effect=lambda: _MockAcquire(healthy_conn))

    raising_pool = AsyncMock()
    raising_pool.acquire = MagicMock(side_effect=lambda: _MockAcquire(raising_conn))

    pools = {"general": healthy_pool, "home": raising_pool}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "home"]
    mock_db.pool = MagicMock(side_effect=lambda name: pools[name])

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
    return app


async def test_list_approvals_flat_reports_sources_degraded_on_pool_failure(app):
    """One pool raising mid-query must surface meta.sources_degraded, not a
    silent drop of that butler's rows from the flat list."""
    row = _make_action(tool_name="notify")
    app = _app_with_one_healthy_one_raising_butler(app, healthy_rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["meta"]["sources_degraded"] == ["home"]


async def test_list_actions_reports_sources_degraded_on_pool_failure(app):
    """The butler-scoped preview cannot silently turn a dropped pool into zero actions."""
    app = _app_with_one_healthy_one_raising_butler(app, healthy_rows=[_make_action()])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions?butler=home")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["sources_degraded"] == ["home"]


async def test_list_approvals_history_reports_sources_degraded_on_pool_failure(app):
    """Same contract for the decided-approvals history endpoint."""
    row = _make_action(tool_name="notify", status="approved")
    app = _app_with_one_healthy_one_raising_butler(app, healthy_rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/history")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["meta"]["sources_degraded"] == ["home"]


async def test_list_approvals_flat_no_sources_degraded_when_all_pools_healthy(app):
    """No sources_degraded key when every pool answers successfully."""
    row = _make_action(tool_name="notify")
    app = _app_with_two_butlers(app, home_rows=[row], general_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals")

    assert resp.status_code == 200
    body = resp.json()
    assert "sources_degraded" not in body["meta"]


# ---------------------------------------------------------------------------
# butler filter param + butler field (bu-d3fhz)
# ---------------------------------------------------------------------------


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


async def test_list_executed_actions_butler_filter(app):
    """?butler= on /actions/executed scopes results to that butler only.

    Guards against leaking every butler's executed actions: with two butlers
    each holding one executed row, ?butler=home must return only home's row.
    """
    home_action = _make_action(tool_name="notify", status="executed")
    general_action = _make_action(tool_name="send_telegram", status="executed")
    app = _app_with_two_butlers(app, home_rows=[home_action], general_rows=[general_action])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions/executed?butler=home")
    assert resp.status_code == 200
    actions = resp.json()["data"]
    assert len(actions) == 1
    assert actions[0]["butler"] == "home"
    assert actions[0]["tool_name"] == "notify"


# ---------------------------------------------------------------------------
# list_rules: active (tri-state) + butler filters (bu-2176m)
# ---------------------------------------------------------------------------


def _make_rule(*, tool_name="send_email", active=True):
    """Return a dict matching approval_rules columns."""
    return {
        "id": uuid4(),
        "tool_name": tool_name,
        "arg_constraints": {},
        "description": "test rule",
        "created_from": None,
        "created_at": _NOW,
        "expires_at": None,
        "max_uses": None,
        "use_count": 0,
        "active": active,
    }


def _rules_app_with_capture(app, *, rows, butler_name="general"):
    """Mock DB for /rules that records the SQL + args passed to fetch/fetchval."""
    captured: dict[str, object] = {}
    mock_conn = AsyncMock()

    async def _fetch(sql, *args):
        if "approval_rules" in sql and "COUNT" not in sql:
            captured["sql"] = sql
            captured["args"] = args
        return rows

    async def _fetchval(sql, *args):
        if "to_regclass" in sql:
            return True
        if "COUNT" in sql:
            captured["count_sql"] = sql
            captured["count_args"] = args
            return len(rows)
        return 0

    mock_conn.fetch = AsyncMock(side_effect=_fetch)
    mock_conn.fetchval = AsyncMock(side_effect=_fetchval)
    mock_conn.fetchrow = AsyncMock(return_value=None)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = [butler_name]
    mock_db.pool.return_value = mock_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
    return app, captured


async def test_list_rules_default_returns_active_only_filter_absent(app):
    """No params: query carries no ``active`` WHERE filter (returns all rows)."""
    app, captured = _rules_app_with_capture(app, rows=[_make_rule(active=True)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules")
    assert resp.status_code == 200
    assert "active = " not in captured["sql"]
    assert captured["args"] == ()


async def test_list_rules_active_true_filters_to_active(app):
    """active=true threads ``active = $1`` with True into the query."""
    app, captured = _rules_app_with_capture(app, rows=[_make_rule(active=True)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules?active=true")
    assert resp.status_code == 200
    assert "active = $1" in captured["sql"]
    assert captured["args"] == (True,)


async def test_list_rules_active_false_returns_inactive_revoked(app):
    """active=false surfaces inactive/revoked rules (active = false)."""
    revoked = _make_rule(active=False)
    app, captured = _rules_app_with_capture(app, rows=[revoked])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules?active=false")
    assert resp.status_code == 200
    assert "active = $1" in captured["sql"]
    assert captured["args"] == (False,)
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["active"] is False


def _rules_app_with_two_butlers(app, *, home_rows=None, general_rows=None):
    """Mock DB with two butlers (home, general) each owning approval_rules."""
    home_rows = home_rows or []
    general_rows = general_rows or []

    def _make_conn(rows):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)

        def fetchval_mock(*args, **kwargs):
            sql = args[0] if args else ""
            if "to_regclass" in sql:
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


async def test_list_rules_butler_filter_returns_only_that_butler(app):
    """?butler=home returns only home's rules, not general's."""
    app = _rules_app_with_two_butlers(
        app,
        home_rows=[_make_rule(tool_name="notify")],
        general_rows=[_make_rule(tool_name="send_telegram")],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules?butler=home")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["tool_name"] == "notify"


async def test_list_rules_no_butler_filter_aggregates_all(app):
    """Without ?butler=, rules from all butlers are aggregated."""
    app = _rules_app_with_two_butlers(
        app,
        home_rows=[_make_rule(tool_name="notify")],
        general_rows=[_make_rule(tool_name="send_telegram")],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules")
    assert resp.status_code == 200
    tools = {r["tool_name"] for r in resp.json()["data"]}
    assert tools == {"notify", "send_telegram"}


async def test_list_rules_unknown_butler_returns_empty(app):
    """?butler=nonexistent returns empty list, not 404."""
    app = _rules_app_with_two_butlers(app, home_rows=[_make_rule()])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/rules?butler=nonexistent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


async def test_gated_tools_lists_configured_tools_even_when_they_have_no_rules(app):
    """The autonomy baseline must show every configured gate, not only grants."""
    active_notify_rule = _make_rule(tool_name="notify")
    app, _ = _rules_app_with_capture(
        app,
        rows=[active_notify_rule],
        butler_name="messenger",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/gated-tools")

    assert response.status_code == 200, response.text
    tools = {(item["butler"], item["tool_name"]): item for item in response.json()["data"]}

    # Messenger's real roster config is the authoritative gate inventory.
    assert ("messenger", "notify") in tools
    assert ("messenger", "telegram_reply_to_message") in tools
    assert tools[("messenger", "notify")]["risk_tier"] == "medium"
    assert tools[("messenger", "notify")]["active_rules"][0]["id"] == str(active_notify_rule["id"])
    # A zero-rule tool is still visible as an always-ask boundary.
    assert tools[("messenger", "telegram_reply_to_message")]["active_rules"] == []


async def test_gated_tools_excludes_expired_and_exhausted_rules(app):
    """The autonomy ledger only counts rules that can still auto-approve."""
    expired = _make_rule(tool_name="notify")
    expired["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    exhausted = _make_rule(tool_name="notify")
    exhausted["max_uses"] = 3
    exhausted["use_count"] = 3

    app, _ = _rules_app_with_capture(
        app,
        rows=[expired, exhausted],
        butler_name="messenger",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/gated-tools")

    assert response.status_code == 200, response.text
    tools = {(item["butler"], item["tool_name"]): item for item in response.json()["data"]}
    assert tools[("messenger", "notify")]["active_rules"] == []


async def test_rule_suggestions_for_found_action_returns_redacted_scope(app):
    """A teaching digest can safely preview a found action's suggested rule."""
    action = _make_action(tool_name="send_email")
    action["tool_args"] = {
        "recipient": "private@example.com",
        "subject": "A sensitive subject stays visible",
    }
    app, _ = _app_with_mock_db(app, fetchrow_return=action)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/approvals/rules/suggestions/{action['id']}")

    assert response.status_code == 200, response.text
    suggestion = response.json()["data"]
    assert suggestion["action_id"] == str(action["id"])
    assert suggestion["tool_args"]["recipient"] == "[REDACTED]"
    assert suggestion["suggested_constraints"]["recipient"] == {
        "type": "exact",
        "value": "[REDACTED]",
    }
    assert suggestion["suggested_constraints"]["subject"] == {"type": "any"}


# ---------------------------------------------------------------------------
# §8.7 — defer bounds, policy round-trip, audit.append on verbs
# ---------------------------------------------------------------------------


def _make_pending_row(*, tool_name="send_email", status="pending"):
    """Return a dict matching the RFC 0021 pending_actions dossier columns."""
    return {
        "id": uuid4(),
        "tool_name": tool_name,
        "tool_args": {"to": "user@example.com", "subject": "Hello"},
        "status": status,
        "requested_at": _NOW,
        "agent_summary": "Test action",
        "session_id": None,
        "expires_at": None,
        "decided_by": None,
        "decided_at": None,
        "execution_result": None,
        "approval_rule_id": None,
        "why": "Sending a welcome email to new user",
        "evidence": [
            {
                "type": "fact",
                "ref": "user:signup:2026-05-16T10:00:00Z",
                "note": "User signed up",
            },
            {
                "type": "text",
                "ref": "Email not yet sent",
                "note": "Delivery has not started",
            },
        ],
        "blast_radius": "contact",
        "reversibility": "compensable",
    }


@pytest.mark.parametrize(
    "hours,expected_status",
    [
        (1, 200),  # lower bound inclusive
        (168, 200),  # upper bound inclusive
        (0, 422),  # below lower bound
        (169, 422),  # above upper bound
    ],
    ids=["hours-1-ok", "hours-168-ok", "hours-0-422", "hours-169-422"],
)
async def test_defer_hours_bounds(app, hours, expected_status, monkeypatch):
    """POST /api/approvals/{id}/defer validates 1 ≤ hours ≤ 168."""
    from butlers.api.routers.approvals import _get_db_manager

    action_id = uuid4()
    pending_row = _make_pending_row(status="pending")
    pending_row["id"] = action_id

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=pending_row)
    mock_conn.execute = AsyncMock()
    # audit.append uses fetchval
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    mock_pool.fetchrow = AsyncMock(return_value=pending_row)

    # to_regclass returns truthy so _find_named_approvals_pools includes this pool
    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)
    # Updated fetchrow to return the action when queried by ID
    mock_conn.fetchrow = AsyncMock(return_value=pending_row)
    # fetchrow for the deferred update
    updated_row = dict(pending_row)
    updated_row["expires_at"] = _NOW

    async def fetchrow_side(*args, **kwargs):
        return pending_row if "id" in str(args) else updated_row

    mock_conn.fetchrow = AsyncMock(side_effect=lambda *a, **k: pending_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []

    import butlers.api.routers.audit as audit_router

    audit_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_append(*args: object, **kwargs: object) -> int:
        audit_calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(audit_router, "append", fake_append)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/approvals/{action_id}/defer",
            json={"hours": hours},
        )

    assert resp.status_code == expected_status, f"hours={hours}: {resp.text}"
    defer_audits = [call for call in audit_calls if call[0][2] == "approval.defer"]
    if expected_status == 200:
        assert len(defer_audits) == 1
        assert defer_audits[0][1]["result"] == "success"
    else:
        assert defer_audits == []


async def test_defer_expired_pending_action_expires_instead_of_extending(app):
    """POST /api/approvals/{id}/defer cannot revive an expired pending approval."""
    from butlers.api.routers.approvals import _get_db_manager

    action_id = uuid4()
    pending_row = _make_pending_row(status="pending")
    pending_row["id"] = action_id
    pending_row["expires_at"] = _NOW - timedelta(minutes=30)
    expired_row = dict(pending_row)
    expired_row["status"] = "expired"
    expired_row["decided_by"] = "system:expiry"
    expired_row["decided_at"] = _NOW

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    async def fetchrow_side(query, *args, **kwargs):
        if "UPDATE pending_actions SET expires_at" in query:
            pytest.fail("expired approvals must not be extended")
        if "UPDATE pending_actions SET status" in query:
            return expired_row
        if "SELECT * FROM pending_actions" in query:
            return pending_row
        return pending_row

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)
    mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_side)
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    mock_pool.fetchrow = AsyncMock(return_value=pending_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/approvals/{action_id}/defer",
            json={"hours": 24},
        )

    assert resp.status_code == 409
    assert "expired" in resp.json()["detail"]
    assert any(
        "INSERT INTO approval_events" in call.args[0] for call in mock_conn.execute.await_args_list
    )


async def test_policy_round_trip(app, monkeypatch):
    """GET /api/approvals/policy returns 200; PUT persists and returns updated policy."""
    from butlers.api.routers.approvals import _get_db_manager

    policy_row = {
        "id": 1,
        "quiet_start_hour": 22,
        "quiet_end_hour": 7,
        "timezone": "America/New_York",
        "updated_at": _NOW,
    }

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=policy_row)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []

    import butlers.api.routers.audit as audit_router

    audit_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_append(*args: object, **kwargs: object) -> int:
        audit_calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(audit_router, "append", fake_append)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # GET
        get_resp = await client.get("/api/approvals/policy")
        assert get_resp.status_code == 200
        policy = get_resp.json()["data"]
        assert policy["quiet_start_hour"] == 22
        assert policy["quiet_end_hour"] == 7
        assert policy["timezone"] == "America/New_York"

        # PUT — update and verify 200 with updated values
        put_resp = await client.put(
            "/api/approvals/policy",
            json={"quiet_start_hour": 23, "quiet_end_hour": 8, "timezone": "UTC"},
        )
        assert put_resp.status_code == 200
        updated = put_resp.json()["data"]
        assert updated["quiet_start_hour"] == 23
        assert updated["timezone"] == "UTC"

        # The stable payload shape accepts disabled hours only as a complete
        # pair. A partial persisted policy would otherwise be ambiguous and
        # fail-open at runtime.
        partial_resp = await client.put(
            "/api/approvals/policy",
            json={"quiet_start_hour": 23, "quiet_end_hour": None, "timezone": "UTC"},
        )
        assert partial_resp.status_code == 422

        invalid_zone_resp = await client.put(
            "/api/approvals/policy",
            json={"quiet_start_hour": 23, "quiet_end_hour": 8, "timezone": "Mars/Olympus"},
        )
        assert invalid_zone_resp.status_code == 422

    policy_audits = [call for call in audit_calls if call[0][2] == "approvals.policy"]
    assert len(policy_audits) == 1
    assert policy_audits[0][1]["result"] == "success"


async def test_approve_audits_action(app):
    """POST /api/approvals/{id}/approve calls audit.append('approval.approve', ...)."""
    from unittest.mock import patch

    import butlers.api.routers.audit as audit_router
    from butlers.api.routers.approvals import _get_db_manager

    action_id = uuid4()
    pending_row = _make_pending_row(status="pending")
    pending_row["id"] = action_id

    approved_result = {
        "id": str(action_id),
        "tool_name": "send_email",
        "tool_args": {"to": "user@example.com", "subject": "Hello"},
        "status": "approved",
        "requested_at": _NOW.isoformat(),
        "butler": "general",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "dashboard:rest-api",
        "decided_at": _NOW.isoformat(),
        "execution_result": None,
        "approval_rule_id": None,
    }

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=pending_row)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    mock_pool.fetchrow = AsyncMock(return_value=pending_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    audit_calls = []

    async def fake_append(pool, actor, action, *, target=None, note=None, **kw):
        audit_calls.append({"actor": actor, "action": action, "target": target, "note": note, **kw})
        return 1

    with patch.object(audit_router, "append", fake_append):
        with patch(
            "butlers.modules.approvals.operations.approve_action",
            AsyncMock(return_value=approved_result),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/approvals/{action_id}/approve", json={})

    assert resp.status_code == 200, resp.text
    # audit.append must have been called with approval.approve
    approve_audits = [c for c in audit_calls if c["action"] == "approval.approve"]
    assert len(approve_audits) >= 1
    assert approve_audits[0]["target"] == str(action_id)
    assert approve_audits[0]["result"] == "success"


async def test_approve_no_daemon_reachable_reports_not_dispatched(app):
    """Regression (bu-j1xkd): approve with no reachable butler must NOT claim it ran.

    When no daemon can dispatch the action, the row stays status='approved'
    (un-run). The API response must surface dispatched=False / status='approved'
    so the FE does not falsely toast success.
    """
    from unittest.mock import patch

    import butlers.api.routers.audit as audit_router
    from butlers.api.routers.approvals import _get_db_manager

    action_id = uuid4()
    pending_row = _make_pending_row(status="pending")
    pending_row["id"] = action_id

    # approve_action returns the row in 'approved' state (dispatch not yet run).
    approved_result = {
        "id": str(action_id),
        "tool_name": "send_email",
        "tool_args": {"to": "user@example.com", "subject": "Hello"},
        "status": "approved",
        "requested_at": _NOW.isoformat(),
        "butler": "general",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "dashboard:rest-api",
        "decided_at": _NOW.isoformat(),
        "execution_result": None,
        "approval_rule_id": None,
    }

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=pending_row)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    mock_pool.fetchrow = AsyncMock(return_value=pending_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    # No reachable butlers — get_client raises so _dispatch_approved_action
    # exhausts its targets and returns None (action stays 'approved').
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    mock_mcp.get_client = AsyncMock(side_effect=RuntimeError("no daemon"))

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async def fake_append(pool, actor, action, *, target=None, note=None, **kw):
        return 1

    with patch.object(audit_router, "append", fake_append):
        with patch(
            "butlers.modules.approvals.operations.approve_action",
            AsyncMock(return_value=approved_result),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/approvals/{action_id}/approve", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["status"] == "approved"
    assert body["dispatched"] is False


async def test_approve_daemon_reachable_reports_dispatched(app):
    """Regression (bu-j1xkd): approve that actually dispatches reports executed.

    When a daemon runs the tool, the row reaches status='executed' and the
    response must report dispatched=True.
    """
    from unittest.mock import patch

    import butlers.api.routers.audit as audit_router
    import butlers.modules.approvals.operations as approvals_ops
    from butlers.api.routers.approvals import _get_db_manager

    action_id = uuid4()
    pending_row = _make_pending_row(status="pending")
    pending_row["id"] = action_id

    approved_result = {
        "id": str(action_id),
        "tool_name": "send_email",
        "tool_args": {"to": "user@example.com", "subject": "Hello"},
        "status": "approved",
        "requested_at": _NOW.isoformat(),
        "butler": "general",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "dashboard:rest-api",
        "decided_at": _NOW.isoformat(),
        "execution_result": None,
        "approval_rule_id": None,
    }
    # After dispatch, mark_executed returns the row in 'executed' state.
    executed_result = {**approved_result, "status": "executed", "execution_result": {"ok": True}}

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=pending_row)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    mock_pool.fetchrow = AsyncMock(return_value=pending_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    # A reachable butler whose dispatch_approved_action tool runs the original
    # tool and returns the action in its final 'executed' state.
    import json as _json

    mcp_block = MagicMock()
    mcp_block.text = _json.dumps(executed_result)
    mcp_result = MagicMock()
    mcp_result.is_error = False
    mcp_result.content = [mcp_block]
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=mcp_result)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = ["general"]
    mock_mcp.get_client = AsyncMock(return_value=mock_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async def fake_append(pool, actor, action, *, target=None, note=None, **kw):
        return 1

    with patch.object(audit_router, "append", fake_append):
        with patch.object(approvals_ops, "approve_action", AsyncMock(return_value=approved_result)):
            with patch.object(
                approvals_ops, "mark_executed", AsyncMock(return_value=executed_result)
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post(f"/api/approvals/{action_id}/approve", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["status"] == "executed"
    assert body["dispatched"] is True


async def test_deny_audits_action(app):
    """POST /api/approvals/{id}/deny calls audit.append('approval.deny', ...)."""
    from unittest.mock import patch

    import butlers.api.routers.audit as audit_router
    from butlers.api.routers.approvals import _get_db_manager

    action_id = uuid4()
    pending_row = _make_pending_row(status="pending")
    pending_row["id"] = action_id

    rejected_result = {
        "id": str(action_id),
        "tool_name": "send_email",
        "tool_args": {},
        "status": "rejected",
        "requested_at": _NOW.isoformat(),
        "butler": "general",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "dashboard:rest-api",
        "decided_at": _NOW.isoformat(),
        "execution_result": None,
        "approval_rule_id": None,
    }

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=pending_row)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_NullTxCtx())

    def fetchval_side(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return 1

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_side)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    mock_pool.fetchrow = AsyncMock(return_value=pending_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    audit_calls = []

    async def fake_append(pool, actor, action, *, target=None, note=None, **kw):
        audit_calls.append({"actor": actor, "action": action, "target": target, "note": note, **kw})
        return 1

    with patch.object(audit_router, "append", fake_append):
        with patch(
            "butlers.modules.approvals.operations.reject_action",
            AsyncMock(return_value=rejected_result),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/approvals/{action_id}/deny",
                    json={"reason": "Not authorized"},
                )

    assert resp.status_code == 200, resp.text
    deny_audits = [c for c in audit_calls if c["action"] == "approval.deny"]
    assert len(deny_audits) >= 1
    assert deny_audits[0]["target"] == str(action_id)
    assert deny_audits[0]["note"] == "Not authorized"
    assert deny_audits[0]["result"] == "success"


async def test_decision_dossier_returned_on_actions_list(app):
    """GET /api/approvals/actions includes the typed RFC 0021 dossier."""
    row = _make_pending_row()
    app, _ = _app_with_mock_db(app, fetch_rows=[row], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    actions = resp.json()["data"]
    assert len(actions) == 1
    assert actions[0]["why"] == "Sending a welcome email to new user"
    assert actions[0]["evidence"] == [
        {
            "type": "fact",
            "ref": "user:signup:2026-05-16T10:00:00Z",
            "note": "User signed up",
        },
        {
            "type": "text",
            "ref": "Email not yet sent",
            "note": "Delivery has not started",
        },
    ]
    assert actions[0]["blast_radius"] == "contact"
    assert actions[0]["reversibility"] == "compensable"


# ---------------------------------------------------------------------------
# emit_approvals_event — fans approval lifecycle events onto the unified
# fleet event bus (WS /api/events/stream). The earlier dedicated
# WS /api/approvals/stream route was retired in bu-01r64.2; see
# tests/api/test_events.py::test_approvals_event_fans_onto_bus for bus-fan
# coverage.
# ---------------------------------------------------------------------------


async def test_emit_approvals_event_includes_expected_fields(app):
    """emit_approvals_event builds the expected event shape (kind/ts/approval_id/...)."""
    from unittest.mock import patch

    from butlers.api.routers.approvals import emit_approvals_event

    with patch("butlers.api.routers.events.emit_event") as mock_emit:
        emit_approvals_event(
            "executed",
            "ccc-333",
            butler="general",
            tool_name="notify",
            status="executed",
        )

    mock_emit.assert_called_once()
    event_type, event = mock_emit.call_args[0]
    assert event_type == "approval"
    assert event["kind"] == "executed"
    assert event["approval_id"] == "ccc-333"
    assert event["butler"] == "general"
    assert event["tool_name"] == "notify"
    assert event["status"] == "executed"


# ---------------------------------------------------------------------------
# Detail dossier surfaces the originating session_id (bu-86c4c.12 — Trust
# Console evidence wiring: link the dossier back to the session/trace that
# produced the proposed action).
# ---------------------------------------------------------------------------


async def test_detail_returns_typed_decision_dossier_fields(app):
    """The detail endpoint carries risk labels and typed evidence verbatim."""
    row = _make_pending_row()
    app, _ = _app_with_mock_db(app, fetchrow_return=row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/approvals/{row['id']}")

    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["blast_radius"] == "contact"
    assert detail["reversibility"] == "compensable"
    assert detail["evidence"] == row["evidence"]


async def test_detail_includes_originating_session_id(app):
    """GET /api/approvals/{id} surfaces session_id so the dossier can link to
    the originating session/trace that proposed the action."""
    action_id = uuid4()
    session_id = uuid4()
    app, mock_conn = _app_with_mock_db(
        app,
        fetchrow_return={
            **_make_action(),
            "id": action_id,
            "session_id": session_id,
        },
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/approvals/{action_id}")

    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["session_id"] == str(session_id)


async def test_detail_session_id_null_when_action_has_none(app):
    """GET /api/approvals/{id} returns session_id: null (not a KeyError) for
    actions with no recorded session (legacy rows / non-agent-originated)."""
    action_id = uuid4()
    app, mock_conn = _app_with_mock_db(
        app,
        fetchrow_return={**_make_action(), "id": action_id, "session_id": None},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/approvals/{action_id}")

    assert resp.status_code == 200
    assert resp.json()["data"]["session_id"] is None


# ---------------------------------------------------------------------------
# Detail dossier resolves target_contact from entity_id (bu — approvals UX)
# ---------------------------------------------------------------------------


async def test_detail_resolves_target_contact_from_entity_id(app):
    """GET /api/approvals/{id} resolves a tool_args.entity_id into a named,
    linkable target_contact so the dossier never shows a bare UUID."""
    entity_id = uuid4()
    action_id = uuid4()
    action_row = {
        "id": action_id,
        "tool_name": "notify",
        "tool_args": {"entity_id": str(entity_id), "text": "hi"},
        "status": "pending",
        "requested_at": _NOW,
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": None,
        "decided_at": None,
        "execution_result": None,
        "approval_rule_id": None,
        "why": None,
        "evidence": [],
    }
    contact_row = {"id": entity_id, "name": "Ada Lovelace", "roles": ["owner"]}

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    # detail SELECT * FROM pending_actions WHERE id = $1
    mock_conn.fetchrow = AsyncMock(return_value=action_row)

    def fetchval_mock(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return None

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_mock)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    # _resolve_target_contact queries public.entities via pool.fetchrow directly
    mock_pool.fetchrow = AsyncMock(return_value=contact_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["general"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/approvals/{action_id}")

    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["target_contact"] is not None
    assert detail["target_contact"]["id"] == str(entity_id)
    assert detail["target_contact"]["name"] == "Ada Lovelace"
    assert detail["target_contact"]["roles"] == ["owner"]


# ---------------------------------------------------------------------------
# Detail dossier resolves entity UUIDs to canonical names (bu-4ni21)
# ---------------------------------------------------------------------------


async def test_detail_resolves_referenced_entities_from_tool_args(app):
    """GET /api/approvals/{id} resolves entity UUIDs in tool_args (e.g. the
    subject/object of relationship_assert_fact) into named referenced_entities
    so the dossier explains who/what a fact references instead of bare UUIDs."""
    subject_id = uuid4()
    object_id = uuid4()
    action_id = uuid4()
    action_row = {
        "id": action_id,
        "tool_name": "relationship_assert_fact",
        "tool_args": {
            "subject": str(subject_id),
            "predicate": "works-at",
            "object": str(object_id),
            "object_kind": "entity",
            "src": "backfill",
        },
        "status": "pending",
        "requested_at": _NOW,
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": None,
        "decided_at": None,
        "execution_result": None,
        "approval_rule_id": None,
        "why": None,
        "evidence": [],
    }
    entity_rows = [
        {
            "id": subject_id,
            "canonical_name": "Tze How Lee",
            "entity_type": "person",
            "roles": ["owner"],
        },
        {
            "id": object_id,
            "canonical_name": "Acme Research & Technologies",
            "entity_type": "organization",
            "roles": [],
        },
    ]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchrow = AsyncMock(return_value=action_row)

    def fetchval_mock(*args, **kwargs):
        sql = args[0] if args else ""
        if "to_regclass" in sql or "EXISTS" in sql:
            return True
        return None

    mock_conn.fetchval = AsyncMock(side_effect=fetchval_mock)

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())
    # _resolve_target_contact -> no entity_id, returns None.
    mock_pool.fetchrow = AsyncMock(return_value=None)
    # _resolve_referenced_entities queries public.entities via pool.fetch.
    mock_pool.fetch = AsyncMock(return_value=entity_rows)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["general"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = []
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/approvals/{action_id}")

    assert resp.status_code == 200
    refs = resp.json()["data"]["referenced_entities"]
    by_id = {r["id"]: r for r in refs}
    assert by_id[str(subject_id)]["name"] == "Tze How Lee"
    assert by_id[str(subject_id)]["entity_type"] == "person"
    assert by_id[str(subject_id)]["roles"] == ["owner"]
    assert by_id[str(object_id)]["name"] == "Acme Research & Technologies"
    assert by_id[str(object_id)]["entity_type"] == "organization"


# ---------------------------------------------------------------------------
# _resolve_referenced_entities helper — unit coverage (bu-4ni21)
# ---------------------------------------------------------------------------


def _entity_db(fetch_return=None, *, fetch_raises=False):
    pool = AsyncMock()
    if fetch_raises:
        pool.fetch = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        pool.fetch = AsyncMock(return_value=fetch_return or [])
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = ["general"]
    db.pool = MagicMock(return_value=pool)
    return db, pool


async def test_resolve_referenced_entities_skips_non_uuid_and_preserves_order():
    from butlers.api.routers.approvals import _resolve_referenced_entities

    subject_id = uuid4()
    object_id = uuid4()
    db, pool = _entity_db(
        fetch_return=[
            {"id": object_id, "canonical_name": "Org", "entity_type": "organization", "roles": []},
            {"id": subject_id, "canonical_name": "Person", "entity_type": "person", "roles": []},
        ]
    )
    tool_args = {
        "subject": str(subject_id),
        "predicate": "works-at",  # not a UUID -> skipped
        "object": str(object_id),
        "conf": 1,  # not a string -> skipped
    }
    refs = await _resolve_referenced_entities(db, tool_args)
    # Order follows first-seen order in tool_args (subject before object),
    # not the DB row order.
    assert [r.id for r in refs] == [str(subject_id), str(object_id)]
    # Only the subject/object UUIDs are queried.
    assert sorted(str(u) for u in pool.fetch.call_args.args[1]) == sorted(
        [str(subject_id), str(object_id)]
    )


async def test_resolve_referenced_entities_drops_unknown_uuids():
    from butlers.api.routers.approvals import _resolve_referenced_entities

    known_id = uuid4()
    unknown_id = uuid4()
    db, _ = _entity_db(
        fetch_return=[
            {"id": known_id, "canonical_name": "Known", "entity_type": "person", "roles": []},
        ]
    )
    refs = await _resolve_referenced_entities(
        db, {"subject": str(known_id), "object": str(unknown_id)}
    )
    assert [r.id for r in refs] == [str(known_id)]


async def test_resolve_referenced_entities_no_uuids_returns_empty():
    from butlers.api.routers.approvals import _resolve_referenced_entities

    db, pool = _entity_db()
    refs = await _resolve_referenced_entities(db, {"text": "hello", "n": 3})
    assert refs == []
    pool.fetch.assert_not_called()


async def test_resolve_referenced_entities_fails_open_on_db_error():
    from butlers.api.routers.approvals import _resolve_referenced_entities

    db, _ = _entity_db(fetch_raises=True)
    refs = await _resolve_referenced_entities(db, {"subject": str(uuid4())})
    assert refs == []


# ---------------------------------------------------------------------------
# Re-gate guard: _dispatch_approved_action must not record success when the
# tool re-enters the approval gate and returns {status: pending_approval}.
# Regression test for bu-km0y2.
# ---------------------------------------------------------------------------


def _build_dispatch_mocks(
    *,
    action_id,
    tool_name: str = "telegram_send_message",
    tool_args: dict | None = None,
    mcp_text_payload: str | None = None,
    mcp_is_error: bool = False,
    mark_executed_return: dict | None = None,
):
    """Build the minimal mocks needed to exercise _dispatch_approved_action."""
    from butlers.api.db import DatabaseManager
    from butlers.api.deps import MCPClientManager

    tool_args = tool_args or {"chat_id": "12345", "text": "Hello"}

    # MCP content block
    mcp_block = MagicMock()
    mcp_block.text = mcp_text_payload or '{"ok": true}'

    mcp_result = MagicMock()
    mcp_result.is_error = mcp_is_error
    mcp_result.content = [mcp_block] if mcp_text_payload is not None else []

    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=mcp_result)

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = ["messenger"]
    mock_mcp.get_client = AsyncMock(return_value=mock_client)

    # DB pool — mark_executed is patched at the module level in callers
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock()

    class _MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            pass

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=_MockAcquire())

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["messenger"]
    mock_db.pool = MagicMock(return_value=mock_pool)

    executed_result = mark_executed_return or {
        "id": str(action_id),
        "tool_name": tool_name,
        "tool_args": tool_args,
        "status": "executed",
        "requested_at": _NOW.isoformat(),
        "butler": "messenger",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "dashboard:rest-api",
        "decided_at": _NOW.isoformat(),
        "execution_result": {"success": True},
        "approval_rule_id": None,
    }

    return mock_mcp, mock_db, mock_pool, executed_result


async def test_dispatch_approved_action_executes_via_butler_tool():
    """Fix (bu-1q9wh): a gated tool is executed via the owning butler's un-gated
    ``dispatch_approved_action`` tool — NOT by re-calling the gated tool by name.

    Re-calling the gated tool by name re-entered the approval gate, which parked a
    phantom pending action and never ran the underlying tool (the message was
    never sent and the row stayed in the queue). Routing through the un-gated
    executor runs the original function and returns the action in its final
    ``executed`` state.
    """
    import json

    from butlers.api.routers.approvals import _dispatch_approved_action

    action_id = uuid4()
    executed_payload = {
        "id": str(action_id),
        "tool_name": "telegram_send_message",
        "tool_args": {"chat_id": "206570151", "text": "Hello"},
        "status": "executed",
        "requested_at": _NOW.isoformat(),
        "butler": "messenger",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "human:dashboard",
        "decided_at": _NOW.isoformat(),
        "execution_result": {"success": True, "result": {"message_id": "tg-1"}},
        "approval_rule_id": None,
    }

    mock_mcp, mock_db, mock_pool, _ = _build_dispatch_mocks(
        action_id=action_id,
        tool_name="telegram_send_message",
        tool_args={"chat_id": "206570151", "text": "Hello"},
        mcp_text_payload=json.dumps(executed_payload),
        mcp_is_error=False,
    )

    result = await _dispatch_approved_action(
        mock_mcp,
        mock_db,
        mock_pool,
        str(action_id),
        "telegram_send_message",
        {"chat_id": "206570151", "text": "Hello"},
        "messenger",
    )

    # The dispatcher must call the un-gated executor tool with just the action id,
    # never the gate-wrapped tool by name (which would re-park the action).
    call = mock_mcp.get_client.return_value.call_tool.call_args
    assert call.args[0] == "dispatch_approved_action", (
        f"Must invoke the un-gated executor, not {call.args[0]!r}"
    )
    assert call.args[1] == {"action_id": str(action_id)}
    assert result is not None
    assert result["status"] == "executed"


async def test_dispatch_approved_action_butler_error_returns_none():
    """When the owning butler cannot execute the action (error dict), and no other
    butler can either, the dispatcher returns None so the action stays 'approved'
    for retry rather than being falsely marked dispatched.
    """
    import json

    from butlers.api.routers.approvals import _dispatch_approved_action

    action_id = uuid4()
    err_payload = json.dumps({"error": "No tool executor wired on this butler"})

    mock_mcp, mock_db, mock_pool, _ = _build_dispatch_mocks(
        action_id=action_id,
        tool_name="telegram_send_message",
        tool_args={"chat_id": "206570151", "text": "Hi"},
        mcp_text_payload=err_payload,
        mcp_is_error=False,
    )

    result = await _dispatch_approved_action(
        mock_mcp,
        mock_db,
        mock_pool,
        str(action_id),
        "telegram_send_message",
        {"chat_id": "206570151", "text": "Hi"},
        "messenger",
    )

    assert result is None


def _mcp_result(text: str | None, *, is_error: bool = False) -> MagicMock:
    """Build a mock MCP tool result with a single text block (or no content)."""
    result = MagicMock()
    result.is_error = is_error
    if text is None:
        result.content = []
    else:
        block = MagicMock()
        block.text = text
        result.content = [block]
    return result


async def test_dispatch_approved_action_falls_back_to_next_butler():
    """When the owning butler declines (error dict), the next butler is tried in
    order and its successful execution is returned.
    """
    import json

    from butlers.api.routers.approvals import _dispatch_approved_action

    action_id = uuid4()
    executed_payload = {
        "id": str(action_id),
        "tool_name": "telegram_send_message",
        "tool_args": {},
        "status": "executed",
        "requested_at": _NOW.isoformat(),
        "butler": "general",
        "agent_summary": None,
        "session_id": None,
        "expires_at": None,
        "decided_by": "human:dashboard",
        "decided_at": _NOW.isoformat(),
        "execution_result": {"success": True},
        "approval_rule_id": None,
    }

    messenger_client = MagicMock()
    messenger_client.call_tool = AsyncMock(
        return_value=_mcp_result(json.dumps({"error": "No tool executor wired"}))
    )
    general_client = MagicMock()
    general_client.call_tool = AsyncMock(return_value=_mcp_result(json.dumps(executed_payload)))
    clients = {"messenger": messenger_client, "general": general_client}

    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = ["messenger", "general"]
    mock_mcp.get_client = AsyncMock(side_effect=lambda name: clients[name])

    result = await _dispatch_approved_action(
        mock_mcp,
        MagicMock(),
        MagicMock(),
        str(action_id),
        "telegram_send_message",
        {},
        "messenger",
    )

    assert result is not None
    assert result["status"] == "executed"
    # The owning butler is tried first, then the fallback — in order.
    assert [c.args[0] for c in mock_mcp.get_client.await_args_list] == ["messenger", "general"]


async def test_dispatch_approved_action_mcp_error_returns_none():
    """An MCP-level error from the butler's tool is a failed dispatch (returns None),
    leaving the action 'approved' for retry.
    """
    from butlers.api.routers.approvals import _dispatch_approved_action

    action_id = uuid4()
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=_mcp_result("{}", is_error=True))
    mock_mcp = MagicMock(spec=MCPClientManager)
    mock_mcp.butler_names = ["messenger"]
    mock_mcp.get_client = AsyncMock(return_value=mock_client)

    result = await _dispatch_approved_action(
        mock_mcp,
        MagicMock(),
        MagicMock(),
        str(action_id),
        "telegram_send_message",
        {"chat_id": "206570151", "text": "hi"},
        "messenger",
    )

    assert result is None


def test_first_json_block_handles_json_text_and_empty():
    """_first_json_block: empty content → None, JSON text → decoded, non-JSON → {value}."""
    from butlers.api.routers.approvals import _first_json_block

    assert _first_json_block(_mcp_result(None)) is None
    assert _first_json_block(_mcp_result('{"a": 1}')) == {"a": 1}
    assert _first_json_block(_mcp_result("oops")) == {"value": "oops"}


async def test_dispatch_approved_action_re_gate_notify_email_guard_uses_pending_action_id():
    """Re-gate guard: notify email-guard path keys the phantom id as pending_action_id.

    The notify email-guard returns {status: pending_approval, pending_action_id: ...}
    (NOT action_id). The re-gate guard must extract the phantom id from that key so
    the error message names the phantom action correctly instead of falling back
    to '<unknown>'.

    Regression test for bu-2r332.
    """
    import json
    from unittest.mock import patch

    import butlers.modules.approvals.operations as approvals_ops
    from butlers.api.routers.approvals import _dispatch_approved_action

    action_id = uuid4()
    phantom_action_id = uuid4()

    # notify email-guard returns pending_approval with pending_action_id (not action_id)
    notify_email_guard_payload = json.dumps(
        {
            "status": "pending_approval",
            "error": (
                "Delivery blocked: email target 'someone@example.com' is a "
                "non-standing contact and no standing approval rule matches."
            ),
            "pending_action_id": str(phantom_action_id),
        }
    )

    mock_mcp, mock_db, mock_pool, _ = _build_dispatch_mocks(
        action_id=action_id,
        tool_name="notify",
        tool_args={"channel": "email", "message": "Hello", "recipient": "someone@example.com"},
        mcp_text_payload=notify_email_guard_payload,
        mcp_is_error=False,
    )

    captured: dict = {}

    async def _capture(conn, *, action_id, execution_result, success):
        captured["success"] = success
        captured["result"] = execution_result
        return {
            "id": str(action_id),
            "status": "executed",
            "tool_name": "notify",
            "tool_args": {},
            "requested_at": _NOW.isoformat(),
            "butler": "switchboard",
            "agent_summary": None,
            "session_id": None,
            "expires_at": None,
            "decided_by": None,
            "decided_at": None,
            "execution_result": execution_result,
            "approval_rule_id": None,
        }

    with patch.object(approvals_ops, "mark_executed", side_effect=_capture):
        result = await _dispatch_approved_action(
            mock_mcp,
            mock_db,
            mock_pool,
            str(action_id),
            "notify",
            {"channel": "email", "message": "Hello", "recipient": "someone@example.com"},
        )

    # Must be recorded as failure — the notify email-guard re-parked the action
    assert captured["success"] is False, "Re-gate via notify email-guard must be a failure"

    # Error must include the phantom id from pending_action_id (not fall back to <unknown>)
    error_msg = captured["result"].get("error", "")
    assert str(phantom_action_id) in error_msg, (
        f"Error must reference the phantom action id from pending_action_id={phantom_action_id}; "
        f"got: {error_msg!r}"
    )
    assert "<unknown>" not in error_msg, (
        "Error must NOT fall back to '<unknown>' when pending_action_id is present"
    )

    assert result is not None
