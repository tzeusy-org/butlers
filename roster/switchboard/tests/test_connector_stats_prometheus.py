"""Tests for connector stats and fanout endpoints (OTel/Prometheus pipeline).

These tests verify the Prometheus-backed connector stats and fanout API
endpoints that replaced the deprecated SQL rollup pipeline (butlers-ufzc).

Tested behaviors:
- get_connector_stats: queries Prometheus range API, returns ConnectorStatsHourly
  (period=24h) or ConnectorStatsDaily (period=7d/30d).
- get_connector_fanout: queries Prometheus instant API for per-connector fanout.
- get_ingestion_fanout: queries Prometheus instant API for cross-connector matrix.
  Falls back to DB-backed fan-out when PROMETHEUS_URL is not set or Prometheus
  returns an error.
- get_connector_stats falls back to public.ingestion_events when PROMETHEUS_URL is
  not set. Websocket connectors (e.g. home_assistant) that never write heartbeat
  counter-deltas correctly show non-zero volume via this fallback.
- get_connector_fanout returns empty list when PROMETHEUS_URL is not set (no DB
  fallback for fanout).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Helper: load the router module with a fresh import
# ---------------------------------------------------------------------------


def _load_router():
    """Reload the switchboard router to pick up patched env vars."""
    mod_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_under_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: minimal stubs for FastAPI dependency injection
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal pool stub — returns empty list for fetch, raises for fetchrow/fetchval.

    The no-Prometheus path now calls pool.fetch() to query public.ingestion_events.
    Returning [] simulates a connector with no events (empty timeseries).
    """

    async def fetchrow(self, *args, **kwargs):
        raise RuntimeError("Should not query DB via fetchrow in these endpoints")

    async def fetch(self, *args, **kwargs):
        return []

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("Should not query DB via fetchval in these endpoints")


