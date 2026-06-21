"""Tests for calendar workspace API endpoints.

Condensed to 3 tests (bu-2yw2d) from 10 (bu-egmz6).

Keeps: required-params validation (422), workspace read structure, sync trigger count.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


def _proposal_row(
    *,
    butler_name: str | None = "general",
    title: str = "Dentist appointment",
    status: str = "pending",
    confidence: float | None = 0.82,
    source_snippet: str | None = "Your appointment is confirmed for Feb 22 at 2pm",
    source_event_id: str | None = "ingest-evt-1",
    entity_ids: list | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    start = start or datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    end = end or datetime(2026, 2, 22, 15, 0, tzinfo=UTC)
    now = datetime.now(tz=UTC)
    return {
        "proposal_id": uuid4(),
        "butler_name": butler_name,
        "title": title,
        "start_at": start,
        "end_at": end,
        "description": "From your inbox",
        "location": "123 Main St",
        "timezone": "UTC",
        "source_event_id": source_event_id,
        "source_snippet": source_snippet,
        "confidence": confidence,
        "entity_ids": entity_ids if entity_ids is not None else [],
        "status": status,
        "accepted_event_id": None,
        "created_at": now,
        "updated_at": now,
    }


def _build_app(
    app,
    *,
    workspace_rows: dict[str, list[dict]] | None = None,
    source_rows: dict[str, list[dict]] | None = None,
    proposal_rows: dict[str, list[dict]] | None = None,
    proposals_raise: bool = False,
    mcp_clients: dict[str, AsyncMock] | None = None,
    calendar_butlers: list[str] | None = None,
) -> tuple:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=calendar_butlers)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_proposals AS p" in query:
            if proposals_raise:
                # Simulate the table being absent / query failing for every
                # schema. The real fan_out isolates this per-butler, but a
                # top-level raise must also fail open (empty entries, no 500).
                raise RuntimeError('relation "calendar_event_proposals" does not exist')
            rows_to_scan = proposal_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            return rows_to_scan
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
# Server-side facets + keyset pagination (bu-xr1i95)
# ---------------------------------------------------------------------------


def _paged_event_row(*, idx: int, writable: bool = True) -> dict:
    """A workspace event row at a distinct, strictly-increasing start time."""
    row = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        calendar_id="primary",
        metadata={"source_type": "provider_event"},
    )
    base = datetime(2026, 2, 22, 9, 0, tzinfo=UTC)
    row["instance_starts_at"] = base + timedelta(hours=idx)
    row["instance_ends_at"] = base + timedelta(hours=idx, minutes=30)
    row["title"] = f"Event {idx}"
    row["writable"] = writable
    return row


def _build_paginating_app(app, *, rows: list[dict]):
    """Build an app whose fan_out mock faithfully simulates keyset pagination.

    The workspace branch sorts rows by ``(starts_at, id)``, applies the keyset
    ``> cursor`` predicate when present, and honors the SQL ``LIMIT`` (always the
    last positional arg) — mirroring what Postgres would do, so the router's
    page slicing / has_more / next_cursor logic is exercised end-to-end.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.butlers_with_module = MagicMock(return_value=["general"])

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            data = sorted(rows, key=lambda r: (r["instance_starts_at"], r["instance_id"]))
            if "(i.starts_at, i.id) >" in query:
                cursor_starts, cursor_id = args[-3], args[-2]
                data = [
                    r
                    for r in data
                    if (r["instance_starts_at"], r["instance_id"]) > (cursor_starts, cursor_id)
                ]
            limit = args[-1]
            return {"general": data[:limit]}
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)
    mock_mgr = AsyncMock(spec=MCPClientManager)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db


_PAGE_PARAMS = {
    "view": "user",
    "start": "2026-02-22T00:00:00Z",
    "end": "2026-02-23T00:00:00Z",
}


async def test_workspace_unknown_status_facet_returns_400(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace", params={**_PAGE_PARAMS, "status": "bogus"}
        )
    assert resp.status_code == 400


async def test_workspace_unknown_source_type_facet_returns_400(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace", params={**_PAGE_PARAMS, "source_type": "bogus"}
        )
    assert resp.status_code == 400


