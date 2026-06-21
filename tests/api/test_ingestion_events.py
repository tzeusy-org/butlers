"""Tests for ingestion events API endpoints.

Condensed from 47 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: cursor-paginated list 200 + 503, event detail 200 + 404 (combined),
       status/uuid validation 422 (parametrized).

bu-ty7gh: adds audit-log assertions and decomposition_output gate tests.
bu-1f91v.3: list response now uses cursor pagination (next_cursor, has_more) — no total field.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.pricing import PricingConfig
from butlers.api.routers.ingestion_events import _get_db_manager, _get_rollup_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_event_row(*, event_id=None, status="ingested"):
    return {
        "id": event_id or str(uuid4()),
        "received_at": _NOW,
        "source_channel": "telegram_bot",
        "source_provider": "telegram",
        "source_endpoint_identity": None,
        "source_sender_identity": None,
        "source_thread_identity": None,
        "external_event_id": None,
        "dedupe_key": None,
        "dedupe_strategy": None,
        "ingestion_tier": None,
        "policy_tier": None,
        "triage_decision": "accepted",
        "triage_target": "atlas",
        "status": status,
        "filter_reason": None,
        "error_detail": None,
    }


def _app_with_mock_db(app: FastAPI, *, shared_pool=None, shared_pool_error=None):
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = AsyncMock()
            shared_pool.fetchval = AsyncMock(return_value=0)
            shared_pool.fetch = AsyncMock(return_value=[])
            shared_pool.fetchrow = AsyncMock(return_value=None)
            shared_pool.execute = AsyncMock(return_value=None)
        mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.fan_out = AsyncMock(return_value={})
    mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_pricing] = lambda: PricingConfig(models={})
    return mock_db


# ---------------------------------------------------------------------------
# List + 503 fallback
# ---------------------------------------------------------------------------


async def test_list_returns_cursor_paginated_and_503_fallback(app):
    """GET /api/ingestion/events returns cursor-paginated envelope (no total field)."""
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/events")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    meta = body["meta"]
    # Cursor pagination: next_cursor + has_more; no total field.
    assert "next_cursor" in meta
    assert "has_more" in meta
    assert "total" not in meta

    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/ingestion/events")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Channel filter — channels CSV, source_channel compat, precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_channels",
    [
        ("?channels=email,telegram", ["email", "telegram"]),
        ("?source_channel=email", ["email"]),
        # channels wins over deprecated source_channel when both are set
        ("?channels=email&source_channel=telegram", ["email"]),
        # empty channels= is treated as no filter
        ("?channels=", None),
        # unknown channel forwarded verbatim (200, empty result — not an error)
        ("?channels=nonexistent_channel", ["nonexistent_channel"]),
        # neither param → None
        ("", None),
    ],
    ids=[
        "channels-csv",
        "source_channel-compat",
        "channels-wins",
        "empty-no-filter",
        "unknown-forwarded",
        "absent-none",
    ],
)
async def test_channels_filter_forwarding(app, query, expected_channels):
    """channels/source_channel resolve to the `channels` kwarg forwarded to core."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events{query}")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("channels") == expected_channels


# ---------------------------------------------------------------------------
# Status filter — statuses CSV, precedence over single status
# ---------------------------------------------------------------------------


async def test_statuses_param_forwarded_to_core(app):
    """GET /api/ingestion/events?statuses=ingested,error passes list to core;
    'skipped' is accepted both as CSV member and as a single status value."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?statuses=ingested,error")
            resp_skipped = await client.get("/api/ingestion/events?status=skipped")

    assert resp.status_code == 200
    first_kwargs = mock_list.await_args_list[0].kwargs
    assert first_kwargs.get("statuses") == ["ingested", "error"]

    assert resp_skipped.status_code == 200
    second_kwargs = mock_list.await_args_list[1].kwargs
    assert second_kwargs.get("status") == "skipped"


async def test_statuses_and_status_both_forwarded(app):
    """When both statuses and status are set, both reach core (core prefers statuses)."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?statuses=ingested&status=error")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("statuses") == ["ingested"]
    assert call_kwargs.get("status") == "error"


# ---------------------------------------------------------------------------
# Event detail — 200 found, 404 not found
# ---------------------------------------------------------------------------


