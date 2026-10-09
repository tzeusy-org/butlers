"""REQ-core-telemetry-015: surviving HTTP transport, independent of MCP modules."""

from __future__ import annotations

import httpx
import pytest

from butlers.core import prometheus

pytestmark = pytest.mark.unit


async def _exercise_http_contract(monkeypatch, query, expected_path, expected_params, result):
    real_client = httpx.AsyncClient
    requests = []
    response = httpx.Response(200, json={"status": "success", "data": {"result": result}})
    failure = None

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == expected_path
        assert dict(request.url.params) == expected_params
        assert request.content == b""
        assert request.extensions["timeout"] == {
            "connect": 10.0,
            "read": 30.0,
            "write": 30.0,
            "pool": 30.0,
        }
        if failure is not None:
            raise failure
        return response

    def client(**kwargs):
        assert set(kwargs) == {"timeout"}
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(prometheus.httpx, "AsyncClient", client)
    assert await query() == result
    response = httpx.Response(200, json={"status": "success", "data": {"result": []}})
    assert await query() == []

    for response, expected in (
        (httpx.Response(400, json={"error": "invalid expression"}), "invalid expression"),
        (httpx.Response(422, json={"errorType": "bad_data"}), "bad_data"),
        (httpx.Response(503, json={}), "{}"),
        (httpx.Response(502, text="unavailable"), "unavailable"),
        (httpx.Response(200, json={"status": "error", "error": "query refused"}), "query refused"),
        (httpx.Response(200, json={"status": "unknown"}), "Prometheus returned status: unknown"),
    ):
        assert await query() == [{"error": expected}]

    response = httpx.Response(500, content=b"")
    assert "500" in (await query())[0]["error"]
    response = httpx.Response(200, text="not json")
    assert (await query())[0]["error"].startswith("Unexpected error during Prometheus")
    failure = httpx.ConnectError("controlled network failure")
    assert await query() == [
        {"error": "Network error contacting Prometheus: controlled network failure"}
    ]
    failure = RuntimeError("controlled unexpected failure")
    assert (await query())[0]["error"].endswith("controlled unexpected failure")
    failure = None
    # Relocation preserves the original extraction behavior outside the request try block.
    response = httpx.Response(200, json={"status": "success", "data": {}})
    with pytest.raises(KeyError, match="result"):
        await query()
    assert len(requests) == 13


async def test_instant_query_preserves_http_result_and_error_contract(monkeypatch):
    for evaluation_time in (None, "2026-10-04T00:00:00Z", ""):
        params = {"query": "up"}
        if evaluation_time is not None:
            params["time"] = evaluation_time
        with monkeypatch.context() as scoped:
            await _exercise_http_contract(
                scoped,
                lambda: prometheus.async_query("http://prometheus.invalid", "up", evaluation_time),
                "/api/v1/query",
                params,
                [{"metric": {"job": "fixture"}, "value": [1, "2"]}],
            )


async def test_range_query_preserves_http_result_and_error_contract(monkeypatch):
    await _exercise_http_contract(
        monkeypatch,
        lambda: prometheus.async_query_range("http://prometheus.invalid", "up", "1", "2", "15s"),
        "/api/v1/query_range",
        {"query": "up", "start": "1", "end": "2", "step": "15s"},
        [{"metric": {"job": "fixture"}, "values": [[1, "2"], [2, "3"]]}],
    )
