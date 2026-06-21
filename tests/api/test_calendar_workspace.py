"""Tests for calendar workspace API endpoints.

Condensed to 3 tests (bu-2yw2d) from 10 (bu-egmz6).

Keeps: required-params validation (422), workspace read structure, sync trigger count.
"""

from __future__ import annotations

import asyncio
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


def _overlay_row(
    *,
    butler: str,
    date: str,
    entries: list[dict] | None = None,
    has_entries: bool | None = None,
    envelope_butler: str | None = "__match__",
) -> dict:
    """Build a ``calendar.v_overlay_contributions`` row (column + JSONB value).

    ``butler`` is the view's hardcoded source column. ``envelope_butler`` is the
    ``value->>'butler'`` field; ``"__match__"`` (default) makes it equal the
    column (the valid case), and an explicit value exercises the mismatch guard.
    """
    payload_entries = entries or []
    payload_butler = butler if envelope_butler == "__match__" else envelope_butler
    return {
        "butler": butler,
        "key": f"calendar/overlay/{date}",
        "value": {
            "butler": payload_butler,
            "date": date,
            "has_entries": (len(payload_entries) > 0) if has_entries is None else has_entries,
            "entries": payload_entries,
        },
    }


def _build_app(
    app,
    *,
    workspace_rows: dict[str, list[dict]] | None = None,
    source_rows: dict[str, list[dict]] | None = None,
    proposal_rows: dict[str, list[dict]] | None = None,
    proposals_raise: bool = False,
    overlay_rows: dict[str, list[dict]] | None = None,
    overlays_raise: bool = False,
    mcp_clients: dict[str, AsyncMock] | None = None,
    calendar_butlers: list[str] | None = None,
) -> tuple:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=calendar_butlers)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar.v_overlay_contributions" in query:
            if overlays_raise:
                raise RuntimeError('relation "calendar.v_overlay_contributions" does not exist')
            rows_to_scan = overlay_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            return rows_to_scan
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
# Overlays lane (view=overlays) — cached domain-context projection (bu-5m9ve8)
# ---------------------------------------------------------------------------


