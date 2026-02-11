"""Tests for audit log endpoints and helpers.

Verifies the GET /api/audit-log endpoint with pagination and filtering,
the log_audit_entry helper function, and the AuditEntry Pydantic model.

Issues: butlers-26h.15.7
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.models.audit import AuditEntry
from butlers.api.routers.audit import _get_db_manager, log_audit_entry

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
_ENTRY_ID = uuid4()


def _make_audit_record(
    *,
    entry_id: UUID | None = None,
    butler: str = "switchboard",
    operation: str = "trigger",
    request_summary: dict | None = None,
    result: str = "success",
    error: str | None = None,
    user_context: dict | None = None,
    created_at: datetime = _NOW,
) -> dict:
    """Create a dict mimicking an asyncpg Record for dashboard_audit_log."""
    return {
        "id": entry_id or _ENTRY_ID,
        "butler": butler,
        "operation": operation,
        "request_summary": request_summary or {},
        "result": result,
        "error": error,
        "user_context": user_context or {},
        "created_at": created_at,
    }


def _app_with_mock_db(
    *,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
):
    """Create a FastAPI app with a mocked DatabaseManager."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock()

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    # Also override _get_db_manager in other routers that share the same pattern
    from butlers.api.routers.butlers import _get_db_manager as butlers_get_db
    from butlers.api.routers.schedules import _get_db_manager as schedules_get_db
    from butlers.api.routers.state import _get_db_manager as state_get_db

    app.dependency_overrides[schedules_get_db] = lambda: mock_db
    app.dependency_overrides[state_get_db] = lambda: mock_db
    app.dependency_overrides[butlers_get_db] = lambda: mock_db

    return app, mock_db, mock_pool


# ---------------------------------------------------------------------------
# TestAuditLogListEndpoint
# ---------------------------------------------------------------------------


