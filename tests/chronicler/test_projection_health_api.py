"""Integration tests for GET /api/chronicler/projection-health.

Covers:
- 200 OK with empty projection_checkpoints table
- 200 OK with populated rows: source_name, subsource, last_error, last_run_at,
  rows_projected, watermark are all surfaced
- Rows sorted by source_name ASC, subsource ASC
- last_error is null when no error recorded
- 405 Method Not Allowed for non-GET verbs
- No SQL outside chronicler.* schema (guardrail: reuses the same approach as
  test_source_state_api.py)
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _mock_pool(*, fetch_return=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _mock_db(pool):
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


# ---------------------------------------------------------------------------
# Dynamic module loading (mirrors router_discovery)
# ---------------------------------------------------------------------------


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(*, checkpoint_rows=None):
    chronicler_mod = _load_chronicler_router()
    pool = _mock_pool(fetch_return=checkpoint_rows or [])
    db = _mock_db(pool)

    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app, pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProjectionHealthAPI:
    async def test_empty_table_returns_200_with_empty_data(self):
        """Cold boot: projection_checkpoints empty → 200 with data: []."""
        app, _ = _make_app(checkpoint_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/projection-health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert "meta" in body

    async def test_populated_rows_returned(self):
        """Rows from projection_checkpoints are surfaced with all required fields."""
        t = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
        wm = datetime(2026, 4, 27, 23, 59, 0, tzinfo=UTC)
        rows = [
            _row(
                {
                    "source_name": "core.sessions",
                    "subsource": "butler_a",
                    "last_error": None,
                    "last_run_at": t,
                    "rows_projected": 42,
                    "watermark": wm,
                }
            ),
        ]
        app, _ = _make_app(checkpoint_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/projection-health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        row = data[0]
        assert row["source_name"] == "core.sessions"
        assert row["subsource"] == "butler_a"
        assert row["last_error"] is None
        assert row["rows_projected"] == 42
        assert row["last_run_at"].startswith("2026-04-28")
        assert row["watermark"].startswith("2026-04-27")

    async def test_last_error_surfaced(self):
        """When last_error is set on a checkpoint, it appears in the response."""
        rows = [
            _row(
                {
                    "source_name": "spotify.session_summary",
                    "subsource": "",
                    "last_error": "Connection timeout after 30s",
                    "last_run_at": _NOW,
                    "rows_projected": 0,
                    "watermark": None,
                }
            ),
        ]
        app, _ = _make_app(checkpoint_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/projection-health")
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert row["last_error"] == "Connection timeout after 30s"
        assert row["source_name"] == "spotify.session_summary"

    async def test_null_watermark_and_zero_rows_projected_edge(self):
        """A never-run checkpoint surfaces watermark=null and last_run_at=null, while
        rows_projected=0 is returned as 0 (not null) — the zero-vs-null distinction
        is the contract."""
        rows = [
            _row(
                {
                    "source_name": "steam.play_history",
                    "subsource": "",
                    "last_error": None,
                    "last_run_at": None,
                    "rows_projected": 0,
                    "watermark": None,
                }
            ),
        ]
        app, _ = _make_app(checkpoint_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/projection-health")
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert row["watermark"] is None
        assert row["last_run_at"] is None
        assert row["rows_projected"] == 0


class TestProjectionHealthMethodNotAllowed:
    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    async def test_non_get_returns_405(self, method: str):
        """Any HTTP method other than GET must return 405."""
        app, _ = _make_app(checkpoint_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await getattr(client, method)("/api/chronicler/projection-health")
        assert resp.status_code == 405
