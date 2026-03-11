"""Tests for the DB-backed fanout fallback in GET /ingestion/fanout.

The /ingestion/fanout endpoint derives the connector × butler fanout matrix
from Prometheus when available.  When Prometheus is not configured or returns
an error, it falls back to a DB query that fans out across butler sessions
tables and joins against shared.ingestion_events.

This approach correctly handles all triage decisions — including pass_through
messages where triage_target is NULL — because it uses the butler whose
sessions table actually contains the request_id, not the triage_target column.

These tests verify:
- DB fallback is used when PROMETHEUS_URL is not set.
- DB fallback is used when Prometheus returns an error.
- DB fallback correctly aggregates rows from multiple butlers.
- DB fallback produces empty results when no sessions exist.
- DB fallback exception is caught and returns empty data (not 500).
- The SQL query used by _ingestion_fanout_from_db joins sessions with
  shared.ingestion_events and groups by connector + butler.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: load the router module with a fresh import (avoids module-cache
# contamination from other test files that also import the router).
# ---------------------------------------------------------------------------


def _load_router(module_name: str = "_sw_router_db_fallback"):
    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location(module_name, router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fake DatabaseManager with controllable fan_out results
# ---------------------------------------------------------------------------


class _FakeFanoutDB:
    """DatabaseManager stub with a configurable fan_out response."""

    def __init__(self, fan_out_result: dict | None = None) -> None:
        self._fan_out_result: dict = fan_out_result or {}
        self._fan_out_calls: list[str] = []
        self._fan_out_args: list[tuple] = []

    @property
    def butler_names(self) -> list[str]:
        return list(self._fan_out_result.keys())

    def pool(self, name: str):
        raise RuntimeError("_FakeFanoutDB.pool() should not be called in DB-fallback tests")

    async def fan_out(self, query: str, args: tuple = (), butler_names=None) -> dict:
        self._fan_out_calls.append(query)
        self._fan_out_args.append(args)
        return self._fan_out_result


class _FakeRow(dict):
    """asyncpg.Record-like dict with attribute access."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


# ---------------------------------------------------------------------------
# Tests: DB fallback is used when Prometheus is absent
# ---------------------------------------------------------------------------


async def test_db_fallback_used_when_no_prometheus_url():
    """When PROMETHEUS_URL is not set, fan_out is called (DB fallback)."""
    os.environ.pop("PROMETHEUS_URL", None)

    db = _FakeFanoutDB(fan_out_result={})
    mod = _load_router("_sw_fanout_nourl_db")
    await mod.get_ingestion_fanout(period="24h", db=db)

    assert len(db._fan_out_calls) == 1, "fan_out must be called for DB fallback"
    assert "shared.ingestion_events" in db._fan_out_calls[0]
    assert "sessions" in db._fan_out_calls[0]


async def test_db_fallback_empty_when_no_sessions():
    """DB fallback returns empty list when fan_out returns no rows."""
    os.environ.pop("PROMETHEUS_URL", None)

    db = _FakeFanoutDB(fan_out_result={})
    mod = _load_router("_sw_fanout_empty")
    result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert result.data == []


async def test_db_fallback_single_butler_single_connector():
    """DB fallback correctly builds a FanoutRow from a single butler + connector."""
    os.environ.pop("PROMETHEUS_URL", None)

    fan_out_result = {
        "health": [
            _FakeRow(
                connector_type="gmail",
                endpoint_identity="user@example.com",
                message_count=5,
            ),
        ]
    }

    db = _FakeFanoutDB(fan_out_result=fan_out_result)
    mod = _load_router("_sw_fanout_single")
    result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert len(result.data) == 1
    row = result.data[0]
    assert row.connector_type == "gmail"
    assert row.endpoint_identity == "user@example.com"
    assert row.target_butler == "health"
    assert row.message_count == 5


async def test_db_fallback_multiple_butlers():
    """DB fallback aggregates rows from multiple butlers correctly."""
    os.environ.pop("PROMETHEUS_URL", None)

    fan_out_result = {
        "health": [
            _FakeRow(
                connector_type="gmail",
                endpoint_identity="user@example.com",
                message_count=3,
            ),
        ],
        "relationship": [
            _FakeRow(
                connector_type="telegram_bot",
                endpoint_identity="bot_123",
                message_count=10,
            ),
            _FakeRow(
                connector_type="gmail",
                endpoint_identity="user@example.com",
                message_count=7,
            ),
        ],
    }

    db = _FakeFanoutDB(fan_out_result=fan_out_result)
    mod = _load_router("_sw_fanout_multi")
    result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert len(result.data) == 3
    by_butler = {(r.target_butler, r.connector_type, r.endpoint_identity): r for r in result.data}

    assert ("health", "gmail", "user@example.com") in by_butler
    assert by_butler[("health", "gmail", "user@example.com")].message_count == 3

    assert ("relationship", "telegram_bot", "bot_123") in by_butler
    assert by_butler[("relationship", "telegram_bot", "bot_123")].message_count == 10

    assert ("relationship", "gmail", "user@example.com") in by_butler
    assert by_butler[("relationship", "gmail", "user@example.com")].message_count == 7


