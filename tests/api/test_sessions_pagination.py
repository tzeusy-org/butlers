"""Tests for /api/sessions and /api/butlers/{name}/sessions pagination.

Verifies:
- Backend accepts limit up to 1000 (raised from 200)
- limit > 1000 is rejected with 422
- Pagination meta (has_more, total, offset, limit) is correct
- Butler-scoped endpoint also accepts limit up to 1000
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_row(*, butler: str = "atlas") -> dict:
    return {
        "id": uuid4(),
        "prompt": "test prompt",
        "trigger_source": "api",
        "request_id": None,
        "success": True,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_ms": 500,
        "model": "claude-sonnet",
        "complexity": None,
        "input_tokens": 1234,
        "output_tokens": 567,
    }


def _make_app_with_sessions(rows: list[dict], *, total: int | None = None) -> object:
    """Wire a fresh app with mock fan_out returning the given rows."""
    if total is None:
        total = len(rows)

    def _make_record(row: dict):
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    # fan_out returns {butler_name: [rows]}
    mock_db.fan_out = AsyncMock(
        side_effect=lambda sql, args, **kw: (
            {"atlas": [[total]]} if "count" in sql else {"atlas": [_make_record(r) for r in rows]}
        )
    )

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


def _make_butler_app_with_sessions(rows: list[dict], *, total: int | None = None) -> object:
    """Wire a fresh app with mock pool for butler-scoped endpoint."""
    if total is None:
        total = len(rows)

    def _make_record(row: dict):
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=[_make_record(r) for r in rows])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Cross-butler /api/sessions pagination tests
# ---------------------------------------------------------------------------


async def test_sessions_accepts_limit_500() -> None:
    """GET /api/sessions accepts limit=500 (above old 200 cap, within new 1000 cap)."""
    app = _make_app_with_sessions([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=500")
    assert resp.status_code == 200


async def test_sessions_accepts_limit_1000() -> None:
    """GET /api/sessions accepts limit=1000 (new maximum)."""
    app = _make_app_with_sessions([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=1000")
    assert resp.status_code == 200


async def test_sessions_rejects_limit_above_1000() -> None:
    """GET /api/sessions rejects limit=1001 with 422 Unprocessable Entity."""
    app = _make_app_with_sessions([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=1001")
    assert resp.status_code == 422


async def test_sessions_has_more_true_when_more_pages_exist() -> None:
    """PaginationMeta.has_more is True when total > offset + limit."""
    rows = [_make_session_row() for _ in range(50)]
    app = _make_app_with_sessions(rows, total=300)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 300
    assert body["meta"]["has_more"] is True


async def test_sessions_has_more_false_on_last_page() -> None:
    """PaginationMeta.has_more is False when offset + limit >= total."""
    rows = [_make_session_row() for _ in range(10)]
    app = _make_app_with_sessions(rows, total=10)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 10
    assert body["meta"]["has_more"] is False


async def test_sessions_rows_include_token_counts() -> None:
    """Regression (bu-u3sga): /api/sessions list rows expose input/output_tokens.

    The Sessions list 'Tokens' column was permanently '—' because the summary
    projection/model dropped token fields. Each list row must carry them so the
    frontend can render the column.
    """
    rows = [_make_session_row()]
    app = _make_app_with_sessions(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50")
    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["input_tokens"] == 1234
    assert item["output_tokens"] == 567


async def test_butler_sessions_rows_include_token_counts() -> None:
    """Regression (bu-u3sga): butler-scoped session list rows expose tokens."""
    rows = [_make_session_row()]
    app = _make_butler_app_with_sessions(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/sessions?limit=50")
    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["input_tokens"] == 1234
    assert item["output_tokens"] == 567


async def test_sessions_offset_pagination() -> None:
    """GET /api/sessions with offset returns correct meta fields."""
    rows = [_make_session_row() for _ in range(50)]
    app = _make_app_with_sessions(rows, total=250)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50&offset=200")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["offset"] == 200
    assert body["meta"]["limit"] == 50
    # offset(200) + limit(50) == total(250) → has_more False
    assert body["meta"]["has_more"] is False


# ---------------------------------------------------------------------------
# Butler-scoped /api/butlers/{name}/sessions pagination tests
# ---------------------------------------------------------------------------


async def test_butler_sessions_accepts_limit_1000() -> None:
    """GET /api/butlers/{name}/sessions accepts limit=1000."""
    app = _make_butler_app_with_sessions([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/sessions?limit=1000")
    assert resp.status_code == 200


async def test_butler_sessions_rejects_limit_above_1000() -> None:
    """GET /api/butlers/{name}/sessions rejects limit=1001 with 422."""
    app = _make_butler_app_with_sessions([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/sessions?limit=1001")
    assert resp.status_code == 422


async def test_butler_sessions_has_more_reflects_total() -> None:
    """Butler-scoped endpoint: has_more is True when more pages exist."""
    rows = [_make_session_row() for _ in range(100)]
    app = _make_butler_app_with_sessions(rows, total=500)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/sessions?limit=100&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 500
    assert body["meta"]["has_more"] is True
