"""Tests for data operations API (§6.5 + §6.7).

Covers:
- POST /api/data/export returns a signed URL and calls audit.append.
- DELETE /api/data/wipe: exact phrase passes.
- DELETE /api/data/wipe: trailing whitespace fails.
- DELETE /api/data/wipe: lowercase phrase fails.
- DELETE /api/data/wipe: missing phrase field returns 422.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.data_ops import _get_db_manager

pytestmark = pytest.mark.unit

_EXACT_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"


def _make_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])  # no butler schemas
    return pool


def _make_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/data/export
# ---------------------------------------------------------------------------


async def test_export_returns_signed_url(app):
    """POST /api/data/export returns a signed URL with 60-minute TTL."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/data/export", json={"scope": "all"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "signed_url" in data
    assert data["scope"] == "all"
    assert "expires_at" in data


async def test_export_calls_audit(app):
    """POST /api/data/export calls audit.append with action=data.export."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/data/export", json={"scope": "contacts"})

    mock_audit.assert_called_once()
    call_args = mock_audit.call_args
    # pool, actor, action are positional; note is keyword-only
    assert call_args.args[2] == "data.export"
    assert call_args.kwargs["note"] == "contacts"


# ---------------------------------------------------------------------------
# DELETE /api/data/wipe — phrase validation (§6.7)
# ---------------------------------------------------------------------------


async def test_wipe_exact_phrase_passes(app):
    """Exact phrase allows the wipe to proceed."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.request(
                "DELETE",
                "/api/data/wipe",
                json={"phrase": _EXACT_PHRASE},
            )

    assert resp.status_code == 200
    assert resp.json()["data"]["wiped"] is True


async def test_wipe_trailing_whitespace_fails(app):
    """Phrase with trailing whitespace is rejected (no trim)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={"phrase": _EXACT_PHRASE + " "},
        )

    assert resp.status_code == 422


async def test_wipe_lowercase_phrase_fails(app):
    """Lowercase phrase is rejected (no case-fold)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={"phrase": _EXACT_PHRASE.lower()},
        )

    assert resp.status_code == 422


async def test_wipe_missing_phrase_returns_422(app):
    """Missing phrase field returns 422 (Pydantic validation error)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={},
        )

    assert resp.status_code == 422


async def test_wipe_leading_whitespace_fails(app):
    """Phrase with leading whitespace is rejected."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={"phrase": " " + _EXACT_PHRASE},
        )

    assert resp.status_code == 422
