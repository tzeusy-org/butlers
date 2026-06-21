"""Tests for GET /api/butlers/{name}/logs endpoint.

Covers:
- Basic retrieval returns LogLines response shape.
- ?level= filter (minimum-severity semantics).
- ?since= filter.
- ?limit= default (100) and maximum (1000) enforcement.
- limit > 1000 is rejected with 422.
- 503 when the butler pool is not registered.
- DB query failure returns 503.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.butler_logs import _get_db_manager as _logs_get_db

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    level: str = "INFO",
    msg: str = "test message",
    source: str | None = "spawner",
    request_id=None,
    metadata=None,
    ts: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "ts": ts or _NOW,
            "level": level,
            "msg": msg,
            "source": source,
            "request_id": request_id,
            "metadata": metadata,
        }[key]
    )
    return row


def _make_app(rows: list, *, pool_raises: Exception | None = None) -> object:
    """Create app wired with a mock pool returning the given rows."""
    mock_pool = AsyncMock()
    if pool_raises is not None:
        mock_pool.fetch = AsyncMock(side_effect=pool_raises)
    else:
        mock_pool.fetch = AsyncMock(return_value=rows)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db
    return app


def _make_app_no_pool() -> object:
    """Create app where pool() raises KeyError (butler not registered)."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("general")

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Basic retrieval
# ---------------------------------------------------------------------------


async def test_logs_returns_loglines_shape() -> None:
    """GET /api/butlers/{name}/logs returns a LogLines response with correct shape."""
    row = _make_row(level="INFO", msg="session started", source="spawner")
    app = _make_app([row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert "lines" in body
    assert len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["level"] == "INFO"
    assert line["msg"] == "session started"
    assert line["source"] == "spawner"


async def test_logs_empty_result() -> None:
    """Returns empty lines list when no log rows exist."""
    app = _make_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs")
    assert resp.status_code == 200
    assert resp.json()["lines"] == []


# ---------------------------------------------------------------------------
# 503 paths
# ---------------------------------------------------------------------------


async def test_logs_503_when_pool_missing() -> None:
    """Returns 503 when the butler's pool is not registered."""
    app = _make_app_no_pool()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs")
    assert resp.status_code == 503


async def test_logs_503_on_db_query_failure() -> None:
    """Returns 503 when the DB query raises an unexpected exception."""
    app = _make_app([], pool_raises=RuntimeError("db exploded"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Level filter
# ---------------------------------------------------------------------------


async def test_logs_level_filter_passes_correct_levels() -> None:
    """?level=WARN causes the query to pass 'WARN' and 'ERROR' in the IN clause."""
    mock_pool = AsyncMock()
    captured_args: list = []

    async def _capture_fetch(sql, *args):
        captured_args.extend(args)
        return []

    mock_pool.fetch = _capture_fetch

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs?level=WARN")

    assert resp.status_code == 200
    # WARN and ERROR should be in the filter args
    assert "WARN" in captured_args
    assert "ERROR" in captured_args
    # INFO and DEBUG should not be passed
    assert "INFO" not in captured_args
    assert "DEBUG" not in captured_args


async def test_logs_level_filter_info_excludes_debug() -> None:
    """?level=INFO includes INFO, WARN, ERROR but not DEBUG."""
    mock_pool = AsyncMock()
    captured_args: list = []

    async def _capture_fetch(sql, *args):
        captured_args.extend(args)
        return []

    mock_pool.fetch = _capture_fetch

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs?level=INFO")

    assert resp.status_code == 200
    assert "INFO" in captured_args
    assert "WARN" in captured_args
    assert "ERROR" in captured_args
    assert "DEBUG" not in captured_args


async def test_logs_invalid_level_returns_422() -> None:
    """?level=TRACE (unknown) returns 422."""
    app = _make_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs?level=TRACE")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Since filter
# ---------------------------------------------------------------------------


async def test_logs_since_filter_passed_to_query() -> None:
    """?since= timestamp is forwarded to the DB query."""
    mock_pool = AsyncMock()
    captured_args: list = []

    async def _capture_fetch(sql, *args):
        captured_args.extend(args)
        return []

    mock_pool.fetch = _capture_fetch

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db

    since_str = "2026-01-01T00:00:00Z"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/general/logs?since={since_str}")

    assert resp.status_code == 200
    # One of the args should be a datetime
    from datetime import datetime

    dt_args = [a for a in captured_args if isinstance(a, datetime)]
    assert len(dt_args) == 1


# ---------------------------------------------------------------------------
# Limit enforcement
# ---------------------------------------------------------------------------


async def test_logs_default_limit_is_100() -> None:
    """Default limit is 100 rows."""
    mock_pool = AsyncMock()
    captured_args: list = []

    async def _capture_fetch(sql, *args):
        captured_args.extend(args)
        return []

    mock_pool.fetch = _capture_fetch
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs")

    assert resp.status_code == 200
    assert 100 in captured_args


@pytest.mark.parametrize(
    "limit,expected_status",
    [
        (1000, 200),  # upper bound accepted
        (1001, 422),  # above bound rejected
        (0, 422),  # below bound rejected
    ],
)
async def test_logs_limit_bounds(limit, expected_status) -> None:
    """limit must be within 1..1000; boundary values accepted/rejected accordingly."""
    app = _make_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/general/logs?limit={limit}")
    assert resp.status_code == expected_status


async def test_logs_large_result_trimmed_by_limit() -> None:
    """Endpoint passes the limit to the DB query (trim at DB layer)."""
    mock_pool = AsyncMock()
    captured_args: list = []

    async def _capture_fetch(sql, *args):
        captured_args.extend(args)
        return []

    mock_pool.fetch = _capture_fetch
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_logs_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/logs?limit=42")

    assert resp.status_code == 200
    assert 42 in captured_args
