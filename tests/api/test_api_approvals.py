"""Tests for approvals API endpoints.

Condensed from 56 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list paginated structure, 404/422 error paths (parametrized),
       graceful empty when no approvals table.

Extended (bu-d3fhz): butler filter param + butler field on ApprovalAction.
Extended (bu-5xiu9): defer bounds, policy round-trip, audit.append on verbs.
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


async def test_list_executed_actions_butler_filter(app):
    """?butler= param on /actions/executed returns only that butler's rows."""
    home_action = _make_action(tool_name="notify", status="executed")
    general_action = _make_action(tool_name="send_telegram", status="executed")
    app = _app_with_two_butlers(app, home_rows=[home_action], general_rows=[general_action])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions/executed?butler=home")
    assert resp.status_code == 200
    body = resp.json()
    actions = body["data"]
    assert len(actions) == 1
    assert actions[0]["butler"] == "home"
    assert actions[0]["tool_name"] == "notify"


# ---------------------------------------------------------------------------
# §8.7 — defer bounds, policy round-trip, audit.append on verbs
# ---------------------------------------------------------------------------


def _make_pending_row(*, tool_name="send_email", status="pending"):
    """Return a dict matching pending_actions columns including why/evidence."""
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
        "evidence": ["User signed up at 2026-05-16T10:00:00Z", "Email not yet sent"],
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
async def test_defer_hours_bounds(app, hours, expected_status):
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


async def test_policy_round_trip(app):
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

    # Verify conn.execute was called for the INSERT/ON CONFLICT UPDATE
    mock_conn.execute.assert_called()


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
        audit_calls.append({"actor": actor, "action": action, "target": target, "note": note})
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
        audit_calls.append({"actor": actor, "action": action, "target": target, "note": note})
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


async def test_why_and_evidence_returned_on_actions_list(app):
    """GET /api/approvals/actions includes why and evidence on each action row."""
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
        "User signed up at 2026-05-16T10:00:00Z",
        "Email not yet sent",
    ]


# ---------------------------------------------------------------------------
# §8.3 — WebSocket /api/approvals/stream
# ---------------------------------------------------------------------------


async def test_approvals_stream_auth_gate_rejects_bad_key(app):
    """WS /api/approvals/stream closes when api_key is wrong."""
    import os
    from unittest.mock import patch

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with patch.dict(os.environ, {"DASHBOARD_API_KEY": "secret123"}):
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/approvals/stream?api_key=wrongkey"):
                pass


async def test_approvals_stream_auth_gate_allows_correct_key(app):
    """WS /api/approvals/stream accepts the correct api_key."""
    import os
    from unittest.mock import patch

    from starlette.testclient import TestClient

    from butlers.api.routers.approvals import _approvals_ring

    _approvals_ring.clear()

    with patch.dict(os.environ, {"DASHBOARD_API_KEY": "secret123"}):
        client = TestClient(app)
        with client.websocket_connect("/api/approvals/stream?api_key=secret123"):
            # With empty ring buffer, next message is a keepalive — but
            # we just confirm the connection was accepted (no close code 4401).
            pass  # connected and disconnected cleanly


async def test_approvals_stream_no_auth_when_key_not_configured(app):
    """WS /api/approvals/stream accepts connections when DASHBOARD_API_KEY is not set."""
    import os
    from unittest.mock import patch

    from starlette.testclient import TestClient

    from butlers.api.routers.approvals import _approvals_ring

    _approvals_ring.clear()

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("DASHBOARD_API_KEY", None)
        client = TestClient(app)
        with client.websocket_connect("/api/approvals/stream"):
            pass  # connected cleanly


async def test_approvals_stream_snapshot_on_connect(app):
    """Connecting client receives ring-buffered events as snapshot messages."""
    from starlette.testclient import TestClient

    from butlers.api.routers.approvals import _approvals_ring, emit_approvals_event

    _approvals_ring.clear()
    # Pre-populate ring buffer
    emit_approvals_event("approved", "aaa-111", butler="home", tool_name="send_email")
    emit_approvals_event("rejected", "bbb-222", butler="home", tool_name="send_email")

    client = TestClient(app)
    received = []
    with client.websocket_connect("/api/approvals/stream") as ws:
        for _ in range(2):
            msg = ws.receive_json()
            received.append(msg)

    _approvals_ring.clear()

    assert len(received) == 2
    assert all(m["snapshot"] is True for m in received)
    kinds = {m["kind"] for m in received}
    assert kinds == {"approved", "rejected"}


async def test_emit_approvals_event_publishes_to_subscribers(app):
    """emit_approvals_event delivers events to subscriber queues."""
    import asyncio

    from butlers.api.routers.approvals import (
        _approvals_subscribers,
        emit_approvals_event,
    )

    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _approvals_subscribers.append(q)
    try:
        emit_approvals_event(
            "executed",
            "ccc-333",
            butler="general",
            tool_name="notify",
            status="executed",
        )
        assert not q.empty()
        event = q.get_nowait()
        assert event["kind"] == "executed"
        assert event["approval_id"] == "ccc-333"
        assert event["butler"] == "general"
    finally:
        try:
            _approvals_subscribers.remove(q)
        except ValueError:
            pass


async def test_why_null_evidence_empty_on_legacy_rows(app):
    """Legacy rows (why=NULL, evidence=[]) are returned with why=null and evidence=[]."""
    legacy_row = {
        "id": uuid4(),
        "tool_name": "send_email",
        "tool_args": {"to": "user@example.com"},
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
    app, _ = _app_with_mock_db(app, fetch_rows=[legacy_row], fetchval_return=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/approvals/actions")
    assert resp.status_code == 200
    actions = resp.json()["data"]
    assert len(actions) == 1
    assert actions[0]["why"] is None
    assert actions[0]["evidence"] == []