async def test_event_detail_200_and_404(app):
    event_id = str(uuid4())
    pool_found = AsyncMock()
    pool_found.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))
    pool_found.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool_found)
    with patch("butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_ok = await client.get(f"/api/ingestion/events/{event_id}")
    assert resp_ok.status_code == 200

    pool_missing = AsyncMock()
    pool_missing.fetchrow = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool_missing)
    with patch("butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_404 = await client.get(f"/api/ingestion/events/{uuid4()}")
    assert resp_404.status_code == 404


# ---------------------------------------------------------------------------
# Validation errors (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/ingestion/events/not-a-uuid", 422),
        ("/api/ingestion/events?status=invalid_status", 422),
    ],
    ids=["bad-uuid-422", "bad-status-422"],
)
async def test_ingestion_validation_errors(app, path, expected):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# Audit log fires on GET /api/ingestion/events/{request_id}
# ---------------------------------------------------------------------------


async def test_event_detail_emits_audit_log(app):
    """GET detail must emit an audit log entry before returning the payload."""
    event_id = str(uuid4())
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))
    pool.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{event_id}")

    assert resp.status_code == 200
    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.await_args.kwargs
    assert call_kwargs["operation"] == "ingestion.event.payload_fetch"
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["path_params"] == {"request_id": event_id}
    assert call_kwargs["body"]["reason"] == "detail_view"


# ---------------------------------------------------------------------------
# decomposition_output is omitted by default and included only with ?include=decomposition
# ---------------------------------------------------------------------------


async def _detail_response(app, event_id: str, url: str) -> dict:
    """Helper: perform GET and return parsed JSON body."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(url)
    assert resp.status_code == 200
    return resp.json()


async def test_decomposition_output_omitted_by_default(app):
    """decomposition_output must be null in the default response (no ?include=decomposition)."""
    event_id = str(uuid4())
    # Provide an inbox lifecycle row that contains decomposition_output.
    inbox_row = MagicMock()
    inbox_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "lifecycle_state": "classified",
            "decomposition_output": '{"intent": "question"}',
        }[key]
    )

    main_pool = AsyncMock()
    main_pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))
    main_pool.fetch = AsyncMock(return_value=[])

    switchboard_pool = AsyncMock()
    switchboard_pool.fetchrow = AsyncMock(return_value=inbox_row)

    mock_db = _app_with_mock_db(app, shared_pool=main_pool)
    # Override pool() so switchboard lookup succeeds.
    mock_db.pool.side_effect = lambda name: (
        switchboard_pool if name == "switchboard" else (_ for _ in ()).throw(KeyError(name))
    )

    with patch("butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock):
        body = await _detail_response(app, event_id, f"/api/ingestion/events/{event_id}")

    assert body["data"]["decomposition_output"] is None
    assert body["data"]["lifecycle_state"] == "classified"


async def test_decomposition_output_included_with_flag(app):
    """decomposition_output is returned when ?include=decomposition is passed; audit reason changes."""
    event_id = str(uuid4())
    inbox_row = MagicMock()
    inbox_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "lifecycle_state": "classified",
            "decomposition_output": '{"intent": "question"}',
        }[key]
    )

    main_pool = AsyncMock()
    main_pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))
    main_pool.fetch = AsyncMock(return_value=[])

    switchboard_pool = AsyncMock()
    switchboard_pool.fetchrow = AsyncMock(return_value=inbox_row)

    mock_db = _app_with_mock_db(app, shared_pool=main_pool)
    mock_db.pool.side_effect = lambda name: (
        switchboard_pool if name == "switchboard" else (_ for _ in ()).throw(KeyError(name))
    )

    with patch(
        "butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock
    ) as mock_audit:
        body = await _detail_response(
            app, event_id, f"/api/ingestion/events/{event_id}?include=decomposition"
        )

    # decomposition_output must be present (and non-null from the inbox row).
    assert body["data"]["decomposition_output"] is not None

    # Audit reason must reflect the broader disclosure.
    call_kwargs = mock_audit.await_args.kwargs
    assert call_kwargs["body"]["reason"] == "decomposition_disclosed"


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{id}/replays — replay history from public.audit_log
# ---------------------------------------------------------------------------


async def test_replays_returns_empty_list_when_no_audit_entries(app):
    """GET /replays returns an empty list when no audit_log entries exist."""
    event_id = str(uuid4())
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}/replays")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


async def test_replays_returns_audit_log_entries(app):
    """GET /replays returns entries sourced from public.audit_log."""
    event_id = str(uuid4())
    import json

    ts = _NOW
    audit_row = MagicMock()
    audit_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "ts": ts,
            "actor": "dashboard",
            "note": json.dumps({"result": "pending", "source": "filtered_events"}),
        }[key]
    )

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[audit_row])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}/replays")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    entry = body["data"][0]
    assert entry["actor"] == "dashboard"
    assert entry["result"] == "pending"


async def test_replays_503_on_missing_pool(app):
    """GET /replays returns 503 when the shared pool is unavailable."""
    event_id = str(uuid4())
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}/replays")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{id}/sender-contact — contact resolution
# ---------------------------------------------------------------------------


async def test_sender_contact_resolved(app):
    """GET /sender-contact resolves sender to contact name when found."""
    event_id = str(uuid4())
    pool = AsyncMock()
    # ingestion_event_get returns the event row with sender identity set
    row = _make_event_row(event_id=event_id)
    row["source_sender_identity"] = "alice@example.com"
    pool.fetchrow = AsyncMock(return_value=row)

    # resolve_contact_by_channel returns None (default) — we'll patch it
    _app_with_mock_db(app, shared_pool=pool)

    from uuid import uuid4 as _uuid4

    from butlers.identity import ResolvedContact

    resolved = ResolvedContact(
        contact_id=_uuid4(),
        name="Alice Smith",
        roles=[],
        entity_id=None,
    )

    with patch(
        "butlers.api.routers.ingestion_events.resolve_contact_by_channel",
        new_callable=AsyncMock,
        return_value=resolved,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{event_id}/sender-contact")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["resolved"] is True
    assert body["data"]["name"] == "Alice Smith"


async def test_sender_contact_unresolved(app):
    """GET /sender-contact returns resolved=False when no contact matches."""
    event_id = str(uuid4())
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))
    _app_with_mock_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events.resolve_contact_by_channel",
        new_callable=AsyncMock,
        return_value=None,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{event_id}/sender-contact")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["resolved"] is False
    assert body["data"]["name"] is None


async def test_sender_contact_404_on_missing_event(app):
    """GET /sender-contact returns 404 when the event does not exist."""
    event_id = str(uuid4())
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}/sender-contact")

    assert resp.status_code == 404


async def test_replay_post_writes_audit_log(app):
    """POST /{id}/replay appends an entry to public.audit_log on success."""
    event_id = str(uuid4())
    pool = AsyncMock()
    # ingestion_event_replay_request result — simulate filtered event replay
    pool.fetchrow = AsyncMock(
        return_value=MagicMock(**{"__getitem__": MagicMock(return_value=event_id)})
    )
    _app_with_mock_db(app, shared_pool=pool)

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_replay_request",
            new_callable=AsyncMock,
            return_value={"outcome": "ok", "id": event_id, "source": "filtered_events"},
        ),
        patch(
            "butlers.api.routers.ingestion_events._audit_append",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_audit_append,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/ingestion/events/{event_id}/replay")

    assert resp.status_code == 200
    mock_audit_append.assert_awaited_once()
    call_kwargs = mock_audit_append.await_args.kwargs
    assert call_kwargs["action"] == "ingestion.event.replay"
    assert call_kwargs["target"] == event_id


# ---------------------------------------------------------------------------
# ?q= search parameter on GET /api/ingestion/events (bu-mxtn2)
# ---------------------------------------------------------------------------


async def test_list_events_passes_q_param_to_core(app):
    """GET /api/ingestion/events?q=foo passes q to ingestion_events_list."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?q=hello")

    assert resp.status_code == 200
    # Verify q was forwarded to the core function
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("q") == "hello"


