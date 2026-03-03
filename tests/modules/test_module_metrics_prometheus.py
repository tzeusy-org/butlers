"""Tests for Prometheus query layer and read-side MCP tools (butlers-lxiq.4).

Covers acceptance criteria:
1. metrics_query returns vector result on success
2. PromQL parse errors (Prometheus 400) are surfaced as error strings
3. Network errors are caught and returned as descriptive strings (no unhandled exceptions)
4. metrics_query_range returns matrix result on success
5. Invalid step errors surfaced from Prometheus
6. metrics_list returns all definitions; returns empty list when none exist
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.modules.metrics.prometheus import async_query, async_query_range

pytestmark = pytest.mark.unit

PROM_URL = "http://prometheus:9090"

# ---------------------------------------------------------------------------
# Helpers — build fake httpx.Response objects
# ---------------------------------------------------------------------------


def _make_response(status_code: int, body: dict | str) -> httpx.Response:
    """Return a minimal httpx.Response with a fake request attached.

    ``raise_for_status()`` requires the response to have a request set;
    we attach a dummy one so the response behaves like a real network response.
    """
    if isinstance(body, dict):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = body.encode()
        headers = {"content-type": "text/plain"}
    # Attach a fake request so raise_for_status() works correctly.
    fake_request = httpx.Request("GET", "http://prometheus:9090/api/v1/query")
    return httpx.Response(status_code, content=content, headers=headers, request=fake_request)


def _prom_vector_response(results: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "vector", "result": results}}


def _prom_matrix_response(results: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": results}}


def _mock_client(response: httpx.Response | Exception):
    """Return a context-manager mock that yields a fake AsyncClient.

    ``response`` is either the value returned by client.get() or an exception
    to be raised.
    """
    mock_client = MagicMock()
    if isinstance(response, Exception):
        mock_client.get = AsyncMock(side_effect=response)
    else:
        mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls


# ---------------------------------------------------------------------------
# AC1: metrics_query returns vector result on success
# ---------------------------------------------------------------------------


class TestAsyncQuerySuccess:
    """async_query returns Prometheus vector result on success."""

    async def test_returns_vector_result(self):
        expected = [{"metric": {"__name__": "up", "job": "prometheus"}, "value": [1234, "1"]}]
        resp = _make_response(200, _prom_vector_response(expected))

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query(PROM_URL, "up")

        assert result == expected

    async def test_returns_empty_list_when_no_series(self):
        resp = _make_response(200, _prom_vector_response([]))

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query(PROM_URL, "nonexistent_metric")

        assert result == []

    async def test_passes_query_param(self):
        resp = _make_response(200, _prom_vector_response([]))
        mock_cls = _mock_client(resp)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", mock_cls):
            await async_query(PROM_URL, "up")

        call_kwargs = mock_cls.return_value.get.call_args
        assert call_kwargs.kwargs["params"]["query"] == "up"

    async def test_passes_optional_time_param(self):
        resp = _make_response(200, _prom_vector_response([]))
        mock_cls = _mock_client(resp)
        ts = "2024-01-01T00:00:00Z"

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", mock_cls):
            await async_query(PROM_URL, "up", time=ts)

        call_kwargs = mock_cls.return_value.get.call_args
        assert call_kwargs.kwargs["params"]["time"] == ts

    async def test_omits_time_param_when_none(self):
        resp = _make_response(200, _prom_vector_response([]))
        mock_cls = _mock_client(resp)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", mock_cls):
            await async_query(PROM_URL, "up", time=None)

        call_kwargs = mock_cls.return_value.get.call_args
        assert "time" not in call_kwargs.kwargs["params"]

    async def test_calls_correct_endpoint(self):
        resp = _make_response(200, _prom_vector_response([]))
        mock_cls = _mock_client(resp)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", mock_cls):
            await async_query(PROM_URL, "up")

        call_args = mock_cls.return_value.get.call_args
        assert call_args.args[0] == f"{PROM_URL}/api/v1/query"


# ---------------------------------------------------------------------------
# AC2: PromQL parse errors (Prometheus 400) are surfaced as error strings
# ---------------------------------------------------------------------------


class TestAsyncQueryPromQLErrors:
    """Prometheus 400 errors are returned as [{"error": "..."}] without raising."""

    async def test_400_promql_error_returned_as_error_dict(self):
        error_body = {"status": "error", "errorType": "bad_data", "error": "invalid PromQL"}
        resp = _make_response(400, error_body)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query(PROM_URL, "bad{PromQL")

        assert len(result) == 1
        assert "error" in result[0]
        assert "invalid PromQL" in result[0]["error"]

    async def test_500_server_error_returned_as_error_dict(self):
        resp = _make_response(500, "Internal Server Error")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query(PROM_URL, "up")

        assert len(result) == 1
        assert "error" in result[0]

    async def test_non_success_status_in_body_surfaced(self):
        """Prometheus returns 200 with status=error for some query errors."""
        error_body = {
            "status": "error",
            "errorType": "execution",
            "error": "query timed out",
        }
        resp = _make_response(200, error_body)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query(PROM_URL, "up")

        assert len(result) == 1
        assert "error" in result[0]
        assert "query timed out" in result[0]["error"]

    async def test_does_not_raise_on_http_error(self):
        """async_query must never raise regardless of HTTP status."""
        resp = _make_response(503, "Service Unavailable")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            # Should not raise
            result = await async_query(PROM_URL, "up")

        assert isinstance(result, list)
        assert result[0].get("error")


# ---------------------------------------------------------------------------
# AC3: Network errors are caught and returned as descriptive strings
# ---------------------------------------------------------------------------


class TestAsyncQueryNetworkErrors:
    """Network errors are caught and returned as [{"error": "..."}] without raising."""

    async def test_connect_error_returns_error_dict(self):
        exc = httpx.ConnectError("Connection refused")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(exc)):
            result = await async_query(PROM_URL, "up")

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["error"]  # non-empty

    async def test_timeout_returns_error_dict(self):
        exc = httpx.TimeoutException("Request timed out")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(exc)):
            result = await async_query(PROM_URL, "up")

        assert len(result) == 1
        assert "error" in result[0]

    async def test_no_unhandled_exceptions_on_network_error(self):
        """async_query must never raise; all errors returned as dicts."""
        exc = httpx.NetworkError("DNS resolution failed")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(exc)):
            result = await async_query(PROM_URL, "up")

        assert isinstance(result, list)
        assert result[0].get("error")

    async def test_error_string_is_descriptive(self):
        """Error messages should describe the network problem."""
        exc = httpx.ConnectError("Connection refused")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(exc)):
            result = await async_query(PROM_URL, "up")

        error_msg = result[0]["error"]
        # Should mention the nature of the error, not just "error"
        assert len(error_msg) > 5


# ---------------------------------------------------------------------------
# AC4: metrics_query_range returns matrix result on success
# ---------------------------------------------------------------------------


class TestAsyncQueryRangeSuccess:
    """async_query_range returns Prometheus matrix result on success."""

    async def test_returns_matrix_result(self):
        expected = [
            {
                "metric": {"__name__": "up"},
                "values": [[1234567890, "1"], [1234567905, "1"]],
            }
        ]
        resp = _make_response(200, _prom_matrix_response(expected))

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query_range(
                PROM_URL, "up", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "15s"
            )

        assert result == expected

    async def test_returns_empty_list_when_no_series(self):
        resp = _make_response(200, _prom_matrix_response([]))

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query_range(
                PROM_URL,
                "nonexistent_metric",
                "2024-01-01T00:00:00Z",
                "2024-01-01T01:00:00Z",
                "15s",
            )

        assert result == []

    async def test_passes_all_params(self):
        resp = _make_response(200, _prom_matrix_response([]))
        mock_cls = _mock_client(resp)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", mock_cls):
            await async_query_range(
                PROM_URL,
                "rate(http_requests_total[5m])",
                "2024-01-01T00:00:00Z",
                "2024-01-01T01:00:00Z",
                "60s",
            )

        call_kwargs = mock_cls.return_value.get.call_args
        params = call_kwargs.kwargs["params"]
        assert params["query"] == "rate(http_requests_total[5m])"
        assert params["start"] == "2024-01-01T00:00:00Z"
        assert params["end"] == "2024-01-01T01:00:00Z"
        assert params["step"] == "60s"

    async def test_calls_correct_endpoint(self):
        resp = _make_response(200, _prom_matrix_response([]))
        mock_cls = _mock_client(resp)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", mock_cls):
            await async_query_range(
                PROM_URL, "up", "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "15s"
            )

        call_args = mock_cls.return_value.get.call_args
        assert call_args.args[0] == f"{PROM_URL}/api/v1/query_range"


# ---------------------------------------------------------------------------
# AC5: Invalid step errors surfaced from Prometheus
# ---------------------------------------------------------------------------


class TestAsyncQueryRangeErrors:
    """Invalid step and other range-query errors are surfaced without raising."""

    async def test_invalid_step_400_returned_as_error_dict(self):
        error_body = {
            "status": "error",
            "errorType": "bad_data",
            "error": (
                "invalid parameter 'step': "
                "zero or negative query resolution step widths are not accepted"
            ),
        }
        resp = _make_response(400, error_body)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query_range(PROM_URL, "up", "2024-01-01", "2024-01-02", "0s")

        assert len(result) == 1
        assert "error" in result[0]
        # The Prometheus error message about step should be surfaced
        assert "step" in result[0]["error"] or "resolution" in result[0]["error"]

    async def test_non_success_status_in_range_body_surfaced(self):
        error_body = {"status": "error", "errorType": "timeout", "error": "range query timed out"}
        resp = _make_response(200, error_body)

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query_range(PROM_URL, "up", "2024-01-01", "2024-01-02", "15s")

        assert len(result) == 1
        assert "error" in result[0]

    async def test_range_network_error_no_exception_raised(self):
        exc = httpx.ConnectError("Connection refused")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(exc)):
            result = await async_query_range(PROM_URL, "up", "2024-01-01", "2024-01-02", "15s")

        assert isinstance(result, list)
        assert result[0].get("error")

    async def test_range_does_not_raise_on_http_error(self):
        resp = _make_response(503, "Service Unavailable")

        with patch("butlers.modules.metrics.prometheus.httpx.AsyncClient", _mock_client(resp)):
            result = await async_query_range(PROM_URL, "up", "2024-01-01", "2024-01-02", "15s")

        assert isinstance(result, list)
        assert result[0].get("error")


# ---------------------------------------------------------------------------
# AC6: metrics_list returns all definitions; empty list when none
# ---------------------------------------------------------------------------


class TestMetricsListMCPTool:
    """metrics_list MCP tool returns definitions from the storage layer."""

    def _setup_module_with_captured_tools(self, cfg, db) -> tuple[Any, dict]:
        """Helper: create MetricsModule with a tool-capturing fake MCP."""
        from butlers.modules.metrics import MetricsModule

        mod = MetricsModule()
        registered_tools: dict = {}

        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        return mod, fake_mcp, registered_tools

    async def test_metrics_list_returns_all_definitions(self, monkeypatch):
        from butlers.modules.metrics import MetricsModule, MetricsModuleConfig, storage

        defns = [
            {
                "name": "req_count",
                "type": "counter",
                "help": "Requests",
                "labels": ["status"],
                "registered_at": "2024-01-01",
            },
            {
                "name": "active_sessions",
                "type": "gauge",
                "help": "Active sessions",
                "labels": [],
                "registered_at": "2024-01-01",
            },
        ]
        monkeypatch.setattr(
            storage,
            "state_list",
            AsyncMock(
                return_value=[
                    {"key": "metrics_catalogue:req_count", "value": defns[0]},
                    {"key": "metrics_catalogue:active_sessions", "value": defns[1]},
                ]
            ),
        )

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        cfg = MetricsModuleConfig(prometheus_query_url=PROM_URL)
        fake_db = MagicMock()

        await mod.register_tools(mcp=fake_mcp, config=cfg, db=fake_db)

        assert "metrics_list" in registered_tools
        result = await registered_tools["metrics_list"]()
        assert result == defns

    async def test_metrics_list_returns_empty_list_when_no_definitions(self, monkeypatch):
        from butlers.modules.metrics import MetricsModule, MetricsModuleConfig, storage

        monkeypatch.setattr(storage, "state_list", AsyncMock(return_value=[]))

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        cfg = MetricsModuleConfig(prometheus_query_url=PROM_URL)
        fake_db = MagicMock()

        await mod.register_tools(mcp=fake_mcp, config=cfg, db=fake_db)

        result = await registered_tools["metrics_list"]()
        assert result == []

    async def test_metrics_list_returns_empty_list_when_db_is_none(self):
        from butlers.modules.metrics import MetricsModule, MetricsModuleConfig

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        cfg = MetricsModuleConfig(prometheus_query_url=PROM_URL)

        await mod.register_tools(mcp=fake_mcp, config=cfg, db=None)

        result = await registered_tools["metrics_list"]()
        assert result == []


# ---------------------------------------------------------------------------
# MCP tool wiring — metrics_query integration with module
# ---------------------------------------------------------------------------


class TestMetricsQueryMCPTool:
    """metrics_query MCP tool delegates to async_query correctly."""

    async def test_metrics_query_returns_vector_result(self, monkeypatch):
        import butlers.modules.metrics as metrics_module
        from butlers.modules.metrics import MetricsModule, MetricsModuleConfig

        expected = [{"metric": {"__name__": "up"}, "value": [1234, "1"]}]
        # Patch the name as imported into __init__.py (the closure references it directly).
        monkeypatch.setattr(metrics_module, "async_query", AsyncMock(return_value=expected))

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        cfg = MetricsModuleConfig(prometheus_query_url=PROM_URL)

        await mod.register_tools(mcp=fake_mcp, config=cfg, db=MagicMock())

        result = await registered_tools["metrics_query"](query="up")
        assert result == expected

    async def test_metrics_query_returns_error_when_config_none(self):
        from butlers.modules.metrics import MetricsModule

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        await mod.register_tools(mcp=fake_mcp, config=None, db=MagicMock())

        result = await registered_tools["metrics_query"](query="up")
        assert len(result) == 1
        assert "error" in result[0]


# ---------------------------------------------------------------------------
# MCP tool wiring — metrics_query_range integration with module
# ---------------------------------------------------------------------------


class TestMetricsQueryRangeMCPTool:
    """metrics_query_range MCP tool delegates to async_query_range correctly."""

    async def test_metrics_query_range_returns_matrix_result(self, monkeypatch):
        import butlers.modules.metrics as metrics_module
        from butlers.modules.metrics import MetricsModule, MetricsModuleConfig

        expected = [{"metric": {}, "values": [[1234, "1"], [1249, "1"]]}]
        # Patch the name as imported into __init__.py (the closure references it directly).
        monkeypatch.setattr(metrics_module, "async_query_range", AsyncMock(return_value=expected))

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        cfg = MetricsModuleConfig(prometheus_query_url=PROM_URL)

        await mod.register_tools(mcp=fake_mcp, config=cfg, db=MagicMock())

        result = await registered_tools["metrics_query_range"](
            query="up",
            start="2024-01-01T00:00:00Z",
            end="2024-01-01T01:00:00Z",
            step="15s",
        )
        assert result == expected

    async def test_metrics_query_range_returns_error_when_config_none(self):
        from butlers.modules.metrics import MetricsModule

        mod = MetricsModule()
        registered_tools: dict = {}
        fake_mcp = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        fake_mcp.tool = capture_tool
        await mod.register_tools(mcp=fake_mcp, config=None, db=MagicMock())

        result = await registered_tools["metrics_query_range"](
            query="up",
            start="2024-01-01T00:00:00Z",
            end="2024-01-01T01:00:00Z",
            step="15s",
        )
        assert len(result) == 1
        assert "error" in result[0]
