"""Unit tests for GET /api/health/measurements (bu-3uzhk).

Verifies that the measurements endpoint reads from the `facts` table
(predicate = ``measurement_{type}``, scope = ``health``) — the same surface
written by the ``measurement_log`` MCP tool.

Coverage:
- 200 empty list when no measurements
- 200 with a measurement entry built from a facts row
- type filter (predicate match)
- since/until date filters
- 503 when the health DB pool is unavailable
- regression: no query touches the legacy measurements table
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# The health butler router is auto-discovered by create_app() and registered
# in sys.modules under the key 'health_api_router'.  We must override the
# _get_db_manager function *from that module*, not from a separately loaded
# copy, because FastAPI dependency_overrides uses object identity.
#
# Strategy: call create_app() once to trigger router discovery, then extract
# the dependency function from sys.modules for later use in overrides.
# ---------------------------------------------------------------------------

_APP_SEED = create_app(api_key="")  # trigger discovery
_health_get_db_manager = sys.modules["health_api_router"]._get_db_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass mimicking asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _row(data: dict) -> _Row:
    return _Row(data)


def _make_fact_row(
    *,
    mtype: str = "weight",
    value: Any = {"kg": 75.5},
    notes: str | None = None,
    valid_at: datetime | None = None,
) -> _Row:
    """Build an asyncpg-like fact row for a measurement."""
    return _row(
        {
            "id": uuid.uuid4(),
            "predicate": f"measurement_{mtype}",
            "valid_at": valid_at or _NOW,
            "created_at": _NOW,
            "metadata": {
                "value": value,
                **({"notes": notes} if notes is not None else {}),
            },
        }
    )


def _make_app(*, fetch_rows=None, fetchval_result=0):
    """Build a test app with the health DB mocked."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchval = AsyncMock(return_value=fetchval_result)

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    # create_app() is idempotent for router discovery once modules are in sys.modules.
    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app, pool


def _make_app_unavailable():
    """Build a test app where the health pool lookup raises KeyError (503)."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("health")

    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_measurements_empty():
    """GET /api/health/measurements returns 200 + empty data list when no facts."""
    app, _ = _make_app(fetch_rows=[], fetchval_result=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health/measurements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


async def test_list_measurements_scalar_value_normalised_to_dict():
    """Scalar measurement values (non-dict) are wrapped in {value: ...} for the Measurement model."""
    row = _make_fact_row(mtype="heart_rate", value=72)
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health/measurements")
    assert resp.status_code == 200
    entry = resp.json()["data"][0]
    assert entry["type"] == "heart_rate"
    # Scalar value is wrapped into a dict for the Measurement model (value: dict).
    assert entry["value"] == {"value": 72}


async def test_list_measurements_type_filter_uses_predicate():
    """GET /api/health/measurements?type=weight queries facts with predicate=measurement_weight."""
    row = _make_fact_row(mtype="weight", value={"kg": 70.0})
    app, pool = _make_app(fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health/measurements?type=weight")
    assert resp.status_code == 200
    # Verify the count query targeted facts (not measurements) and the predicate.
    fetchval_calls = pool.fetchval.call_args_list
    assert len(fetchval_calls) >= 1
    sql = fetchval_calls[0][0][0]
    assert "facts" in sql
    assert "scope" in sql
    # First positional arg after the SQL is the predicate value.
    first_arg = fetchval_calls[0][0][1]
    assert first_arg == "measurement_weight"


async def test_list_measurements_no_type_filter_uses_like_prefix():
    """GET /api/health/measurements with no type filter uses a LIKE prefix rather than a
    hardcoded allowlist, so wellness-ingest facts (measurement_spo2, etc.) are included."""
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health/measurements")
    assert resp.status_code == 200
    fetchval_calls = pool.fetchval.call_args_list
    assert len(fetchval_calls) >= 1
    sql = fetchval_calls[0][0][0]
    assert "facts" in sql
    # Without a type filter the endpoint uses a LIKE prefix — no list parameter is passed.
    assert "LIKE" in sql
    assert "measurement~_%" in sql


async def test_list_measurements_since_until_passed_to_query():
    """since/until query params are forwarded as valid_at filters to the facts query."""
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/health/measurements",
            params={"since": "2026-01-01T00:00:00Z", "until": "2026-12-31T23:59:59Z"},
        )
    assert resp.status_code == 200
    fetchval_calls = pool.fetchval.call_args_list
    sql = fetchval_calls[0][0][0]
    assert "valid_at" in sql


async def test_list_measurements_503_when_pool_unavailable():
    """GET /api/health/measurements returns 503 when the health DB is not available."""
    app = _make_app_unavailable()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health/measurements")
    assert resp.status_code == 503


async def test_list_measurements_does_not_query_measurements_table():
    """GET /api/health/measurements must NOT touch the legacy measurements table.

    This is a regression guard: the old endpoint queried ``health.measurements``.
    The new endpoint reads from ``facts`` only (same surface as measurement_log).
    """
    row = _make_fact_row(mtype="temperature", value=36.6)
    app, pool = _make_app(fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/health/measurements")

    # Inspect all SQL strings passed to pool.fetchval and pool.fetch.
    all_sql: list[str] = []
    for call in pool.fetchval.call_args_list:
        all_sql.append(call[0][0])
    for call in pool.fetch.call_args_list:
        all_sql.append(call[0][0])

    for sql in all_sql:
        assert "FROM measurements" not in sql, (
            f"Query must not touch the legacy measurements table:\n{sql}"
        )


def test_no_health_measurements_table_references():
    """Regression guard: NO source file should query the dropped health.measurements table.

    Scans src/, roster/, and conftest.py for any SQL pattern that touches the
    ``measurements`` table.  Migration files (which legitimately reference the table)
    and this test file are excluded.
    """
    import pathlib
    import re

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    bad_patterns = [
        r"FROM\s+measurements\b",
        r"FROM\s+health\.measurements\b",
        r"INSERT\s+INTO\s+measurements\b",
        r"UPDATE\s+measurements\b",
    ]
    # Scan src/, roster/, conftest.py — exclude tests/ (test fixtures may reference history),
    # exclude migration files (they DEFINE or DROP the table), exclude this test file itself.
    candidate_globs = ["src/**/*.py", "roster/**/*.py", "conftest.py"]
    excludes = {
        # Defines the table — legitimate reference.
        "roster/health/migrations/001_health_tables.py",
        # Drops the table — legitimate DDL reference in the drop migration.
        "roster/health/migrations/002_drop_measurements_table.py",
    }
    offenders = []
    for glob in candidate_globs:
        for p in repo_root.glob(glob):
            rel = str(p.relative_to(repo_root))
            if rel in excludes:
                continue
            content = p.read_text()
            for pattern in bad_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    offenders.append(f"{rel} matches {pattern!r}")
    assert not offenders, (
        "These files still reference the dropped measurements table:\n" + "\n".join(offenders)
    )
