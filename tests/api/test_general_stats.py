"""Tests for GET /api/general/stats endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics asyncpg Record (supports dict() and attr access)."""

    def __getitem__(self, key):
        return super().__getitem__(key)


def _row(data: dict) -> _Row:
    return _Row(data)


def _make_app(app, *, fetchval_seq=None, fetch_seq=None):
    """Wire the shared app with a mocked pool for /stats.

    fetchval_seq: list of values returned by successive fetchval calls.
    fetch_seq: list of row-lists returned by successive fetch calls.
    """
    # Import the dependency stub from the dynamically loaded router.
    # The router is registered under /api/general so we import it via
    # the app's registered routes — but it's simpler to import the module
    # directly since it's already been loaded by create_app().
    import sys

    # The router module is loaded dynamically under 'general_api_router' or
    # similar; however, the dependency override target is the function object
    # inside the loaded module.  We locate it via the router's route objects.
    from butlers.api.router_discovery import discover_butler_routers

    # Trigger router discovery to ensure all butler routers are registered.
    discover_butler_routers()

    # Locate _get_db_manager from the already-imported module
    _get_db_manager_fn = None
    for mod_name, mod in sys.modules.items():
        if "general" in mod_name and hasattr(mod, "_get_db_manager"):
            obj = getattr(mod, "_get_db_manager")
            # Use the first match that is a plain function (not a class method)
            if callable(obj) and not isinstance(obj, type):
                _get_db_manager_fn = obj
                break

    if _get_db_manager_fn is None:
        pytest.skip("Could not locate _get_db_manager for general router")

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

    app.dependency_overrides[_get_db_manager_fn] = lambda: mock_db
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
        import sys

        _get_db_manager_fn = None
        for mod_name, mod in sys.modules.items():
            if "general" in mod_name and hasattr(mod, "_get_db_manager"):
                obj = getattr(mod, "_get_db_manager")
                if callable(obj) and not isinstance(obj, type):
                    _get_db_manager_fn = obj
                    break

        if _get_db_manager_fn is None:
            pytest.skip("Could not locate _get_db_manager for general router")

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("general")
        app.dependency_overrides[_get_db_manager_fn] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/stats")

        assert resp.status_code == 503
