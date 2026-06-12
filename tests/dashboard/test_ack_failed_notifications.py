"""Tests for POST /api/notifications/ack-failed — bulk acknowledge endpoint.

Verifies the three key behaviours:
  1. Updates all failed → read and returns the count.
  2. Invalidates the briefing cache (per-owner when resolved, all otherwise).
  3. Returns zero gracefully when the Switchboard pool is unavailable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.briefing.cache import BriefingCache
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager as _notif_get_db
from butlers.api.routers.notifications import get_cache

pytestmark = pytest.mark.unit


def _make_owner_row(owner_id: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: owner_id if k == "id" else None)
    return row


class TestAckFailedNotifications:
    def _make_app(self, pool: AsyncMock | None, cache: BriefingCache) -> object:
        app = create_app(api_key="")
        mock_db = MagicMock(spec=DatabaseManager)
        if pool is None:
            mock_db.pool.side_effect = KeyError("switchboard")
        else:
            mock_db.pool.return_value = pool
        app.dependency_overrides[_notif_get_db] = lambda: mock_db
        app.dependency_overrides[get_cache] = lambda: cache
        return app

    async def test_ack_failed_returns_acknowledged_count(self):
        """POST /ack-failed returns the number of rows updated."""
        owner_id = "owner-ack-001"

        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 7")
        pool.fetchrow = AsyncMock(return_value=_make_owner_row(owner_id))

        cache = BriefingCache(ttl_seconds=300)
        cache.set(owner_id, {"state_class": "urgent"})

        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/notifications/ack-failed")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["acknowledged"] == 7

    async def test_ack_failed_invalidates_cache_for_resolved_owner(self):
        """POST /ack-failed invalidates the per-owner cache entry."""
        owner_id = "owner-ack-002"

        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 3")
        pool.fetchrow = AsyncMock(return_value=_make_owner_row(owner_id))

        cache = BriefingCache(ttl_seconds=300)
        cache.set(owner_id, {"state_class": "urgent"})

        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/notifications/ack-failed")

        assert cache.get(owner_id) is None

    async def test_ack_failed_invalidates_all_when_owner_not_found(self):
        """When owner lookup returns None, invalidate_all() is called."""
        other_owner = "other-owner-ack"

        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 5")
        pool.fetchrow = AsyncMock(return_value=None)

        cache = BriefingCache(ttl_seconds=300)
        cache.set(other_owner, {"state_class": "quiet"})

        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/notifications/ack-failed")

        assert cache.get(other_owner) is None

    async def test_ack_failed_returns_zero_when_pool_unavailable(self):
        """When the Switchboard pool is unavailable, returns acknowledged=0 (no error)."""
        cache = BriefingCache(ttl_seconds=300)
        app = self._make_app(pool=None, cache=cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/notifications/ack-failed")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["acknowledged"] == 0

    async def test_ack_failed_returns_zero_when_update_returns_none(self):
        """When execute() returns an unexpected value, acknowledged defaults to 0."""
        owner_id = "owner-ack-003"

        pool = AsyncMock()
        pool.execute = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value=_make_owner_row(owner_id))

        cache = BriefingCache(ttl_seconds=300)
        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/notifications/ack-failed")

        assert resp.status_code == 200
        assert resp.json()["data"]["acknowledged"] == 0
