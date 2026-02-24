"""Tests for approvals API endpoints.

Verifies the API contract (status codes, response shapes, filtering, pagination)
for approvals dashboard endpoints.

Issue: butlers-0p6.6
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api import deps as api_deps
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import wire_db_dependencies
from butlers.api.routers.approvals import _clear_table_cache, _get_db_manager

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_approvals_cache():
    """Clear the table discovery cache before each test to prevent cross-test pollution."""
    _clear_table_cache()
    yield
    _clear_table_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_ACTION_ID = uuid4()
_RULE_ID = uuid4()


def _make_pending_action_record(
    *,
    action_id=None,
    tool_name="telegram_send_message",
    tool_args=None,
    status="pending",
    requested_at=_NOW,
    agent_summary=None,
    session_id=None,
    expires_at=None,
    decided_by=None,
    decided_at=None,
    execution_result=None,
    approval_rule_id=None,
) -> dict:
    """Create a dict mimicking an asyncpg Record for pending_actions columns."""
    return {
        "id": action_id or _ACTION_ID,
        "tool_name": tool_name,
        "tool_args": tool_args or {"chat_id": "12345", "text": "Hello"},
        "status": status,
        "requested_at": requested_at,
        "agent_summary": agent_summary,
        "session_id": session_id,
        "expires_at": expires_at,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "execution_result": execution_result,
        "approval_rule_id": approval_rule_id,
    }


def _make_approval_rule_record(
    *,
    rule_id=None,
    tool_name="telegram_send_message",
    arg_constraints=None,
    description="Auto-approve messages to support chat",
    created_from=None,
    created_at=_NOW,
    expires_at=None,
    max_uses=None,
    use_count=0,
    active=True,
) -> dict:
    """Create a dict mimicking an asyncpg Record for approval_rules columns."""
    return {
        "id": rule_id or _RULE_ID,
        "tool_name": tool_name,
        "arg_constraints": arg_constraints or {"chat_id": {"type": "exact", "value": "12345"}},
        "description": description,
        "created_from": created_from,
        "created_at": created_at,
        "expires_at": expires_at,
        "max_uses": max_uses,
        "use_count": use_count,
        "active": active,
    }


def _app_with_mock_db(
    *,
    has_approvals_tables=True,
    fetch_rows: list | None = None,
    fetchval_return=None,
    fetchrow_return=None,
    fetchval_side_effect=None,
):
    """Create a FastAPI app with a mocked DatabaseManager."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_rows or [])

    # Set up fetchval with side_effect or return value
    if fetchval_side_effect is not None:
        # For table existence check + other queries
        if has_approvals_tables:
            # Prepend True for table existence check
            full_side_effect = [True] + list(fetchval_side_effect)
            mock_conn.fetchval = AsyncMock(side_effect=full_side_effect)
        else:
            mock_conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        if has_approvals_tables:
            # Return True for table existence check, then fetchval_return for others
            def fetchval_mock(*args, **kwargs):
                # First call is table check
                if "information_schema.tables" in args[0]:
                    return True
                return fetchval_return

            mock_conn.fetchval = AsyncMock(side_effect=fetchval_mock)
        else:
            mock_conn.fetchval = AsyncMock(return_value=fetchval_return)

    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)
    mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.__aexit__ = AsyncMock(return_value=None)

    # Mock the acquire() context manager properly
    class MockAcquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            pass

    mock_pool.acquire.return_value = MockAcquire()

    mock_db = MagicMock(spec=DatabaseManager)
    # Return the pool when queried, or raise KeyError if no approvals
    if has_approvals_tables:
        mock_db.pool.return_value = mock_pool
        mock_db.butler_names = ["general", "switchboard"]
    else:
        mock_db.pool.side_effect = KeyError("No pool")
        mock_db.butler_names = []

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_conn


