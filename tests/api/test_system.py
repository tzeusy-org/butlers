"""Tests for the /api/system/* endpoints.

Coverage:
- GET /api/system/instance -- version, uptime, started_at shape
- GET /api/system/database -- total size, schema breakdown, largest tables; 503 on DB error
- GET /api/system/backups -- graceful degradation: always 200 with null fields
- GET /api/system/egress -- owner gate (403 when owner not found), happy path,
  actor registry grouping, catalog_covers_from
- GET /api/system/butlers/heartbeat -- registry + session fan-out, schema_unreachable flag

All tests use dependency_overrides to inject a mock DatabaseManager so no
live PostgreSQL connection is required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.system import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool(**fetchval_map) -> MagicMock:
    """Return an AsyncMock asyncpg pool with configurable fetch/fetchval."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _make_app_with_db(mock_db: DatabaseManager) -> object:
    """Wire a fresh FastAPI app with the given mock DatabaseManager."""
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_record(row: dict) -> MagicMock:
    """Build an asyncpg-Record-like MagicMock from a plain dict."""
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda k: row[k])
    rec.get = MagicMock(side_effect=lambda k, default=None: row.get(k, default))
    # attribute access for common keys used in the router
    for k, v in row.items():
        setattr(rec, k, v)
    return rec


# ---------------------------------------------------------------------------
# GET /api/system/instance
# ---------------------------------------------------------------------------


class TestInstanceEndpoint:
    async def test_returns_200_with_required_fields(self):
        """Instance endpoint returns version, uptime_seconds, started_at."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/instance")
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], float | int)
        assert data["uptime_seconds"] >= 0
        assert "started_at" in data
        # started_at must be an ISO 8601 string
        parsed = datetime.fromisoformat(data["started_at"])
        assert parsed.tzinfo is not None

    async def test_version_is_not_empty(self):
        """Version field is never empty (falls back to 'unknown' on error)."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/instance")
        assert resp.status_code == 200
        version = resp.json()["data"]["version"]
        assert isinstance(version, str)
        assert version != ""

    async def test_uptime_increases_over_time(self):
        """Two sequential calls return non-negative and non-decreasing uptime."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1 = await client.get("/api/system/instance")
            r2 = await client.get("/api/system/instance")
        u1 = r1.json()["data"]["uptime_seconds"]
        u2 = r2.json()["data"]["uptime_seconds"]
        assert u2 >= u1


# ---------------------------------------------------------------------------
# GET /api/system/database
# ---------------------------------------------------------------------------


class TestDatabaseEndpoint:
    def _make_db(
        self,
        *,
        total_bytes: int = 1024 * 1024,
        schema_rows: list[dict] | None = None,
        table_rows: list[dict] | None = None,
        fail_at: str | None = None,
    ) -> DatabaseManager:
        """Wire a mock DatabaseManager for the database endpoint."""
        if schema_rows is None:
            schema_rows = [
                {"schema_name": "general", "size_bytes": 512 * 1024, "table_count": 5},
                {"schema_name": "health", "size_bytes": 256 * 1024, "table_count": 3},
            ]
        if table_rows is None:
            table_rows = [
                {"schema_name": "general", "table_name": "sessions", "size_bytes": 400 * 1024},
            ]

        pool = AsyncMock()

        async def _fetchval(sql, *args):
            if fail_at == "total":
                raise RuntimeError("permission denied")
            return total_bytes

        async def _fetch(sql, *args):
            if fail_at == "schema":
                raise RuntimeError("permission denied")
            if fail_at == "table":
                raise RuntimeError("permission denied")
            if "GROUP BY" in sql:
                return [_make_record(r) for r in schema_rows]
            return [_make_record(r) for r in table_rows]

        pool.fetchval = AsyncMock(side_effect=_fetchval)
        pool.fetch = AsyncMock(side_effect=_fetch)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = pool
        return mock_db

    async def test_happy_path_returns_required_fields(self):
        """Database endpoint returns total_size_bytes, schemas, largest_tables."""
        mock_db = self._make_db()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/database")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_size_bytes" in data
        assert "schemas" in data
        assert "largest_tables" in data
        assert data["growth_rate_bytes_per_day"] is None

    async def test_schema_breakdown_structure(self):
        """Each schema entry has schema_name, size_bytes, table_count."""
        mock_db = self._make_db()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/database")
        schemas = resp.json()["data"]["schemas"]
        assert len(schemas) == 2
        for s in schemas:
            assert "schema_name" in s
            assert "size_bytes" in s
            assert "table_count" in s

    async def test_returns_503_on_total_query_failure(self):
        """Returns HTTP 503 when pg_database_size() fails."""
        mock_db = self._make_db(fail_at="total")
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/database")
        assert resp.status_code == 503

    async def test_returns_503_when_switchboard_pool_missing(self):
        """Returns HTTP 503 when the switchboard pool is not registered."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/database")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/system/backups