async def _get_overlays(app, *, start="2026-02-22T00:00:00Z", end="2026-02-23T00:00:00Z"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(
            "/api/calendar/workspace",
            params={"view": "overlays", "start": start, "end": end},
        )


async def test_workspace_overlays_projects_in_range(app):
    """view=overlays projects cached contributions into overlay_contribution entries.

    Each in-range entry is tagged ``source_type=overlay_contribution``,
    non-editable, with ``kind``/``priority``/``source_butler``/``meta`` in
    metadata, and ``has_domain_context`` is true. Out-of-range envelopes do not
    contribute entries.
    """
    in_range = _overlay_row(
        butler="finance",
        date="2026-02-22",
        entries=[
            {
                "kind": "bill_due",
                "label": "Electric Co",
                "priority": "high",
                "meta": {"amount": 84.2, "currency": "SGD"},
            }
        ],
    )
    out_of_range = _overlay_row(
        butler="finance",
        date="2026-03-15",
        entries=[{"kind": "bill_due", "label": "Future Bill", "priority": "low", "meta": {}}],
    )
    app, _, _ = _build_app(
        app,
        overlay_rows={"finance": [in_range, out_of_range]},
        calendar_butlers=["finance", "travel"],
    )

    # The window covers the SGT date 2026-02-22 (anchored at Asia/Singapore
    # midnight = 2026-02-21T16:00Z). Use a wide UTC window to include it.
    resp = await _get_overlays(app, start="2026-02-21T00:00:00Z", end="2026-02-23T00:00:00Z")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["has_domain_context"] is True
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["view"] == "overlays"
    assert entry["source_type"] == "overlay_contribution"
    assert entry["editable"] is False
    assert entry["title"] == "Electric Co"
    assert entry["all_day"] is True
    assert entry["metadata"]["kind"] == "bill_due"
    assert entry["metadata"]["priority"] == "high"
    assert entry["metadata"]["source_butler"] == "finance"
    assert entry["metadata"]["meta"] == {"amount": 84.2, "currency": "SGD"}
    # Overlays are read-only domain context — no provider sources/lanes.
    assert body["source_freshness"] == []
    assert body["lanes"] == []


async def test_workspace_overlays_fail_open_on_missing_view(app):
    """A missing/unreadable view degrades to empty + has_domain_context=false, never 500."""
    app, _, _ = _build_app(app, overlays_raise=True, calendar_butlers=["finance"])

    resp = await _get_overlays(app, start="2026-02-21T00:00:00Z", end="2026-02-23T00:00:00Z")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    assert body["has_domain_context"] is False


async def test_workspace_overlays_butler_mismatch_skipped(app):
    """A row whose value->>'butler' disagrees with the source column is skipped."""
    mismatch = _overlay_row(
        butler="finance",
        date="2026-02-22",
        entries=[{"kind": "bill_due", "label": "Spoofed", "priority": "high", "meta": {}}],
        envelope_butler="travel",
    )
    app, _, _ = _build_app(app, overlay_rows={"finance": [mismatch]}, calendar_butlers=["finance"])

    resp = await _get_overlays(app, start="2026-02-21T00:00:00Z", end="2026-02-23T00:00:00Z")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    # Mismatched envelopes contribute no domain context.
    assert body["has_domain_context"] is False


async def test_workspace_overlays_empty_envelope_sets_domain_context(app):
    """An in-range envelope with has_entries=false yields no entries but domain context.

    Honest empty-state: the specialist contributed for the date (even with zero
    domain events), so ``has_domain_context`` is true while ``entries`` is empty.
    """
    empty = _overlay_row(butler="health", date="2026-02-22", entries=[], has_entries=False)
    app, _, _ = _build_app(app, overlay_rows={"health": [empty]}, calendar_butlers=["health"])

    resp = await _get_overlays(app, start="2026-02-21T00:00:00Z", end="2026-02-23T00:00:00Z")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    assert body["has_domain_context"] is True


async def test_workspace_user_view_excludes_overlay_contributions(app):
    """Overlay rows never appear in the user view (overlays are a separate lane)."""
    overlay = _overlay_row(
        butler="finance",
        date="2026-02-22",
        entries=[{"kind": "bill_due", "label": "Electric Co", "priority": "high", "meta": {}}],
    )
    app, _, _ = _build_app(
        app,
        overlay_rows={"finance": [overlay]},
        calendar_butlers=["finance"],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/workspace",
            params={
                "view": "user",
                "start": "2026-02-21T00:00:00Z",
                "end": "2026-02-23T00:00:00Z",
            },
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert all(e["source_type"] != "overlay_contribution" for e in body["entries"])


# ---------------------------------------------------------------------------
# Day-briefing card (GET /day-briefing) — structured "tomorrow at a glance"
# reading the precomputed overlay view, grouped by butler/kind (bu-jj0b3n)
# ---------------------------------------------------------------------------


async def _get_day_briefing(app, *, date="2026-02-22", timezone=None):
    params = {"date": date}
    if timezone is not None:
        params["timezone"] = timezone
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/calendar/workspace/day-briefing", params=params)


async def test_day_briefing_groups_by_butler_and_kind(app):
    """A populated day returns a structured payload grouped by butler/kind, no LLM."""
    finance = _overlay_row(
        butler="finance",
        date="2026-02-22",
        entries=[
            {
                "kind": "bill_due",
                "label": "Electric Co",
                "priority": "high",
                "meta": {"amount": 84.2, "currency": "SGD"},
            },
            {
                "kind": "subscription_renewal",
                "label": "Spotify",
                "priority": "low",
                "meta": {"amount": 12, "currency": "SGD"},
            },
        ],
    )
    health = _overlay_row(
        butler="health",
        date="2026-02-22",
        entries=[
            {"kind": "appointment", "label": "Dentist", "priority": "high", "meta": {}},
        ],
    )
    # The cached view is read through ONE deterministic pool (the first
    # calendar butler), returning every butler's rows — so both envelopes sit
    # under that single reader key, exactly as the cross-schema UNION view yields.
    app, _, mock_mgr = _build_app(
        app,
        overlay_rows={"finance": [finance, health]},
        calendar_butlers=["finance", "health"],
    )

    resp = await _get_day_briefing(app, date="2026-02-22")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["date"] == "2026-02-22"
    assert body["has_domain_context"] is True
    assert body["has_entries"] is True
    # Flat chip-ready list carries every underlying item.
    assert len(body["entries"]) == 3
    assert all(e["source_type"] == "overlay_contribution" for e in body["entries"])

    # Grouped by butler (sorted), then kind (sorted).
    groups = body["groups"]
    assert [g["source_butler"] for g in groups] == ["finance", "health"]
    finance_group = groups[0]
    assert finance_group["count"] == 2
    assert [k["kind"] for k in finance_group["kinds"]] == ["bill_due", "subscription_renewal"]
    assert finance_group["kinds"][0]["entries"][0]["title"] == "Electric Co"
    health_group = groups[1]
    assert health_group["count"] == 1
    assert health_group["kinds"][0]["kind"] == "appointment"

    # NO per-open LLM call: the day-briefing read never touches MCP.
    mock_mgr.get_client.assert_not_called()


async def test_day_briefing_honest_empty_state_when_no_contribution(app):
    """No specialist contributed for the date → has_domain_context false, empty groups."""
    app, _, _ = _build_app(app, overlay_rows={}, calendar_butlers=["finance", "health"])

    resp = await _get_day_briefing(app, date="2026-02-22")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    assert body["groups"] == []
    assert body["has_domain_context"] is False
    assert body["has_entries"] is False


async def test_day_briefing_empty_envelope_sets_domain_context(app):
    """An in-range envelope with has_entries=false → domain context true, zero entries."""
    empty = _overlay_row(butler="health", date="2026-02-22", entries=[], has_entries=False)
    app, _, _ = _build_app(app, overlay_rows={"health": [empty]}, calendar_butlers=["health"])

    resp = await _get_day_briefing(app, date="2026-02-22")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    assert body["groups"] == []
    assert body["has_domain_context"] is True
    assert body["has_entries"] is False


async def test_day_briefing_fail_open_on_missing_view(app):
    """A missing/unreadable view degrades to the empty-state, never HTTP 500."""
    app, _, _ = _build_app(app, overlays_raise=True, calendar_butlers=["finance"])

    resp = await _get_day_briefing(app, date="2026-02-22")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entries"] == []
    assert body["groups"] == []
    assert body["has_domain_context"] is False


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

    async def _fetchval(query, *args):
        # Models the atomic undo-claim guarded UPDATE ... RETURNING id: by
        # default the claim succeeds (returns the action_id from $1).
        if "calendar_action_log" in query and args:
            return args[0]
        return None

    _pool.fetchval = AsyncMock(side_effect=_fetchval)
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


async def test_undo_concurrent_dispatches_inverse_exactly_once(app):
    """Two concurrent undos of the same action dispatch the inverse exactly once.

    Regression for the TOCTOU race (bu-iphtg): the already-undone read-guard and
    the marker write were non-transactional, so two concurrent undos could each
    pass the guard and recreate/restore the event twice. The atomic claim
    (guarded conditional UPDATE) must let exactly one caller win and dispatch;
    the loser returns 409 with NO second inverse dispatch.
    """
    row = _undo_action_row(
        action_type="workspace_user_delete",
        action_result={"status": "deleted", "pre_state": _UNDO_PRE_STATE},
    )
    app, mock_db, mock_client = _build_undo_app(
        app, action_rows={"general": [row]}, mcp_status="created"
    )

    # The guarded UPDATE ... RETURNING id only matches the row while no 'undo'
    # marker exists: the first claim wins (returns the id), every later claim
    # matches zero rows (returns None). Model that with a one-shot counter so
    # the outcome is deterministic regardless of coroutine interleaving.
    claim_calls = {"n": 0}

    async def _claim_once(query, *args):
        if "calendar_action_log" in query and "RETURNING id" in query:
            claim_calls["n"] += 1
            return args[0] if claim_calls["n"] == 1 else None
        return None

    mock_db.pool.return_value.fetchval = AsyncMock(side_effect=_claim_once)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_a, resp_b = await asyncio.gather(
            client.post(f"/api/calendar/workspace/undo/{row['id']}"),
            client.post(f"/api/calendar/workspace/undo/{row['id']}"),
        )

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [200, 409], statuses

    winner = resp_a if resp_a.status_code == 200 else resp_b
    loser = resp_b if resp_a.status_code == 200 else resp_a
    assert winner.json()["data"]["undone"] is True
    assert "already undone" in loser.json()["detail"]

    # Exactly ONE inverse dispatch — the whole point of the atomic claim.
    assert mock_client.call_tool.await_count == 1
    tool_name, _ = mock_client.call_tool.await_args.args
    assert tool_name == "calendar_create_event"


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


# ---------------------------------------------------------------------------
# Find time — POST /api/calendar/workspace/find-time (bu-140q93)
# ---------------------------------------------------------------------------


async def test_find_time_returns_ranked_slots(app):
    slots_payload = [
        {
            "start_at": "2026-06-22T09:00:00+00:00",
            "end_at": "2026-06-22T10:00:00+00:00",
            "timezone": "UTC",
        },
        {
            "start_at": "2026-06-22T11:00:00+00:00",
            "end_at": "2026-06-22T12:00:00+00:00",
            "timezone": "UTC",
        },
    ]
    mcp_result = {
        "slots": slots_payload,
        "duration_minutes": 60,
        "calendar_ids": ["primary"],
    }
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=_mock_mcp_result(mcp_result))

    async def _get_client(name: str):
        return mock_client

    app, _, mock_mgr = _build_app(app)
    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/find-time",
            json={
                "butler_name": "general",
                "duration_minutes": 60,
                "search_start": "2026-06-22T08:00:00Z",
                "search_end": "2026-06-22T18:00:00Z",
                "constraints": {"part_of_day": "morning", "avoid_weekdays": ["FR"]},
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["duration_minutes"] == 60
    assert data["calendar_ids"] == ["primary"]
    assert [s["start_at"] for s in data["slots"]] == [
        "2026-06-22T09:00:00Z",
        "2026-06-22T11:00:00Z",
    ]

    # The MCP tool was dispatched with the structured arguments.
    tool_name, arguments = mock_client.call_tool.await_args.args
    assert tool_name == "calendar_find_free_slots"
    assert arguments["duration_minutes"] == 60
    assert arguments["constraints"] == {"part_of_day": "morning", "avoid_weekdays": ["FR"]}


async def test_find_time_rejects_bad_duration(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/find-time",
            json={
                "butler_name": "general",
                "duration_minutes": 0,
                "search_start": "2026-06-22T08:00:00Z",
                "search_end": "2026-06-22T18:00:00Z",
            },
        )
    assert resp.status_code == 422


async def test_find_time_rejects_inverted_window(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/find-time",
            json={
                "butler_name": "general",
                "duration_minutes": 60,
                "search_start": "2026-06-22T18:00:00Z",
                "search_end": "2026-06-22T08:00:00Z",
            },
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Butler-event recurrence dry-run preview (bu-15srd1)
#
# The preview endpoint is a pure recurrence projection — no DB / MCP / LLM — so
# these tests hit the bare app without _build_app().
# ---------------------------------------------------------------------------


async def test_butler_event_preview_returns_dates_no_write(app):
    """A valid weekly RRULE returns weekly projected dates and writes nothing.

    The endpoint takes no DB/MCP dependency, so reaching a 200 with projected
    dates is itself the no-write guarantee — there is no persistence path to hit.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={
                "rrule": "RRULE:FREQ=WEEKLY",
                "start_at": "2026-06-22T09:00:00Z",
                "limit": 6,
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # First occurrence is the start date itself; subsequent ones are 7 days apart.
    occ = [datetime.fromisoformat(s) for s in data["occurrences"]]
    assert len(occ) == 6
    assert occ[0] == datetime(2026, 6, 22, 9, 0, tzinfo=UTC)
    assert (occ[1] - occ[0]) == timedelta(days=7)
    assert data["effective_cron"] == "0 9 * * 1"  # Monday 09:00
    assert data["notes"] == []
    # 90-day window holds ~13 weekly hits; the cap leaves "+N more".
    assert data["total_in_window"] > 6
    assert data["more_count"] == data["total_in_window"] - 6


async def test_butler_event_preview_caps_daily_with_more_sentinel(app):
    """A daily rule fills the 90-day window and surfaces the +N more sentinel."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={
                "rrule": "RRULE:FREQ=DAILY",
                "start_at": "2026-06-22T09:00:00Z",
                "limit": 6,
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["occurrences"]) == 6
    # Daily over a 90-day window (inclusive of the start) -> 91 occurrences.
    assert data["total_in_window"] == 91
    assert data["more_count"] == 85


async def test_butler_event_preview_until_truncates_window(app):
    """An ``until_at`` earlier than the 90-day horizon bounds the projection."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={
                "rrule": "RRULE:FREQ=DAILY",
                "start_at": "2026-06-22T09:00:00Z",
                "until_at": "2026-06-25T09:00:00Z",
                "limit": 10,
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 22, 23, 24, 25 -> 4 occurrences, none beyond the until bound.
    assert data["total_in_window"] == 4
    assert data["more_count"] == 0
    assert len(data["occurrences"]) == 4


async def test_butler_event_preview_lossy_interval_note(app):
    """A biweekly INTERVAL degrades to weekly and the loss is surfaced in notes."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={
                "rrule": "RRULE:FREQ=WEEKLY;INTERVAL=2",
                "start_at": "2026-06-22T09:00:00Z",
                "limit": 6,
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    occ = [datetime.fromisoformat(s) for s in data["occurrences"]]
    # The scheduler ignores INTERVAL -> the projection fires every week, not every 2.
    assert (occ[1] - occ[0]) == timedelta(days=7)
    assert any("INTERVAL=2" in note for note in data["notes"])


async def test_butler_event_preview_cron_passthrough(app):
    """A raw cron expression is expanded verbatim with no lossy notes."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={
                "cron": "0 9 * * *",
                "start_at": "2026-06-22T00:00:00Z",
                "limit": 3,
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["effective_cron"] == "0 9 * * *"
    assert data["notes"] == []
    assert len(data["occurrences"]) == 3


async def test_butler_event_preview_invalid_rrule_422(app):
    """An unparseable RRULE (no FREQ) fails fast with 422 and no partial result."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={"rrule": "totally-not-a-rule", "start_at": "2026-06-22T09:00:00Z"},
        )
    assert resp.status_code == 422


async def test_butler_event_preview_invalid_cron_422(app):
    """An unparseable cron expression returns 422."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={"cron": "not a cron", "start_at": "2026-06-22T09:00:00Z"},
        )
    assert resp.status_code == 422


async def test_butler_event_preview_requires_exactly_one_recurrence(app):
    """Supplying both (or neither) rrule and cron is a 422 validation error."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        both = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={"rrule": "RRULE:FREQ=DAILY", "cron": "0 9 * * *"},
        )
        neither = await client.post(
            "/api/calendar/workspace/butler-events/preview",
            json={},
        )
    assert both.status_code == 422
    assert neither.status_code == 422


