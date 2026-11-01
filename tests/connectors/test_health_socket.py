"""The shared connector ``/metrics`` response is scrapeable Prometheus text."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import Counter
from prometheus_client.parser import text_string_to_metric_families

from butlers.connectors.health_socket import prometheus_metrics_response

pytestmark = pytest.mark.unit

_PROBE = Counter("health_socket_test_probe", "Marker series for the metrics route test.")


def test_metrics_route_serves_prometheus_text_not_json():
    # Returning generate_latest() bytes from a FastAPI route JSON-encodes them
    # into one quoted string; the shared response must stay exposition text.
    _PROBE.inc()
    app = FastAPI()

    @app.get("/metrics")
    async def metrics():
        return prometheus_metrics_response()

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=")
    families = {f.name: f for f in text_string_to_metric_families(response.text)}
    assert families["health_socket_test_probe"].samples[0].value >= 1