async def test_list_events_q_absent_passes_none(app):
    """GET /api/ingestion/events without ?q= passes q=None."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("q") is None


# ---------------------------------------------------------------------------
# GET /api/ingestion/rollup (bu-mxtn2)
# ---------------------------------------------------------------------------


def _app_with_mock_rollup_db(app: FastAPI, *, shared_pool=None, shared_pool_error=None):
    """Override the rollup router's DB dependency stub."""
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = AsyncMock()
            shared_pool.fetchval = AsyncMock(return_value=0)
            shared_pool.fetch = AsyncMock(return_value=[])
        mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_rollup_db_manager] = lambda: mock_db
    return mock_db


async def test_rollup_returns_correct_shape(app):
    """GET /api/ingestion/rollup returns {events, sessions, cost, window}."""
    _app_with_mock_rollup_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_window_rollup",
        new_callable=AsyncMock,
        return_value={
            "events": 42,
            "sessions": 7,
            "cost": None,
            "window": {"from": None, "to": None},
        },
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/rollup")

    assert resp.status_code == 200
    body = resp.json()
    assert body["events"] == 42
    assert body["sessions"] == 7
    assert body["cost"] is None
    assert "window" in body


async def test_rollup_passes_filters_to_core(app):
    """GET /api/ingestion/rollup forwards all filter params to ingestion_window_rollup."""
    _app_with_mock_rollup_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_window_rollup",
        new_callable=AsyncMock,
        return_value={
            "events": 0,
            "sessions": 0,
            "cost": None,
            "window": {"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00"},
        },
    ) as mock_rollup:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/ingestion/rollup"
                "?from=2026-01-01T00:00:00Z"
                "&to=2026-01-02T00:00:00Z"
                "&channels=email,telegram"
                "&statuses=ingested,error"
                "&q=test+query"
            )

    assert resp.status_code == 200
    assert mock_rollup.await_args is not None
    call_kwargs = mock_rollup.await_args.kwargs
    # channels and statuses are passed as lists after CSV parsing
    assert call_kwargs.get("channels") == ["email", "telegram"]
    assert call_kwargs.get("statuses") == ["ingested", "error"]
    assert call_kwargs.get("q") == "test query"