# ---------------------------------------------------------------------------
# Butler-event snooze / dismiss actions (bu-ul4dgm)
#
# The butler-events mutation surface gains ``dismiss`` (wires ``reminder_dismiss``)
# and ``snooze`` (reschedules ``due_at`` through ``calendar_update_butler_event``).
# These exercise the router dispatch + 404 routing through the mocked MCP manager.
# ---------------------------------------------------------------------------


def _butler_events_app(app, *, call_tool):
    """Wire a workspace app whose single MCP client uses ``call_tool``."""
    app, mock_db, mock_mgr = _build_app(app)
    mock_client = AsyncMock()
    mock_client.call_tool = call_tool

    async def _get_client(name: str):
        return mock_client

    mock_mgr.get_client = AsyncMock(side_effect=_get_client)
    return app, mock_client


async def test_butler_event_dismiss_wires_reminder_dismiss(app):
    """``action=dismiss`` dispatches ``reminder_dismiss`` and preserves the envelope."""
    call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "dismissed", "event_id": "rem-1"})
    )
    app, mock_client = _butler_events_app(app, call_tool=call_tool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events",
            json={
                "butler_name": "general",
                "action": "dismiss",
                "request_id": "req-dismiss-1",
                "payload": {"event_id": "rem-1"},
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action"] == "dismiss"
    assert data["tool_name"] == "reminder_dismiss"
    assert data["result"]["status"] == "dismissed"

    # reminder_dismiss only accepts event_id — no request_id / extra args forwarded.
    tool_name, arguments = mock_client.call_tool.await_args_list[0].args
    assert tool_name == "reminder_dismiss"
    assert arguments == {"event_id": "rem-1"}


async def test_butler_event_dismiss_requires_event_id(app):
    """A dismiss with no event_id is a 422 and never touches the MCP layer."""
    call_tool = AsyncMock()
    app, mock_client = _butler_events_app(app, call_tool=call_tool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events",
            json={"butler_name": "general", "action": "dismiss", "payload": {}},
        )

    assert resp.status_code == 422
    mock_client.call_tool.assert_not_awaited()


async def test_butler_event_dismiss_unknown_id_returns_404(app):
    """An unknown dismiss target (tool raises) is remapped to a 404."""
    call_tool = AsyncMock(side_effect=RuntimeError("Reminder event rem-x not found"))
    app, _ = _butler_events_app(app, call_tool=call_tool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events",
            json={
                "butler_name": "general",
                "action": "dismiss",
                "payload": {"event_id": "rem-x"},
            },
        )

    assert resp.status_code == 404


async def test_butler_event_snooze_moves_due_at(app):
    """``action=snooze`` reschedules via calendar_update_butler_event using due_at."""
    call_tool = AsyncMock(
        return_value=_mock_mcp_result(
            {"status": "updated", "source_type": "butler_reminder", "reminder_id": "rem-2"}
        )
    )
    app, mock_client = _butler_events_app(app, call_tool=call_tool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events",
            json={
                "butler_name": "general",
                "action": "snooze",
                "request_id": "req-snooze-1",
                "payload": {"event_id": "rem-2", "due_at": "2026-06-23T09:00:00Z"},
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action"] == "snooze"
    assert data["tool_name"] == "calendar_update_butler_event"
    assert data["result"]["status"] == "updated"

    tool_name, arguments = mock_client.call_tool.await_args_list[0].args
    assert tool_name == "calendar_update_butler_event"
    assert arguments["event_id"] == "rem-2"
    # due_at is normalized to the update tool's start_at parameter.
    assert arguments["start_at"] == "2026-06-23T09:00:00Z"
    assert "due_at" not in arguments
    assert arguments["request_id"] == "req-snooze-1"


async def test_butler_event_snooze_requires_new_time(app):
    """A snooze with neither due_at nor start_at is a 422, no MCP dispatch."""
    call_tool = AsyncMock()
    app, mock_client = _butler_events_app(app, call_tool=call_tool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events",
            json={
                "butler_name": "general",
                "action": "snooze",
                "payload": {"event_id": "rem-2"},
            },
        )

    assert resp.status_code == 422
    mock_client.call_tool.assert_not_awaited()


async def test_butler_event_snooze_unknown_id_returns_404(app):
    """An unknown snooze target (tool returns an error envelope) is a 404."""
    call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "error", "error": "Reminder rem-x not found"})
    )
    app, _ = _butler_events_app(app, call_tool=call_tool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/workspace/butler-events",
            json={
                "butler_name": "general",
                "action": "snooze",
                "payload": {"event_id": "rem-x", "due_at": "2026-06-23T09:00:00Z"},
            },
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Proposals accept / dismiss (bu-88exay)
#
# These patch the read-model accessors at the router boundary so the endpoint
# logic (idempotency, fail-closed accept, transition guards, audit) is exercised
# in isolation from SQL. The MCP create is driven through the real
# ``_call_mcp_tool`` path via the mocked manager.
# ---------------------------------------------------------------------------

import butlers.api.routers.calendar_workspace as _cw  # noqa: E402
from butlers.api.read_models.calendar_workspace_v1 import (  # noqa: E402
    CalendarProposalRow,
)


def _proposal_dto(
    *,
    status: str = "pending",
    accepted_event_id=None,
    butler_name: str = "general",
    proposal_id=None,
) -> CalendarProposalRow:
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    end = datetime(2026, 2, 22, 15, 0, tzinfo=UTC)
    now = datetime.now(tz=UTC)
    return CalendarProposalRow(
        proposal_id=proposal_id or uuid4(),
        butler_name=butler_name,
        title="Dentist appointment",
        start_at=start,
        end_at=end,
        description="From your inbox",
        location="123 Main St",
        timezone="UTC",
        source_event_id="ingest-evt-1",
        source_snippet="Your appointment is confirmed",
        confidence=0.82,
        entity_ids=[],
        status=status,
        accepted_event_id=accepted_event_id,
        created_at=now,
        updated_at=now,
        db_butler=butler_name,
    )


def _patch_proposal_reads(monkeypatch, *, fetch, update=None):
    monkeypatch.setattr(_cw, "query_calendar_proposal_by_id", fetch)
    if update is not None:
        monkeypatch.setattr(_cw, "update_calendar_proposal_status", update)


async def test_accept_proposal_creates_event_and_flips_status(app, monkeypatch):
    """Accept routes the stored payload through calendar_create_butler_event and
    marks the proposal accepted with the created event id."""
    proposal_id = uuid4()
    event_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)
    accepted = _proposal_dto(status="accepted", accepted_event_id=event_id, proposal_id=proposal_id)

    fetch = AsyncMock(return_value=pending)
    update = AsyncMock(return_value=accepted)
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)

    create_client = AsyncMock()
    create_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "created", "event_id": str(event_id)})
    )
    app, _, mock_mgr = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/accept")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["accepted_event_id"] == str(event_id)

    # Routed through the butler-event create tool (Butlers subcalendar default).
    create_client.call_tool.assert_awaited_once()
    tool_name, args = create_client.call_tool.await_args.args
    assert tool_name == "calendar_create_butler_event"
    assert args["butler_name"] == "general"
    assert "calendar_id" not in args  # never targets the user's primary
    # The proposal's description/location survive accept (bu-cb0ap): they are
    # forwarded to the create tool rather than silently dropped.
    assert args["description"] == "From your inbox"
    assert args["location"] == "123 Main St"

    update.assert_awaited_once()
    assert update.await_args.kwargs["status"] == "accepted"
    assert update.await_args.kwargs["accepted_event_id"] == event_id


