"""Tests for the lifestyle taste-ledger read surface (bu-2jtfw.10).

GET /api/lifestyle/taste/summary, /taste/works, /taste/verdicts.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.router_discovery import discover_butler_routers

pytestmark = pytest.mark.unit

_MODULE_NAME = "lifestyle_api_router"


class _Row(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


def _row(data: dict) -> _Row:
    return _Row(data)


def _get_db_dep():
    discover_butler_routers()
    if _MODULE_NAME not in sys.modules:
        raise RuntimeError(
            f"Router module '{_MODULE_NAME}' not found after discovery. "
            "Ensure roster/lifestyle/api/router.py is present."
        )
    return sys.modules[_MODULE_NAME]._get_db_manager


def _wire_pool(app, mock_pool):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return mock_db


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestTasteSummary:
    async def test_populated_counts_and_grouping(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=[220, 340, 61, 12])
        pool.fetch = AsyncMock(
            side_effect=[
                [_row({"kind": "track", "n": 220})],
                [
                    _row({"signal_kind": "listen_completed", "n": 200}),
                    _row({"signal_kind": "listen_skipped", "n": 140}),
                ],
            ]
        )
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/summary")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_works"] == 220
        assert data["total_signals"] == 340
        assert data["total_verdicts"] == 61
        assert data["recent_signals_7d"] == 12
        assert data["works_by_kind"] == {"track": 220}
        assert data["signals_by_kind"] == {"listen_completed": 200, "listen_skipped": 140}
        assert data["ledger_available"] is True

    async def test_pre_migration_missing_table_reports_unavailable(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(
            side_effect=asyncpg.UndefinedTableError("relation does not exist")
        )
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/summary")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Taste ledger is not available"

    async def test_genuine_query_failure_sets_ledger_unavailable(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("connection reset"))
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/summary")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ledger_available"] is False
        assert data["total_works"] == 0

    async def test_missing_pool_returns_503(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("lifestyle")
        app.dependency_overrides[_get_db_dep()] = lambda: mock_db

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/summary")

        assert resp.status_code == 503


class TestTasteWorks:
    async def test_meta_total_reflects_count_not_page_length(self, app):
        """The literal bug this bead fixes: total must come from COUNT(*), not
        len(page), so it can exceed the returned page."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=250)
        pool.fetch = AsyncMock(
            return_value=[
                _row(
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "kind": "track",
                        "title": "Song A",
                        "external_ids": {"primary": "spotify:track:a"},
                        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
                    }
                )
            ]
        )
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/works", params={"limit": 1})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["meta"]["total"] == 250
        assert body["meta"]["total"] > len(body["data"])

    async def test_kind_filter_applied_to_both_count_and_rows(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = AsyncMock(return_value=[])
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/works", params={"kind": "track"})

        assert resp.status_code == 200
        assert pool.fetchval.await_args.args[-1] == "track"
        assert pool.fetch.await_args.args[1] == "track"

    async def test_pre_migration_missing_table_reports_unavailable(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(
            side_effect=asyncpg.UndefinedTableError("relation does not exist")
        )
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/works")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Taste ledger is not available"


class TestTasteVerdicts:
    async def test_lists_verdicts_newest_first_with_real_total(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=61)
        pool.fetch = AsyncMock(
            return_value=[
                _row(
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "work_id": None,
                        "predicate": "likes_genre",
                        "verdict_text": "loves jazz",
                        "source": "legacy_fact",
                        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
                    }
                )
            ]
        )
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/verdicts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 61
        assert body["data"][0]["verdict_text"] == "loves jazz"
        assert body["data"][0]["work_id"] is None

    async def test_pre_migration_missing_table_reports_unavailable(self, app):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(
            side_effect=asyncpg.UndefinedTableError("relation does not exist")
        )
        _wire_pool(app, pool)

        async with await _client(app) as client:
            resp = await client.get("/api/lifestyle/taste/verdicts")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Taste ledger is not available"
