"""Tests for calendar workspace API endpoints."""

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
    app,
    *,
    workspace_rows: dict[str, list[dict]] | None = None,
    source_rows: dict[str, list[dict]] | None = None,
    mcp_clients: dict[str, AsyncMock] | None = None,
    calendar_butlers: list[str] | None = None,
) -> tuple:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    # When calendar_butlers is provided, simulate module-aware filtering.
    # When None, simulate a deployment with no module metadata (legacy).
    mock_db.butlers_with_module = MagicMock(return_value=calendar_butlers)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            result: dict[str, list[dict]] = {}
            want_butlers: set[str] | None = None
            want_sources: set[str] | None = None
            if "COALESCE(s.butler_name" in query and len(args) >= 4 and isinstance(args[3], list):
                want_butlers = set(args[3])
            if "s.source_key = ANY" in query and args and isinstance(args[-1], list):
                want_sources = set(args[-1])

            rows_to_scan = workspace_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}

            for butler, rows in rows_to_scan.items():
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


class TestWorkspaceRead:
    async def test_workspace_requires_view_start_and_end(self, app):
        app, _, _ = _build_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/calendar/workspace")

        assert resp.status_code == 422

    async def test_workspace_returns_entries_freshness_and_lanes(self, app):
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
        entry = body["entries"][0]
        assert entry["view"] == "user"
        assert entry["source_type"] == "provider_event"
        assert entry["calendar_id"] == "primary"
        assert entry["editable"] is True
        assert len(body["source_freshness"]) == 1
        assert body["source_freshness"][0]["source_key"] == source_key
        assert body["source_freshness"][0]["sync_state"] == "fresh"
        assert body["lanes"] == []

    async def test_workspace_filters_by_butler_and_source(self, app):
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
            app, workspace_rows={"general": [general_row], "relationship": [relationship_row]}
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
    async def test_workspace_meta_returns_sources_capabilities_and_lanes(self, app):
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
        app, _, _ = _build_app(app, source_rows=rows)

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
        assert payload["default_timezone"] == "Asia/Singapore"


class TestWorkspaceSync:
    async def test_sync_all_triggers_each_target_butler(self, app):
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
                ),
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
                ),
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
        general_client.call_tool.assert_awaited_once_with(
            "calendar_force_sync", {"calendar_id": "primary"}
        )
        relationship_client.call_tool.assert_awaited_once_with(
            "calendar_force_sync", {"calendar_id": "butlers-cal"}
        )

    async def test_sync_all_preserves_detail_for_string_payloads(self, app):
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
                ),
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
                ),
            ],
        }
        general_client = AsyncMock()
        relationship_client = AsyncMock()
        general_client.call_tool = AsyncMock(return_value=_mock_mcp_result("triggered"))
        relationship_client.call_tool = AsyncMock(return_value=_mock_mcp_result("triggered"))
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
        targets = resp.json()["data"]["targets"]
        assert len(targets) == 2
        assert {target["detail"] for target in targets} == {"triggered"}

    async def test_sync_source_key_targets_specific_source(self, app):
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
            app,
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

    async def test_sync_source_preserves_detail_for_non_dict_payloads(self, app):
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
            app,
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
        assert target["detail"] == '["ok", 1]'


def _mcp_result(payload: dict | str) -> list:
    block = MagicMock()
    block.text = json.dumps(payload)
    return [block]