async def test_accept_proposal_idempotent_no_second_write(app, monkeypatch):
    """Accepting an already-accepted proposal returns the existing event id with
    no second provider write."""
    proposal_id = uuid4()
    event_id = uuid4()
    accepted = _proposal_dto(status="accepted", accepted_event_id=event_id, proposal_id=proposal_id)

    fetch = AsyncMock(return_value=accepted)
    update = AsyncMock()
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)

    create_client = AsyncMock()
    create_client.call_tool = AsyncMock()
    app, _, _ = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/accept")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["accepted_event_id"] == str(event_id)
    create_client.call_tool.assert_not_awaited()  # no second provider write
    update.assert_not_awaited()


async def test_accept_proposal_fail_closed_on_provider_error(app, monkeypatch):
    """If the provider create fails, the row is NOT marked accepted (stays
    pending) so the user can retry."""
    proposal_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)

    fetch = AsyncMock(return_value=pending)
    update = AsyncMock()
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)

    create_client = AsyncMock()
    create_client.call_tool = AsyncMock(side_effect=RuntimeError("provider boom"))
    app, _, _ = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/accept")

    assert resp.status_code == 503  # provider unreachable / MCP failure
    update.assert_not_awaited()  # row left pending — retry remains possible


async def test_accept_proposal_unknown_id_returns_404(app, monkeypatch):
    fetch = AsyncMock(return_value=None)
    _patch_proposal_reads(monkeypatch, fetch=fetch)
    app, _, _ = _build_app(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{uuid4()}/accept")
    assert resp.status_code == 404


async def test_accept_dismissed_proposal_is_conflict(app, monkeypatch):
    proposal_id = uuid4()
    dismissed = _proposal_dto(status="dismissed", proposal_id=proposal_id)
    fetch = AsyncMock(return_value=dismissed)
    update = AsyncMock()
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)
    create_client = AsyncMock()
    create_client.call_tool = AsyncMock()
    app, _, _ = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/accept")
    assert resp.status_code == 409
    create_client.call_tool.assert_not_awaited()
    update.assert_not_awaited()


