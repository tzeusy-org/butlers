"""Connector metrics sidecar: real loopback HTTP behavior and actual Helm render."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[2]
_CHART = _ROOT / "deploy/helm/butlers"
_SPEC = importlib.util.spec_from_file_location(
    "connector_metrics_proxy", _ROOT / "scripts/connector_metrics_proxy.py"
)
assert _SPEC is not None and _SPEC.loader is not None
proxy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(proxy)

_METRICS = b"# TYPE probe_total counter\nprobe_total 3.0\n"
_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class _FakeHealthServer(BaseHTTPRequestHandler):
    """Stands in for a connector health server: /metrics plus routes to keep private."""

    hits: list[tuple[str, str]] = []

    def _serve(self) -> None:
        self.hits.append((self.command, self.path))
        body = _METRICS if self.path == "/metrics" else b"private"
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _serve

    def log_message(self, format, *args):  # noqa: A002
        return


@contextmanager
def _serving(server: ThreadingHTTPServer) -> Iterator[int]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def _proxy_to(upstream_port: int) -> Iterator[str]:
    with _serving(proxy.build_server("127.0.0.1", 0, upstream_port, 2.0)) as port:
        yield f"http://127.0.0.1:{port}"


def _request(url: str, method: str = "GET") -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), response.headers["Content-Type"]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers["Content-Type"]


def test_proxy_relays_metrics_and_refuses_every_other_route():
    _FakeHealthServer.hits = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHealthServer)
    with _serving(upstream) as upstream_port, _proxy_to(upstream_port) as base:
        assert _request(f"{base}/metrics") == (200, _METRICS, _CONTENT_TYPE)
        assert _request(f"{base}/health")[0] == 404
        assert _request(f"{base}/reload", method="POST")[0] == 405
        assert _request(f"{base}/metrics", method="POST")[0] == 405

    # Only the metrics scrape ever reached the connector's health server.
    assert _FakeHealthServer.hits == [("GET", "/metrics")]


class _RedirectingHealthServer(_FakeHealthServer):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/metrics":
            return self._serve()
        self.hits.append((self.command, self.path))
        self.send_response(302)
        self.send_header("Location", "/health")
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_proxy_does_not_follow_upstream_redirects(monkeypatch):
    # An env proxy must not divert the loopback fetch either.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    _FakeHealthServer.hits = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectingHealthServer)
    with _serving(upstream) as upstream_port, _proxy_to(upstream_port) as base:
        assert _request(f"{base}/metrics")[0] == 502

    assert _FakeHealthServer.hits == [("GET", "/metrics")]


def test_proxy_reports_bad_gateway_while_connector_is_down():
    closed = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHealthServer)
    dead_port = closed.server_address[1]
    closed.server_close()
    with _proxy_to(dead_port) as base:
        assert _request(f"{base}/metrics")[0] == 502


def _render(*settings: str) -> list[dict]:
    command = ["helm", "template", "butlers", str(_CHART)]
    for setting in (
        "image.tag=synthetic",
        "frontendImage.tag=synthetic",
        "database.host=postgres.invalid",
        *settings,
    ):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _connector_pods(docs: list[dict]) -> dict[str, dict]:
    return {
        doc["metadata"]["name"]: doc["spec"]["template"]
        for doc in docs
        if doc["kind"] == "Deployment" and doc["metadata"]["name"].startswith("connector-")
    }


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_chart_exposes_only_proxied_metrics_for_connectors_with_a_health_port():
    connectors = yaml.safe_load((_CHART / "values.yaml").read_text())["connectors"]
    docs = _render()
    pods = _connector_pods(docs)

    for name, values in connectors.items():
        pod = pods[f"connector-{name}"]
        sidecars = [c for c in pod["spec"]["containers"] if c["name"] == "metrics-proxy"]
        if "healthPort" not in values:
            assert sidecars == [] and "annotations" not in pod["metadata"], name
            continue
        (sidecar,) = sidecars
        assert sidecar["command"][-2:] == [
            f"--upstream-port={values['healthPort']}",
            "--listen-port=9464",
        ], name
        assert sidecar["ports"] == [{"name": "metrics", "containerPort": 9464}]
        assert pod["metadata"]["labels"]["butlers.io/connector-metrics"] == "true"
        annotations = pod["metadata"]["annotations"]
        assert annotations["k8s.grafana.com/scrape"] == "true"
        assert annotations["k8s.grafana.com/job"] == f"connector-{name}"
        assert annotations["prometheus.io/port"] == "9464"

    host_ingress = next(d for d in docs if d["metadata"]["name"] == "butlers-host-ingress")
    # The externally reachable rule admits the metrics port and nothing else.
    assert {
        "ports": [{"protocol": "TCP", "port": 9464}],
        "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "lgtm"}}}],
    } in host_ingress["spec"]["ingress"]
    assert not any(d["kind"] == "PodMonitor" for d in docs)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_chart_podmonitor_and_disable_switch():
    docs = _render(
        "connectorMetrics.podMonitor.enabled=true",
        "connectorMetrics.podMonitor.labels.release=lgtm",
    )
    (monitor,) = [d for d in docs if d["kind"] == "PodMonitor"]
    assert monitor["metadata"]["labels"]["release"] == "lgtm"
    assert monitor["spec"]["selector"]["matchLabels"]["butlers.io/connector-metrics"] == "true"
    assert monitor["spec"]["podMetricsEndpoints"][0]["port"] == "metrics"

    disabled = _render("connectorMetrics.enabled=false", "connectorMetrics.podMonitor.enabled=true")
    assert not any(d["kind"] == "PodMonitor" for d in disabled)
    for name, pod in _connector_pods(disabled).items():
        assert [c["name"] for c in pod["spec"]["containers"]] == ["connector"], name
    host_ingress = next(d for d in disabled if d["metadata"]["name"] == "butlers-host-ingress")
    assert len(host_ingress["spec"]["ingress"]) == 1
