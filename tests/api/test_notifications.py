"""Tests for GET /api/notifications, /api/notifications/stats, and the
butler-scoped /api/butlers/{name}/notifications -- the source_available
degraded-mode contract (bu-qvnce.1).

A genuinely unavailable Switchboard source — whether pool acquisition raises
``KeyError`` or a live pool query fails — must be distinguishable from a
truthful "no notifications match" result. Both render an all-zero/empty
payload, but only the unavailable case carries ``source_available: false``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import httpx
import pytest

from butlers.api.briefing.cache import BriefingCache
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import (
    _get_db_manager,
    _normalize_notification_metadata,
    get_cache,
)

pytestmark = pytest.mark.unit


def _make_unavailable_db() -> MagicMock:
    """A DatabaseManager stand-in whose switchboard pool is unreachable."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("switchboard")
    return mock_db


def _make_available_db() -> tuple[MagicMock, AsyncMock]:
    """A DatabaseManager stand-in with a working switchboard pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    return mock_db, pool


_METADATA_NORMALIZATION_CASES = [
    pytest.param({"origin": "test"}, {"origin": "test"}, id="mapping"),
    pytest.param(None, None, id="null"),
    pytest.param('{"origin": "legacy"}', {"origin": "legacy"}, id="encoded-object"),
    pytest.param('{"broken":', {"_raw": '{"broken":'}, id="malformed-string"),
    pytest.param("[]", {"_raw": "[]"}, id="encoded-array"),
    pytest.param('"plain text"', {"_raw": '"plain text"'}, id="encoded-string"),
    pytest.param("42", {"_raw": "42"}, id="encoded-number"),
    pytest.param("true", {"_raw": "true"}, id="encoded-boolean"),
    pytest.param("null", {"_raw": "null"}, id="encoded-null"),
    pytest.param(
        '"{\\"inner\\": \\"json\\"}"',
        {"_raw": '"{\\"inner\\": \\"json\\"}"'},
        id="does-not-recursively-decode",
    ),
    pytest.param([], None, id="actual-array"),
    pytest.param(42, None, id="actual-number"),
    pytest.param(True, None, id="actual-boolean"),
]


def _notification_row(metadata: object) -> dict[str, object]:
    """Build a notification row without coercing its decoded JSONB metadata."""
    return {
        "id": uuid4(),
        "source_butler": "finance",
        "channel": "telegram",
        "recipient": "12345",
        "message": "Notification metadata compatibility test.",
        "metadata": metadata,
        "status": "read",
        "effective_status": "read",
        "error": None,
        "session_id": None,
        "trace_id": None,
        "created_at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    }


@pytest.mark.parametrize(("value", "expected"), _METADATA_NORMALIZATION_CASES)
def test_normalize_notification_metadata_uses_one_layer_object_or_null_contract(
    value: object, expected: dict | None
) -> None:
    """The API boundary preserves the exact legacy fallback matrix."""
    normalized = _normalize_notification_metadata(value)

    assert normalized == expected
    if isinstance(value, dict):
        assert normalized is not value


@pytest.mark.parametrize(
    "path", ["/api/notifications", "/api/butlers/finance/notifications"], ids=["global", "scoped"]
)
@pytest.mark.parametrize(("metadata", "expected"), _METADATA_NORMALIZATION_CASES)
async def test_notification_list_routes_normalize_legacy_metadata(
    app, path: str, metadata: object, expected: dict | None
) -> None:
    """Global and scoped lists retain the same object-or-null metadata contract."""
    mock_db, pool = _make_available_db()
    pool.fetchval.return_value = 1
    row = _notification_row(metadata)
    row["status"] = "failed"
    row["effective_status"] = "terminal_failed"
    pool.fetch.return_value = [row]
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)

    assert response.status_code == 200
    notification = response.json()["data"][0]
    assert notification["metadata"] == expected
    assert notification["status"] == "failed"
    assert notification["effective_status"] == "terminal_failed"


@pytest.mark.parametrize(("metadata", "expected"), _METADATA_NORMALIZATION_CASES)
async def test_mark_notification_read_normalizes_legacy_metadata(
    app, metadata: object, expected: dict | None
) -> None:
    """Mark-read returns the same normalized metadata shape as notification lists."""
    row = _notification_row(metadata)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[row, None])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_cache] = lambda: BriefingCache(ttl_seconds=300)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(f"/api/notifications/{row['id']}/read")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["metadata"] == expected
    assert body["status"] == "read"
    assert body["effective_status"] == "read"


async def test_list_notifications_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is False


async def test_list_butler_notifications_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/finance/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is False


async def test_notification_stats_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["total"] == 0
    assert body["data"]["source_available"] is False


@pytest.mark.parametrize("path", ["/api/notifications", "/api/butlers/finance/notifications"])
@pytest.mark.parametrize("query_method", ["fetchval", "fetch"], ids=["count", "rows"])
async def test_notification_list_query_failure_returns_degraded_envelope(app, path, query_method):
    """A live pool can fail during either notification-list database query."""
    mock_db, pool = _make_available_db()
    getattr(pool, query_method).side_effect = ConnectionError("connection reset by peer")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get(path)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"] == {"total": 0, "offset": 0, "limit": 50, "has_more": False}
    assert body["source_available"] is False


@pytest.mark.parametrize("query_method", ["fetchval", "fetch"], ids=["counts", "groups"])
async def test_notification_stats_query_failure_returns_degraded_envelope(app, query_method):
    """A live pool can fail during either notification-stats query type."""
    mock_db, pool = _make_available_db()
    getattr(pool, query_method).side_effect = ConnectionError("connection reset by peer")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {
        "total": 0,
        "sent": 0,
        "failed": 0,
        "by_channel": {},
        "by_butler": {},
        "source_available": False,
    }


async def test_notification_stats_missing_table_remains_available_empty_stats(app):
    """An unmigrated notifications table is legitimate absence, not degradation."""
    mock_db, pool = _make_available_db()
    pool.fetchval.side_effect = asyncpg.exceptions.UndefinedTableError(
        'relation "notifications" does not exist'
    )
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    assert resp.json()["data"]["source_available"] is True


async def test_notification_stats_does_not_mask_response_mapping_errors(app):
    """Only database query failures receive the degraded stats fallback."""
    mock_db, pool = _make_available_db()
    pool.fetch.return_value = [{}]
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 500


async def test_notification_list_missing_table_remains_available_empty_page(app):
    """An unmigrated notifications table is legitimate absence, not degradation."""
    mock_db, pool = _make_available_db()
    pool.fetchval.side_effect = asyncpg.exceptions.UndefinedTableError(
        'relation "notifications" does not exist'
    )
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is True


async def test_notification_list_does_not_mask_response_mapping_errors(app):
    """Only database query failures receive the degraded page fallback."""
    mock_db, pool = _make_available_db()
    pool.fetch.return_value = [{}]
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/notifications")

    assert resp.status_code == 500


async def test_list_notifications_reports_source_available_on_success(app):
    mock_db, _pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_available"] is True


async def test_notification_stats_reports_source_available_on_success(app):
    mock_db, _pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["source_available"] is True


async def test_notification_stats_omits_window_predicate_by_default(app):
    """No since/until -> every query against `notifications` carries no bound
    `created_at` predicate (all-time rollup, unchanged pre-existing behavior)."""
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    fetchval_calls = pool.fetchval.call_args_list
    assert all("created_at >=" not in call.args[0] for call in fetchval_calls)
    assert all("created_at <=" not in call.args[0] for call in fetchval_calls)
    fetch_calls = pool.fetch.call_args_list
    assert all("created_at >=" not in call.args[0] for call in fetch_calls)
    assert all("created_at <=" not in call.args[0] for call in fetch_calls)


async def test_notification_stats_by_butler_is_scoped_to_terminal_failures(app):
    """by_butler breaks down the same terminal failures as ``failed``."""
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    fetch_calls = pool.fetch.call_args_list
    by_butler_call = next(c for c in fetch_calls if "source_butler" in c.args[0])
    assert "status = 'failed'" in by_butler_call.args[0]
    assert "AND NOT (" in by_butler_call.args[0]
    assert "EXISTS" in by_butler_call.args[0]
    by_channel_call = next(
        c for c in fetch_calls if "channel" in c.args[0] and "source_butler" not in c.args[0]
    )
    assert "status = 'failed'" not in by_channel_call.args[0]


async def test_notification_stats_threads_since_until_into_every_query(app):
    """?since=...&until=... binds a created_at window on total/sent/failed/
    by_channel/by_butler -- the windowed-verdict facet (bu-y0v0c)."""
    mock_db, pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/notifications/stats?since=2026-07-04T00:00:00Z&until=2026-07-05T00:00:00Z"
        )

    assert resp.status_code == 200

    fetchval_calls = pool.fetchval.call_args_list
    # total, sent, failed
    assert len(fetchval_calls) == 3
    for call in fetchval_calls:
        sql = call.args[0]
        assert "created_at >=" in sql
        assert "created_at <=" in sql
        assert len(call.args) - 1 == 2  # two bound window values

    fetch_calls = pool.fetch.call_args_list
    # by_channel, by_butler
    assert len(fetch_calls) == 2
    for call in fetch_calls:
        sql = call.args[0]
        assert "created_at >=" in sql
        assert "created_at <=" in sql