async def test_rollup_503_on_db_unavailable(app):
    """GET /api/ingestion/rollup returns 503 when shared pool unavailable."""
    _app_with_mock_rollup_db(app, shared_pool_error=KeyError("no shared pool"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/rollup")

    assert resp.status_code == 503


async def test_rollup_missing_cost_returns_null(app):
    """GET /api/ingestion/rollup passes null cost through when core returns None."""
    _app_with_mock_rollup_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_window_rollup",
        new_callable=AsyncMock,
        return_value={
            "events": 10,
            "sessions": 2,
            "cost": None,
            "window": {"from": None, "to": None},
        },
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/rollup")

    assert resp.status_code == 200
    assert resp.json()["cost"] is None


# ---------------------------------------------------------------------------
# GET /api/ingestion/events/{id}/payload — raw payload endpoint (bu-9kn9t)
# ---------------------------------------------------------------------------


def _app_with_switchboard_pool(
    app: FastAPI, *, main_pool=None, switchboard_pool=None, main_pool_error=None
):
    """Wire mock_db with both credential shared pool and switchboard pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    if main_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = main_pool_error
    else:
        if main_pool is None:
            main_pool = AsyncMock()
        mock_db.credential_shared_pool.return_value = main_pool
    if switchboard_pool is not None:
        mock_db.pool.side_effect = lambda name: (
            switchboard_pool if name == "switchboard" else (_ for _ in ()).throw(KeyError(name))
        )
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_pricing] = lambda: PricingConfig(models={})
    return mock_db


async def test_payload_200_returns_content_and_emits_audit(app):
    """GET /payload returns 200 with content + audit log entry when event and inbox row exist."""
    event_id = str(uuid4())

    main_pool = AsyncMock()
    main_pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))

    import json as _json

    inbox_row = MagicMock()
    inbox_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "raw_payload": _json.dumps({"content": "hello world", "metadata": {}}),
            "source_channel": "telegram_bot",
        }[key]
    )
    inbox_row.get = MagicMock(
        side_effect=lambda key, default=None: {
            "raw_payload": _json.dumps({"content": "hello world", "metadata": {}}),
            "source_channel": "telegram_bot",
        }.get(key, default)
    )

    switchboard_pool = AsyncMock()
    switchboard_pool.fetchrow = AsyncMock(return_value=inbox_row)

    _app_with_switchboard_pool(app, main_pool=main_pool, switchboard_pool=switchboard_pool)

    with patch(
        "butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{event_id}/payload")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["content"] == "hello world"
    assert body["data"]["truncated"] is False
    assert body["data"]["bytes"] > 0
    # Audit must have fired
    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.await_args.kwargs
    assert call_kwargs["operation"] == "ingestion.event.payload_read"
    assert call_kwargs["response_status"] == 200


async def test_payload_200_envelope_without_content_key(app):
    """Connector envelopes (no top-level 'content' key) must not report 0 bytes.

    home_assistant/wellness events store the full envelope
    (event/sender/source/control/payload) with no ``content`` key. The reader
    must fall back to the whole payload rather than returning an empty string.
    """
    event_id = str(uuid4())

    main_pool = AsyncMock()
    main_pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))

    import json as _json

    envelope = {
        "event": {"external_event_id": "ha:sensor.weight:1"},
        "source": {"channel": "wellness", "provider": "home_assistant"},
        "payload": {"raw": {"new_state": {"state": "69.35"}}},
    }
    inbox_data = {"raw_payload": _json.dumps(envelope), "source_channel": "wellness"}

    inbox_row = MagicMock()
    inbox_row.__getitem__ = MagicMock(side_effect=lambda key: inbox_data[key])
    inbox_row.get = MagicMock(side_effect=lambda key, default=None: inbox_data.get(key, default))

    switchboard_pool = AsyncMock()
    switchboard_pool.fetchrow = AsyncMock(return_value=inbox_row)

    _app_with_switchboard_pool(app, main_pool=main_pool, switchboard_pool=switchboard_pool)

    with patch("butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{event_id}/payload")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["bytes"] > 0
    assert "69.35" in body["data"]["content"]
    assert body["data"]["channel"] == "wellness"


async def test_payload_404_when_event_missing(app):
    """GET /payload returns 404 when the event does not exist in ingestion_events."""
    main_pool = AsyncMock()
    main_pool.fetchrow = AsyncMock(return_value=None)

    switchboard_pool = AsyncMock()
    switchboard_pool.fetchrow = AsyncMock(return_value=None)

    _app_with_switchboard_pool(app, main_pool=main_pool, switchboard_pool=switchboard_pool)

    with patch("butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{uuid4()}/payload")

    assert resp.status_code == 404


async def test_payload_503_when_switchboard_unavailable(app):
    """GET /payload returns 503 when the switchboard pool is not accessible."""
    event_id = str(uuid4())
    main_pool = AsyncMock()
    main_pool.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))

    # No switchboard pool — pool() raises KeyError
    _app_with_switchboard_pool(app, main_pool=main_pool, switchboard_pool=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}/payload")

    assert resp.status_code == 503


async def test_payload_503_when_main_pool_unavailable(app):
    """GET /payload returns 503 when the credential shared pool is not accessible."""
    event_id = str(uuid4())
    _app_with_switchboard_pool(app, main_pool_error=KeyError("no shared pool"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}/payload")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# sort=cost parameter — GET /api/ingestion/events (core_126)
# ---------------------------------------------------------------------------


async def test_sort_cost_forwarded_to_core(app):
    """GET /api/ingestion/events?sort=cost passes sort='cost' to ingestion_events_list."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?sort=cost")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("sort") == "cost"


async def test_sort_cost_invalid_cursor_returns_422(app):
    """GET /api/ingestion/events?sort=cost with a keyset cursor returns 422."""
    _app_with_mock_db(app)

    # A valid keyset cursor (wrong type for cost sort)
    import base64
    import json

    keyset_cursor = base64.urlsafe_b64encode(
        json.dumps({"ra": "2026-01-01T00:00:00+00:00", "id": str(uuid4())}).encode()
    ).decode()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events?sort=cost&cursor={keyset_cursor}")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# cost_usd write-back — GET /api/ingestion/events/{id}/rollup (core_126)
# ---------------------------------------------------------------------------


async def test_event_rollup_writes_cost_usd_back(app):
    """GET /api/ingestion/events/{id}/rollup writes cost_usd to ingestion_events when
    sessions are found (lazy write-through, core_126)."""
    from uuid import uuid4 as _uuid4

    request_id = str(_uuid4())
    shared_pool = AsyncMock()
    shared_pool.execute = AsyncMock(return_value="UPDATE 1")
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_pricing] = lambda: PricingConfig(models={})

    rollup_with_sessions = {
        "request_id": request_id,
        "total_sessions": 2,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "total_cost": 0.0042,
        "by_butler": {
            "atlas": {"sessions": 2, "input_tokens": 100, "output_tokens": 50, "cost": 0.0042}
        },
    }

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_sessions",
            new_callable=AsyncMock,
            return_value=[{"id": str(_uuid4()), "butler_name": "atlas", "cost_usd": 0.0042}],
        ),
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_rollup",
            return_value=rollup_with_sessions,
        ),
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_set_cost_usd",
            new_callable=AsyncMock,
        ) as mock_set_cost,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{request_id}/rollup")

    assert resp.status_code == 200
    # cost_usd write-back must have been called with the total cost
    mock_set_cost.assert_awaited_once()
    call_args = mock_set_cost.await_args
    assert call_args.args[1] == request_id
    assert abs(call_args.args[2] - 0.0042) < 1e-9


async def test_event_rollup_skips_write_when_no_sessions(app):
    """GET /api/ingestion/events/{id}/rollup does NOT write cost_usd when no sessions found."""
    from uuid import uuid4 as _uuid4

    request_id = str(_uuid4())
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = AsyncMock()
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_pricing] = lambda: PricingConfig(models={})

    rollup_empty = {
        "request_id": request_id,
        "total_sessions": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,
        "by_butler": {},
    }

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_sessions",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_rollup",
            return_value=rollup_empty,
        ),
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_set_cost_usd",
            new_callable=AsyncMock,
        ) as mock_set_cost,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{request_id}/rollup")

    assert resp.status_code == 200
    # No write-back when total_sessions == 0
    mock_set_cost.assert_not_awaited()
