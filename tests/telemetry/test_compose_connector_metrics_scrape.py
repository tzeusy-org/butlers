"""Static contract: every Compose connector scrape job addresses a reachable listener.

Connector health servers bind ``127.0.0.1`` inside their own container, so the
Compose Prometheus can only reach ``/metrics`` through a metrics-proxy sidecar
that shares the connector's network namespace. A job pointed straight at the
loopback health port scrapes nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMETHEUS_PATH = _REPO_ROOT / "observability/prometheus/prometheus.yml"
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"
_OBSERVABILITY_COMPOSE_PATH = _REPO_ROOT / "docker-compose.observability.yml"
_PROXY_SCRIPT = "/app/scripts/connector_metrics_proxy.py"
_PROXY_DEFAULT_LISTEN_PORT = 9464
# Its webhook must accept the phone's POSTs, so its health server already binds
# 0.0.0.0 (src/butlers/connectors/owntracks.py) and needs no proxy.
_BINDS_ALL_INTERFACES = {"connector-owntracks"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _connector_targets() -> list[tuple[str, str, int]]:
    targets = []
    for job in _load(_PROMETHEUS_PATH)["scrape_configs"]:
        if not job["job_name"].startswith("connector-"):
            continue
        for static in job["static_configs"]:
            for target in static["targets"]:
                host, port = target.rsplit(":", 1)
                targets.append((job["job_name"], host, int(port)))
    return targets


def _flags(argv: list[str]) -> dict[str, str]:
    return dict(arg.removeprefix("--").split("=", 1) for arg in argv if arg.startswith("--"))


@pytest.mark.parametrize(("job", "host", "port"), _connector_targets())
def test_connector_scrape_target_reaches_metrics(job: str, host: str, port: int) -> None:
    base = _load(_COMPOSE_PATH)["services"]
    observability = _load(_OBSERVABILITY_COMPOSE_PATH)["services"]
    connector = base[host]
    health_port = int(connector["environment"]["CONNECTOR_HEALTH_PORT"])

    if host in _BINDS_ALL_INTERFACES:
        assert port == health_port
        return

    (proxy,) = (
        service
        for service in observability.values()
        if service.get("network_mode") == f"service:{host}"
    )
    assert proxy["entrypoint"][-1] == _PROXY_SCRIPT
    flags = _flags(proxy.get("command", []))
    assert int(flags["upstream-port"]) == health_port
    assert int(flags.get("listen-port", _PROXY_DEFAULT_LISTEN_PORT)) == port
    assert port != health_port, f"{job} must scrape the proxy, not the loopback health port"
    # A sidecar enabled without its connector fails `compose up`, so it follows
    # the connector's own profile (or the observability profile when it has none).
    assert proxy["profiles"] == connector.get("profiles", ["observability"])