async def test_dismiss_proposal_flips_status_no_provider_write(app, monkeypatch):
    proposal_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)
    dismissed = _proposal_dto(status="dismissed", proposal_id=proposal_id)

    fetch = AsyncMock(return_value=pending)
    update = AsyncMock(return_value=dismissed)
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)
    app, _, mock_mgr = _build_app(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/dismiss")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "dismissed"
    assert data["accepted_event_id"] is None
    update.assert_awaited_once()
    assert update.await_args.kwargs["status"] == "dismissed"
    # No provider write — the MCP manager is never asked for a client.
    mock_mgr.get_client.assert_not_awaited()


async def test_dismiss_proposal_idempotent_already_dismissed(app, monkeypatch):
    proposal_id = uuid4()
    dismissed = _proposal_dto(status="dismissed", proposal_id=proposal_id)
    fetch = AsyncMock(return_value=dismissed)
    update = AsyncMock()
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)
    app, _, _ = _build_app(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "dismissed"
    update.assert_not_awaited()


async def test_dismiss_accepted_proposal_is_conflict(app, monkeypatch):
    proposal_id = uuid4()
    accepted = _proposal_dto(status="accepted", accepted_event_id=uuid4(), proposal_id=proposal_id)
    fetch = AsyncMock(return_value=accepted)
    update = AsyncMock()
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)
    app, _, _ = _build_app(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/dismiss")
    assert resp.status_code == 409
    update.assert_not_awaited()


async def test_dismiss_proposal_unknown_id_returns_404(app, monkeypatch):
    fetch = AsyncMock(return_value=None)
    _patch_proposal_reads(monkeypatch, fetch=fetch)
    app, _, _ = _build_app(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{uuid4()}/dismiss")
    assert resp.status_code == 404


async def test_accept_proposal_lost_race_to_dismiss_is_conflict(app, monkeypatch):
    """If a proposal is concurrently dismissed after the guard read, the guarded
    update no-ops (returns None) and the refreshed row is dismissed → 409, not 500."""
    proposal_id = uuid4()
    event_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)
    dismissed = _proposal_dto(status="dismissed", proposal_id=proposal_id)

    # First read sees pending (passes the guard); refresh after the lost-race
    # update sees the concurrently-dismissed row.
    fetch = AsyncMock(side_effect=[pending, dismissed])
    update = AsyncMock(return_value=None)  # guarded update matched no pending row
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)

    create_client = AsyncMock()
    create_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "created", "event_id": str(event_id)})
    )
    app, _, _ = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/accept")
    assert resp.status_code == 409


