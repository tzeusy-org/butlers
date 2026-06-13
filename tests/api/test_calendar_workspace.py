"""Tests for calendar workspace API endpoints.

Condensed to 3 tests (bu-2yw2d) from 10 (bu-egmz6).

Keeps: required-params validation (422), workspace read structure, sync trigger count.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.calendar_workspace import _get_db_manager

pytestmark = pytest.mark.unit


def _mock_mcp_result(payload: object) -> object:
    block = MagicMock()
    block.text = json.dumps(payload)
    result = MagicMock()
    result.content = [block]
    return result


def _workspace_event_row(
    *,
    lane: str,
    source_key: str,
    source_kind: str,
    butler_name: str | None,
    calendar_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    end = datetime(2026, 2, 22, 15, 0, tzinfo=UTC)
    synced_at = datetime.now(tz=UTC)
    return {
        "instance_id": uuid4(),
        "source_id": uuid4(),
        "source_key": source_key,
        "source_kind": source_kind,
        "lane": lane,
        "provider": "google" if lane == "user" else "internal",
        "calendar_id": calendar_id,
        "butler_name": butler_name,
        "display_name": source_key,
        "writable": True,
        "source_metadata": {"projection": "test"},
        "event_id": uuid4(),
        "origin_ref": str(uuid4()),
        "title": "Calendar item",
        "description": "desc",
        "location": "loc",
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": metadata or {},
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": end,
        "instance_status": "confirmed",
        "instance_metadata": {},
        "cursor_name": "provider_sync" if lane == "user" else "projection",
        "last_synced_at": synced_at,
        "last_success_at": synced_at,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


def _workspace_source_row(
    *,
    source_key: str,
    source_kind: str,
    lane: str,
    butler_name: str | None,
    provider: str,
    calendar_id: str | None = None,
    writable: bool = False,
    metadata: dict | None = None,
) -> dict:
    synced_at = datetime.now(tz=UTC)
    return {
        "source_id": uuid4(),
        "source_key": source_key,
        "source_kind": source_kind,
        "lane": lane,
        "provider": provider,
        "calendar_id": calendar_id,
        "butler_name": butler_name,
        "display_name": source_key,
        "writable": writable,
        "source_metadata": metadata if metadata is not None else {"projection": "test"},
        "cursor_name": "provider_sync" if lane == "user" else "projection",
        "last_synced_at": synced_at,
        "last_success_at": synced_at,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


def _build_app(
    app,
    *,
    workspace_rows: dict[str, list[dict]] | None = None,
    source_rows: dict[str, list[dict]] | None = None,
    mcp_clients: dict[str, AsyncMock] | None = None,
    calendar_butlers: list[str] | None = None,
) -> tuple:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=calendar_butlers)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            rows_to_scan = workspace_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            return rows_to_scan
        if "FROM calendar_sources AS s" in query:
            rows_to_scan = source_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            return rows_to_scan
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)

    mock_mgr = AsyncMock(spec=MCPClientManager)
    mcp_map = mcp_clients or {}

    async def _get_client(name: str):
        return mcp_map[name]

    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr


# ---------------------------------------------------------------------------
# 422 when required params missing
# ---------------------------------------------------------------------------


async def test_workspace_requires_view_start_and_end(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Workspace read — returns entries + source_freshness + lanes
# ---------------------------------------------------------------------------


async def test_workspace_returns_entries_and_source_freshness(app):
    source_key = "provider:google:primary"
    user_row = _workspace_event_row(
        lane="user",
        source_key=source_key,
        source_kind="provider_event",
        butler_name=None,
        calendar_id="primary",
        metadata={"source_type": "provider_event", "provider_event_id": "evt-1"},
    )
    app, _, _ = _build_app(app, workspace_rows={"general": [user_row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace",
            params={
                "view": "user",
                "start": "2026-02-22T00:00:00Z",
                "end": "2026-02-23T00:00:00Z",
            },
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body["entries"]) == 1
    assert body["entries"][0]["view"] == "user"
    assert len(body["source_freshness"]) == 1


# ---------------------------------------------------------------------------
# Sync all — triggers each target butler
# ---------------------------------------------------------------------------


async def test_sync_all_triggers_each_target_butler(app):
    source_rows = {
        "general": [
            _workspace_source_row(
                source_key="provider:google:primary",
                source_kind="provider_event",
                lane="user",
                butler_name=None,
                provider="google",
                calendar_id="primary",
                writable=True,
            )
        ],
        "relationship": [
            _workspace_source_row(
                source_key="provider:google:butlers",
                source_kind="provider_event",
                lane="user",
                butler_name=None,
                provider="google",
                calendar_id="butlers-cal",
                writable=True,
            )
        ],
    }
    general_client = AsyncMock()
    relationship_client = AsyncMock()
    general_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "sync_triggered"})
    )
    relationship_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "sync_triggered"})
    )
    app, _, _ = _build_app(
        app,
        source_rows=source_rows,
        mcp_clients={"general": general_client, "relationship": relationship_client},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/calendar/workspace/sync", json={"all": True})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["scope"] == "all"
    assert data["triggered_count"] == 2


# ---------------------------------------------------------------------------
# Meta — primary resolver + submittable writable calendars (bu-608g8)
# ---------------------------------------------------------------------------


async def test_meta_resolves_primary_from_account_email(app):
    """The primary resolver must identify the primary calendar from DB sources.

    Google's primary calendar has an ``id`` equal to the account email, which
    discovery records in ``metadata.account_email``. The resolver returns that
    calendar_id (not null) without needing a live MCP call.
    """
    account_email = "owner@example.com"
    source_rows = {
        "general": [
            _workspace_source_row(
                source_key="provider:google:owner@example.com",
                source_kind="provider_event",
                lane="user",
                butler_name=None,
                provider="google",
                calendar_id=account_email,
                writable=True,
                metadata={"account_email": account_email},
            ),
            _workspace_source_row(
                source_key="provider:google:work",
                source_kind="provider_event",
                lane="user",
                butler_name=None,
                provider="google",
                calendar_id="work@example.com",
                writable=True,
                metadata={"account_email": account_email},
            ),
        ]
    }
    app, _, _ = _build_app(app, source_rows=source_rows, calendar_butlers=["general"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/meta")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["primary_calendar_id"] == account_email


async def test_meta_writable_calendars_carry_owning_butler(app):
    """User-lane writable calendars must resolve to an owning butler.

    ``s.butler_name`` is NULL for user-lane provider calendars; the owning
    butler is the schema (db_butler) the source lives in. Writable calendars
    must surface that butler so they are submittable, and only submittable
    calendars are returned.
    """
    source_rows = {
        "general": [
            _workspace_source_row(
                source_key="provider:google:primary",
                source_kind="provider_event",
                lane="user",
                butler_name=None,
                provider="google",
                calendar_id="owner@example.com",
                writable=True,
                metadata={"account_email": "owner@example.com"},
            )
        ]
    }
    app, _, _ = _build_app(app, source_rows=source_rows, calendar_butlers=["general"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/meta")

    assert resp.status_code == 200
    data = resp.json()["data"]
    writable = data["writable_calendars"]
    assert len(writable) == 1
    assert writable[0]["butler_name"] == "general"
    assert writable[0]["calendar_id"] == "owner@example.com"
