"""Regression guard for the tailnet-only dashboard health monitor runbook."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_RUNBOOK = Path("docs/operations/tailnet-health-monitoring.md")
_OPERATIONS_INDEX = Path("docs/operations/index.md")
_COMPOSE_SCRIPT = Path("scripts/compose.sh")
_COMPOSE_FILE = Path("docker-compose.yml")


def _runbook_text() -> str:
    assert _RUNBOOK.is_file(), "Tailnet health-monitor runbook is missing."
    return _RUNBOOK.read_text(encoding="utf-8")


def _compose_script_text() -> str:
    return _COMPOSE_SCRIPT.read_text(encoding="utf-8")


def _compose_services() -> dict:
    compose = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8"))
    return compose["services"]


def test_tailnet_health_monitoring_runbook_exists() -> None:
    """Operators need a checked-in, reviewable monitoring contract."""
    assert _RUNBOOK.is_file(), (
        "The canonical tailnet health-monitor runbook is missing; document the "
        "production /butlers-api/api/health contract before asking an operator "
        "or Uptime Kuma agent to configure it."
    )


def test_operations_index_links_to_tailnet_health_monitoring_runbook() -> None:
    """The operator-facing contract must be discoverable from Operations."""
    index = _OPERATIONS_INDEX.read_text(encoding="utf-8")
    assert "[Tailnet Health Monitoring](tailnet-health-monitoring.md)" in index


def test_production_serve_mapping_exposes_the_canonical_health_route() -> None:
    """Bind the documented host-owned production route to the rendered API target."""
    import ast
    import shutil
    import subprocess
    from urllib.parse import urlsplit

    chart = Path("deploy/helm/butlers")
    production = yaml.safe_load((chart / "values.prod.yaml").read_text(encoding="utf-8"))
    prefix = production["publicUrl"]["apiPrefix"]
    node_port = production["nodePorts"]["dashboard"]
    assert (prefix, node_port) == ("butlers-api", 31200)
    helm = shutil.which("helm")
    assert helm is not None, "Installed Helm is required for the production health-route render."
    rendered = subprocess.run(
        [
            helm,
            "template",
            "butlers",
            str(chart),
            "-f",
            str(chart / "values.prod.yaml"),
            "--set",
            "image.tag=source-test",
            "--set",
            "frontendImage.tag=source-test",
            "--set",
            "database.host=db.invalid",
            "--set",
            "trackerEgress.enabled=false",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rendered.returncode == 0, rendered.stderr
    resources = [resource for resource in yaml.safe_load_all(rendered.stdout) if resource]
    services = [
        resource
        for resource in resources
        if resource.get("kind") == "Service"
        and resource.get("metadata", {}).get("name") == "dashboard-api-host"
    ]
    assert len(services) == 1
    service = services[0]["spec"]
    assert service["type"] == "NodePort"
    assert service["externalTrafficPolicy"] == "Local"
    ports = [port for port in service["ports"] if port.get("nodePort") == node_port]
    assert len(ports) == 1
    port = ports[0]
    assert (port["port"], port.get("protocol", "TCP")) == (41200, "TCP")
    selector = service["selector"]
    assert selector
    workloads = [
        resource
        for resource in resources
        if resource.get("kind") == "Deployment"
        and all(
            resource["spec"]["template"]["metadata"]["labels"].get(key) == value
            for key, value in selector.items()
        )
    ]
    assert len(workloads) == 1
    workload = workloads[0]
    assert workload["metadata"]["name"] == "dashboard-api"
    target = port["targetPort"]
    assert isinstance(target, str)
    targets = [
        (container, container_port)
        for container in workload["spec"]["template"]["spec"]["containers"]
        for container_port in container.get("ports", [])
        if container_port.get("name") == target
    ]
    assert len(targets) == 1
    container, container_port = targets[0]
    assert container["name"] == "dashboard-api"
    assert container_port["containerPort"] == port["port"]
    assert container_port.get("protocol", "TCP") == port.get("protocol", "TCP")
    assert container["args"] == [
        "dashboard",
        "--host",
        "0.0.0.0",
        "--port",
        str(container_port["containerPort"]),
    ]
    env = {item["name"]: item.get("value") for item in container["env"]}
    redirect = urlsplit(env["GOOGLE_OAUTH_REDIRECT_URI"])
    assert redirect.scheme == "https"
    assert redirect.path == f"/{prefix}/api/oauth/google/callback"

    # Bind the selected workload's CLI to the real factory and its health handler.
    cli = ast.parse(Path("src/butlers/cli.py").read_text(encoding="utf-8"))
    commands = [
        node for node in cli.body if isinstance(node, ast.FunctionDef) and node.name == "dashboard"
    ]
    assert len(commands) == 1
    assert any(
        isinstance(decorator, ast.Call)
        and ast.unparse(decorator.func) == "cli.command"
        and (not decorator.args or ast.literal_eval(decorator.args[0]) == "dashboard")
        for decorator in commands[0].decorator_list
    )
    assert any(
        isinstance(decorator, ast.Call)
        and ast.unparse(decorator.func) == "click.option"
        and decorator.args
        and ast.literal_eval(decorator.args[0]) == "--port"
        for decorator in commands[0].decorator_list
    )
    calls = [
        node
        for node in ast.walk(commands[0])
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "uvicorn.run"
    ]
    assert len(calls) == 1
    assert ast.literal_eval(calls[0].args[0]) == "butlers.api.app:create_app"
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert ast.literal_eval(keywords["factory"]) is True
    assert isinstance(keywords["port"], ast.Name) and keywords["port"].id == "port"
    app = ast.parse(Path("src/butlers/api/app.py").read_text(encoding="utf-8"))
    factories = [
        node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    ]
    assert len(factories) == 1
    handlers = [
        node
        for node in factories[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "health"
    ]
    assert len(handlers) == 1
    paths = {
        ast.literal_eval(decorator.args[0])
        for decorator in handlers[0].decorator_list
        if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "app.get"
    }
    assert "/api/health" in paths

    # This is an operator contract, not a claim about installed/live Serve state.
    associations = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in _runbook_text().splitlines()
        if line.startswith("| Production Helm |")
    ]
    assert associations == [
        [
            "Production Helm",
            f"https://<TAILNET_DNS_NAME>/{prefix}/api/health",
            f"/{prefix}",
            f"http://localhost:{port['nodePort']}",
            "/api/health",
            "strict TLS; host operator-owned Serve",
        ]
    ]

    text = _compose_script_text()
    assert 'API_PREFIX="butlers-dev-api"' in text
    assert "export DASHBOARD_HOST_PORT=42200" in text
    assert '"/${API_PREFIX}|http://localhost:${DASHBOARD_HOST_PORT}"' in text
    assert 'TAILSCALE_HTTPS_PORT="${TAILSCALE_HTTPS_PORT:-443}"' in text
    assert 'tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}"' in text


def test_dashboard_api_keeps_the_serve_target_loopback_bound() -> None:
    """Tailnet TLS terminates at Serve; Compose must not publish the API to the LAN."""
    ports = _compose_services()["dashboard-api"]["ports"]
    assert "127.0.0.1:${DASHBOARD_HOST_PORT:-41200}:41200" in ports


def test_runbook_pins_tailnet_strict_tls_pull_monitor_contract() -> None:
    """The handoff must not weaken the proven route into a generic liveness check."""
    text = _runbook_text()

    required_fragments = (
        "https://<TAILNET_DNS_NAME>/butlers-api/api/health",
        "tailnet-only",
        "strict TLS",
        "HTTP(s) pull monitor",
        "HTTP 200",
        '"status": "ok"',
        '"status": "starting"',
        "60 seconds",
        "10 seconds",
        "two retries",
        "owner-supplied notification route",
        "host operator who owns Tailscale",
        "separate tailnet node",
    )
    for fragment in required_fragments:
        assert fragment in text, f"Runbook must retain {fragment!r}."

    forbidden_fragments = (
        "EXTERNAL_DEADMAN_URL",
        "Push monitor",
        "disable TLS verification",
    )
    lowered_text = text.casefold()
    for fragment in forbidden_fragments:
        assert fragment.casefold() not in lowered_text, (
            f"Runbook must not direct operators toward {fragment!r}."
        )
