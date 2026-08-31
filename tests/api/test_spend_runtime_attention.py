"""Spend durable fleet-halt attention projection contracts.

REQ-dashboard-spend-dashboard-001; REQ-runtime-attention-outbox-003.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.spend import _get_db_manager

pytestmark = pytest.mark.unit


async def test_spend_attention_preserves_empty_vs_unavailable(app, monkeypatch, caplog) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: db
    headers = {"X-API-Key": "owner-key"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        empty = await client.get("/api/spend/runtime-attention", headers=headers)
        pool.fetchrow.side_effect = RuntimeError("protected raw detail")
        unavailable = await client.get("/api/spend/runtime-attention", headers=headers)

    assert empty.status_code == 200
    assert empty.json()["data"] == {"available": True, "episode": None}
    assert unavailable.status_code == 200
    assert unavailable.json()["data"] == {"available": False, "episode": None}
    assert "protected raw detail" not in caplog.text


async def test_spend_attention_is_sanitized_and_owner_gated(app, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    now = datetime(2026, 8, 26, tzinfo=UTC)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "episode_id": UUID("00000000-0000-0000-0000-000000000009"),
            "lifecycle_state": "sent",
            "created_at": now,
            "updated_at": now,
            "delivered_at": now,
            "delivery_error_class": None,
            "delivery_error_detail": None,
        }
    )
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.get("/api/spend/runtime-attention")
        allowed = await client.get(
            "/api/spend/runtime-attention", headers={"X-API-Key": "owner-key"}
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert set(allowed.json()["data"]["episode"]) == {
        "episode_id",
        "lifecycle_state",
        "created_at",
        "updated_at",
        "delivered_at",
        "safe_reason",
    }
    assert pool.fetchrow.await_count == 1
