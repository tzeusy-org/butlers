"""Retained dev Compose and Helm control-topology contracts."""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_key_absent_control_workload_and_session_network_contract():
    """Source/render proof; never creates containers, pods, keys or traffic."""
    import shutil

    import yaml

    root = Path(__file__).resolve().parents[2]
    services = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    # Production source stays unsplit at five, independently of the dev overlay.
    for suffix in ("", "-hotreload"):
        baseline = services["butlers-up" + suffix]
        assert "--exclude" not in " ".join(baseline["entrypoint"])
        assert (
            baseline["environment"]["BUTLERS_MAX_GLOBAL_SESSIONS"]
            == "${BUTLERS_MAX_GLOBAL_SESSIONS:-5}"
        )

    class OverlayLoader(yaml.SafeLoader):
        pass

    OverlayLoader.add_constructor("!reset", lambda loader, node: loader.construct_sequence(node))
    OverlayLoader.add_constructor("!override", lambda loader, node: loader.construct_mapping(node))
    overlay = yaml.load(
        (root / "docker-compose.control-stage.yml").read_text(), Loader=OverlayLoader
    )
    for name, override in overlay["services"].items():
        services[name].update(override)
    for suffix in ("", "-hotreload"):
        control = services["butlers-control" + suffix]
        domains = services["butlers-up" + suffix]
        launch = " ".join(control["entrypoint"])
        assert "control --profile KEY_ABSENT_STAGE" in launch
        assert "--session-host butlers-control-session" in launch
        assert "--exclude switchboard" in " ".join(domains["entrypoint"])
        assert int(control["environment"]["BUTLERS_MAX_GLOBAL_SESSIONS"]) == 1
        assert int(domains["environment"]["BUTLERS_MAX_GLOBAL_SESSIONS"]) == 4
        assert "41100/health" in " ".join(control["healthcheck"]["test"])
        assert "41199" not in " ".join(control["healthcheck"]["test"])
        assert all(":41199" not in port for port in control["ports"])
        assert "control-session" not in domains["networks"]
        assert control["networks"]["control-session"]["aliases"] == ["butlers-control-session"]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert compose["networks"]["control-session"]["internal"] is True
    members = {
        name
        for name, service in services.items()
        if "control-session" in service.get("networks", [])
    }
    assert members == {"butlers-control", "butlers-control-hotreload", "control-session-health"}
    assert services["control-session-health"]["profiles"] == ["control-health"]
    assert (
        services["dashboard-api"]["environment"]["SWITCHBOARD_MCP_URL"]
        == "http://${BUTLERS_SWITCHBOARD_SERVICE:-butlers-up}:41100/sse"
    )
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm not installed; Compose source assertions completed")
    chart = root / "deploy/helm/butlers"

    def render(environment, *extra):
        command = [
            helm,
            "template",
            "butlers",
            str(chart),
            "-f",
            str(chart / f"values.{environment}.yaml"),
            "--set",
            "image.tag=source-test",
            "--set",
            "frontendImage.tag=source-test",
            "--set",
            "database.host=db.invalid",
            "--set",
            "trackerEgress.enabled=false",
            *extra,
        ]
        return subprocess.run(command, capture_output=True, text=True)

    result = render("dev", "--set", "butlersControl.enabled=true")
    assert result.returncode == 0, result.stderr
    docs = {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc
    }
    control = docs[("Deployment", "butlers-control")]
    domains = docs[("Deployment", "butlers-up")]
    for workload in (control, domains):
        assert workload["spec"]["replicas"] == 1
        assert workload["spec"]["strategy"]["type"] == "Recreate"
        pod = workload["spec"]["template"]["spec"]
        contexts = [pod.get("securityContext") or {}]
        contexts.extend(container.get("securityContext") or {} for container in pod["containers"])
        assert all(
            context.get("appArmorProfile", {}).get("type") != "Unconfined" for context in contexts
        ), workload["metadata"]["name"]
    control_container = control["spec"]["template"]["spec"]["containers"][0]
    domain_container = domains["spec"]["template"]["spec"]["containers"][0]
    capacities = [
        int(
            next(
                env["value"]
                for env in container["env"]
                if env["name"] == "BUTLERS_MAX_GLOBAL_SESSIONS"
            )
        )
        for container in (control_container, domain_container)
    ]
    assert capacities == [1, 4]
    assert sum(capacities) == 5
    for probe in ("readinessProbe", "startupProbe", "livenessProbe"):
        assert control_container[probe]["httpGet"] == {"path": "/health", "port": "service"}
    assert "--exclude switchboard" in " ".join(domain_container["command"])
    # Both processes reach Switchboard (including its own identity probe) in control.
    for container in (control_container, domain_container):
        env = {item["name"]: item.get("value") for item in container["env"]}
        assert env["SWITCHBOARD_MCP_URL"] == "http://butlers-control:41100/sse"
    ports = docs[("Service", "butlers-control")]["spec"]["ports"]
    assert {(port["name"], port["port"]) for port in ports} == {
        ("service", 41100),
        ("session", 41199),
    }
    assert 41100 not in {port["port"] for port in docs[("Service", "butlers-up")]["spec"]["ports"]}
    policy = docs[("NetworkPolicy", "butlers-control-ingress")]["spec"]
    assert policy["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "butlers-control"
    service_rule, session_rule = policy["ingress"]
    assert service_rule["ports"] == [{"protocol": "TCP", "port": 41100}]
    assert session_rule["ports"] == [{"protocol": "TCP", "port": 41199}]
    assert {
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
        for peer in session_rule["from"]
    } == {"session-host", "control-session-health"}
    assert all(
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/instance"] == "butlers"
        for peer in session_rule["from"]
    )
    assert all(set(peer) == {"podSelector"} for peer in session_rule["from"])
    assert render("dev", "--set", "butlersUp.maxGlobalSessions=6").returncode != 0
    assert render("dev", "--set", "butlersUp.maxGlobalSessions=1").returncode != 0
    for invalid in ("0", "-1", "4.5", "invalid"):
        assert render("dev", "--set", f"butlersUp.maxGlobalSessions={invalid}").returncode != 0
    prod = render("prod", "--set", "butlersControl.enabled=false")
    assert prod.returncode == 0, prod.stderr
    prod_docs = [doc for doc in yaml.safe_load_all(prod.stdout) if doc]
    assert not any(doc["metadata"]["name"] == "butlers-control" for doc in prod_docs)
    prod_domains = next(
        doc
        for doc in prod_docs
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "butlers-up"
    )
    prod_container = prod_domains["spec"]["template"]["spec"]["containers"][0]
    assert "SWITCHBOARD_MCP_URL" not in {item["name"] for item in prod_container["env"]}
