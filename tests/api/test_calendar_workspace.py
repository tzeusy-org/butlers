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


def _audit_row(
    *,
    action_type: str = "workspace_user_create",
    action_status: str = "applied",
    source_butler: str | None = None,
    source_session_id: str | None = None,
    request_id: str | None = None,
    origin_ref: str | None = None,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": uuid4(),
        "idempotency_key": f"key-{uuid4()}",
        "request_id": request_id,
        "action_type": action_type,
        "action_status": action_status,
        "origin_ref": origin_ref,
        "action_payload": {"title": "Test event", "start_at": "2026-06-20T10:00:00Z"},
        "error": None,
        "created_at": now,
        "updated_at": now,
        "applied_at": now if action_status == "applied" else None,
        "source_butler": source_butler,
        "source_session_id": source_session_id,
    }


def _count_row(count: int) -> dict:
    return {"count": count}


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
        "origin_instance_ref": str(uuid4()),
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
    entry = body["entries"][0]
    assert entry["view"] == "user"
    # Regression guard (bu-99m0s): the v1 read-model strictly reads
    # ``origin_instance_ref`` from every workspace row, so it must round-trip
    # into the entry metadata rather than raising KeyError -> HTTP 500.
    assert entry["metadata"]["origin_instance_ref"] == user_row["origin_instance_ref"]
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


# ---------------------------------------------------------------------------
# Mutation response: conflicts and suggested_slots surfaced as first-class fields
# ---------------------------------------------------------------------------


