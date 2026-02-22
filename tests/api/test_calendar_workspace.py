"""Tests for calendar workspace API endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
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
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    source_writable: bool = True,
    metadata: dict | None = None,
) -> dict:
    start = starts_at or datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    end = ends_at or datetime(2026, 2, 22, 15, 0, tzinfo=UTC)
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
        "writable": source_writable,
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
        "source_metadata": {"projection": "test"},
        "cursor_name": "provider_sync" if lane == "user" else "projection",
        "last_synced_at": synced_at,
        "last_success_at": synced_at,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


def _build_app(
    *,
    workspace_rows: dict[str, list[dict]] | None = None,
    source_rows: dict[str, list[dict]] | None = None,
    mcp_clients: dict[str, AsyncMock] | None = None,
) -> tuple:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            result: dict[str, list[dict]] = {}
            want_butlers: set[str] | None = None
            want_sources: set[str] | None = None
            if "COALESCE(s.butler_name" in query and len(args) >= 4 and isinstance(args[3], list):
                want_butlers = set(args[3])
            if "s.source_key = ANY" in query and args and isinstance(args[-1], list):
                want_sources = set(args[-1])

            for butler, rows in (workspace_rows or {}).items():
                filtered = []
                for row in rows:
                    row_butler = row.get("butler_name")
                    row_source = row.get("source_key")
                    if want_butlers is not None and row_butler not in want_butlers:
                        continue
                    if want_sources is not None and row_source not in want_sources:
                        continue
                    filtered.append(row)
                result[butler] = filtered
            return result
        if "FROM calendar_sources AS s" in query:
            return source_rows or {}
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)

    mock_mgr = AsyncMock(spec=MCPClientManager)
    mcp_map = mcp_clients or {}

    async def _get_client(name: str):
        return mcp_map[name]

    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr


class TestWorkspaceRead:
    async def test_workspace_requires_view_start_and_end(self):
        app, _, _ = _build_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/calendar/workspace")

        assert resp.status_code == 422

    async def test_workspace_returns_entries_freshness_and_lanes(self):
        source_key = "provider:google:primary"
        user_row = _workspace_event_row(
            lane="user",
            source_key=source_key,
            source_kind="provider_event",
            butler_name=None,
            calendar_id="primary",
            metadata={"source_type": "provider_event", "provider_event_id": "evt-1"},
        )
        app, _, _ = _build_app(workspace_rows={"general": [user_row]})

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
        entry = body["entries"][0]
        assert entry["view"] == "user"
        assert entry["source_type"] == "provider_event"
        assert entry["calendar_id"] == "primary"
        assert entry["editable"] is True
        assert len(body["source_freshness"]) == 1
        assert body["source_freshness"][0]["source_key"] == source_key
        assert body["source_freshness"][0]["sync_state"] == "fresh"
        assert body["lanes"] == []

    async def test_workspace_filters_by_butler_and_source(self):
        general_row = _workspace_event_row(
            lane="butler",
            source_key="internal_scheduler:general",
            source_kind="internal_scheduler",
            butler_name="general",
            metadata={"source_type": "internal_scheduler", "cron": "0 9 * * *"},
        )
        relationship_row = _workspace_event_row(
            lane="butler",
            source_key="internal_scheduler:relationship",
            source_kind="internal_scheduler",
            butler_name="relationship",
            metadata={"source_type": "internal_scheduler", "cron": "0 10 * * *"},
        )
        app, _, _ = _build_app(
            workspace_rows={"general": [general_row], "relationship": [relationship_row]}
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/calendar/workspace",
                params={
                    "view": "butler",
                    "start": "2026-02-22T00:00:00Z",
                    "end": "2026-02-23T00:00:00Z",
                    "butlers": ["general"],
                    "sources": ["internal_scheduler:general"],
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert len(body["entries"]) == 1
        assert body["entries"][0]["butler_name"] == "general"
        assert body["entries"][0]["source_type"] == "scheduled_task"


class TestWorkspaceMeta:
    async def test_workspace_meta_returns_sources_capabilities_and_lanes(self):
        rows = {
            "general": [
                _workspace_source_row(
                    source_key="provider:google:primary",
                    source_kind="provider_event",
                    lane="user",
                    butler_name=None,
                    provider="google",
                    calendar_id="primary",
                    writable=True,
                ),
                _workspace_source_row(
                    source_key="internal_scheduler:general",
                    source_kind="internal_scheduler",
                    lane="butler",
                    butler_name="general",
                    provider="internal",
                    writable=True,
                ),
            ]
        }
        app, _, _ = _build_app(source_rows=rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/calendar/workspace/meta")

        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["capabilities"]["sync"]["global"] is True
        assert payload["capabilities"]["sync"]["by_source"] is True
        assert len(payload["connected_sources"]) == 2
        assert len(payload["writable_calendars"]) == 1
        assert payload["writable_calendars"][0]["calendar_id"] == "primary"
        assert len(payload["lane_definitions"]) == 1
        assert payload["lane_definitions"][0]["butler_name"] == "general"
        assert payload["default_timezone"] == "UTC"


class TestWorkspaceSync:
    async def test_sync_all_triggers_each_target_butler(self):
        general_client = AsyncMock()
        relationship_client = AsyncMock()
        general_client.call_tool = AsyncMock(
            return_value=_mock_mcp_result({"status": "sync_triggered"})
        )
        relationship_client.call_tool = AsyncMock(
            return_value=_mock_mcp_result({"status": "sync_triggered"})
        )
        app, _, _ = _build_app(
            mcp_clients={"general": general_client, "relationship": relationship_client}
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/calendar/workspace/sync", json={"all": True})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scope"] == "all"
        assert data["triggered_count"] == 2
        general_client.call_tool.assert_awaited_once_with("calendar_force_sync", {})
        relationship_client.call_tool.assert_awaited_once_with("calendar_force_sync", {})

    async def test_sync_all_preserves_detail_for_string_payloads(self):
        general_client = AsyncMock()
        relationship_client = AsyncMock()
        general_client.call_tool = AsyncMock(return_value=_mock_mcp_result("triggered"))
        relationship_client.call_tool = AsyncMock(return_value=_mock_mcp_result("triggered"))
        app, _, _ = _build_app(
            mcp_clients={"general": general_client, "relationship": relationship_client}
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/calendar/workspace/sync", json={"all": True})

        assert resp.status_code == 200
        targets = resp.json()["data"]["targets"]
        assert len(targets) == 2
        assert {target["detail"] for target in targets} == {"triggered"}

    async def test_sync_source_key_targets_specific_source(self):
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
            "relationship": [],
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
            source_rows=source_rows,
            mcp_clients={"general": general_client, "relationship": relationship_client},
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/sync",
                json={"source_key": "provider:google:primary"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scope"] == "source"
        assert data["triggered_count"] == 1
        general_client.call_tool.assert_awaited_once_with(
            "calendar_force_sync",
            {"calendar_id": "primary"},
        )
        relationship_client.call_tool.assert_not_awaited()

    async def test_sync_source_preserves_detail_for_non_dict_payloads(self):
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
            ]
        }
        general_client = AsyncMock()
        relationship_client = AsyncMock()
        general_client.call_tool = AsyncMock(return_value=_mock_mcp_result(["ok", 1]))
        relationship_client.call_tool = AsyncMock(
            return_value=_mock_mcp_result({"status": "sync_triggered"})
        )
        app, _, _ = _build_app(
            source_rows=source_rows,
            mcp_clients={"general": general_client, "relationship": relationship_client},
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/sync",
                json={"source_key": "provider:google:primary"},
            )

        assert resp.status_code == 200
        target = resp.json()["data"]["targets"][0]
        assert target["detail"] == "[\"ok\", 1]"
