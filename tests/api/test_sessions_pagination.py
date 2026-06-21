"""Tests for /api/sessions and /api/butlers/{name}/sessions pagination.

Verifies:
- Cross-butler /api/sessions uses keyset (cursor) pagination — no offset/total
- Keyset meta (limit, next_cursor, has_more) is correct
- next_cursor points to the last returned row; malformed cursor -> 422
- Backend accepts limit up to 1000; limit > 1000 is rejected with 422
- Butler-scoped endpoint keeps offset/total and accepts limit up to 1000
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.read_models.sessions_v1 import decode_session_cursor
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_row(*, butler: str = "atlas", started_at: datetime | None = None) -> dict:
    return {
        "id": uuid4(),
        "prompt": "test prompt",
        "trigger_source": "api",
        "request_id": None,
        "success": True,
        "started_at": started_at or _NOW,
        "completed_at": _NOW,
        "duration_ms": 500,
        "model": "claude-sonnet",
        "complexity": None,
        "input_tokens": 1234,
        "output_tokens": 567,
    }


def _make_app_with_sessions(rows: list[dict]) -> object:
    """Wire a fresh app with mock fan_out returning the given rows.

    The keyset list endpoint runs a single data fan_out per request (no
    count(*)); this mock returns the supplied rows for that query.  The
    read-model fetches ``limit + 1`` and computes ``has_more`` from the merged
    length, so pass ``limit + 1`` rows to exercise ``has_more=True``.
    """

    def _make_record(row: dict):
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(
        side_effect=lambda sql, args, **kw: {"atlas": [_make_record(r) for r in rows]}
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


async def test_sessions_has_more_true_when_more_rows_exist() -> None:
    """KeysetMeta.has_more is True when the merge yields more than limit rows.

    The read-model fetches limit+1 per butler; returning limit+1 rows means a
    next page exists, so has_more is True and next_cursor is populated.
    """
    rows = [
        _make_session_row(started_at=_NOW - timedelta(seconds=i)) for i in range(51)
    ]  # limit(50) + 1
    app = _make_app_with_sessions(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["limit"] == 50
    assert body["meta"]["has_more"] is True
    assert body["meta"]["next_cursor"] is not None
    # Exactly limit rows are returned (the +1 sentinel is dropped).
    assert len(body["data"]) == 50
    # "total" / "offset" are gone from the keyset envelope.
    assert "total" not in body["meta"]
    assert "offset" not in body["meta"]


async def test_sessions_has_more_false_on_last_page() -> None:
    """KeysetMeta.has_more is False and next_cursor is null on the last page."""
    rows = [_make_session_row(started_at=_NOW - timedelta(seconds=i)) for i in range(10)]
    app = _make_app_with_sessions(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["has_more"] is False
    assert body["meta"]["next_cursor"] is None
    assert len(body["data"]) == 10


async def test_sessions_next_cursor_points_to_last_returned_row() -> None:
    """next_cursor decodes to the (started_at, id) of the last RETURNED row."""
    rows = [_make_session_row(started_at=_NOW - timedelta(seconds=i)) for i in range(51)]
    app = _make_app_with_sessions(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?limit=50")
    body = resp.json()
    last_item = body["data"][-1]
    cursor_started_at, cursor_id = decode_session_cursor(body["meta"]["next_cursor"])
    assert str(cursor_id) == last_item["id"]
    # Compare as instants — the JSON envelope serializes tz as 'Z' while the
    # cursor encodes '+00:00'; both denote the same moment.
    assert cursor_started_at == datetime.fromisoformat(last_item["started_at"])


async def test_sessions_malformed_cursor_returns_422() -> None:
    """A cursor that is not a valid base64url JSON keyset token -> 422."""
    app = _make_app_with_sessions([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?cursor=not-a-valid-cursor")
    assert resp.status_code == 422


async def test_sessions_accepts_valid_cursor() -> None:
    """A well-formed cursor is accepted and a second page is fetched (200)."""
    from butlers.api.read_models.sessions_v1 import encode_session_cursor

    cursor = encode_session_cursor(_NOW, uuid4())
    app = _make_app_with_sessions([_make_session_row()])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/sessions?limit=50&cursor={cursor}")
    assert resp.status_code == 200


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
