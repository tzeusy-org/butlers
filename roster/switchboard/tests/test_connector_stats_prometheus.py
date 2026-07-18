"""Tests for connector stats and fanout endpoints.

The FANOUT endpoints remain Prometheus-backed (butlers-ufzc); the connector
STATS time-series endpoint is now sourced entirely from the database (bu-c48im)
— it no longer consults Prometheus at all (Prometheus has no per-connector
skip/filtered metric, and its former source_api_calls/dedupe counters were
unrendered). The real-schema behaviour of the stats UNION (ingestion_events +
partitioned connectors.filtered_events) is covered by the integration test
tests/integration/test_connector_stats_filtered_events_db.py; the mocked-pool
tests here pin the Python-side mapping and SQL shape.

Tested behaviors:
- get_connector_stats: DB-sourced hourly/daily series from public.ingestion_events
  UNIONed with connectors.filtered_events, carrying a DISTINCT messages_filtered
  series and a meta.hourly_events_available degraded flag. Websocket connectors
  (e.g. home_assistant) that never write heartbeat counter-deltas still show
  non-zero volume via this DB path.
- get_connector_fanout: queries Prometheus instant API for per-connector fanout;
  returns empty list when PROMETHEUS_URL is not set (no DB fallback for fanout).
- get_ingestion_fanout: queries Prometheus instant API for cross-connector matrix.
  Falls back to DB-backed fan-out when PROMETHEUS_URL is not set or Prometheus
  returns an error.
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

    The connector-stats DB path calls pool.fetch() to query the ingestion_events
    + filtered_events UNION. Returning [] simulates a connector with no events
    (empty timeseries).
    """

    async def fetchrow(self, *args, **kwargs):
        raise RuntimeError("Should not query DB via fetchrow in these endpoints")

    async def fetch(self, *args, **kwargs):
        return []

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("Should not query DB via fetchval in these endpoints")


