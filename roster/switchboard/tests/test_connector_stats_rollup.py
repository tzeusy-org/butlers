"""Tests for connector stats and fanout endpoints (OTel/Prometheus pipeline).

These tests verify the Prometheus-backed connector stats and fanout API
endpoints that replaced the deprecated SQL rollup pipeline (butlers-ufzc).

Tested behaviors:
- get_connector_stats: queries Prometheus range API, returns ConnectorStatsHourly
  (period=24h) or ConnectorStatsDaily (period=7d/30d).
- get_connector_fanout: queries Prometheus instant API for per-connector fanout.
- get_ingestion_fanout: queries Prometheus instant API for cross-connector matrix.
- All endpoints fall back to empty lists when PROMETHEUS_URL is not set.
- All endpoints fall back to empty lists when Prometheus returns an error.
- No-op stubs: the deprecated rollup functions now return empty result dicts.
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
# Tests: deprecated rollup stubs return empty results (no DB needed)
# ---------------------------------------------------------------------------


async def test_hourly_rollup_stub_returns_empty():
    """Deprecated run_connector_stats_hourly_rollup returns empty result without DB access."""
    jobs_path = Path(__file__).resolve().parents[1] / "jobs" / "connector_stats.py"
    spec = importlib.util.spec_from_file_location("_sw_connector_stats_jobs", jobs_path)
    jobs_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jobs_mod)

    result = await jobs_mod.run_connector_stats_hourly_rollup(None)
    assert result == {"rows_processed": 0, "connectors_updated": 0}


async def test_daily_rollup_stub_returns_empty():
    """Deprecated run_connector_stats_daily_rollup returns empty result without DB access."""
    jobs_path = Path(__file__).resolve().parents[1] / "jobs" / "connector_stats.py"
    spec = importlib.util.spec_from_file_location("_sw_connector_stats_jobs_daily", jobs_path)
    jobs_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jobs_mod)

    result = await jobs_mod.run_connector_stats_daily_rollup(None)
    assert result == {"stats_updated": 0, "fanout_updated": 0}


async def test_pruning_stub_returns_empty():
    """Deprecated run_connector_stats_pruning returns empty result without DB access."""
    jobs_path = Path(__file__).resolve().parents[1] / "jobs" / "connector_stats.py"
    spec = importlib.util.spec_from_file_location("_sw_connector_stats_jobs_pruning", jobs_path)
    jobs_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jobs_mod)

    result = await jobs_mod.run_connector_stats_pruning(None)
    assert result == {
        "heartbeat_partitions_dropped": 0,
        "hourly_rows_deleted": 0,
        "daily_rows_deleted": 0,
        "fanout_rows_deleted": 0,
    }


# ---------------------------------------------------------------------------
# Fixtures: minimal stubs for FastAPI dependency injection
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal pool stub that raises on any DB access."""

    async def fetchrow(self, *args, **kwargs):
        raise RuntimeError("Should not query DB in Prometheus-backed endpoints")

    async def fetch(self, *args, **kwargs):
        raise RuntimeError("Should not query DB in Prometheus-backed endpoints")

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("Should not query DB in Prometheus-backed endpoints")


class _FakeDB:
    def pool(self, name: str):
        return _FakePool()


# ---------------------------------------------------------------------------
# Tests: get_connector_stats endpoint — no Prometheus URL → empty list
# ---------------------------------------------------------------------------


async def test_get_connector_stats_no_prometheus_url():
    """When PROMETHEUS_URL is not set, get_connector_stats returns empty list."""
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
    assert result.data == []


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
# Tests: get_ingestion_fanout — no Prometheus URL → empty list
# ---------------------------------------------------------------------------


async def test_get_ingestion_fanout_no_prometheus_url():
    """When PROMETHEUS_URL is not set, get_ingestion_fanout returns empty list."""
    import os

    os.environ.pop("PROMETHEUS_URL", None)

    sys.modules.pop("switchboard_api_models", None)
    import importlib
    from pathlib import Path

    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_sw_router_ifanout_nourl", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

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


async def test_get_ingestion_fanout_prometheus_error_returns_empty():
    """When Prometheus returns an error, get_ingestion_fanout returns empty list."""
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