async def test_db_fallback_sorted_by_connector_then_count_desc():
    """DB fallback sorts rows by (connector_type, endpoint_identity, -message_count)."""
    os.environ.pop("PROMETHEUS_URL", None)

    fan_out_result = {
        "butler_b": [
            _FakeRow(
                connector_type="telegram_bot",
                endpoint_identity="bot_a",
                message_count=5,
            ),
        ],
        "butler_a": [
            _FakeRow(
                connector_type="gmail",
                endpoint_identity="user@example.com",
                message_count=20,
            ),
            _FakeRow(
                connector_type="telegram_bot",
                endpoint_identity="bot_a",
                message_count=15,
            ),
        ],
    }

    db = _FakeFanoutDB(fan_out_result=fan_out_result)
    mod = _load_router("_sw_fanout_sorted")
    result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert len(result.data) == 3
    # sorted by (connector_type, endpoint_identity, -message_count)
    # gmail < telegram_bot alphabetically, so gmail comes first
    assert result.data[0].connector_type == "gmail"
    # telegram_bot rows: butler_a has 15 > butler_b has 5 → butler_a first
    tg_rows = [r for r in result.data if r.connector_type == "telegram_bot"]
    assert tg_rows[0].target_butler == "butler_a"
    assert tg_rows[1].target_butler == "butler_b"


async def test_db_fallback_prometheus_error_triggers_db():
    """When Prometheus returns an error, DB fallback is invoked."""
    fake_error = [{"error": "upstream timeout"}]

    with patch(
        "butlers.modules.metrics.prometheus.async_query",
        new=AsyncMock(return_value=fake_error),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            fan_out_result = {
                "atlas": [
                    _FakeRow(
                        connector_type="gmail",
                        endpoint_identity="inbox@org.com",
                        message_count=8,
                    ),
                ],
            }
            db = _FakeFanoutDB(fan_out_result=fan_out_result)
            mod = _load_router("_sw_fanout_prom_err")
            result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert len(db._fan_out_calls) == 1, "fan_out must be called after Prometheus error"
    assert len(result.data) == 1
    row = result.data[0]
    assert row.connector_type == "gmail"
    assert row.endpoint_identity == "inbox@org.com"
    assert row.target_butler == "atlas"
    assert row.message_count == 8


async def test_db_fallback_exception_returns_empty():
    """When fan_out raises, DB fallback catches the error and returns empty list."""
    os.environ.pop("PROMETHEUS_URL", None)

    class _FailFanoutDB(_FakeFanoutDB):
        async def fan_out(self, query: str, args: tuple = (), butler_names=None) -> dict:
            raise RuntimeError("DB connection failed")

    db = _FailFanoutDB()
    mod = _load_router("_sw_fanout_exc")
    result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert result.data == []


async def test_db_fallback_query_contains_hours_interval():
    """DB fallback SQL passes the period's hours count as a query argument."""
    os.environ.pop("PROMETHEUS_URL", None)

    db = _FakeFanoutDB(fan_out_result={})
    mod = _load_router("_sw_fanout_interval")
    await mod.get_ingestion_fanout(period="7d", db=db)

    assert len(db._fan_out_calls) == 1
    sql = db._fan_out_calls[0]
    # Parameterized query: interval is passed via $1 arg, not interpolated
    assert "$1" in sql, f"Expected parameterized '$1' in SQL, got: {sql}"
    # 7d = 168 hours — must be passed as the first arg
    assert db._fan_out_args[0] == (168,), f"Expected args=(168,), got: {db._fan_out_args[0]}"


async def test_db_fallback_skips_zero_count_rows():
    """DB fallback omits rows where message_count is 0."""
    os.environ.pop("PROMETHEUS_URL", None)

    fan_out_result = {
        "health": [
            _FakeRow(
                connector_type="gmail",
                endpoint_identity="user@example.com",
                message_count=0,
            ),
        ],
        "relationship": [
            _FakeRow(
                connector_type="telegram_bot",
                endpoint_identity="bot_x",
                message_count=3,
            ),
        ],
    }

    db = _FakeFanoutDB(fan_out_result=fan_out_result)
    mod = _load_router("_sw_fanout_zero")
    result = await mod.get_ingestion_fanout(period="24h", db=db)

    assert len(result.data) == 1
    assert result.data[0].target_butler == "relationship"
    assert result.data[0].message_count == 3
