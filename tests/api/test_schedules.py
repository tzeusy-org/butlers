"""Tests for schedules API endpoints.

Condensed: 22 → ~8 tests [bu-gg4y1].
Keeps: list contract (paginated structure), GET/POST/PUT/DELETE/PATCH endpoint
status codes, unreachable butler 503, naive-datetime rejection.
Drops: field-by-field assertions, mock-wiring call_args checks.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_db, get_mcp_manager
from butlers.api.routers.schedules import _call_mcp_tool, _coerce_complexity

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_row(**kwargs) -> dict:
    base = {
        "id": uuid4(),
        "name": "daily_digest",
        "cron": "0 9 * * *",
        "dispatch_mode": "prompt",
        "prompt": "Send a daily digest",
        "job_name": None,
        "job_args": None,
        "timezone": None,
        "start_at": None,
        "end_at": None,
        "until_at": None,
        "display_title": None,
        "calendar_event_id": None,
        "source": "db",
        "enabled": True,
        "next_run_at": None,
        "last_run_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    return {**base, **kwargs}


def _mock_mcp_result(payload: dict) -> list:
    import json

    block = MagicMock()
    block.text = json.dumps(payload)
    return [block]


def _wire_db(app, *, fetch_rows=None):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas", "switchboard"]
    app.dependency_overrides[get_db] = lambda: mock_db
    return app, mock_db, mock_pool


def _wire_mcp(app, *, result=None):
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=result or _mock_mcp_result({"ok": True}))
    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(return_value=mock_client)
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_client


def _wire_unreachable(app):
    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(
        side_effect=ButlerUnreachableError("atlas", cause=ConnectionRefusedError())
    )
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app


async def test_list_returns_paginated_structure(app):
    _wire_db(app, fetch_rows=[_make_row(name="task-a"), _make_row(name="task-b")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/schedules")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert len(body["data"]) == 2
    assert body["data"][0]["name"] == "task-a"


async def test_list_surfaces_deadline_supersession(app):
    """A superseded deadline carries typed superseded_by/superseded_at; others carry null."""
    newer = uuid4()
    at = "2026-11-02T10:15:00+00:00"
    _wire_db(
        app,
        fetch_rows=[
            _make_row(
                name="paperwork:due:old",
                dispatch_mode="job",
                enabled=False,
                job_args={"superseded_by": str(newer), "superseded_at": at},
            ),
            _make_row(name="owner-off", enabled=False),
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/schedules")
    old, off = resp.json()["data"]
    assert (old["superseded_by"], datetime.fromisoformat(old["superseded_at"])) == (
        str(newer),
        datetime.fromisoformat(at),
    )
    assert (off["superseded_by"], off["superseded_at"]) == (None, None)


@pytest.mark.parametrize(
    "stored,expected",
    [
        (None, "workhorse"),  # missing/null column defaults to workhorse, not medium
        ("", "workhorse"),  # empty string treated as missing
        # Every retired pre-core_092 tier normalizes to its canonical successor
        # (bu-etpc3: previously only "medium" was remapped; the other five
        # surfaced unnormalized). Contract shared with model_routing._DEPRECATED_TIER_MAP.
        ("trivial", "cheap"),
        ("medium", "workhorse"),  # legacy invalid tier coerced on read (bu-fev4q/bu-65nop)
        ("high", "reasoning"),
        ("extra_high", "reasoning"),
        ("discretion", "specialty"),
        ("self_healing", "specialty"),
        ("reasoning", "reasoning"),  # valid tiers pass through unchanged
        ("legacy", "legacy"),
        # Unknown junk degrades to workhorse (read path never surfaces an
        # invalid tier), matching coerce_complexity_tier(strict=False).
        ("bogus-tier", "workhorse"),
    ],
)
def test_coerce_complexity(stored, expected):
    assert _coerce_complexity(stored) == expected


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("medium", "workhorse"),
        ("high", "reasoning"),
        ("self_healing", "specialty"),
        ("bogus-tier", "workhorse"),
    ],
)
async def test_list_coerces_legacy_complexity(app, stored, expected):
    _wire_db(app, fetch_rows=[_make_row(name="legacy", complexity=stored)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/schedules")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["complexity"] == expected


async def test_list_503_when_db_unavailable(app):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("no pool")
    app.dependency_overrides[get_db] = lambda: mock_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/nonexistent/schedules")
    assert resp.status_code == 503


def _deadline_apps(mock_db):
    """An owner app and a non-owner app over the same DB manager."""
    from butlers.api.app import create_app
    from tests.api.auth_helpers import _DomainOwnerState, create_authenticated_domain_app

    owner = create_authenticated_domain_app()
    owner.dependency_overrides[get_db] = lambda: mock_db
    no_owner = create_app(api_key="synthetic-owner-key")
    no_owner.state.owner_auth_service = _DomainOwnerState("synthetic-owner-key")
    no_owner.dependency_overrides[get_db] = lambda: mock_db
    return owner, no_owner


async def _get(app, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        return await client.get(path)


async def test_deadline_reader_refuses_non_owner_and_unknown_butler_before_pool():
    """Project links resolve deadlines here; refusals never reach a butler pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    owner, no_owner = _deadline_apps(mock_db)
    deadline_id = uuid4()

    assert (await _get(no_owner, f"/api/butlers/atlas/deadlines/{deadline_id}")).status_code == 401
    unknown = await _get(owner, f"/api/butlers/nobody/deadlines/{deadline_id}")
    assert unknown.status_code == 404
    mock_db.pool.assert_not_called()


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_deadline_reader_returns_deadlines_only_without_prompt(db_pool):
    """Real migrated scheduled_tasks: a deadline reads back, a cron task id is not found."""
    from butlers.core.temporal.deadlines_db import deadline_create

    due = datetime.now(UTC).date() + timedelta(days=10)
    deadline_id = await deadline_create(
        db_pool,
        name="car-insurance",
        prompt="Remind the owner to renew.",
        target_date=due,
        lead_time_days=14,
        alert_thresholds=[{"days_before": 3, "severity": "warning"}],
    )
    cron_id = await db_pool.fetchval(
        "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, source, task_type) "
        "VALUES ('daily-digest', '0 9 * * *', 'digest', 'prompt', 'db', 'cron') RETURNING id"
    )
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.pool.return_value = db_pool
    owner, _ = _deadline_apps(mock_db)

    found = await _get(owner, f"/api/butlers/atlas/deadlines/{deadline_id}")
    assert found.status_code == 200
    assert found.json()["data"] == {
        "id": str(deadline_id),
        "butler": "atlas",
        "name": "car-insurance",
        "display_title": None,
        "target_date": due.isoformat(),
        "deadline_status": "pending",
        "enabled": True,
    }
    not_a_deadline = await _get(owner, f"/api/butlers/atlas/deadlines/{cron_id}")
    assert not_a_deadline.status_code == 404
    missing = await _get(owner, f"/api/butlers/atlas/deadlines/{uuid4()}")
    assert missing.status_code == 404