class _FakePoolWithRows:
    """Pool stub returning synthetic UNION rows (ingested/failed/filtered) for
    connector-stats DB-path tests."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def fetchrow(self, *args, **kwargs):
        raise RuntimeError("Not used in connector stats DB path")

    async def fetch(self, *args, **kwargs):
        return self._rows

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("Not used in connector stats DB path")


class _FakeDBWithRows:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def pool(self, name: str):
        return _FakePoolWithRows(self._rows)

    @property
    def butler_names(self) -> list[str]:
        return []

    async def fan_out_with_status(
        self, query: str, args: tuple = (), butler_names=None
    ) -> tuple[dict, list[str]]:
        return {}, []


class _FakeDB:
    def pool(self, name: str):
        return _FakePool()

    @property
    def butler_names(self) -> list[str]:
        return []

    async def fan_out_with_status(
        self, query: str, args: tuple = (), butler_names=None
    ) -> tuple[dict, list[str]]:
        return {}, []


# ---------------------------------------------------------------------------
# Tests: get_connector_stats endpoint — DB-sourced series (bu-c48im)
# ---------------------------------------------------------------------------


async def test_get_connector_stats_empty_db_returns_empty():
    """get_connector_stats sources the series from the DB UNION. An empty pool
    (no events) returns an empty list with the degraded flag left honestly True."""
    import importlib
    from pathlib import Path

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
    # Empty pool → empty series; a no-rows result is not a failure, so the
    # degraded flag stays True (only a genuine query error flips it false).
    assert result.data == []
    assert result.meta.hourly_events_available is True


async def test_get_connector_stats_websocket_connector_db_sourced():
    """Websocket connectors (e.g. home_assistant) that never write heartbeat
    counter-deltas correctly show non-zero volume via the DB UNION path."""
    import importlib
    from datetime import UTC, datetime
    from pathlib import Path

    # Simulate two hours of UNION rows for a websocket connector
    bucket1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    bucket2 = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

    # asyncpg returns Record objects; use dicts with dict-like access via mapping
    class _FakeRecord(dict):
        pass

    # bu-c48im: the DB series is now skip-aware — the UNION query returns a
    # messages_filtered column (connectors.filtered_events volume) alongside
    # ingested/failed.
    fake_rows = [
        _FakeRecord(
            {
                "bucket": bucket1,
                "messages_ingested": 120,
                "messages_failed": 2,
                "messages_filtered": 9,
            }
        ),
        _FakeRecord(
            {
                "bucket": bucket2,
                "messages_ingested": 87,
                "messages_failed": 0,
                "messages_filtered": 0,
            }
        ),
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
    assert row0.messages_filtered == 9  # skip-aware DB series (bu-c48im)
    assert hasattr(row0, "hour")
    # Skip series is DISTINCT — never folded into messages_ingested.
    assert row0.messages_ingested == 120
    row1 = result.data[1]
    assert row1.messages_ingested == 87
    assert row1.messages_failed == 0
    assert row1.messages_filtered == 0
    # Degraded flag rides the meta bag, honestly True on a successful query.
    assert result.meta is not None
    assert result.meta.hourly_events_available is True


# ---------------------------------------------------------------------------
# Tests: get_connector_stats period → daily rows (DB-sourced, bu-c48im)
# ---------------------------------------------------------------------------


async def test_get_connector_stats_7d_returns_daily_rows():
    """period=7d returns ConnectorStatsDaily rows from the DB path, with the
    DISTINCT messages_filtered series preserved per bucket."""
    from datetime import UTC, datetime

    class _FakeRecord(dict):
        pass

    day1 = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)
    fake_rows = [
        _FakeRecord(
            {
                "bucket": day1,
                "messages_ingested": 100,
                "messages_failed": 4,
                "messages_filtered": 12,
            }
        ),
    ]

    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_7d_test", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.get_connector_stats(
        connector_type="email",
        endpoint_identity="user@example.com",
        period="7d",
        db=_FakeDBWithRows(fake_rows),
    )

    assert len(result.data) == 1
    row = result.data[0]
    # ConnectorStatsDaily has .day attribute
    assert hasattr(row, "day")
    assert row.connector_type == "email"
    assert row.endpoint_identity == "user@example.com"
    assert row.messages_ingested == 100
    assert row.messages_filtered == 12
    assert result.meta.hourly_events_available is True


async def test_get_connector_stats_omits_unrendered_legacy_counters():
    """Both hourly and daily DB buckets omit counters they never populated.

    Lifetime source API and dedupe totals remain available through the connector
    registry; these time-series rows only report event volume for their bucket.
    """
    from datetime import UTC, datetime

    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_legacy_counter_contract", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for period in ("24h", "7d"):
        result = await mod.get_connector_stats(
            connector_type="telegram_bot",
            endpoint_identity="bot@123",
            period=period,
            db=_FakeDBWithRows(
                [
                    {
                        "bucket": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                        "messages_ingested": 8,
                        "messages_failed": 1,
                        "messages_filtered": 3,
                    }
                ]
            ),
        )

        payload = result.data[0].model_dump()
        assert payload["messages_ingested"] == 8
        assert payload["messages_failed"] == 1
        assert payload["messages_filtered"] == 3
        assert "source_api_calls" not in payload
        assert "dedupe_accepted" not in payload


async def test_get_connector_stats_db_failure_degrades_honestly():
    """A genuine DB-query failure returns an empty series with the
    meta.hourly_events_available flag flipped false — never a fabricated
    clean-zero chart (bu-c48im)."""

    class _RaisingPool:
        async def fetch(self, *args, **kwargs):
            raise RuntimeError("connection reset")

        async def fetchrow(self, *args, **kwargs):
            raise RuntimeError("not expected")

        async def fetchval(self, *args, **kwargs):
            raise RuntimeError("not expected")

    class _RaisingDB:
        def pool(self, name: str):
            return _RaisingPool()

        @property
        def butler_names(self) -> list[str]:
            return []

        async def fan_out_with_status(
            self, query: str, args: tuple = (), butler_names=None
        ) -> tuple[dict, list[str]]:
            return {}, []

    sys.modules.pop("switchboard_api_models", None)
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_degrade_test", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.get_connector_stats(
        connector_type="telegram_bot",
        endpoint_identity="bot@123",
        period="24h",
        db=_RaisingDB(),
    )

    assert result.data == []
    assert result.meta.hourly_events_available is False


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

    # _FakeDB.fan_out_with_status returns empty dict (no butlers / no rows) → empty data list
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

            # _FakeDB.fan_out_with_status returns empty dict → empty data list
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
      so asyncpg decodes a tz-aware datetime (not naive),
    - UNION connectors.filtered_events so the skip series is skip-aware (bu-c48im),
      all in a SINGLE fetch() call.

    This test captures the actual SQL sent to the pool and asserts these properties.
    """
    import importlib
    from pathlib import Path

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

        async def fan_out_with_status(
            self, query: str, args: tuple = (), butler_names=None
        ) -> tuple[dict, list[str]]:
            return {}, []

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
    # AT TIME ZONE 'UTC' appears (inside + after date_trunc) on each UNION branch,
    # so at least two occurrences overall.
    tz_count = sql.count("AT TIME ZONE 'UTC'")
    assert tz_count >= 2, (
        f"Query must apply AT TIME ZONE 'UTC' twice (inside and after date_trunc) to produce "
        f"a tz-aware bucket; found {tz_count} occurrence(s) in: {sql!r}"
    )
    # Skip-aware (bu-c48im): the series UNIONs connectors.filtered_events so a
    # self-persisting connector's skip volume is not invisible on the histogram.
    assert "connectors.filtered_events" in sql, (
        "Query must UNION connectors.filtered_events to source the DISTINCT "
        "messages_filtered skip series"
    )
    assert "UNION ALL" in sql
