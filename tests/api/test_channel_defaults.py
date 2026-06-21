"""Tests for /api/ingestion/channel-defaults endpoints.

Covers:
- GET returns 200 with correct fields when row exists
- GET returns 404 when no row exists for channel
- GET returns 503 on DB unavailable
- PATCH upserts and returns updated document
- PATCH validates per-channel schema (missing fields → 400)
- PATCH rejects unknown channels (400)
- PATCH rejects invalid priority_action (400)
- PATCH returns 503 on DB unavailable
- DELETE returns 405 (no DELETE surface)
- Audit entry emitted on PATCH

§3.9 / §3.12 — Phase 3d (bu-1f91v.9)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.channel_defaults import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _app_with_mock_db(
    app: FastAPI,
    *,
    shared_pool=None,
    shared_pool_error=None,
):
    """Wire the app with a mock DatabaseManager over the shared pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = AsyncMock()
            shared_pool.fetchrow = AsyncMock(return_value=None)
            shared_pool.execute = AsyncMock(return_value=None)
        mock_db.credential_shared_pool.return_value = shared_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


def _make_record(row: dict):
    """Return a MagicMock that supports dict-style item access."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _channel_row(channel: str = "email") -> dict:
    policy = {"priority_action": "pass_through", "max_age_days": 30}
    if channel == "email":
        pass  # already set above
    else:
        policy = {"priority_action": "pass_through"}
    return {
        "channel": channel,
        "default_policy_json": policy,
        "updated_at": _NOW,
        "updated_by": "dashboard",
    }


# ---------------------------------------------------------------------------
# GET /api/ingestion/channel-defaults/{channel}
# ---------------------------------------------------------------------------


async def test_get_channel_default_200(app):
    row = _channel_row("email")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/channel-defaults/email")

    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "email"
    assert "default_policy_json" in body
    assert "updated_at" in body
    assert "updated_by" in body


async def test_get_channel_default_404_missing(app):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/channel-defaults/nonexistent")

    assert resp.status_code == 404


async def test_get_channel_default_503_on_db_unavailable(app):
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/channel-defaults/email")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PATCH /api/ingestion/channel-defaults/{channel}
# ---------------------------------------------------------------------------


async def test_patch_channel_default_emits_audit(app):
    """PATCH upserts (200, channel echoed) and emits the channel_default.update audit."""
    row = _channel_row("telegram")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(row))
    _app_with_mock_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.channel_defaults._audit_append", new_callable=AsyncMock
    ) as mock_audit:
        mock_audit.return_value = 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/ingestion/channel-defaults/telegram",
                json={"default_policy_json": {"priority_action": "pass_through"}},
            )

    assert resp.status_code == 200
    assert resp.json()["channel"] == "telegram"
    # Audit-action enum + target are the contract here (guardrail: audit coverage).
    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.await_args.kwargs
    assert call_kwargs["action"] == "ingestion.channel_default.update"
    assert call_kwargs["target"] == "telegram"


async def test_patch_channel_default_400_missing_required_field(app):
    """PATCH with missing required field should return HTTP 400."""
    pool = AsyncMock()
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # email requires both priority_action and max_age_days
        resp = await client.patch(
            "/api/ingestion/channel-defaults/email",
            json={"default_policy_json": {"max_age_days": 30}},
            # missing priority_action
        )

    assert resp.status_code == 400


async def test_patch_channel_default_400_unknown_channel(app):
    """PATCH with an unknown channel name should return HTTP 400."""
    pool = AsyncMock()
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/ingestion/channel-defaults/foobar_unknown_channel",
            json={"default_policy_json": {"priority_action": "pass_through"}},
        )

    assert resp.status_code == 400


async def test_patch_channel_default_400_invalid_priority_action(app):
    """PATCH with invalid priority_action should return HTTP 400."""
    pool = AsyncMock()
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/ingestion/channel-defaults/telegram",
            json={"default_policy_json": {"priority_action": "route_to:finance"}},
        )

    assert resp.status_code == 400


async def test_patch_channel_default_503_on_db_unavailable(app):
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/ingestion/channel-defaults/telegram",
            json={"default_policy_json": {"priority_action": "pass_through"}},
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /api/ingestion/channel-defaults/{channel} — 405 No DELETE surface
# ---------------------------------------------------------------------------


async def test_delete_channel_default_405(app):
    """DELETE must return HTTP 405 — no DELETE surface per spec."""
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/ingestion/channel-defaults/email")

    assert resp.status_code == 405