async def test_workspace_valid_facets_pass_through(app):
    """Valid facet params are accepted (server-side wiring); 200, not 400."""
    row = _paged_event_row(idx=0)
    app, _ = _build_paginating_app(app, rows=[row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace",
            params={
                **_PAGE_PARAMS,
                "status": "active",
                "source_type": "provider_event",
                "editable": "true",
            },
        )
    assert resp.status_code == 200


async def test_workspace_malformed_cursor_returns_400(app):
    app, _ = _build_paginating_app(app, rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace", params={**_PAGE_PARAMS, "cursor": "!!not-base64!!"}
        )
    assert resp.status_code == 400


async def test_workspace_keyset_pagination_walks_all_pages_without_overlap(app):
    rows = [_paged_event_row(idx=i) for i in range(5)]
    app, _ = _build_paginating_app(app, rows=rows)

    seen_ids: list[str] = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Page 1
        resp = await client.get("/api/calendar/workspace", params={**_PAGE_PARAMS, "limit": 2})
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert "total" not in body  # keyset convention: no total
        assert len(body["entries"]) == 2
        assert body["has_more"] is True
        assert isinstance(body["next_cursor"], str) and body["next_cursor"]
        seen_ids += [e["entry_id"] for e in body["entries"]]

        # Page 2 — follows the cursor, strictly-next, no overlap
        resp = await client.get(
            "/api/calendar/workspace",
            params={**_PAGE_PARAMS, "limit": 2, "cursor": body["next_cursor"]},
        )
        body = resp.json()["data"]
        assert len(body["entries"]) == 2
        assert body["has_more"] is True
        page2_ids = [e["entry_id"] for e in body["entries"]]
        assert not (set(page2_ids) & set(seen_ids))  # no overlap
        seen_ids += page2_ids

        # Page 3 — last page
        resp = await client.get(
            "/api/calendar/workspace",
            params={**_PAGE_PARAMS, "limit": 2, "cursor": body["next_cursor"]},
        )
        body = resp.json()["data"]
        assert len(body["entries"]) == 1
        assert body["has_more"] is False
        assert body["next_cursor"] is None
        seen_ids += [e["entry_id"] for e in body["entries"]]

    # Every event surfaced exactly once across the three pages.
    assert len(seen_ids) == 5
    assert len(set(seen_ids)) == 5


async def test_workspace_editable_facet_filters_server_side(app):
    """The editable facet narrows to writable sources (simulated server-side)."""
    writable = _paged_event_row(idx=0, writable=True)
    read_only = _paged_event_row(idx=1, writable=False)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.butlers_with_module = MagicMock(return_value=["general"])

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            data = [writable, read_only]
            # The endpoint passes editable as a bound param; simulate the
            # server-side ``s.writable = $n`` predicate over the projection.
            if "s.writable" in query:
                want = args[-2]  # editable value precedes the trailing LIMIT
                data = [r for r in data if bool(r["writable"]) is bool(want)]
            return {"general": data[: args[-1]]}
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)
    mock_mgr = AsyncMock(spec=MCPClientManager)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace", params={**_PAGE_PARAMS, "editable": "true"}
        )
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["editable"] is True


# ---------------------------------------------------------------------------
# Proposals lane (view=proposals) — pending-only projection (bu-dn65mb)
# ---------------------------------------------------------------------------