@pytest.mark.parametrize(
    "method,path_tpl,body,exp_status",
    [
        (
            "POST",
            "/api/butlers/atlas/schedules",
            {"name": "t", "cron": "*/5 * * * *", "prompt": "x"},
            201,
        ),
        ("PUT", "/api/butlers/atlas/schedules/{sid}", {"cron": "0 12 * * *"}, 200),
        ("DELETE", "/api/butlers/atlas/schedules/{sid}", None, 200),
        ("PATCH", "/api/butlers/atlas/schedules/{sid}/toggle", None, 200),
    ],
)
async def test_crud_endpoint_status_codes(app, method, path_tpl, body, exp_status):
    sid = uuid4()
    path = path_tpl.replace("{sid}", str(sid))
    result = {"id": str(sid), "ok": True}
    if method == "PATCH":
        result = {
            "id": str(sid),
            "name": "daily_digest",
            "source": "db",
            "status": "updated",
            "outcome": "applied",
            "requested_enabled": False,
            "observed_enabled": False,
            "changed": True,
            "next_run_at": None,
            "audit": {
                "action": "schedule.toggle",
                "result": "success",
                "target": f"schedule:{sid}",
            },
        }
    for wire_result in (
        _mock_mcp_result(result),
        CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            structured_content=None,
            meta=None,
        ),
    ):
        _wire_mcp(app, result=wire_result)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            if method == "POST":
                resp = await client.post(path, json=body)
            elif method == "PUT":
                resp = await client.put(path, json=body)
            elif method == "DELETE":
                resp = await client.delete(path)
            else:
                resp = await client.patch(path, json={"enabled": False})
        assert resp.status_code == exp_status
        assert resp.json()["data"] == result
        if method == "POST":
            # Exercise the private metadata path with the same real SDK result
            # shape; the real cookie -> MCP -> SQL positive remains PG-owned.
            mgr = app.dependency_overrides[get_mcp_manager]()
            assert (
                await _call_mcp_tool(
                    mgr, "atlas", "schedule_create", body, meta={"private": "synthetic"}
                )
                == result
            )


async def test_toggle_returns_server_observed_receipt_and_requested_state(app):
    sid = uuid4()
    result = {
        "id": str(sid),
        "name": "daily_digest",
        "source": "db",
        "status": "unchanged",
        "outcome": "already_requested",
        "requested_enabled": False,
        "observed_enabled": False,
        "changed": False,
        "next_run_at": None,
        "audit": {
            "action": "schedule.toggle",
            "result": "success",
            "target": f"schedule:{sid}",
        },
    }
    app, client = _wire_mcp(app, result=_mock_mcp_result(result))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.patch(
            f"/api/butlers/atlas/schedules/{sid}/toggle",
            json={"enabled": False},
        )

    assert resp.status_code == 200
    assert resp.json()["data"]["observed_enabled"] is False
    assert resp.json()["data"]["changed"] is False
    assert client.call_tool.await_args.args == (
        "schedule_toggle",
        {"id": str(sid), "enabled": False},
    )


