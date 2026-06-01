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


async def test_channels_param_forwarded_to_core(app):
    """GET /api/ingestion/events?channels=email,telegram passes list to ingestion_events_list."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?channels=email,telegram")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("channels") == ["email", "telegram"]


async def test_source_channel_compat_forwarded_as_list(app):
    """GET /api/ingestion/events?source_channel=email passes single-element list (compat)."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?source_channel=email")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("channels") == ["email"]


async def test_channels_wins_over_source_channel(app):
    """channels param takes precedence over deprecated source_channel when both are set."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?channels=email&source_channel=telegram")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    # channels wins — source_channel=telegram is ignored
    assert call_kwargs.get("channels") == ["email"]


async def test_empty_channels_param_means_no_filter(app):
    """channels= (empty string) is treated as no filter (no channel restriction)."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?channels=")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("channels") is None


async def test_unknown_channel_returns_200_no_error(app):
    """An unknown/invalid channel value returns 200 with an empty result, not an error."""
    _app_with_mock_db(app)

    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None, "has_more": False},
    ) as mock_list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events?channels=nonexistent_channel")

    assert resp.status_code == 200
    call_kwargs = mock_list.await_args.kwargs
    assert call_kwargs.get("channels") == ["nonexistent_channel"]


async def test_no_channel_param_passes_none(app):
    """When neither channels nor source_channel is provided, None is passed to core."""
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
    assert call_kwargs.get("channels") is None


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