async def test_dismiss_proposal_lost_race_to_accept_is_conflict(app, monkeypatch):
    """If a proposal is concurrently accepted after the guard read, the guarded
    update no-ops (returns None) and the refreshed row is accepted → 409, not 500."""
    proposal_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)
    accepted = _proposal_dto(status="accepted", accepted_event_id=uuid4(), proposal_id=proposal_id)

    fetch = AsyncMock(side_effect=[pending, accepted])
    update = AsyncMock(return_value=None)
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)
    app, _, _ = _build_app(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/calendar/workspace/proposals/{proposal_id}/dismiss")
    assert resp.status_code == 409


async def test_accept_proposal_applies_inline_overrides(app, monkeypatch):
    """Inline overrides in the request body take precedence over stored values."""
    proposal_id = uuid4()
    event_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)
    accepted = _proposal_dto(status="accepted", accepted_event_id=event_id, proposal_id=proposal_id)

    fetch = AsyncMock(return_value=pending)
    update = AsyncMock(return_value=accepted)
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)

    create_client = AsyncMock()
    create_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "created", "event_id": str(event_id)})
    )
    app, _, _ = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/calendar/workspace/proposals/{proposal_id}/accept",
            json={"title": "Overridden title"},
        )

    assert resp.status_code == 200
    _tool, args = create_client.call_tool.await_args.args
    assert args["title"] == "Overridden title"
    # Unoverridden description/location still fall back to the stored proposal.
    assert args["description"] == "From your inbox"
    assert args["location"] == "123 Main St"


async def test_accept_proposal_overrides_description_and_location(app, monkeypatch):
    """Inline description/location overrides take precedence over stored values (bu-cb0ap)."""
    proposal_id = uuid4()
    event_id = uuid4()
    pending = _proposal_dto(status="pending", proposal_id=proposal_id)
    accepted = _proposal_dto(status="accepted", accepted_event_id=event_id, proposal_id=proposal_id)

    fetch = AsyncMock(return_value=pending)
    update = AsyncMock(return_value=accepted)
    _patch_proposal_reads(monkeypatch, fetch=fetch, update=update)

    create_client = AsyncMock()
    create_client.call_tool = AsyncMock(
        return_value=_mock_mcp_result({"status": "created", "event_id": str(event_id)})
    )
    app, _, _ = _build_app(app, mcp_clients={"general": create_client})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/calendar/workspace/proposals/{proposal_id}/accept",
            json={"description": "Bring insurance card", "location": "Suite 200"},
        )

    assert resp.status_code == 200
    _tool, args = create_client.call_tool.await_args.args
    assert args["description"] == "Bring insurance card"
    assert args["location"] == "Suite 200"