@pytest.mark.parametrize("body", [None, {}, {"enabled": None}, {"enabled": "false"}])
async def test_toggle_requires_explicit_boolean_before_mcp(app, body):
    sid = uuid4()
    app, mcp_client = _wire_mcp(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.patch(
            f"/api/butlers/atlas/schedules/{sid}/toggle",
            json=body,
        )

    assert response.status_code == 422
    mcp_client.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    ("code", "status"),
    [("SCHEDULE_NOT_FOUND", 404), ("SCHEDULE_TOML_MANAGED", 409), ("SCHEDULE_MANAGED", 409)],
)
async def test_toggle_returns_typed_refusal(app, code, status):
    sid = uuid4()
    app, _client = _wire_mcp(
        app,
        result=_mock_mcp_result(
            {
                "id": str(sid),
                "status": "error",
                "code": code,
                "message": "bounded refusal",
                "error": "bounded refusal",
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.patch(
            f"/api/butlers/atlas/schedules/{sid}/toggle",
            json={"enabled": True},
        )

    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "method,path_tpl,body",
    [
        (
            "POST",
            "/api/butlers/atlas/schedules",
            {"name": "t", "cron": "*/5 * * * *", "prompt": "x"},
        ),
        ("PUT", "/api/butlers/atlas/schedules/{sid}", {"cron": "0 12 * * *"}),
        ("DELETE", "/api/butlers/atlas/schedules/{sid}", None),
        ("PATCH", "/api/butlers/atlas/schedules/{sid}/toggle", {"enabled": False}),
    ],
)
async def test_crud_503_when_butler_unreachable(app, method, path_tpl, body):
    sid = uuid4()
    path = path_tpl.replace("{sid}", str(sid))
    _wire_unreachable(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if method == "POST":
            resp = await client.post(path, json=body)
        elif method == "PUT":
            resp = await client.put(path, json=body)
        elif method == "DELETE":
            resp = await client.delete(path)
        else:
            resp = await client.patch(path, json=body)
    assert resp.status_code == 503

    private_text = "synthetic-private-result"
    success_looking = {"id": str(sid), "status": "created", "private": private_text}
    for wire_result in (
        CallToolResult(
            content=[TextContent(type="text", text=json.dumps(success_looking))],
            structured_content=success_looking,
            meta=None,
            is_error=True,
        ),
        CallToolResult(content=[], structured_content=success_looking, meta=None),
        CallToolResult(
            content=[TextContent(type="text", text=private_text)],
            structured_content=None,
            meta=None,
        ),
        CallToolResult(
            content=[TextContent(type="text", text='["synthetic-private-result"]')],
            structured_content=None,
            meta=None,
        ),
    ):
        _, mock_client = _wire_mcp(app)
        mock_client.call_tool.return_value = wire_result
        mgr = app.dependency_overrides[get_mcp_manager]()
        with pytest.raises(HTTPException) as refused:
            await _call_mcp_tool(
                mgr, "atlas", "schedule_create", body or {}, meta={"private": private_text}
            )
        assert refused.value.status_code == 503
        assert refused.value.detail == "Owner creation unavailable; retry the same operation"
        assert private_text not in refused.value.detail
        if wire_result.is_error:
            # Explicit SDK error must also refuse ordinary CRUD, even when
            # its content and structured payload look like successful create.
            with pytest.raises(HTTPException) as ordinary:
                await _call_mcp_tool(mgr, "atlas", "schedule_create", body or {})
            assert ordinary.value.status_code == 503
            assert private_text not in ordinary.value.detail
    mock_client.call_tool.side_effect = RuntimeError(private_text)
    with pytest.raises(HTTPException) as raised:
        await _call_mcp_tool(
            mgr, "atlas", "schedule_create", body or {}, meta={"private": private_text}
        )
    assert raised.value.status_code == 503
    assert raised.value.detail == "Owner creation unavailable; retry the same operation"


@pytest.mark.parametrize(
    "method,path_tpl",
    [
        ("POST", "/api/butlers/atlas/schedules"),
        ("PUT", "/api/butlers/atlas/schedules/{sid}"),
    ],
)
async def test_naive_datetime_rejected(app, method, path_tpl):
    sid = uuid4()
    path = path_tpl.replace("{sid}", str(sid))
    naive_body = {
        "name": "x",
        "cron": "0 9 * * *",
        "prompt": "p",
        "start_at": "2026-03-01T14:00:00",
    }
    _wire_mcp(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if method == "POST":
            resp = await client.post(path, json=naive_body)
        else:
            resp = await client.put(path, json={"start_at": "2026-03-01T14:00:00"})
    assert resp.status_code == 422