async def test_workspace_proposals_returns_pending_only(app):
    """view=proposals projects pending proposals into proposed_event entries.

    Accepted/dismissed proposals are excluded at the SQL boundary (the query
    carries ``status='pending'``), so the mock returns only the pending rows
    that the WHERE clause would match.  The projected entry must be tagged
    ``source_type=proposed_event``, non-editable, with confidence/source_snippet
    /source_event_id provenance in metadata.
    """
    pending = _proposal_row(
        title="Dentist appointment",
        confidence=0.82,
        source_snippet="confirmed for Feb 22 at 2pm",
        source_event_id="ingest-evt-1",
    )
    app, _, _ = _build_app(app, proposal_rows={"general": [pending]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace",
            params={
                "view": "proposals",
                "start": "2026-02-22T00:00:00Z",
                "end": "2026-02-23T00:00:00Z",
            },
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["view"] == "proposals"
    assert entry["source_type"] == "proposed_event"
    assert entry["editable"] is False
    assert entry["title"] == "Dentist appointment"
    assert entry["metadata"]["confidence"] == 0.82
    assert entry["metadata"]["source_snippet"] == "confirmed for Feb 22 at 2pm"
    assert entry["metadata"]["source_event_id"] == "ingest-evt-1"
    # Proposals have no provider sources/lanes.
    assert body["source_freshness"] == []
    assert body["lanes"] == []


async def test_workspace_proposals_fail_open_on_missing_table(app):
    """A missing calendar_event_proposals table degrades to empty, never 500."""
    app, _, _ = _build_app(app, proposals_raise=True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace",
            params={
                "view": "proposals",
                "start": "2026-02-22T00:00:00Z",
                "end": "2026-02-23T00:00:00Z",
            },
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    assert body["source_freshness"] == []
    assert body["lanes"] == []


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


async def test_audit_payload_summary_redacts_to_allowlist(app):
    """The audit payload_summary carries only allowlisted keys, never raw internal fields.

    Guards _extract_payload_summary: action_payload JSONB may contain arbitrary
    fields, but the audit response must surface only the recognised allowlist
    (title/start_at/...) and drop anything else (e.g. internal_field).
    """
    rows = {
        "general": [
            _audit_row(
                action_type="workspace_user_create",
                action_status="applied",
            )
        ]
    }
    # Override the payload to contain a mix of allowlisted + internal fields.
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
    # Non-allowlisted internal field is redacted out of the summary.
    assert "internal_field" not in summary
    assert set(summary) == {"title", "start_at"}


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


# ---------------------------------------------------------------------------
# Mutation undo — POST /api/calendar/workspace/undo/{action_id}
# ---------------------------------------------------------------------------


_UNDO_PRE_STATE = {
    "event_id": "evt-1",
    "calendar_id": "primary",
    "title": "Original title",
    "start_at": "2026-06-20T10:00:00+00:00",
    "end_at": "2026-06-20T11:00:00+00:00",
    "timezone": "UTC",
    "description": "Original description",
    "body": None,
    "location": "Room A",
    "attendees": ["a@example.com"],
    "recurrence_rule": None,
    "color_id": None,
}


def _undo_action_row(
    *,
    action_type: str,
    action_status: str = "applied",
    action_result: dict | None = None,
    action_payload: dict | None = None,
    origin_ref: str | None = None,
) -> dict:
    return {
        "id": uuid4(),
        "action_type": action_type,
        "action_status": action_status,
        "origin_ref": origin_ref,
        "action_payload": action_payload or {},
        "action_result": action_result,
    }


def _build_undo_app(
    app,
    *,
    action_rows: dict[str, list[dict]],
    mcp_status: str = "updated",
    calendar_butlers: list[str] | None = None,
):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.butlers_with_module = MagicMock(
        return_value=calendar_butlers if calendar_butlers is not None else ["general"]
    )

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_action_log" in query:
            rows = action_rows
            if butler_names is not None:
                rows = {k: v for k, v in rows.items() if k in butler_names}
            return rows
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)
    _pool = MagicMock()
    _pool.execute = AsyncMock()
    mock_db.pool = MagicMock(return_value=_pool)

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_mock_mcp_result({"status": mcp_status}))

    mock_mgr = AsyncMock(spec=MCPClientManager)

    async def _get_client(name: str):
        return mock_client

    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_client


async def test_undo_update_reverse_applies_pre_state(app):
    """Undo of an applied update dispatches calendar_update_event restoring pre-state."""
    row = _undo_action_row(
        action_type="workspace_user_update",
        action_result={"status": "updated", "pre_state": _UNDO_PRE_STATE},
    )
    app, _, mock_client = _build_undo_app(app, action_rows={"general": [row]}, mcp_status="updated")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{row['id']}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_type"] == "workspace_user_update"
    assert data["inverse_tool"] == "calendar_update_event"
    assert data["undone"] is True
    assert data["request_id"].startswith("undo-")

    tool_name, arguments = mock_client.call_tool.await_args.args
    assert tool_name == "calendar_update_event"
    assert arguments["event_id"] == "evt-1"
    assert arguments["title"] == "Original title"
    assert arguments["start_at"] == "2026-06-20T10:00:00+00:00"
    assert arguments["calendar_id"] == "primary"
    # Fresh request_id flows to the dispatch (idempotent + audited).
    assert arguments["request_id"] == data["request_id"]


async def test_undo_delete_recreates_event(app):
    """Undo of an applied delete dispatches calendar_create_event from the pre-image."""
    row = _undo_action_row(
        action_type="workspace_user_delete",
        action_result={"status": "deleted", "pre_state": _UNDO_PRE_STATE},
    )
    app, _, mock_client = _build_undo_app(app, action_rows={"general": [row]}, mcp_status="created")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{row['id']}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["inverse_tool"] == "calendar_create_event"
    assert data["undone"] is True

    tool_name, arguments = mock_client.call_tool.await_args.args
    assert tool_name == "calendar_create_event"
    assert arguments["title"] == "Original title"
    assert arguments["calendar_id"] == "primary"
    assert "event_id" not in arguments  # create does not target an id
    assert arguments["request_id"] == data["request_id"]


async def test_undo_create_deletes_event(app):
    """Undo of an applied create dispatches calendar_delete_event against the created id."""
    row = _undo_action_row(
        action_type="workspace_user_create",
        action_result={
            "status": "created",
            "calendar_id": "primary",
            "event": {"event_id": "evt-created-9"},
        },
        origin_ref="evt-created-9",
    )
    app, _, mock_client = _build_undo_app(app, action_rows={"general": [row]}, mcp_status="deleted")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{row['id']}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["inverse_tool"] == "calendar_delete_event"
    assert data["undone"] is True

    tool_name, arguments = mock_client.call_tool.await_args.args
    assert tool_name == "calendar_delete_event"
    assert arguments["event_id"] == "evt-created-9"
    assert arguments["calendar_id"] == "primary"
    assert arguments["request_id"] == data["request_id"]


async def test_undo_non_applied_returns_409(app):
    """Undo of a pending/failed/noop action fails fast with 409, dispatching nothing."""
    row = _undo_action_row(
        action_type="workspace_user_update",
        action_status="failed",
        action_result={"status": "error"},
    )
    app, _, mock_client = _build_undo_app(app, action_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{row['id']}")

    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"]
    mock_client.call_tool.assert_not_awaited()


async def test_undo_missing_pre_state_returns_422(app):
    """Applied update without captured pre-state fails fast with 422 diagnostics."""
    row = _undo_action_row(
        action_type="workspace_user_update",
        action_result={"status": "updated"},  # no pre_state
    )
    app, _, mock_client = _build_undo_app(app, action_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{row['id']}")

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["action_id"] == str(row["id"])
    assert detail["action_type"] == "workspace_user_update"
    assert "reason" in detail
    mock_client.call_tool.assert_not_awaited()


async def test_undo_unknown_id_returns_404(app):
    """Undo of an unknown action id returns 404 and dispatches nothing."""
    app, _, mock_client = _build_undo_app(app, action_rows={"general": []})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{uuid4()}")

    assert resp.status_code == 404
    mock_client.call_tool.assert_not_awaited()


async def test_undo_already_undone_returns_409(app):
    """Repeated undo of an already-undone action fails fast with 409."""
    row = _undo_action_row(
        action_type="workspace_user_update",
        action_result={
            "status": "updated",
            "pre_state": _UNDO_PRE_STATE,
            "undo": {"request_id": "undo-prev", "inverse_tool": "calendar_update_event"},
        },
    )
    app, _, mock_client = _build_undo_app(app, action_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/undo/{row['id']}")

    assert resp.status_code == 409
    assert "already undone" in resp.json()["detail"]
    mock_client.call_tool.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sync-health & cursor-recovery cockpit (bu-wwftzj)
# ---------------------------------------------------------------------------


async def test_sync_forwards_full_recovery_flag(app):
    """POST /workspace/sync with full=true forwards full to calendar_force_sync.

    The flag reaches the MCP tool and the per-target ``recovery`` result is
    surfaced; the response echoes ``full``.
    """
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
    general_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "sync_completed", "full": True, "recovery": True})
    )
    app, _, _ = _build_app(
        app,
        source_rows=source_rows,
        mcp_clients={"general": general_client},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/sync",
            json={"source_key": "provider:google:primary", "butler": "general", "full": True},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["full"] is True
    assert data["scope"] == "source"
    assert data["targets"][0]["recovery"] is True

    # The full flag must be forwarded to the MCP tool call.
    call_args = general_client.call_tool.await_args
    assert call_args.args[0] == "calendar_force_sync"
    assert call_args.args[1]["full"] is True


async def test_sync_incremental_default_does_not_force_full(app):
    """Omitting full preserves incremental behavior: full=false is forwarded."""
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
    general_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "sync_completed", "recovery": False})
    )
    app, _, _ = _build_app(app, source_rows=source_rows, mcp_clients={"general": general_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/sync",
            json={"source_key": "provider:google:primary", "butler": "general"},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["full"] is False
    assert data["targets"][0]["recovery"] is False
    assert general_client.call_tool.await_args.args[1]["full"] is False


async def test_meta_carries_per_source_error_kind(app):
    """GET /workspace/meta classifies each source's last_error into error_kind."""
    token_expired = _workspace_source_row(
        source_key="provider:google:primary",
        source_kind="provider_event",
        lane="user",
        butler_name=None,
        provider="google",
        calendar_id="owner@example.com",
        writable=True,
        metadata={"account_email": "owner@example.com"},
    )
    token_expired["last_error"] = "sync token expired (410 Gone)"
    token_expired["last_success_at"] = None
    token_expired["last_error_at"] = datetime.now(tz=UTC)

    healthy = _workspace_source_row(
        source_key="provider:google:work",
        source_kind="provider_event",
        lane="user",
        butler_name=None,
        provider="google",
        calendar_id="work@example.com",
        writable=True,
        metadata={"account_email": "owner@example.com"},
    )

    app, _, _ = _build_app(
        app,
        source_rows={"general": [token_expired, healthy]},
        calendar_butlers=["general"],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/meta")

    assert resp.status_code == 200
    data = resp.json()["data"]
    by_key = {s["source_key"]: s for s in data["connected_sources"]}
    assert by_key["provider:google:primary"]["error_kind"] == "token_expired"
    # Raw last_error is still available alongside the derived classification.
    assert by_key["provider:google:primary"]["last_error"] == "sync token expired (410 Gone)"
    assert by_key["provider:google:work"]["error_kind"] == "none"