class TestAuditLogListEndpoint:
    """Tests for GET /api/audit-log."""

    async def test_returns_paginated_response(self):
        """Endpoint returns PaginatedResponse with correct shape."""
        records = [
            _make_audit_record(butler="atlas", operation="trigger"),
            _make_audit_record(butler="switchboard", operation="tick"),
        ]
        app, mock_db, mock_pool = _app_with_mock_db(fetch_rows=records, fetchval_result=2)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/audit-log")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert body["meta"]["total"] == 2
        assert body["meta"]["offset"] == 0
        assert body["meta"]["limit"] == 50
        assert len(body["data"]) == 2
        assert body["data"][0]["butler"] == "atlas"
        assert body["data"][0]["operation"] == "trigger"

    async def test_empty_audit_log(self):
        """Endpoint returns empty list when no audit entries exist."""
        app, mock_db, mock_pool = _app_with_mock_db(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/audit-log")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_filter_by_butler(self):
        """Endpoint passes butler filter to SQL query."""
        app, mock_db, mock_pool = _app_with_mock_db(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/audit-log?butler=atlas")

        assert resp.status_code == 200

        # Verify the query included the butler filter
        fetch_call = mock_pool.fetch.call_args
        sql = fetch_call[0][0]
        assert "butler = $1" in sql
        # First arg should be "atlas"
        assert fetch_call[0][1] == "atlas"

    async def test_filter_by_operation(self):
        """Endpoint passes operation filter to SQL query."""
        app, mock_db, mock_pool = _app_with_mock_db(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/audit-log?operation=trigger")

        assert resp.status_code == 200

        fetch_call = mock_pool.fetch.call_args
        sql = fetch_call[0][0]
        assert "operation = $1" in sql
        assert fetch_call[0][1] == "trigger"

    async def test_filter_by_time_range(self):
        """Endpoint passes since/until time filters to SQL query."""
        app, mock_db, mock_pool = _app_with_mock_db(fetch_rows=[], fetchval_result=0)

        since = "2026-02-01T00:00:00Z"
        until = "2026-02-10T23:59:59Z"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/api/audit-log?since={since}&until={until}")

        assert resp.status_code == 200

        fetch_call = mock_pool.fetch.call_args
        sql = fetch_call[0][0]
        assert "created_at >= $1" in sql
        assert "created_at <= $2" in sql

    async def test_pagination_offset_limit(self):
        """Endpoint respects offset and limit parameters."""
        app, mock_db, mock_pool = _app_with_mock_db(fetch_rows=[], fetchval_result=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/audit-log?offset=20&limit=10")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 100
        assert body["meta"]["offset"] == 20
        assert body["meta"]["limit"] == 10

        # Verify offset and limit were passed to the SQL query
        fetch_call = mock_pool.fetch.call_args
        args = fetch_call[0]
        # Last two positional args should be offset=20, limit=10
        assert args[-2] == 20
        assert args[-1] == 10


# ---------------------------------------------------------------------------
# TestLogAuditEntry
# ---------------------------------------------------------------------------


class TestLogAuditEntry:
    """Tests for the log_audit_entry helper function."""

    async def test_logs_success_entry(self):
        """Helper inserts a success audit entry into the database."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        await log_audit_entry(
            mock_db,
            butler="atlas",
            operation="trigger",
            request_summary={"prompt": "hello"},
        )

        mock_db.pool.assert_called_once_with("switchboard")
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        assert "INSERT INTO dashboard_audit_log" in call_args[0]
        assert call_args[1] == "atlas"
        assert call_args[2] == "trigger"
        assert json.loads(call_args[3]) == {"prompt": "hello"}
        assert call_args[4] == "success"
        assert call_args[5] is None  # no error
        assert json.loads(call_args[6]) == {}  # default user_context

    async def test_logs_error_entry(self):
        """Helper inserts an error audit entry with error message."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        await log_audit_entry(
            mock_db,
            butler="atlas",
            operation="schedule.create",
            request_summary={"name": "daily"},
            result="error",
            error="MCP call failed",
        )

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        assert call_args[4] == "error"
        assert call_args[5] == "MCP call failed"

    async def test_logs_with_user_context(self):
        """Helper passes user_context to the database insert."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        user_ctx = {"ip": "127.0.0.1", "user_agent": "test-client"}
        await log_audit_entry(
            mock_db,
            butler="atlas",
            operation="state.set",
            request_summary={"key": "foo"},
            user_context=user_ctx,
        )

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        assert json.loads(call_args[6]) == user_ctx

    async def test_swallows_database_errors(self):
        """Helper silently catches errors so audit logging never breaks the caller."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        # Should not raise
        await log_audit_entry(
            mock_db,
            butler="atlas",
            operation="trigger",
            request_summary={},
        )


# ---------------------------------------------------------------------------
# TestAuditEntryModel
# ---------------------------------------------------------------------------


class TestAuditEntryModel:
    """Tests for the AuditEntry Pydantic model."""

    def test_serialization(self):
        """AuditEntry serializes all fields correctly."""
        entry_id = uuid4()
        now = datetime.now(tz=UTC)
        entry = AuditEntry(
            id=entry_id,
            butler="atlas",
            operation="trigger",
            request_summary={"prompt": "hello"},
            result="success",
            error=None,
            user_context={"ip": "127.0.0.1"},
            created_at=now,
        )

        data = entry.model_dump()
        assert data["id"] == entry_id
        assert data["butler"] == "atlas"
        assert data["operation"] == "trigger"
        assert data["request_summary"] == {"prompt": "hello"}
        assert data["result"] == "success"
        assert data["error"] is None
        assert data["user_context"] == {"ip": "127.0.0.1"}
        assert data["created_at"] == now

    def test_default_values(self):
        """AuditEntry uses correct defaults for optional/defaulted fields."""
        entry = AuditEntry(
            id=uuid4(),
            butler="atlas",
            operation="trigger",
            result="success",
            created_at=datetime.now(tz=UTC),
        )

        assert entry.request_summary == {}
        assert entry.error is None
        assert entry.user_context == {}

    def test_json_roundtrip(self):
        """AuditEntry survives JSON serialization and deserialization."""
        entry = AuditEntry(
            id=uuid4(),
            butler="atlas",
            operation="schedule.create",
            request_summary={"name": "daily", "cron": "0 9 * * *"},
            result="error",
            error="MCP call failed",
            user_context={"ip": "10.0.0.1"},
            created_at=datetime.now(tz=UTC),
        )

        json_str = entry.model_dump_json()
        restored = AuditEntry.model_validate_json(json_str)
        assert restored.id == entry.id
        assert restored.butler == entry.butler
        assert restored.operation == entry.operation
        assert restored.request_summary == entry.request_summary
        assert restored.result == entry.result
        assert restored.error == entry.error
        assert restored.user_context == entry.user_context
