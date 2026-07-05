"""Tests for GET /api/identity/email-match-rate (bu-qeaou).

Covers:
- 200 with computed match_rate when the relationship pool is reachable.
- Historical raw "Name <addr>" rows normalize + dedupe against bare-address rows.
- aggregates_available=False (degraded) when the relationship butler is absent.
- aggregates_available=False (degraded) when the underlying query raises —
  never rendered as a truthful 0% match rate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.identity import _get_db_manager

pytestmark = pytest.mark.unit


def _app_with_mock_db(app: FastAPI, *, pool=None, has_relationship: bool = True):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship", "switchboard"] if has_relationship else ["switchboard"]
    if pool is not None:
        mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


async def _get(app: FastAPI, params: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/identity/email-match-rate", params=params or {})


async def test_computes_match_rate(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [
                {"raw_address": "john@example.com"},
                {"raw_address": "jane@example.com"},
            ],
            [{"object": "john@example.com"}],
        ]
    )
    _app_with_mock_db(app, pool=pool)

    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["distinct_senders"] == 2
    assert body["matched_senders"] == 1
    assert body["match_rate"] == pytest.approx(0.5)
    assert body["aggregates_available"] is True


async def test_normalizes_and_dedupes_raw_header_rows(app):
    """A raw 'Name <addr>' row must merge with a bare-address row for the same person."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [
                {"raw_address": "John Doe <john@example.com>"},
                {"raw_address": "john@example.com"},
                {"raw_address": "JOHN@EXAMPLE.COM"},
            ],
            [],
        ]
    )
    _app_with_mock_db(app, pool=pool)

    resp = await _get(app)

    body = resp.json()["data"]
    assert body["distinct_senders"] == 1
    assert body["matched_senders"] == 0
    assert body["match_rate"] == pytest.approx(0.0)


async def test_zero_senders_yields_null_match_rate(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, pool=pool)

    resp = await _get(app)

    body = resp.json()["data"]
    assert body["distinct_senders"] == 0
    assert body["match_rate"] is None
    assert body["aggregates_available"] is True


async def test_degrades_when_relationship_butler_absent(app):
    _app_with_mock_db(app, has_relationship=False)

    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["aggregates_available"] is False
    assert body["distinct_senders"] == 0
    assert body["match_rate"] is None


async def test_degrades_on_query_failure_never_renders_as_zero_percent(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    _app_with_mock_db(app, pool=pool)

    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["aggregates_available"] is False
    # A failed source must never render as a truthful 0-match-rate.
    assert body["match_rate"] is None