# ---------------------------------------------------------------------------


class TestBackupsEndpoint:
    async def test_always_returns_200(self):
        """Backup endpoint always returns HTTP 200 (graceful degradation)."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/backups")
        assert resp.status_code == 200

    async def test_returns_degraded_payload_when_no_backup_configured(self):
        """Returns backup_source_reachable=false and null fields in v1."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/backups")
        data = resp.json()["data"]
        assert data["last_backup_at"] is None
        assert data["last_backup_size_bytes"] is None
        assert data["backup_source_reachable"] is False
        assert data["backup_history"] == []

    async def test_never_returns_503(self):
        """Backup endpoint degrades gracefully -- never 503."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/backups")
        assert resp.status_code != 503


# ---------------------------------------------------------------------------
# GET /api/system/egress
# ---------------------------------------------------------------------------


class TestEgressEndpoint:
    def _make_db_with_owner(
        self,
        *,
        has_owner: bool = True,
        audit_rows: list[dict] | None = None,
        owner_query_fails: bool = False,
        audit_query_fails: bool = False,
    ) -> DatabaseManager:
        """Wire a mock DatabaseManager for the egress endpoint."""
        if audit_rows is None and has_owner:
            audit_rows = [
                {
                    "operation": "llm_api_call",
                    "last_seen_at": _NOW,
                    "total_calls": 42,
                    "first_seen_at": _NOW,
                },
            ]
        elif audit_rows is None:
            audit_rows = []

        pool = AsyncMock()

        async def _fetchrow(sql, *args):
            if owner_query_fails:
                raise RuntimeError("DB error")
            if "ANY(e.roles)" in sql:
                if has_owner:
                    rec = MagicMock()
                    rec.__getitem__ = MagicMock(return_value="fake-uuid")
                    return rec
                return None
            return None

        async def _fetch(sql, *args):
            if audit_query_fails:
                raise RuntimeError("DB error")
            return [_make_record(r) for r in audit_rows]

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetch = AsyncMock(side_effect=_fetch)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = pool
        return mock_db

    async def test_returns_403_when_no_owner_contact(self):
        """Returns HTTP 403 when owner contact is not found in entities."""
        mock_db = self._make_db_with_owner(has_owner=False)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 403
        body = resp.json()
        # detail must carry code="forbidden"
        assert "forbidden" in str(body).lower()

    async def test_returns_403_when_owner_query_raises(self):
        """Returns HTTP 403 when the owner assertion query raises an exception."""
        mock_db = self._make_db_with_owner(has_owner=True, owner_query_fails=True)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 403

    async def test_returns_403_no_partial_data_leaked(self):
        """403 response contains no egress data (no partial data leak)."""
        mock_db = self._make_db_with_owner(has_owner=False)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 403
        body = resp.json()
        # no actors or catalog fields in a 403
        assert "actors" not in body
        assert "data" not in body

    async def test_happy_path_returns_actor_list(self):
        """With owner present and audit rows, returns populated actor list."""
        mock_db = self._make_db_with_owner(has_owner=True)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "actors" in data
        assert "catalog_covers_from" in data
        actors = data["actors"]
        assert len(actors) >= 1

    async def test_known_operation_maps_to_correct_actor(self):
        """llm_api_call maps to actor_id=anthropic.claude and display_name."""
        mock_db = self._make_db_with_owner(
            has_owner=True,
            audit_rows=[
                {
                    "operation": "llm_api_call",
                    "last_seen_at": _NOW,
                    "total_calls": 10,
                    "first_seen_at": _NOW,
                }
            ],
        )
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        actors = resp.json()["data"]["actors"]
        assert any(a["actor_id"] == "anthropic.claude" for a in actors)
        claude = next(a for a in actors if a["actor_id"] == "anthropic.claude")
        assert claude["display_name"] == "Anthropic Claude API"
        assert claude["total_calls"] == 10

    async def test_unknown_operation_grouped_as_other(self):
        """Unrecognized operations are grouped into the 'other' bucket."""
        mock_db = self._make_db_with_owner(
            has_owner=True,
            audit_rows=[
                {
                    "operation": "some_exotic_operation",
                    "last_seen_at": _NOW,
                    "total_calls": 3,
                    "first_seen_at": _NOW,
                }
            ],
        )
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        actors = resp.json()["data"]["actors"]
        other = next((a for a in actors if a["actor_id"] == "other"), None)
        assert other is not None
        assert other["display_name"] == "Other / Unrecognized"

    async def test_catalog_covers_from_is_oldest_audit_timestamp(self):
        """catalog_covers_from reflects the oldest first_seen_at in audit rows."""
        earliest = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        mock_db = self._make_db_with_owner(
            has_owner=True,
            audit_rows=[
                {
                    "operation": "llm_api_call",
                    "last_seen_at": _NOW,
                    "total_calls": 42,
                    "first_seen_at": earliest,
                },
            ],
        )
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 200
        covers_from = resp.json()["data"]["catalog_covers_from"]
        assert covers_from is not None
        assert "2025" in covers_from

    async def test_returns_503_when_switchboard_pool_missing(self):
        """Returns HTTP 503 when the switchboard pool is not registered."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 503

    async def test_returns_503_when_audit_query_fails(self):
        """Returns HTTP 503 when the audit log query raises after owner asserts."""
        mock_db = self._make_db_with_owner(has_owner=True, audit_query_fails=True)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 503

    async def test_multiple_operations_aggregated_by_actor(self):
        """Multiple operations mapping to same actor are summed under one entry."""
        mock_db = self._make_db_with_owner(
            has_owner=True,
            audit_rows=[
                {
                    "operation": "llm_api_call",
                    "last_seen_at": _NOW,
                    "total_calls": 5,
                    "first_seen_at": _NOW,
                },
                # second row also maps to anthropic.claude (same actor)
                {
                    "operation": "llm_api_call",
                    "last_seen_at": _NOW,
                    "total_calls": 3,
                    "first_seen_at": _NOW,
                },
            ],
        )
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        actors = resp.json()["data"]["actors"]
        claude_entries = [a for a in actors if a["actor_id"] == "anthropic.claude"]
        # should be collapsed to one entry
        assert len(claude_entries) == 1
        assert claude_entries[0]["total_calls"] == 8


