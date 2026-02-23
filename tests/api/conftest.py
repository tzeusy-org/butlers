"""Shared fixtures and helpers for notification API tests.

Extracted from test_notifications_router.py, test_notification_endpoints.py,
and test_butler_notifications.py to eliminate duplicated setup code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager

# ---------------------------------------------------------------------------
# Notification row factory
# ---------------------------------------------------------------------------


def make_notification_row(
    *,
    source_butler: str = "atlas",
    channel: str = "telegram",
    recipient: str = "12345",
    message: str = "Hello!",
    metadata: dict | None = None,
    status: str = "sent",
    error: str | None = None,
    session_id=None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for the notifications table."""
    return {
        "id": uuid4(),
        "source_butler": source_butler,
        "channel": channel,
        "recipient": recipient,
        "message": message,
        "metadata": metadata or {},
        "status": status,
        "error": error,
        "session_id": session_id,
        "trace_id": trace_id,
        "created_at": created_at or datetime.now(tz=UTC),
    }


# ---------------------------------------------------------------------------
# App builder helpers
# ---------------------------------------------------------------------------


def build_notifications_app(
    rows: list[dict],
    total: int | None = None,
) -> tuple:
    """Create a FastAPI app with mocked DatabaseManager for list endpoints.

    Returns (app, mock_pool, mock_db) so tests can inspect call args.
    """
    if total is None:
        total = len(rows)

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(
        return_value=[
            MagicMock(
                **{
                    "__getitem__": lambda self, key, row=row: row[key],
                }
            )
            for row in rows
        ]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


def build_stats_app(
    *,
    total: int = 0,
    sent: int = 0,
    failed: int = 0,
    channel_rows: list[dict] | None = None,
    butler_rows: list[dict] | None = None,
) -> tuple:
    """Create a FastAPI app with mocked DatabaseManager for the /stats endpoint.

    Returns (app, mock_pool, mock_db) so tests can inspect call args.
    """
    if channel_rows is None:
        channel_rows = []
    if butler_rows is None:
        butler_rows = []

    mock_pool = AsyncMock()

    async def _fetchval(sql, *args):
        if "status = 'sent'" in sql:
            return sent
        elif "status = 'failed'" in sql:
            return failed
        else:
            return total

    mock_pool.fetchval = AsyncMock(side_effect=_fetchval)

    def _make_record(row: dict) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    async def _fetch(sql, *args):
        if "GROUP BY channel" in sql:
            return [_make_record(r) for r in channel_rows]
        elif "GROUP BY source_butler" in sql:
            return [_make_record(r) for r in butler_rows]
        return []

    mock_pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


def build_app_missing_switchboard() -> object:
    """Return an app where the switchboard pool lookup raises KeyError."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app
