"""The ordinary HTTP approval consumes exactly the owner's displayed call.

These are software transaction/HTTP controls, not PostgreSQL lock evidence.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.api.routers import approvals as routes
from butlers.modules.approvals.previews import args_digest

pytestmark = pytest.mark.unit


async def _admitted_rule_transaction(connection, kind, parameters, writer):
    """Explicit software admission collaborator for the original ordering cases.

    These cases do not have a database or a real factor. The separate migrated
    step-up owner proves admission; retain this owner's complete original
    decision/audit/memory/dispatch and refusal assertions after that boundary.
    """
    from butlers.modules.approvals.stepup import _enlisted_write, _EnlistedWrite

    async with connection.transaction():
        token = _enlisted_write.set(_EnlistedWrite(connection, kind))
        try:
            return await writer(connection)
        finally:
            _enlisted_write.reset(token)


@pytest.mark.parametrize("legacy", [False, True])
async def test_changed_viewed_call_refuses_before_any_effect(app, monkeypatch, legacy):
    action_id = uuid4()
    shown = {"chat_id": "test-recipient", "text": "Owner reviewed this"}
    current = {**shown, "text": "A later unreviewed call"}
    events = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            events.append("begin")
            try:
                yield
            except BaseException:
                events.append("rollback")
                raise
            else:
                events.append("commit")

        async def fetchrow(self, sql, *args):
            events.append("locked" if "FOR UPDATE" in sql else "read")
            return {"tool_name": "telegram_send_message", "tool_args": current}

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()

    async def find(*args):
        return "general", pool

    monkeypatch.setattr(routes, "_find_action_pool", find)
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *a: None)
    approve = AsyncMock(return_value={"error": "approval reached without checking the view"})
    audit = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(routes.approvals_ops, "approve_action", approve)
    monkeypatch.setattr(routes.audit_router, "append", audit)
    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[routes.get_mcp_manager] = lambda: object()
    path = f"/api/approvals/{'actions/' if legacy else ''}{action_id}/approve"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            path,
            json={
                "displayed_call": {
                    "tool_name": "telegram_send_message",
                    "args_digest": args_digest(shown),
                }
            },
        )
    assert response.status_code == 409
    assert events == ["begin", "locked", "rollback"]
    approve.assert_not_awaited()
    audit.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.fixture
async def binding_http(app, monkeypatch):
    """Stateful software boundary; actual routes/DTOs/preview/lock/edits/audit.

    The operation/dispatch adapters are recorded collaborators. This is not
    PostgreSQL, a real side effect, or migration/role proof.
    """
    from copy import deepcopy

    from fastapi import HTTPException

    state = {
        "row": {
            "id": uuid4(),
            "tool_name": "telegram_send_message",
            "tool_args": {"chat_id": "test-recipient", "text": "Original body"},
            "status": "pending",
            "requested_at": datetime.now(UTC),
        },
        "events": [],
        "dispatches": [],
        "audit_fail": False,
    }

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            before = deepcopy(state["row"])
            state["events"].append("begin")
            try:
                yield
            except BaseException:
                state["row"] = before
                state["events"].append("rollback")
                raise
            else:
                state["events"].append("commit")

        async def fetchrow(self, sql, *args):
            assert "FOR UPDATE" in sql
            state["events"].append("lock")
            return deepcopy(state["row"])

        async def execute(self, sql, *args):
            assert sql.startswith("UPDATE pending_actions SET tool_args")
            state["events"].append("edit")
            state["row"]["tool_args"] = deepcopy(args[0])

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()
    monkeypatch.setattr(routes, "run_rule_write", _admitted_rule_transaction)

    async def find(*args):
        return "general", pool

    async def approve(actual_conn, **kwargs):
        assert actual_conn is conn and state["events"][0:2] == ["begin", "lock"]
        state["events"].append("approve")
        state["approval_kwargs"] = kwargs
        if state["row"]["status"] != "pending":
            return {"error": "cannot transition decided action"}
        state["row"]["status"] = "approved"
        row = deepcopy(state["row"])
        row["id"] = str(row["id"])
        return row

    async def audit(*args, **kwargs):
        state["events"].append("audit")
        if state["audit_fail"]:
            raise HTTPException(status_code=503, detail="audit_unavailable")

    async def dispatch(*args):
        state["dispatches"].append(deepcopy(args[4:6]))
        return routes._DispatchOutcome(kind="unreachable")

    monkeypatch.setattr(routes, "_find_action_pool", find)
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *args: None)
    monkeypatch.setattr(routes.approvals_ops, "approve_action", approve)
    monkeypatch.setattr(routes.audit_router, "append", audit)
    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[routes.get_mcp_manager] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, state


@pytest.mark.parametrize("legacy", [False, True])
async def test_unchanged_view_and_explicit_owner_edit_keep_audit_dispatch_order(
    binding_http, legacy
):
    from butlers.modules.approvals.previews import dashboard_call_preview

    client, state = binding_http
    row = state["row"]
    preview = dashboard_call_preview(row["tool_name"], row["tool_args"])
    assert preview["facts"] == [
        {"label": "To", "value": "test-recipient"},
        {"label": "Message", "value": "Original body"},
    ]
    payload = {"displayed_call": preview["displayed_call"]}
    if legacy:
        payload["create_rule"] = True
    else:
        payload["edits"] = {"text": "Explicit owner edit"}
    path = f"/api/approvals/{'actions/' if legacy else ''}{row['id']}/approve"
    response = await client.post(path, json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"
    assert response.json()["data"]["dispatched"] is False
    expected = "Original body" if legacy else "Explicit owner edit"
    assert state["dispatches"] == [
        ("telegram_send_message", {"chat_id": "test-recipient", "text": expected})
    ]
    assert state["events"] == [
        "begin",
        "lock",
        *([] if legacy else ["edit"]),
        "approve",
        "audit",
        "commit",
    ]
    assert state["approval_kwargs"]["create_rule"] is legacy
    assert state["approval_kwargs"]["actor_id"] == "dashboard:rest-api"
    state["events"].clear()
    response = await client.post(path, json=payload)
    assert response.status_code == 409
    assert len(state["dispatches"]) == 1


async def test_missing_forged_and_unshowable_views_refuse_without_writes(binding_http):
    client, state = binding_http
    row = state["row"]
    path = f"/api/approvals/{row['id']}/approve"
    view = {"tool_name": row["tool_name"], "args_digest": args_digest(row["tool_args"])}
    for payload, code in [
        ({}, 409),
        ({"displayed_call": {**view, "tool_name": "other"}}, 409),
        ({"displayed_call": {**view, "args_digest": "invalid"}}, 422),
        ({"displayed_call": {**view, "actor": "human"}}, 422),
    ]:
        state["events"].clear()
        response = await client.post(path, json=payload)
        assert response.status_code == code
        assert not {"edit", "approve", "audit", "commit"} & set(state["events"])
    row["tool_args"]["secret_token"] = "static-test-value"
    response = await client.post(
        path,
        json={
            "displayed_call": {
                **view,
                "args_digest": args_digest(row["tool_args"]),
            }
        },
    )
    assert response.status_code == 409
    assert state["dispatches"] == []
    assert state["row"]["status"] == "pending"


async def test_audit_failure_rolls_back_owner_edit_and_approval_without_dispatch(binding_http):
    client, state = binding_http
    state["audit_fail"] = True
    row = state["row"]
    response = await client.post(
        f"/api/approvals/{row['id']}/approve",
        json={
            "displayed_call": {
                "tool_name": row["tool_name"],
                "args_digest": args_digest(row["tool_args"]),
            },
            "edits": {"text": "Owner edit"},
        },
    )
    assert response.status_code == 503
    assert state["row"]["status"] == "pending"
    assert state["row"]["tool_args"]["text"] == "Original body"
    assert state["events"] == ["begin", "lock", "edit", "approve", "audit", "rollback"]
    assert state["dispatches"] == []


@pytest.mark.parametrize("boundary", ["audit_failure", "committed", "memory_failure"])
async def test_legacy_rule_memory_follows_owning_audit_commit(app, monkeypatch, boundary):
    """Real HTTP/approval/rule operations; software SQL/commit collaborators.

    The memory sink is deliberately separate from the rollback-able owner state.
    This proves call ordering, not real PostgreSQL or durable memory readback.
    """
    from copy import deepcopy

    from fastapi import HTTPException

    from butlers.modules.approvals.delivery_lifecycle import PendingTransition
    from butlers.modules.approvals.models import PendingAction

    row = {
        "id": uuid4(),
        "tool_name": "telegram_send_message",
        "tool_args": {"chat_id": "test-recipient", "text": "Owner reviewed this"},
        "status": "pending",
        "requested_at": datetime.now(UTC),
    }
    owner = {"row": row, "rules": []}
    events = []
    memory = []
    dispatches = []

    class Conn:
        depth = 0

        @asynccontextmanager
        async def transaction(self):
            before = deepcopy(owner)
            outer = self.depth == 0
            self.depth += 1
            events.append("begin" if outer else "savepoint")
            try:
                yield
            except BaseException:
                owner.clear()
                owner.update(before)
                events.append("rollback" if outer else "rollback_to")
                raise
            else:
                events.append("commit" if outer else "release")
            finally:
                self.depth -= 1

        async def fetchrow(self, sql, *args):
            assert "FROM pending_actions" in sql
            return deepcopy(owner["row"])

        async def execute(self, sql, *args):
            if sql.startswith("INSERT INTO approval_rules"):
                events.append("rule_insert")
                owner["rules"].append(args[0])
            else:
                assert sql.startswith("INSERT INTO approval_events")
                events.append("rule_event")

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()
    monkeypatch.setattr(routes, "run_rule_write", _admitted_rule_transaction)

    class Writer:
        async def record_standing_rule(self, rule, *, active):
            events.append("memory")
            assert active is True
            memory.append(rule.to_dict())
            if boundary == "memory_failure":
                raise RuntimeError("test memory sink unavailable")

    async def find(*args):
        return "general", pool

    async def transition(actual_conn, **kwargs):
        assert actual_conn is conn
        assert kwargs["target_status"].value == "approved"
        owner["row"]["status"] = "approved"
        return PendingTransition(PendingAction.from_row(owner["row"]), changed=True)

    async def audit(*args, **kwargs):
        assert args[0] is conn
        events.append("audit")
        if boundary == "audit_failure":
            raise HTTPException(status_code=503, detail="audit_unavailable")

    async def dispatch(*args):
        events.append("dispatch")
        dispatches.append(args[3])
        return routes._DispatchOutcome(kind="unreachable")

    monkeypatch.setattr(routes, "_find_action_pool", find)
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *args: Writer())
    monkeypatch.setattr(routes.approvals_ops, "transition_pending_action", transition)
    for hook in (
        "_record_approval",
        "_check_promotion_threshold",
        "_supersede_matching_suggestions",
    ):
        monkeypatch.setattr(routes.approvals_ops, hook, AsyncMock())
    monkeypatch.setattr(routes.audit_router, "append", audit)
    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[routes.get_mcp_manager] = lambda: object()
    payload = {
        "create_rule": True,
        "displayed_call": {
            "tool_name": row["tool_name"],
            "args_digest": args_digest(row["tool_args"]),
        },
    }
    path = f"/api/approvals/actions/{row['id']}/approve"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(path, json=payload)
        if boundary == "audit_failure":
            assert response.status_code == 503
            assert owner["row"]["status"] == "pending"
            assert owner["rules"] == []
            assert events[-2:] == ["audit", "rollback"]
            assert memory == []
            assert dispatches == []
        else:
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "approved"
            assert len(owner["rules"]) == len(memory) == len(dispatches) == 1
            assert memory[0]["id"] == str(owner["rules"][0])
            assert memory[0]["tool_name"] == row["tool_name"]
            assert memory[0]["created_from"] == str(row["id"])
            assert memory[0]["active"] is True
            assert events.index("audit") < events.index("commit") < events.index("memory")
            assert events.index("memory") < events.index("dispatch")
            response = await client.post(path, json=payload)
            assert response.status_code == 409
            assert len(owner["rules"]) == len(memory) == len(dispatches) == 1


@pytest.mark.parametrize("refusal", ["already_approved", "expires_during_approval"])
async def test_refused_owner_edit_preserves_stored_call(binding_http, monkeypatch, refusal):
    """Actual HTTP/transaction boundary; refusal operation is a collaborator."""
    from copy import deepcopy

    client, state = binding_http
    row = state["row"]
    original = deepcopy(row["tool_args"])
    if refusal == "already_approved":
        row["status"] = "approved"
        expected_code = 409
    else:
        # Model the production operation's expiry transition and error result.
        async def expire(conn, **kwargs):
            state["events"].append("approve")
            state["row"]["status"] = "expired"
            return {"error": "Action expired and cannot be approved"}

        monkeypatch.setattr(routes.approvals_ops, "approve_action", expire)
        expected_code = 400
    response = await client.post(
        f"/api/approvals/{row['id']}/approve",
        json={
            "displayed_call": {
                "tool_name": row["tool_name"],
                "args_digest": args_digest(original),
            },
            "edits": {"text": "Refused owner edit"},
        },
    )
    assert response.status_code == expected_code
    assert state["row"]["tool_args"] == original
    assert state["row"]["status"] == ("approved" if refusal == "already_approved" else "expired")
    assert state["dispatches"] == []
    assert "audit" not in state["events"]
    assert state["events"][-1] == "commit"
