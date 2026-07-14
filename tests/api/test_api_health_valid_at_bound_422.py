"""Regression: health date-filter validation returns a clean 422 (bu-ejmfn).

`_resolve_valid_at_bound` (roster/health/api/router.py) accepts bare
``YYYY-MM-DD`` day keys. A value matching that shape but with an out-of-range
calendar component (``2026-13-40``, ``2026-02-30``) used to skip the explicit
422 branch and reach ``datetime(year, month, day, ...)``, whose bare
``ValueError`` was only caught by the app-wide handler as a 400 — contradicting
the documented 422-on-garbage contract. This mirrors the sessions resolver fix
(PR #3184).

The 422 is raised in the resolver before any DB query, so these are lightweight
mocked-app tests (no testcontainers needed).
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# Trigger router discovery so the health router module is importable from
# sys.modules (FastAPI dependency_overrides keys on object identity).
_APP_SEED = create_app(api_key="")
_health_router = sys.modules["health_api_router"]
_health_get_db = _health_router._get_db_manager

OWNER_TZ = "Asia/Singapore"  # UTC+8, no DST


def _make_app(monkeypatch: pytest.MonkeyPatch):
    """App wired with a mock DB and a fixed owner timezone.

    The invalid-date 422 is raised before any DB fan-out, so the mock pool is
    never queried; the owner-tz resolution is patched to avoid touching it.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = MagicMock()

    async def _fake_resolve(_pool) -> str:
        return OWNER_TZ

    monkeypatch.setattr(_health_router, "resolve_general_timezone", _fake_resolve)

    app = create_app()
    app.dependency_overrides[_health_get_db] = lambda: mock_db
    return app


async def _get(app, url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


@pytest.mark.parametrize("bad", ["2026-13-40", "2026-02-30", "2026-00-10", "2026-01-32"])
async def test_day_key_shaped_invalid_calendar_date_is_a_clean_422(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A YYYY-MM-DD-shaped but out-of-range date must be 422, not a 400 fall-through."""
    app = _make_app(monkeypatch)
    resp = await _get(app, f"/api/health/measurements?since={bad}")
    assert resp.status_code == 422
    # And on the upper bound too (upper=True path).
    resp_upper = await _get(app, f"/api/health/measurements?until={bad}")
    assert resp_upper.status_code == 422


async def test_unparseable_date_is_a_clean_422(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    resp = await _get(app, "/api/health/measurements?since=not-a-date")
    assert resp.status_code == 422


async def test_valid_day_key_still_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard the happy path: a real day key must not be caught by the 422 branch."""
    app = _make_app(monkeypatch)
    # A valid day key gets past the resolver; the (mock) DB fan-out then answers.
    mock_db = app.dependency_overrides[_health_get_db]()
    mock_db.pool.return_value.fetch = AsyncMock(return_value=[])
    mock_db.pool.return_value.fetchval = AsyncMock(return_value=0)
    resp = await _get(app, "/api/health/measurements?since=2026-07-11&until=2026-07-11")
    assert resp.status_code == 200