# ---------------------------------------------------------------------------
# GET /api/system/butlers/heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeatEndpoint:
    def _make_db(
        self,
        *,
        registry_rows: list[dict] | None = None,
        butler_names: list[str] | None = None,
        session_last: datetime | None = None,
        active_count: int = 0,
        registry_fails: bool = False,
        session_fails: bool = False,
    ) -> DatabaseManager:
        """Wire a mock DatabaseManager for the heartbeat endpoint."""
        if registry_rows is None:
            registry_rows = [
                {"name": "general", "last_seen_at": _NOW},
            ]
        if butler_names is None:
            butler_names = ["general"]

        sw_pool = AsyncMock()

        async def _sw_fetch(sql, *args):
            if registry_fails:
                raise RuntimeError("DB error")
            return [_make_record(r) for r in registry_rows]

        sw_pool.fetch = AsyncMock(side_effect=_sw_fetch)

        butler_pool = AsyncMock()

        async def _butler_fetchrow(sql, *args):
            if session_fails:
                raise RuntimeError("schema not found")
            if session_last is not None:
                rec = MagicMock()
                rec.__getitem__ = MagicMock(return_value=session_last)
                return rec
            return None

        async def _butler_fetchval(sql, *args):
            if session_fails:
                raise RuntimeError("schema not found")
            return active_count

        butler_pool.fetchrow = AsyncMock(side_effect=_butler_fetchrow)
        butler_pool.fetchval = AsyncMock(side_effect=_butler_fetchval)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = butler_names

        def _pool(name: str) -> AsyncMock:
            if name == "switchboard":
                return sw_pool
            return butler_pool

        mock_db.pool.side_effect = _pool
        return mock_db

    async def test_happy_path_returns_butler_entries(self):
        """Heartbeat endpoint returns one entry per registered butler."""
        mock_db = self._make_db()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "butlers" in data
        butlers = data["butlers"]
        assert len(butlers) >= 1
        general = next((b for b in butlers if b["name"] == "general"), None)
        assert general is not None

    async def test_entry_has_required_fields(self):
        """Each ButlerHeartbeat entry has all required fields."""
        mock_db = self._make_db()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        butlers = resp.json()["data"]["butlers"]
        for b in butlers:
            assert "name" in b
            assert "last_heartbeat_at" in b
            assert "last_session_at" in b
            assert "active_session_count" in b
            assert "heartbeat_age_seconds" in b

    async def test_heartbeat_age_is_non_negative(self):
        """heartbeat_age_seconds is non-negative when last_heartbeat_at is known."""
        mock_db = self._make_db()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        butlers = resp.json()["data"]["butlers"]
        for b in butlers:
            if b["heartbeat_age_seconds"] is not None:
                assert b["heartbeat_age_seconds"] >= 0

    async def test_missing_registry_entry_gives_null_heartbeat(self):
        """Butler with no registry entry gets null last_heartbeat_at."""
        mock_db = self._make_db(
            registry_rows=[],  # empty registry
            butler_names=["general"],
        )
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        butlers = resp.json()["data"]["butlers"]
        general = next((b for b in butlers if b["name"] == "general"), None)
        assert general is not None
        assert general["last_heartbeat_at"] is None
        assert general["heartbeat_age_seconds"] is None

    async def test_session_query_failure_sets_error_flag(self):
        """When session query fails, error='schema_unreachable' is set."""
        mock_db = self._make_db(session_fails=True)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        assert resp.status_code == 200
        butlers = resp.json()["data"]["butlers"]
        # Entry still included but with error flag
        general = next((b for b in butlers if b["name"] == "general"), None)
        assert general is not None
        assert general["error"] == "schema_unreachable"

    async def test_returns_503_when_registry_query_fails(self):
        """Returns HTTP 503 when butler_registry query raises."""
        mock_db = self._make_db(registry_fails=True)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        assert resp.status_code == 503

    async def test_returns_503_when_switchboard_pool_missing(self):
        """Returns HTTP 503 when the switchboard pool is not registered."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")
        mock_db.butler_names = []
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        assert resp.status_code == 503

    async def test_active_session_count_from_sessions_table(self):
        """active_session_count reflects count(*) WHERE completed_at IS NULL."""
        mock_db = self._make_db(active_count=3)
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/butlers/heartbeat")
        butlers = resp.json()["data"]["butlers"]
        general = next((b for b in butlers if b["name"] == "general"), None)
        assert general is not None
        assert general["active_session_count"] == 3
