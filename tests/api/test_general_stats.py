"""Tests for GET /api/general/stats endpoint."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.router_discovery import discover_butler_routers

pytestmark = pytest.mark.unit

_MODULE_NAME = "general_api_router"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics asyncpg Record (supports dict-style key access)."""

    def __getitem__(self, key):
        return super().__getitem__(key)


def _row(data: dict) -> _Row:
    return _Row(data)


def _get_db_dep():
    """Return the _get_db_manager function from the general API router module.

    Triggers router discovery on first call to ensure the module is loaded,
    then resolves the dependency function by deterministic module name.
    """
    discover_butler_routers()
    if _MODULE_NAME not in sys.modules:
        raise RuntimeError(
            f"Router module '{_MODULE_NAME}' not found after discovery. "
            "Ensure roster/general/api/router.py is present."
        )
    return sys.modules[_MODULE_NAME]._get_db_manager


def _make_app(app, *, fetchval_seq=None, fetch_seq=None):
    """Wire the shared app with a mocked pool for /stats.

    fetchval_seq: list of values returned by successive fetchval calls.
    fetch_seq: list of row-lists returned by successive fetch calls.
    """
    mock_pool = AsyncMock()
    fv_iter = iter(fetchval_seq or [])
    ft_iter = iter(fetch_seq or [])

    async def _fetchval(sql, *args):
        try:
            return next(fv_iter)
        except StopIteration:
            return 0

    async def _fetch(sql, *args):
        try:
            return next(ft_iter)
        except StopIteration:
            return []

    mock_pool.fetchval = AsyncMock(side_effect=_fetchval)
    mock_pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGeneralStatsEmpty:
    """Stats endpoint returns zeros and empty histogram when butler has no data."""

    async def test_empty_butler_returns_zeros(self, app):
        _make_app(
            app,
            fetchval_seq=[0, 0, None, 0],
            fetch_seq=[[]],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_collections"] == 0
        assert data["total_entities"] == 0
        assert data["last_modified_collection"] is None
        assert data["largest_collection_size"] == 0
        assert isinstance(data["size_histogram"], list)

    async def test_empty_butler_histogram_all_brackets_zero(self, app):
        _make_app(
            app,
            fetchval_seq=[0, 0, None, 0],
            fetch_seq=[[]],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 200
        hist = resp.json()["size_histogram"]
        # All brackets must be present with count 0
        brackets = {b["bracket"]: b["count"] for b in hist}
        assert brackets["0"] == 0
        assert brackets["1-10"] == 0
        assert brackets["11-100"] == 0
        assert brackets["101+"] == 0


class TestGeneralStatsPopulated:
    """Stats endpoint reflects real data correctly."""

    async def test_populated_kpis(self, app):
        _make_app(
            app,
            fetchval_seq=[3, 25, "books", 15],
            fetch_seq=[
                [
                    _row({"bracket": "0", "count": 0}),
                    _row({"bracket": "1-10", "count": 1}),
                    _row({"bracket": "11-100", "count": 2}),
                    _row({"bracket": "101+", "count": 0}),
                ]
            ],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_collections"] == 3
        assert data["total_entities"] == 25
        assert data["last_modified_collection"] == "books"
        assert data["largest_collection_size"] == 15

    async def test_histogram_buckets_populated(self, app):
        _make_app(
            app,
            fetchval_seq=[5, 200, "movies", 150],
            fetch_seq=[
                [
                    _row({"bracket": "1-10", "count": 2}),
                    _row({"bracket": "11-100", "count": 2}),
                    _row({"bracket": "101+", "count": 1}),
                ]
            ],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 200
        hist = resp.json()["size_histogram"]
        brackets = {b["bracket"]: b["count"] for b in hist}
        assert brackets["1-10"] == 2
        assert brackets["11-100"] == 2
        assert brackets["101+"] == 1
        # Empty bracket still present with count 0
        assert brackets["0"] == 0

    async def test_histogram_all_brackets_always_present(self, app):
        """All four brackets always appear in the response regardless of data distribution."""
        _make_app(
            app,
            fetchval_seq=[1, 5, "notes", 5],
            fetch_seq=[
                [
                    _row({"bracket": "1-10", "count": 1}),
                ]
            ],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 200
        hist = resp.json()["size_histogram"]
        bracket_names = {b["bracket"] for b in hist}
        assert {"0", "1-10", "11-100", "101+"} == bracket_names


class TestGeneralStats503:
    """Stats endpoint returns 503 when the database pool is unavailable."""

    async def test_missing_pool_returns_503(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("general")
        app.dependency_overrides[_get_db_dep()] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 503