def _app_with_mcp(app, call_side_effect):
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(side_effect=call_side_effect)

    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    mock_pool = AsyncMock()
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["general", "relationship"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_client


class TestCalendarWorkspaceUserEvents:
    async def test_user_event_create_routes_to_calendar_create_event(self, app):
        async def _call(tool_name: str, arguments: dict):
            if tool_name == "calendar_create_event":
                return _mcp_result({"status": "created", "event": {"event_id": "evt-1"}})
            if tool_name == "calendar_sync_status":
                return _mcp_result(
                    {
                        "status": "ok",
                        "projection_freshness": {
                            "last_refreshed_at": "2026-03-01T10:00:00+00:00",
                            "staleness_ms": 42,
                            "sources": [],
                        },
                    }
                )
            raise AssertionError(f"Unexpected tool call: {tool_name}")

        app, mock_client = _app_with_mcp(app, _call)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/user-events",
                json={
                    "butler_name": "general",
                    "action": "create",
                    "request_id": "req-123",
                    "payload": {
                        "title": "Plan day",
                        "start_at": "2026-03-01T10:00:00+00:00",
                        "end_at": "2026-03-01T11:00:00+00:00",
                    },
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["tool_name"] == "calendar_create_event"
        assert body["request_id"] == "req-123"
        assert body["result"]["status"] == "created"
        assert body["projection_version"] == "2026-03-01T10:00:00+00:00"
        assert body["staleness_ms"] == 42
        assert mock_client.call_tool.await_count == 2
        first_call = mock_client.call_tool.await_args_list[0]
        assert first_call.args[0] == "calendar_create_event"
        assert first_call.args[1]["request_id"] == "req-123"

    async def test_user_event_update_forwards_recurrence_scope_payload(self, app):
        async def _call(tool_name: str, arguments: dict):
            if tool_name == "calendar_update_event":
                return _mcp_result(
                    {
                        "status": "updated",
                        "projection_freshness": {
                            "last_refreshed_at": "2026-03-01T11:00:00+00:00",
                            "staleness_ms": 7,
                            "sources": [],
                        },
                    }
                )
            raise AssertionError(f"Unexpected tool call: {tool_name}")

        app, mock_client = _app_with_mcp(app, _call)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/user-events",
                json={
                    "butler_name": "general",
                    "action": "update",
                    "request_id": "req-update-1",
                    "payload": {
                        "event_id": "evt-42",
                        "title": "Updated title",
                        "recurrence_scope": "series",
                    },
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["tool_name"] == "calendar_update_event"
        assert body["projection_version"] == "2026-03-01T11:00:00+00:00"
        assert body["staleness_ms"] == 7
        assert mock_client.call_tool.await_count == 1
        call = mock_client.call_tool.await_args_list[0]
        assert call.args[0] == "calendar_update_event"
        assert call.args[1]["request_id"] == "req-update-1"
        assert call.args[1]["event_id"] == "evt-42"
        assert call.args[1]["recurrence_scope"] == "series"


class TestCalendarWorkspaceButlerEvents:
    async def test_butler_event_create_sets_butler_name_and_request_id(self, app):
        async def _call(tool_name: str, arguments: dict):
            assert tool_name == "calendar_create_butler_event"
            return _mcp_result(
                {
                    "status": "created",
                    "event_id": "9adf0d14-2adf-4d67-8c43-5f62ffe5d7be",
                    "projection_freshness": {
                        "last_refreshed_at": "2026-03-01T12:00:00+00:00",
                        "staleness_ms": 0,
                        "sources": [],
                    },
                }
            )

        app, mock_client = _app_with_mcp(app, _call)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/butler-events",
                json={
                    "butler_name": "general",
                    "action": "create",
                    "request_id": "req-butler-1",
                    "payload": {
                        "title": "Daily prep",
                        "start_at": "2026-03-02T09:00:00+00:00",
                        "end_at": "2026-03-02T09:15:00+00:00",
                        "cron": "0 9 * * *",
                    },
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["tool_name"] == "calendar_create_butler_event"
        assert body["result"]["status"] == "created"
        assert body["projection_version"] == "2026-03-01T12:00:00+00:00"
        assert mock_client.call_tool.await_count == 1
        first_call = mock_client.call_tool.await_args_list[0]
        assert first_call.args[1]["butler_name"] == "general"
        assert first_call.args[1]["request_id"] == "req-butler-1"

    async def test_butler_event_update_forwards_event_target_payload(self, app):
        async def _call(tool_name: str, arguments: dict):
            assert tool_name == "calendar_update_butler_event"
            return _mcp_result(
                {
                    "status": "updated",
                    "projection_freshness": {
                        "last_refreshed_at": "2026-03-02T09:00:00+00:00",
                        "staleness_ms": 3,
                        "sources": [],
                    },
                }
            )

        app, mock_client = _app_with_mcp(app, _call)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/butler-events",
                json={
                    "butler_name": "general",
                    "action": "update",
                    "request_id": "req-butler-update-1",
                    "payload": {
                        "event_id": "5f3790f0-87b0-4a19-9df9-33f2eb660250",
                        "source_hint": "scheduled_task",
                        "title": "Updated prep",
                    },
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["tool_name"] == "calendar_update_butler_event"
        assert body["projection_version"] == "2026-03-02T09:00:00+00:00"
        assert body["staleness_ms"] == 3
        assert mock_client.call_tool.await_count == 1
        call = mock_client.call_tool.await_args_list[0]
        assert call.args[1]["request_id"] == "req-butler-update-1"
        assert call.args[1]["event_id"] == "5f3790f0-87b0-4a19-9df9-33f2eb660250"
        assert call.args[1]["source_hint"] == "scheduled_task"

    async def test_butler_event_toggle_fetches_sync_status_when_projection_missing(self, app):
        async def _call(tool_name: str, arguments: dict):
            if tool_name == "calendar_toggle_butler_event":
                return _mcp_result({"status": "updated", "enabled": False})
            if tool_name == "calendar_sync_status":
                return _mcp_result(
                    {
                        "status": "ok",
                        "projection_freshness": {
                            "last_refreshed_at": "2026-03-02T08:00:00+00:00",
                            "staleness_ms": 150,
                            "sources": [],
                        },
                    }
                )
            raise AssertionError(f"Unexpected tool call: {tool_name}")

        app, mock_client = _app_with_mcp(app, _call)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/calendar/workspace/butler-events",
                json={
                    "butler_name": "relationship",
                    "action": "toggle",
                    "request_id": "toggle-1",
                    "payload": {
                        "event_id": "7a001205-1ef2-4f53-95f9-b9ac6801a0b7",
                        "enabled": False,
                    },
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["tool_name"] == "calendar_toggle_butler_event"
        assert body["staleness_ms"] == 150
        assert mock_client.call_tool.await_count == 2
        assert mock_client.call_tool.await_args_list[0].args[0] == "calendar_toggle_butler_event"
        assert mock_client.call_tool.await_args_list[1].args[0] == "calendar_sync_status"


class TestCalendarFanOutModuleFiltering:
    """fan_out is restricted to butlers with the calendar module enabled."""

    async def test_workspace_read_skips_non_calendar_butlers(self, app):
        """When module metadata is available, fan_out excludes non-calendar butlers."""
        calendar_row = _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            calendar_id="primary",
            metadata={"source_type": "provider_event"},
        )
        # general has calendar, education does NOT
        app, mock_db, _ = _build_app(
            app,
            workspace_rows={"general": [calendar_row], "education": []},
            calendar_butlers=["general"],
        )

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
        # fan_out should have been called with butler_names=["general"] (not education)
        fan_out_calls = mock_db.fan_out.call_args_list
        for call in fan_out_calls:
            _, kwargs = call
            butler_names_arg = kwargs.get(
                "butler_names", call.args[2] if len(call.args) > 2 else None
            )
            if butler_names_arg is not None:
                assert "education" not in butler_names_arg, (
                    f"education was unexpectedly included in fan_out targets: {butler_names_arg}"
                )

    async def test_workspace_meta_skips_non_calendar_butlers(self, app):
        """Meta endpoint fan_out excludes non-calendar butlers via module filter."""
        source_row = _workspace_source_row(
            source_key="provider:google:primary",
            source_kind="provider_event",
            lane="user",
            butler_name=None,
            provider="google",
            calendar_id="primary",
            writable=True,
        )
        app, mock_db, _ = _build_app(
            app,
            source_rows={"general": [source_row], "education": []},
            calendar_butlers=["general"],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/calendar/workspace/meta")

        assert resp.status_code == 200
        fan_out_calls = mock_db.fan_out.call_args_list
        for call in fan_out_calls:
            _, kwargs = call
            butler_names_arg = kwargs.get(
                "butler_names", call.args[2] if len(call.args) > 2 else None
            )
            if butler_names_arg is not None:
                assert "education" not in butler_names_arg

    async def test_workspace_read_falls_back_to_all_butlers_when_no_module_metadata(self, app):
        """When butlers_with_module returns None (no metadata), fan_out queries all butlers."""
        calendar_row = _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            calendar_id="primary",
            metadata={"source_type": "provider_event"},
        )
        # calendar_butlers=None simulates no module metadata available
        app, mock_db, _ = _build_app(
            app,
            workspace_rows={"general": [calendar_row]},
            calendar_butlers=None,
        )

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
        # When module metadata is unavailable, butler_names arg in fan_out should be None
        # (meaning query all butlers)
        fan_out_calls = mock_db.fan_out.call_args_list
        instance_calls = [
            c
            for c in fan_out_calls
            if "FROM calendar_event_instances" in (c.args[0] if c.args else "")
        ]
        # At least one fan_out call for instances; it should pass butler_names=None
        assert len(instance_calls) >= 1
        for call in instance_calls:
            butler_names_arg = call.kwargs.get("butler_names")
            assert butler_names_arg is None, (
                f"Expected butler_names=None (all butlers) but got {butler_names_arg}"
            )

    async def test_workspace_explicit_butler_filter_overrides_module_filter(self, app):
        """An explicit ?butlers= query param takes precedence over module filtering."""
        general_row = _workspace_event_row(
            lane="butler",
            source_key="internal_scheduler:general",
            source_kind="internal_scheduler",
            butler_name="general",
            metadata={"source_type": "internal_scheduler"},
        )
        health_row = _workspace_event_row(
            lane="butler",
            source_key="internal_scheduler:health",
            source_kind="internal_scheduler",
            butler_name="health",
            metadata={"source_type": "internal_scheduler"},
        )
        # calendar_butlers reports general+health, but user only requests general
        app, _, _ = _build_app(
            app,
            workspace_rows={"general": [general_row], "health": [health_row]},
            calendar_butlers=["general", "health"],
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
                },
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert len(body["entries"]) == 1
        assert body["entries"][0]["butler_name"] == "general"
