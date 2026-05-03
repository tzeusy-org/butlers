"""Tests for ingestion events API endpoints.

Condensed from 47 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: paginated list 200 + 503, event detail 200 + 404 (combined), status/uuid validation 422 (parametrized).
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
    return app


# ---------------------------------------------------------------------------
# List + 503 fallback
# ---------------------------------------------------------------------------


async def test_list_returns_paginated_and_503_fallback(app):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/events")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body

    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/ingestion/events")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Event detail — 200 found, 404 not found
# ---------------------------------------------------------------------------


async def test_event_detail_200_and_404(app):
    event_id = str(uuid4())
    pool_found = AsyncMock()
    pool_found.fetchrow = AsyncMock(return_value=_make_event_row(event_id=event_id))
    pool_found.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool_found)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_ok = await client.get(f"/api/ingestion/events/{event_id}")
    assert resp_ok.status_code == 200

    pool_missing = AsyncMock()
    pool_missing.fetchrow = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool_missing)
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