# ---------------------------------------------------------------------------
# Tests: Actions endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_actions_empty():
    """GET /api/approvals/actions returns empty list when no actions exist."""
    app, mock_conn = _app_with_mock_db(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions")

    assert response.status_code == 200
    data = response.json()
    assert data["data"] == []
    assert data["meta"]["total"] == 0
    assert data["meta"]["offset"] == 0
    assert data["meta"]["limit"] == 50


@pytest.mark.asyncio
async def test_list_actions_uses_global_db_dependency_wiring(monkeypatch):
    """Approvals actions endpoint should use wire_db_dependencies override path."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []

    app = create_app()
    wire_db_dependencies(app)
    monkeypatch.setattr(api_deps, "_db_manager", mock_db)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions")

    assert response.status_code == 200
    data = response.json()
    assert data["data"] == []
    assert data["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_list_actions_with_results():
    """GET /api/approvals/actions returns paginated pending actions."""
    action1 = _make_pending_action_record(
        action_id=uuid4(), tool_name="telegram_send_message", status="pending"
    )
    action2 = _make_pending_action_record(
        action_id=uuid4(), tool_name="email_send", status="approved"
    )

    app, mock_conn = _app_with_mock_db(fetch_rows=[action1, action2], fetchval_return=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions?limit=10&offset=0")

    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["data"][0]["tool_name"] == "telegram_send_message"
    assert data["data"][1]["tool_name"] == "email_send"
    assert data["meta"]["total"] == 2
    assert data["meta"]["limit"] == 10


@pytest.mark.asyncio
async def test_list_actions_with_status_filter():
    """GET /api/approvals/actions?status=pending filters by status."""
    action = _make_pending_action_record(status="pending")
    app, mock_conn = _app_with_mock_db(fetch_rows=[action], fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions?status=pending")

    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_list_actions_no_approvals_tables():
    """GET /api/approvals/actions returns empty when no butler has approvals."""
    app, _ = _app_with_mock_db(has_approvals_tables=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions")

    assert response.status_code == 200
    data = response.json()
    assert data["data"] == []
    assert data["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_get_action_by_id():
    """GET /api/approvals/actions/{action_id} returns action details."""
    action = _make_pending_action_record(action_id=_ACTION_ID)
    app, mock_conn = _app_with_mock_db(fetchrow_return=action)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/approvals/actions/{_ACTION_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["id"] == str(_ACTION_ID)
    assert data["data"]["tool_name"] == "telegram_send_message"


@pytest.mark.asyncio
async def test_get_action_not_found():
    """GET /api/approvals/actions/{action_id} returns 404 when action not found."""
    app, mock_conn = _app_with_mock_db(fetchrow_return=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/approvals/actions/{uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_action_invalid_id():
    """GET /api/approvals/actions/{action_id} returns 400 for invalid UUID."""
    app, _ = _app_with_mock_db()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions/not-a-uuid")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_approve_action_not_implemented():
    """POST /api/approvals/actions/{action_id}/approve returns 501 (not implemented)."""
    app, _ = _app_with_mock_db()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/approvals/actions/{uuid4()}/approve")

    assert response.status_code == 501


@pytest.mark.asyncio
async def test_reject_action_not_implemented():
    """POST /api/approvals/actions/{action_id}/reject returns 501 (not implemented)."""
    app, _ = _app_with_mock_db()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approvals/actions/{uuid4()}/reject",
            json={"reason": "Not needed"},
        )

    assert response.status_code == 501


@pytest.mark.asyncio
async def test_expire_stale_actions():
    """POST /api/approvals/actions/expire-stale marks expired pending actions."""
    expired_id1 = uuid4()
    expired_id2 = uuid4()

    app, mock_conn = _app_with_mock_db(
        fetch_rows=[{"id": expired_id1}, {"id": expired_id2}],
        fetchval_side_effect=[expired_id1, expired_id2],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/approvals/actions/expire-stale")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["expired_count"] == 2
    assert str(expired_id1) in data["data"]["expired_ids"]
    assert str(expired_id2) in data["data"]["expired_ids"]


@pytest.mark.asyncio
async def test_list_executed_actions():
    """GET /api/approvals/actions/executed returns executed actions."""
    executed_action = _make_pending_action_record(
        action_id=uuid4(),
        status="executed",
        decided_at=_NOW,
        execution_result={"success": True},
    )

    app, mock_conn = _app_with_mock_db(fetch_rows=[executed_action], fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/actions/executed")

    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["status"] == "executed"


# ---------------------------------------------------------------------------
# Tests: Rules endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rules_empty():
    """GET /api/approvals/rules returns empty list when no rules exist."""
    app, mock_conn = _app_with_mock_db(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/rules")

    assert response.status_code == 200
    data = response.json()
    assert data["data"] == []
    assert data["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_list_rules_with_results():
    """GET /api/approvals/rules returns paginated rules."""
    rule1 = _make_approval_rule_record(rule_id=uuid4(), tool_name="telegram_send_message")
    _make_approval_rule_record(rule_id=uuid4(), tool_name="email_send", active=False)

    app, mock_conn = _app_with_mock_db(fetch_rows=[rule1], fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/rules?active_only=true")

    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["active"] is True


@pytest.mark.asyncio
async def test_get_rule_by_id():
    """GET /api/approvals/rules/{rule_id} returns rule details."""
    rule = _make_approval_rule_record(rule_id=_RULE_ID)
    app, mock_conn = _app_with_mock_db(fetchrow_return=rule)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/approvals/rules/{_RULE_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["id"] == str(_RULE_ID)
    assert data["data"]["tool_name"] == "telegram_send_message"


@pytest.mark.asyncio
async def test_get_rule_not_found():
    """GET /api/approvals/rules/{rule_id} returns 404 when rule not found."""
    app, mock_conn = _app_with_mock_db(fetchrow_return=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/approvals/rules/{uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_rule_not_implemented():
    """POST /api/approvals/rules returns 501 (not yet implemented)."""
    app, _ = _app_with_mock_db()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/approvals/rules",
            json={
                "tool_name": "telegram_send_message",
                "arg_constraints": {"chat_id": {"type": "exact", "value": "12345"}},
                "description": "Test rule",
            },
        )

    assert response.status_code == 501


@pytest.mark.asyncio
async def test_create_rule_from_action_not_implemented():
    """POST /api/approvals/rules/from-action returns 501 (not yet implemented)."""
    app, _ = _app_with_mock_db()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/approvals/rules/from-action",
            json={"action_id": str(uuid4())},
        )

    assert response.status_code == 501


@pytest.mark.asyncio
async def test_revoke_rule_not_implemented():
    """POST /api/approvals/rules/{rule_id}/revoke returns 501 (not yet implemented)."""
    app, _ = _app_with_mock_db()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/approvals/rules/{uuid4()}/revoke")

    assert response.status_code == 501


@pytest.mark.asyncio
async def test_get_rule_suggestions():
    """GET /api/approvals/rules/suggestions/{action_id} returns suggestions."""
    action = _make_pending_action_record(
        action_id=_ACTION_ID,
        tool_args={"chat_id": "12345", "text": "Hello"},
    )
    app, mock_conn = _app_with_mock_db(fetchrow_return=action)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/approvals/rules/suggestions/{_ACTION_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["action_id"] == str(_ACTION_ID)
    assert data["data"]["tool_name"] == "telegram_send_message"
    assert "suggested_constraints" in data["data"]


# ---------------------------------------------------------------------------
# Tests: Metrics endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_metrics():
    """GET /api/approvals/metrics returns aggregate metrics."""
    app, mock_conn = _app_with_mock_db(
        fetchval_side_effect=[
            5,  # total_pending
            10,  # total_approved_today
            2,  # total_rejected_today
            3,  # total_auto_approved_today
            1,  # total_expired_today
            12,  # total_decisions_today
            2,  # failure_count_today (changed from fetch to fetchval)
            7,  # active_rules_count
        ]
    )

    # Mock avg_latency query
    mock_conn.fetchrow = AsyncMock(return_value={"avg_latency": 120.5})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/metrics")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["total_pending"] == 5
    assert data["data"]["total_approved_today"] == 10
    assert data["data"]["total_rejected_today"] == 2
    assert data["data"]["total_auto_approved_today"] == 3
    assert data["data"]["total_expired_today"] == 1
    assert data["data"]["avg_decision_latency_seconds"] == 120.5
    assert data["data"]["auto_approval_rate"] > 0
    assert data["data"]["rejection_rate"] > 0
    assert data["data"]["failure_count_today"] == 2
    assert data["data"]["active_rules_count"] == 7


@pytest.mark.asyncio
async def test_get_metrics_no_approvals_subsystem():
    """GET /api/approvals/metrics returns zeroed metrics when no approvals subsystem."""
    app, _ = _app_with_mock_db(has_approvals_tables=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/approvals/metrics")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["total_pending"] == 0
    assert data["data"]["total_approved_today"] == 0
    assert data["data"]["active_rules_count"] == 0