class _FakePoolWithRows:
    """Pool stub that returns synthetic ingestion_events rows for websocket connector tests."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def fetchrow(self, *args, **kwargs):
        raise RuntimeError("Not used in connector stats fallback")

    async def fetch(self, *args, **kwargs):
        return self._rows

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("Not used in connector stats fallback")


class _FakeDBWithRows:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def pool(self, name: str):
        return _FakePoolWithRows(self._rows)

    @property
    def butler_names(self) -> list[str]:
        return []

    async def fan_out(self, query: str, args: tuple = (), butler_names=None) -> dict:
        return {}


class _FakeDB:
    def pool(self, name: str):
        return _FakePool()

    @property
    def butler_names(self) -> list[str]:
        return []

    async def fan_out(self, query: str, args: tuple = (), butler_names=None) -> dict:
        return {}


# ---------------------------------------------------------------------------
# Tests: get_connector_stats endpoint — no Prometheus URL → DB fallback
# ---------------------------------------------------------------------------


async def test_get_connector_stats_no_prometheus_url_empty_db():
    """When PROMETHEUS_URL is not set, get_connector_stats falls back to
    public.ingestion_events. An empty pool (no events) returns an empty list."""
    import importlib
    import os
    from pathlib import Path

    os.environ.pop("PROMETHEUS_URL", None)

    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_stats_nourl", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.get_connector_stats(
        connector_type="telegram_bot",
        endpoint_identity="bot@123",
        period="24h",
        db=_FakeDB(),
    )
    # DB fallback returns empty list when pool.fetch() returns no rows
    assert result.data == []


async def test_get_connector_stats_no_prometheus_url_websocket_connector():
    """Websocket connectors (e.g. home_assistant) that never write heartbeat
    counter-deltas correctly show non-zero volume via the ingestion_events fallback."""
    import importlib
    import os
    from datetime import UTC, datetime
    from pathlib import Path

    os.environ.pop("PROMETHEUS_URL", None)

    # Simulate two hours of ingestion_events rows for a websocket connector
    bucket1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    bucket2 = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

    # asyncpg returns Record objects; use dicts with dict-like access via mapping
    class _FakeRecord(dict):
        pass

    fake_rows = [
        _FakeRecord({"bucket": bucket1, "messages_ingested": 120, "messages_failed": 2}),
        _FakeRecord({"bucket": bucket2, "messages_ingested": 87, "messages_failed": 0}),
    ]

    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_ws_test", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.get_connector_stats(
        connector_type="home_assistant",
        endpoint_identity="ws://homeassistant.local:8123",
        period="24h",
        db=_FakeDBWithRows(fake_rows),
    )

    assert len(result.data) == 2
    row0 = result.data[0]
    assert row0.connector_type == "home_assistant"
    assert row0.endpoint_identity == "ws://homeassistant.local:8123"
    assert row0.messages_ingested == 120
    assert row0.messages_failed == 2
    assert hasattr(row0, "hour")
    row1 = result.data[1]
    assert row1.messages_ingested == 87
    assert row1.messages_failed == 0


# ---------------------------------------------------------------------------
# Tests: get_connector_stats — Prometheus returns data → ConnectorStatsHourly
# ---------------------------------------------------------------------------


async def test_get_connector_stats_24h_returns_hourly_rows():
    """get_connector_stats with period=24h returns ConnectorStatsHourly list from Prometheus."""
    # Fake Prometheus range query results
    fake_range_result = [
        {
            "metric": {
                "connector_type": "telegram_bot",
                "endpoint_identity": "bot@123",
            },
            "values": [
                [1740000000, "42"],
                [1740003600, "17"],
            ],
        }
    ]

    with patch(
        "butlers.modules.metrics.prometheus.async_query_range",
        new=AsyncMock(return_value=fake_range_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_24h_test", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Call the endpoint function directly
            class _FakeRequest:
                pass

            result = await mod.get_connector_stats(
                connector_type="telegram_bot",
                endpoint_identity="bot@123",
                period="24h",
                db=_FakeDB(),
            )

    assert result.data is not None
    assert len(result.data) > 0
    row = result.data[0]
    # ConnectorStatsHourly has .hour attribute
    assert hasattr(row, "hour")
    assert row.connector_type == "telegram_bot"
    assert row.endpoint_identity == "bot@123"
    assert row.messages_ingested == 42


async def test_get_connector_stats_prometheus_error_returns_empty():
    """When Prometheus returns an error dict, get_connector_stats returns empty list."""
    fake_error_result = [{"error": "connection refused"}]

    with patch(
        "butlers.modules.metrics.prometheus.async_query_range",
        new=AsyncMock(return_value=fake_error_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_err_test", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = await mod.get_connector_stats(
                connector_type="telegram_bot",
                endpoint_identity="bot@123",
                period="24h",
                db=_FakeDB(),
            )

    assert result.data == []


async def test_get_connector_stats_7d_returns_daily_rows():
    """get_connector_stats with period=7d returns ConnectorStatsDaily list from Prometheus."""
    fake_range_result = [
        {
            "metric": {},
            "values": [
                [1740000000, "100"],
                [1740086400, "200"],
            ],
        }
    ]

    with patch(
        "butlers.modules.metrics.prometheus.async_query_range",
        new=AsyncMock(return_value=fake_range_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_7d_test", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = await mod.get_connector_stats(
                connector_type="email",
                endpoint_identity="user@example.com",
                period="7d",
                db=_FakeDB(),
            )

    assert result.data is not None
    assert len(result.data) > 0
    row = result.data[0]
    # ConnectorStatsDaily has .day attribute
    assert hasattr(row, "day")
    assert row.connector_type == "email"
    assert row.endpoint_identity == "user@example.com"


# ---------------------------------------------------------------------------
# Tests: get_connector_fanout — no Prometheus URL → empty list
# ---------------------------------------------------------------------------


async def test_get_connector_fanout_no_prometheus_url():
    """When PROMETHEUS_URL is not set, get_connector_fanout returns empty list."""
    import os

    os.environ.pop("PROMETHEUS_URL", None)

    sys.modules.pop("switchboard_api_models", None)
    import importlib
    from pathlib import Path

    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_fanout_nourl", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.get_connector_fanout(
        connector_type="telegram_bot",
        endpoint_identity="bot@123",
        period="24h",
        db=_FakeDB(),
    )

    assert result.data == []


async def test_get_connector_fanout_returns_rows_from_prometheus():
    """get_connector_fanout returns FanoutRow list from Prometheus instant query."""
    fake_instant_result = [
        {
            "metric": {"target_butler": "health"},
            "value": [1740000000, "15"],
        },
        {
            "metric": {"target_butler": "relationship"},
            "value": [1740000000, "7"],
        },
    ]

    with patch(
        "butlers.modules.metrics.prometheus.async_query",
        new=AsyncMock(return_value=fake_instant_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_fanout_ok", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = await mod.get_connector_fanout(
                connector_type="telegram_bot",
                endpoint_identity="bot@123",
                period="24h",
                db=_FakeDB(),
            )

    assert result.data is not None
    assert len(result.data) == 2
    # Sorted by message_count DESC
    assert result.data[0].target_butler == "health"
    assert result.data[0].message_count == 15
    assert result.data[1].target_butler == "relationship"
    assert result.data[1].message_count == 7
    for row in result.data:
        assert row.connector_type == "telegram_bot"
        assert row.endpoint_identity == "bot@123"


async def test_get_connector_fanout_prometheus_error_returns_empty():
    """When Prometheus returns an error, get_connector_fanout returns empty list."""
    fake_error_result = [{"error": "timeout"}]

    with patch(
        "butlers.modules.metrics.prometheus.async_query",
        new=AsyncMock(return_value=fake_error_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_fanout_err", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = await mod.get_connector_fanout(
                connector_type="telegram_bot",
                endpoint_identity="bot@123",
                period="24h",
                db=_FakeDB(),
            )

    assert result.data == []


# ---------------------------------------------------------------------------
# Tests: get_ingestion_fanout — no Prometheus URL → DB fallback
# ---------------------------------------------------------------------------


async def test_get_ingestion_fanout_no_prometheus_url_uses_db_fallback():
    """When PROMETHEUS_URL is not set, get_ingestion_fanout falls back to DB (returns empty when
    DB has no sessions)."""
    import os

    os.environ.pop("PROMETHEUS_URL", None)

    sys.modules.pop("switchboard_api_models", None)
    import importlib
    from pathlib import Path

    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_ifanout_nourl", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # _FakeDB.fan_out returns empty dict (no butlers / no rows) → empty data list
    result = await mod.get_ingestion_fanout(
        period="24h",
        db=_FakeDB(),
    )

    assert result.data == []


async def test_get_ingestion_fanout_returns_matrix_from_prometheus():
    """get_ingestion_fanout returns cross-connector FanoutRow matrix from Prometheus."""
    fake_instant_result = [
        {
            "metric": {
                "connector_type": "telegram_bot",
                "endpoint_identity": "bot@123",
                "target_butler": "health",
            },
            "value": [1740000000, "20"],
        },
        {
            "metric": {
                "connector_type": "email",
                "endpoint_identity": "user@example.com",
                "target_butler": "relationship",
            },
            "value": [1740000000, "5"],
        },
    ]

    with patch(
        "butlers.modules.metrics.prometheus.async_query",
        new=AsyncMock(return_value=fake_instant_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_ifanout_ok", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = await mod.get_ingestion_fanout(
                period="24h",
                db=_FakeDB(),
            )

    assert result.data is not None
    assert len(result.data) == 2
    # Sorted by connector_type, endpoint_identity, -message_count
    connectors = [(r.connector_type, r.endpoint_identity, r.target_butler) for r in result.data]
    assert ("email", "user@example.com", "relationship") in connectors
    assert ("telegram_bot", "bot@123", "health") in connectors


async def test_get_ingestion_fanout_prometheus_error_falls_back_to_db():
    """When Prometheus returns an error, get_ingestion_fanout falls back to DB
    (returns empty when DB has no sessions)."""
    fake_error_result = [{"error": "bad request"}]

    with patch(
        "butlers.modules.metrics.prometheus.async_query",
        new=AsyncMock(return_value=fake_error_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_ifanout_err", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # _FakeDB.fan_out returns empty dict → empty data list
            result = await mod.get_ingestion_fanout(
                period="24h",
                db=_FakeDB(),
            )

    assert result.data == []


async def test_get_ingestion_fanout_filters_zero_count_rows():
    """get_ingestion_fanout skips series where count rounds to 0."""
    fake_instant_result = [
        {
            "metric": {
                "connector_type": "telegram_bot",
                "endpoint_identity": "bot@123",
                "target_butler": "health",
            },
            "value": [1740000000, "0.4"],  # rounds to 0
        },
        {
            "metric": {
                "connector_type": "telegram_bot",
                "endpoint_identity": "bot@123",
                "target_butler": "memory",
            },
            "value": [1740000000, "3.7"],  # rounds to 3
        },
    ]

    with patch(
        "butlers.modules.metrics.prometheus.async_query",
        new=AsyncMock(return_value=fake_instant_result),
    ):
        with patch.dict("os.environ", {"PROMETHEUS_URL": "http://fake-prom:9090"}):
            sys.modules.pop("switchboard_api_models", None)
            import importlib
            from pathlib import Path

            router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
            spec = importlib.util.spec_from_file_location("_sw_router_ifanout_zero", router_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = await mod.get_ingestion_fanout(
                period="24h",
                db=_FakeDB(),
            )

    assert len(result.data) == 1
    assert result.data[0].target_butler == "memory"
    assert result.data[0].message_count == 3


# ---------------------------------------------------------------------------
# Tests: _connector_stats_from_db SQL correctness (query shape)
# ---------------------------------------------------------------------------


async def test_connector_stats_db_query_uses_coalesce_and_tz_aware_bucket():
    """_connector_stats_from_db must:
    - filter on COALESCE(source_provider, source_channel) so websocket connectors
      stored under source_provider are not excluded,
    - produce a timezone-aware bucket by appending AT TIME ZONE 'UTC' after date_trunc
      so asyncpg decodes a tz-aware datetime (not naive).

    This test captures the actual SQL sent to the pool and asserts both properties.
    """
    import importlib
    import os
    from pathlib import Path

    os.environ.pop("PROMETHEUS_URL", None)

    captured_sql: list[str] = []

    class _CapturingPool:
        async def fetch(self, sql: str, *args, **kwargs):
            captured_sql.append(sql)
            return []

        async def fetchrow(self, *args, **kwargs):
            raise RuntimeError("not expected")

        async def fetchval(self, *args, **kwargs):
            raise RuntimeError("not expected")

    class _CapturingDB:
        def pool(self, name: str):
            return _CapturingPool()

        @property
        def butler_names(self) -> list[str]:
            return []

        async def fan_out(self, query: str, args: tuple = (), butler_names=None) -> dict:
            return {}

    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_sql_check", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    await mod.get_connector_stats(
        connector_type="home_assistant",
        endpoint_identity="ws://ha.local:8123",
        period="24h",
        db=_CapturingDB(),
    )

    assert len(captured_sql) == 1, "Expected exactly one fetch() call to the pool"
    sql = captured_sql[0]

    # Both bugs fixed: COALESCE filter and tz-aware bucket
    assert "COALESCE(source_provider, source_channel)" in sql, (
        "Query must use COALESCE(source_provider, source_channel) to match websocket connectors "
        "where connector type is stored in source_provider, not source_channel"
    )
    # Two AT TIME ZONE 'UTC' occurrences: one inside date_trunc arg, one after date_trunc
    tz_count = sql.count("AT TIME ZONE 'UTC'")
    assert tz_count >= 2, (
        f"Query must apply AT TIME ZONE 'UTC' twice (inside and after date_trunc) to produce "
        f"a tz-aware bucket; found {tz_count} occurrence(s) in: {sql!r}"
    )
