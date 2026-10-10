# Spec: REQ-dashboard-api-069, REQ-dashboard-api-070
"""Authenticated response admission and exact same-owner decision composition."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.api.deps import get_mcp_manager
from butlers.api.routers import approvals
from butlers.api.routers import calendar_workspace as workspace
from butlers.modules.calendar_response import _digest

pytestmark = pytest.mark.unit


async def test_owner_response_rejects_injection_and_binds_actual_command_pool(app, monkeypatch):
    entry_id, request_id, command_id = (uuid.uuid4() for _ in range(3))
    arguments = dict(
        entry_id=str(entry_id),
        request_id=str(request_id),
        response_status="accepted",
        send_updates="none",
    )
    # Invoke the actual owner-read consumer: an ordinary action cannot shadow
    # a response command with the same UUID in another source schema.
    from fastapi import HTTPException

    duplicate_db = SimpleNamespace(
        butlers_with_module=lambda module: ["relationship", "messenger"],
        fan_out_with_status=AsyncMock(
            return_value=(
                {
                    "relationship": [{"id": command_id, "action_type": "workspace_user_create"}],
                    "messenger": [{"id": command_id, "action_type": "workspace_user_respond"}],
                },
                [],
            )
        ),
    )
    with pytest.raises(HTTPException) as duplicate:
        await workspace._find_action_owner(duplicate_db, command_id)
    assert duplicate.value.status_code == 409
    assert duplicate.value.detail == "calendar_action_owner_ambiguous"

    command = {"owner": "messenger", "arguments": arguments}
    payload = {
        "request_digest": _digest(arguments),
        "digest": _digest(command),
        "command": command,
        "phase": "prepared",
    }
    private_args = {
        **arguments,
        "_command_id": str(command_id),
        "_command_digest": payload["digest"],
    }
    receipt = None
    concurrent_inverse_claim = False
    pending = {"tool_name": "calendar_respond", "tool_args": private_args, "status": "pending"}

    async def read(sql, *args):
        assert args == (command_id,)
        if "FROM pending_actions" in sql:
            return pending
        assert "FROM calendar_action_log" in sql
        return {"action_payload": payload, "action_result": receipt}

    pool = SimpleNamespace(fetchrow=AsyncMock(side_effect=read))
    db = SimpleNamespace(
        pool=lambda owner: pool if owner == "messenger" else None,
        butler_names=["messenger", "relationship"],
        configured_butlers_with_module=lambda module: ["messenger", "relationship"],
        fan_out_with_status=AsyncMock(
            return_value=({"messenger": [{"id": entry_id}], "relationship": []}, [])
        ),
    )
    # Real source-qualified dossier read selection refuses wrong, duplicate,
    # incomplete, degraded and changed private command binding without effect.
    review_rows = {"messenger": [{"id": command_id}], "relationship": []}
    db.fan_out_with_status.return_value = (review_rows, [])

    async def review_read(sql, *args):
        assert args == (command_id,)
        if "calendar_action_log" in sql:
            return {"action_type": "workspace_user_respond", "action_payload": payload}
        return pending

    old_reader = pool.fetchrow
    pool.fetchrow = AsyncMock(side_effect=review_read)
    assert await workspace._calendar_response_review_pool(db, command_id, "messenger") == (
        pool,
        private_args,
    )
    with pytest.raises(HTTPException):
        await workspace._calendar_response_review_pool(db, command_id, "relationship")
    for rows, failed in [
        ({"messenger": [{"id": command_id}], "relationship": [{"id": command_id}]}, []),
        ({"messenger": [{"id": command_id}]}, []),
        (review_rows, ["relationship"]),
    ]:
        db.fan_out_with_status.return_value = (rows, failed)
        with pytest.raises(HTTPException):
            await workspace._calendar_response_review_pool(db, command_id, "messenger")
    db.fan_out_with_status.return_value = (review_rows, [])
    pending["tool_args"] = {**private_args, "_command_digest": "wrong"}
    with pytest.raises(HTTPException):
        await workspace._calendar_response_review_pool(db, command_id, "messenger")
    pending["tool_args"] = private_args
    assert await workspace._calendar_response_review_pool(db, command_id, "messenger") == (
        pool,
        private_args,
    )
    # Actual GET reads again after admission. A final read failure is unavailable,
    # and a changed tool/argument/UUID must not become a different earned dossier.
    final_kind = "healthy"

    async def final_read(sql, *args):
        assert args == (command_id,) and "pending_actions AS pa" in sql
        if final_kind == "read-failure":
            raise RuntimeError("Synthetic final dossier read failure")
        if final_kind == "missing":
            return None
        row = {
            "id": command_id,
            "tool_name": "calendar_respond",
            "tool_args": dict(private_args),
            "status": "pending",
            "requested_at": datetime.now(UTC),
        }
        if final_kind == "changed-tool":
            row["tool_name"] = "send_email"
        elif final_kind == "changed-args":
            row["tool_args"] = {**private_args, "response_status": "declined"}
        elif final_kind == "changed-digest":
            row["tool_args"] = {**private_args, "_command_digest": "changed"}
        elif final_kind == "changed-id":
            row["id"] = uuid.uuid4()
        return row

    connection = SimpleNamespace(
        fetchval=AsyncMock(return_value=False), fetchrow=AsyncMock(side_effect=final_read)
    )

    @asynccontextmanager
    async def acquire_review():
        yield connection

    pool.acquire = acquire_review
    app.dependency_overrides[approvals._get_db_manager] = lambda: db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as review_client:
            review_url = f"/api/approvals/{command_id}?review_source=calendar:messenger"
            healthy = await review_client.get(review_url)
            assert healthy.status_code == 200
            assert healthy.json()["data"]["id"] == str(command_id)
            assert healthy.json()["data"]["butler"] == "messenger"
            assert healthy.json()["data"]["proposed_action"]["tool_name"] == "calendar_respond"
            for final_kind in (
                "read-failure",
                "missing",
                "changed-tool",
                "changed-args",
                "changed-digest",
                "changed-id",
            ):
                refused = await review_client.get(review_url)
                assert refused.status_code == (
                    503 if final_kind in {"read-failure", "missing"} else 409
                )
                assert "data" not in refused.json()
                assert "Synthetic" not in refused.text
            final_kind = "healthy"
            assert (await review_client.get(review_url)).status_code == 200
    finally:
        app.dependency_overrides.pop(approvals._get_db_manager, None)
    pool.fetchrow = old_reader
    db.fan_out_with_status.return_value = (
        {"messenger": [{"id": entry_id}], "relationship": []},
        [],
    )
    mcp = SimpleNamespace()
    call = AsyncMock(
        return_value={
            "status": "pending_approval",
            "command_id": str(command_id),
            "action_id": str(command_id),
        }
    )
    decision = AsyncMock()

    async def decide(**kwargs):
        nonlocal receipt
        if concurrent_inverse_claim:
            payload["inverse_claim"] = str(uuid.uuid4())
        assert kwargs["target_pool"] is pool and kwargs["action_butler"] == "messenger"
        assert kwargs["expected_tool_args"] == private_args and kwargs["edits"] is None
        receipt = {
            "status": "applied",
            "projection_available": False,
            "before": {"response_status": "needsAction"},
        }
        return SimpleNamespace(data=SimpleNamespace(dispatched=True))

    decision.side_effect = decide
    monkeypatch.setattr(workspace, "_call_mcp_tool", call)
    monkeypatch.setattr("butlers.api.routers.approvals.approve_owning_action", decision)
    app.dependency_overrides[workspace._get_db_manager] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: mcp
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for field in ("actor", "approval_id", "account", "recipient", "etag", "_command_id"):
                injected = await client.post(
                    "/api/calendar/workspace/respond", json={**arguments, field: "caller"}
                )
                assert injected.status_code == 422
            call.assert_not_awaited()
            response = await client.post("/api/calendar/workspace/respond", json=arguments)
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["status"] == "applied" and data["projection_available"] is False
            assert data["undo_available"] is True and data["source_butler"] == "messenger"
            assert data["command_id"] == str(command_id) and data["approval_id"] == str(command_id)
            assert decision.await_count == 1
            invoked = call.call_args.args
            assert invoked[:3] == (mcp, "messenger", "calendar_respond")
            assert (
                invoked[3]["_blast_radius"] == "self"
                and invoked[3]["_reversibility"] == "compensable"
            )
            db.fan_out_with_status.return_value = (
                {"messenger": [{"id": entry_id}], "relationship": [{"id": entry_id}]},
                [],
            )
            assert (
                await client.post("/api/calendar/workspace/respond", json=arguments)
            ).status_code == 409
            db.fan_out_with_status.return_value = (
                {"messenger": [{"id": entry_id}]},
                ["relationship"],
            )
            assert (
                await client.post("/api/calendar/workspace/respond", json=arguments)
            ).status_code == 503
            assert decision.await_count == 1
            db.fan_out_with_status.return_value = (
                {"messenger": [{"id": entry_id}], "relationship": []},
                [],
            )
            own_pool_lookup = db.pool
            wrong_pool = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
            db.pool = lambda owner: wrong_pool
            before_decisions = decision.await_count
            assert (
                await client.post("/api/calendar/workspace/respond", json=arguments)
            ).status_code == 409
            assert decision.await_count == before_decisions
            db.pool = own_pool_lookup
            command["owner"] = "relationship"
            assert (
                await client.post("/api/calendar/workspace/respond", json=arguments)
            ).status_code == 409
            command["owner"] = "messenger"
            assert (
                await client.post("/api/calendar/workspace/respond", json=arguments)
            ).status_code == 200
            concurrent_inverse_claim = True
            late_claim = await client.post("/api/calendar/workspace/respond", json=arguments)
            assert late_claim.status_code == 200
            assert late_claim.json()["data"]["undo_available"] is False
            assert payload.get("inverse_claim")

    finally:
        app.dependency_overrides.pop(workspace._get_db_manager, None)
        app.dependency_overrides.pop(get_mcp_manager, None)
