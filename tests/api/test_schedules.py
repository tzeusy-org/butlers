"""Tests for schedules API endpoints.

Condensed: 22 → ~8 tests [bu-gg4y1].
Keeps: list contract (paginated structure), GET/POST/PUT/DELETE/PATCH endpoint
status codes, unreachable butler 503, naive-datetime rejection.
Drops: field-by-field assertions, mock-wiring call_args checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.routers.schedules import _get_db_manager

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
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
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
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
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
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
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


async def test_list_503_when_db_unavailable(app):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("no pool")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/nonexistent/schedules")
    assert resp.status_code == 503


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
    _wire_mcp(app, result=_mock_mcp_result({"id": str(sid), "ok": True}))
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
            resp = await client.patch(path, json=body or {})
    assert resp.status_code == exp_status


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
        ("PATCH", "/api/butlers/atlas/schedules/{sid}/toggle", None),
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
            resp = await client.patch(path)
    assert resp.status_code == 503


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
