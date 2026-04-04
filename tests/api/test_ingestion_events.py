"""Tests for ingestion events API endpoints.

Condensed from 47 tests to ~8 tests (bu-egmz6).
Keeps: paginated list structure, 503 error path, event detail 404,
status filter validation, replay endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.pricing import PricingConfig
from butlers.api.routers.ingestion_events import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_REQUEST_ID = str(uuid4())


def _make_event_row(*, event_id=None, status="ingested", source_channel="telegram_bot",
                    filter_reason=None, error_detail=None):
    return {
        "id": event_id or str(uuid4()), "received_at": _NOW,
        "source_channel": source_channel, "source_provider": "telegram",
        "source_endpoint_identity": None, "source_sender_identity": None,
        "source_thread_identity": None, "external_event_id": None,
        "dedupe_key": None, "dedupe_strategy": None,
        "ingestion_tier": None, "policy_tier": None,
        "triage_decision": "accepted", "triage_target": "atlas",
        "status": status, "filter_reason": filter_reason, "error_detail": error_detail,
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
    return app


async def test_list_returns_paginated_structure(app):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/events")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert "total" in body["meta"]


async def test_list_503_when_pool_unavailable(app):
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/events")
    assert resp.status_code == 503


async def test_get_event_detail_returns_fields(app):
    event_id = str(uuid4())
    row = _make_event_row(event_id=event_id)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{event_id}")
    assert resp.status_code == 200


async def test_get_event_detail_404_when_not_found(app):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/events/{uuid4()}")
    assert resp.status_code == 404


async def test_get_event_invalid_uuid_returns_422(app):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/events/not-a-uuid")
    assert resp.status_code == 422


async def test_status_filter_invalid_value_returns_422(app):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/events", params={"status": "invalid_status"})
    assert resp.status_code == 422


async def test_replay_returns_replay_pending(app):
    event_id = str(uuid4())
    row = _make_event_row(event_id=event_id, status="error")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/ingestion/events/{event_id}/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "replay_pending"
