"""Tests for BriefingCache.invalidate() call-site wiring (bu-qzjpm).

Verifies that the three categories of owner-relevant state changes each
trigger cache invalidation:

  (a) PATCH /api/notifications/{id}/read  — notification mark-as-read
  (b) DashboardAuditMiddleware            — audit_log writes with result='error'

Category (c) — butler_registry eligibility transitions — was consolidated onto
the canonical switchboard route POST /api/switchboard/registry/{name}/eligibility
(bu-bmw1m). Its cache-invalidation coverage now lives in
tests/api/test_switchboard.py (test_set_eligibility_*).

Also tests the new BriefingCache.invalidate_all() method directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.briefing.cache import BriefingCache
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager as _notif_get_db
from butlers.api.routers.notifications import get_cache

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Direct unit tests for BriefingCache.invalidate_all()
# ---------------------------------------------------------------------------


class TestBriefingCacheInvalidateAll:
    def test_invalidate_all_clears_all_entries(self):
        """invalidate_all() removes every cached entry; empty-cache call is a no-op."""
        # Empty cache: must not raise.
        BriefingCache(ttl_seconds=300).invalidate_all()

        cache = BriefingCache(ttl_seconds=300)
        cache.set("owner-a", {"greet": "Good morning.", "state_class": "quiet"})
        cache.set("owner-b", {"greet": "Good afternoon.", "state_class": "urgent"})

        assert cache.get("owner-a") is not None
        assert cache.get("owner-b") is not None

        cache.invalidate_all()

        assert cache.get("owner-a") is None
        assert cache.get("owner-b") is None

    def test_invalidate_all_does_not_affect_subsequent_sets(self):
        """After invalidate_all(), new entries can be set and retrieved."""
        cache = BriefingCache(ttl_seconds=300)
        cache.set("owner-a", {"state_class": "quiet"})
        cache.invalidate_all()
        cache.set("owner-a", {"state_class": "urgent"})

        result = cache.get("owner-a")
        assert result is not None
        assert result["state_class"] == "urgent"


# ---------------------------------------------------------------------------
# (a) Notification mark-as-read — PATCH /api/notifications/{id}/read
# ---------------------------------------------------------------------------


def _make_notification_row(notification_id: uuid.UUID) -> MagicMock:
    """Return an asyncpg-like record for a notification row."""
    row = MagicMock()
    fields = {
        "id": notification_id,
        "source_butler": "calendar",
        "channel": "telegram",
        "recipient": "+1234567890",
        "message": "Calendar sync failed",
        "metadata": {},
        "status": "read",
        "error": None,
        "session_id": None,
        "trace_id": None,
        "created_at": datetime(2026, 5, 16, 10, 0, 0, tzinfo=UTC),
    }
    row.__getitem__ = MagicMock(side_effect=lambda k: fields[k])
    return row


def _make_owner_row(owner_id: str) -> MagicMock:
    """Return an asyncpg-like record for an owner contact."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: owner_id if k == "id" else None)
    return row


class TestNotificationMarkAsReadInvalidatesCache:
    def _make_app(self, pool: AsyncMock, cache: BriefingCache) -> object:
        app = create_app(api_key="")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = pool
        app.dependency_overrides[_notif_get_db] = lambda: mock_db
        app.dependency_overrides[get_cache] = lambda: cache
        return app

    async def test_mark_as_read_invalidates_cache_for_resolved_owner(self):
        """PATCH /read marks the notification read and calls cache.invalidate(owner_id)."""
        notification_id = uuid.uuid4()
        owner_id = "owner-uuid-001"

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _make_notification_row(notification_id),  # UPDATE RETURNING
                _make_owner_row(owner_id),  # owner lookup
            ]
        )

        cache = BriefingCache(ttl_seconds=300)
        cache.set(owner_id, {"state_class": "urgent", "greet": "Good morning."})

        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/notifications/{notification_id}/read")

        assert resp.status_code == 200
        # Cache entry must be gone after mark-as-read.
        assert cache.get(owner_id) is None

    async def test_mark_as_read_invalidates_all_when_owner_not_found(self):
        """When owner lookup returns None, invalidate_all() is called."""
        notification_id = uuid.uuid4()
        other_owner = "other-owner-id"

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _make_notification_row(notification_id),  # UPDATE RETURNING
                None,  # owner lookup returns None
            ]
        )

        cache = BriefingCache(ttl_seconds=300)
        cache.set(other_owner, {"state_class": "quiet"})

        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/notifications/{notification_id}/read")

        assert resp.status_code == 200
        # invalidate_all() clears everything.
        assert cache.get(other_owner) is None

    async def test_mark_as_read_returns_404_when_not_found(self):
        """PATCH /read returns 404 when the notification id is not in the DB."""
        notification_id = uuid.uuid4()

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)

        cache = BriefingCache(ttl_seconds=300)
        app = self._make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/notifications/{notification_id}/read")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# (b) Audit middleware — result='error' invalidates cache
# ---------------------------------------------------------------------------


class TestAuditMiddlewareInvalidatesOnError:
    def _make_app_with_cache(self, cache: BriefingCache) -> tuple:
        app = create_app(api_key="")
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool
        return app, mock_db, mock_pool

    async def test_error_response_invalidates_cache(self):
        """A 4xx/5xx API response invalidates the briefing cache."""
        owner_id = "owner-middleware-test"
        cache = BriefingCache(ttl_seconds=300)
        cache.set(owner_id, {"state_class": "quiet"})

        app, mock_db, mock_pool = self._make_app_with_cache(cache)

        @app.post("/api/test-error-endpoint")
        async def _error_endpoint():
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="bad input")

        with (
            patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db),
            patch("butlers.api.dashboard_audit_middleware.get_cache", return_value=cache),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/test-error-endpoint")

        assert resp.status_code == 422
        # Cache must be cleared because the audit row had result='error'.
        assert cache.get(owner_id) is None

    async def test_success_response_does_not_invalidate_cache(self):
        """A 2xx API response does NOT invalidate the briefing cache."""
        owner_id = "owner-middleware-success"
        cache = BriefingCache(ttl_seconds=300)
        cache.set(owner_id, {"state_class": "quiet"})

        app, mock_db, mock_pool = self._make_app_with_cache(cache)

        @app.post("/api/test-success-endpoint")
        async def _success_endpoint():
            return {"ok": True}

        with (
            patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db),
            patch("butlers.api.dashboard_audit_middleware.get_cache", return_value=cache),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/test-success-endpoint")

        assert resp.status_code == 200
        # Cache must NOT be cleared for a successful response.
        assert cache.get(owner_id) is not None