async def test_mutate_user_event_conflict_response_surfaces_conflicts(app):
    """When the MCP tool returns a conflict response, the API surfaces
    ``conflicts`` and ``suggested_slots`` as first-class typed fields on the
    mutation response — not just nested inside the opaque ``result`` dict."""
    conflict_payload = [
        {
            "event_id": "evt-existing",
            "title": "Existing event",
            "start_at": "2026-06-20T10:00:00+00:00",
            "end_at": "2026-06-20T11:00:00+00:00",
            "timezone": "UTC",
        }
    ]
    suggested_slots_payload = [
        {
            "start_at": "2026-06-20T11:00:00+00:00",
            "end_at": "2026-06-20T11:30:00+00:00",
            "timezone": "UTC",
        }
    ]
    mcp_result = {
        "status": "conflict",
        "policy": "suggest",
        "provider": "google",
        "calendar_id": "primary",
        "conflicts": conflict_payload,
        "suggested_slots": suggested_slots_payload,
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_mock_mcp_result(mcp_result))

    # status call for projection freshness
    status_client = AsyncMock()
    status_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "ok", "projection_freshness": None})
    )

    async def _get_client(name: str):
        return mock_client

    app, mock_db, mock_mgr = _build_app(app)
    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/user-events",
            json={
                "butler_name": "general",
                "action": "create",
                "request_id": "req-conflict-1",
                "payload": {
                    "title": "New event",
                    "start_at": "2026-06-20T10:00:00Z",
                    "end_at": "2026-06-20T10:30:00Z",
                    "timezone": "UTC",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]

    # result still has the raw MCP payload
    assert data["result"]["status"] == "conflict"

    # conflicts surfaced as first-class typed list
    assert len(data["conflicts"]) == 1
    c = data["conflicts"][0]
    assert c["event_id"] == "evt-existing"
    assert c["title"] == "Existing event"

    # suggested_slots surfaced as first-class typed list
    assert len(data["suggested_slots"]) == 1
    s = data["suggested_slots"][0]
    assert "start_at" in s
    assert "end_at" in s


async def test_mutate_user_event_success_response_has_empty_conflict_lists(app):
    """On a successful create (no conflict), the response carries empty
    ``conflicts`` and ``suggested_slots`` lists, not null."""
    mcp_result = {
        "status": "created",
        "provider": "google",
        "calendar_id": "primary",
        "event": {"event_id": "evt-new"},
    }

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_mock_mcp_result(mcp_result))

    async def _get_client(name: str):
        return mock_client

    app, mock_db, mock_mgr = _build_app(app)
    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/user-events",
            json={
                "butler_name": "general",
                "action": "create",
                "request_id": "req-ok-1",
                "payload": {
                    "title": "No conflict event",
                    "start_at": "2026-06-20T10:00:00Z",
                    "end_at": "2026-06-20T10:30:00Z",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["result"]["status"] == "created"
    assert data["conflicts"] == []
    assert data["suggested_slots"] == []


# ---------------------------------------------------------------------------
# Audit trail — GET /api/calendar/workspace/audit
# ---------------------------------------------------------------------------


def _build_audit_app(
    app,
    *,
    audit_rows: dict[str, list[dict]] | None = None,
    calendar_butlers: list[str] | None = None,
):
    """Build a test app wired with a fan_out mock that handles audit queries."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.butlers_with_module = MagicMock(
        return_value=calendar_butlers if calendar_butlers is not None else ["general"]
    )

    _audit_rows = audit_rows or {}

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_action_log" in query:
            rows_to_scan = _audit_rows
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            if "count(*)" in query:
                # Return count rows
                return {k: [{"count": len(v)}] for k, v in rows_to_scan.items()}
            # Return data rows (limit already baked into LIMIT/OFFSET in SQL)
            return rows_to_scan
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)

    mock_mgr = AsyncMock(spec=MCPClientManager)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db


async def test_audit_returns_entries_and_total(app):
    """GET /api/calendar/workspace/audit returns entries + total count."""
    session_id = "sess-abc123"
    rows = {
        "general": [
            _audit_row(
                action_type="workspace_user_create",
                action_status="applied",
                source_butler="general",
                source_session_id=session_id,
                origin_ref="evt-google-1",
            ),
            _audit_row(
                action_type="workspace_user_delete",
                action_status="failed",
                source_butler=None,
                source_session_id=None,
            ),
        ]
    }
    app, _ = _build_audit_app(app, audit_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/audit")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert len(data["entries"]) == 2

    # Verify provenance fields are surfaced
    applied_entry = next(e for e in data["entries"] if e["action_status"] == "applied")
    assert applied_entry["action_type"] == "workspace_user_create"
    assert applied_entry["source_butler"] == "general"
    assert applied_entry["source_session_id"] == session_id
    assert applied_entry["origin_ref"] == "evt-google-1"

    failed_entry = next(e for e in data["entries"] if e["action_status"] == "failed")
    assert failed_entry["source_butler"] is None
    assert failed_entry["source_session_id"] is None


async def test_audit_empty_when_no_rows(app):
    """GET /api/calendar/workspace/audit returns empty list when no log rows exist."""
    app, _ = _build_audit_app(app, audit_rows={"general": []})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/audit")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 0
    assert data["entries"] == []


async def test_audit_payload_summary_contains_key_fields(app):
    """The payload_summary in audit entries carries recognised key fields only."""
    rows = {
        "general": [
            _audit_row(
                action_type="workspace_user_create",
                action_status="applied",
            )
        ]
    }
    # Override the payload to contain mixed fields
    rows["general"][0]["action_payload"] = {
        "title": "My event",
        "start_at": "2026-06-20T10:00:00Z",
        "internal_field": "should-not-appear",
    }
    app, _ = _build_audit_app(app, audit_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/audit")

    assert resp.status_code == 200
    entry = resp.json()["data"]["entries"][0]
    summary = entry["payload_summary"]
    assert "title" in summary
    assert "start_at" in summary
    assert "internal_field" not in summary


# ---------------------------------------------------------------------------
# Single-entry lookup — GET /api/calendar/workspace/entries/{entry_id}
# ---------------------------------------------------------------------------


async def test_entry_detail_returns_entry(app):
    """GET /entries/{id} returns 200 with the matching entry."""
    from uuid import UUID

    row = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        calendar_id="primary",
        metadata={"source_type": "provider_event", "provider_event_id": "evt-detail"},
    )
    instance_id: UUID = row["instance_id"]

    app, _, _ = _build_app(
        app,
        workspace_rows={"general": [row]},
        calendar_butlers=["general"],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/calendar/workspace/entries/{instance_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["entry_id"] == str(instance_id)
    assert data["title"] == "Calendar item"
    assert data["view"] == "user"


async def test_entry_detail_returns_404_when_not_found(app):
    """GET /entries/{id} returns 404 when no matching instance exists."""
    from uuid import uuid4

    app, _, _ = _build_app(app, workspace_rows={}, calendar_butlers=["general"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/calendar/workspace/entries/{uuid4()}")

    assert resp.status_code == 404
