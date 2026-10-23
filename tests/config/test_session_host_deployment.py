"""Render/source boundary checks, never evidence of an executed CNI or container."""

from __future__ import annotations

import copy
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[2]
_GUARD = runpy.run_path(str(_ROOT / "scripts/check_session_host_boundary.py"))


def test_authority_guard_refuses_ambient_credentials_mounts_and_direct_host_routes():
    source = yaml.safe_load((_ROOT / "docker-compose.session-host.yml").read_text())
    audit = _GUARD["compose_errors"]
    assert audit(source) == []
    for change in (
        {"environment": {"DATABASE_URL": "fixture-not-a-secret"}},
        {"env_file": ["forbidden.env"]},
        {"volumes": ["./fixture:/root/.codex:ro"]},
        {"networks": {"backend": {}}},
        {"tmpfs": []},
        {"read_only": False},
        {"entrypoint": ["/app/.venv/bin/python", "-m", "butlers.session_host"]},
    ):
        mutated = copy.deepcopy(source)
        mutated["services"]["session-host"].update(change)
        assert audit(mutated), "unsafe authority mutation escaped the real guard"


def _render(*arguments):
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(_ROOT / "deploy/helm/butlers"),
            "--set",
            "image.tag=test",
            "--set",
            "frontendImage.tag=test",
            "--set",
            "database.host=db.invalid",
            "--set",
            "beadsExport.denyTrackerEgress=true",
            "--set",
            "beadsExport.doltEgressCidrs={192.0.2.1/32}",
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def _matches(selector, labels):
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for expression in selector.get("matchExpressions", []):
        value = labels.get(expression["key"])
        if expression["operator"] == "NotIn" and value in expression["values"]:
            return False
        if expression["operator"] == "In" and value not in expression["values"]:
            return False
    return True


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is needed for actual render")
def test_rendered_policy_union_preserves_host_egress_and_control_only_ingress():
    disabled = _render()
    assert not any(
        row["metadata"]["name"] in {"session-host", "session-egress"} for row in disabled
    )
    docs = _render(
        "--set",
        "sessionHost.enabled=true",
        "--set",
        "sessionHost.image=registry.invalid/host@sha256:" + "a" * 64,
        "--set",
        "sessionHost.tlsSecretName=host-tls",
        "--set",
        "sessionHost.proofsConfigMap=reviewed-proofs",
        "--set",
        "sessionHost.gatewayServiceIP=10.43.253.2",
    )
    assert _GUARD["kubernetes_errors"](docs) == []
    policies = [row["spec"] for row in docs if row["kind"] == "NetworkPolicy"]

    def labels(role):
        return {
            "app.kubernetes.io/name": "butlers",
            "app.kubernetes.io/instance": "test",
            "app.kubernetes.io/component": role,
        }

    def allowed(subject, peer, port, direction="Egress", protocol="TCP"):
        selected = [
            p
            for p in policies
            if direction in p.get("policyTypes", []) and _matches(p["podSelector"], labels(subject))
        ]
        if not selected:
            return True
        for policy in selected:
            for rule in policy.get(direction.lower(), []):
                if rule.get("ports") and not any(
                    entry["port"] == port and entry.get("protocol", "TCP") == protocol
                    for entry in rule["ports"]
                ):
                    continue
                for endpoint in rule.get("to" if direction == "Egress" else "from", [{}]):
                    if "ipBlock" in endpoint:
                        continue  # Host has NO direct-IP exemption, including public providers.
                    if "podSelector" not in endpoint or (
                        peer is not None and _matches(endpoint["podSelector"], labels(peer))
                    ):
                        return True
        return False

    for port, protocol in ((443, "TCP"), (41199, "TCP"), (53, "UDP")):
        assert allowed("session-host", "session-egress", port, protocol=protocol)
    for peer, port in (
        ("butlers-up", 41101),
        ("database", 5432),
        ("butlers-control", 41100),
        ("session-egress", 41300),
        (None, 443),
    ):
        assert not allowed("session-host", peer, port)
    assert allowed("session-egress", "butlers-control", 41300, "Ingress")
    assert not allowed("session-egress", "dashboard-api", 41300, "Ingress")
    assert not allowed("session-host", "dashboard-api", 41299, "Ingress")

    # The optional OAuth volume is private, finite, unprivileged-to-the-node
    # emptyDir storage. Neither provider auth nor transport gets enabled in
    # the existing control/dashboard workloads merely by rendering it.
    auth_docs = _render(
        "--set",
        "sessionHost.enabled=true",
        "--set",
        "sessionHost.cliAuthEnabled=true",
        "--set",
        "sessionHost.image=registry.invalid/host@sha256:" + "a" * 64,
        "--set",
        "sessionHost.tlsSecretName=host-tls",
        "--set",
        "sessionHost.proofsConfigMap=reviewed-proofs",
        "--set",
        "sessionHost.gatewayServiceIP=10.43.253.2",
    )
    import json

    host_config = next(
        row
        for row in auth_docs
        if row["kind"] == "ConfigMap" and row["metadata"]["name"] == "test-session-config"
    )
    assert json.loads(host_config["data"]["host.json"])["cli_auth_enabled"] is True
    pod = next(
        row["spec"]["template"]["spec"]
        for row in auth_docs
        if row["kind"] == "Deployment" and row["metadata"]["name"] == "session-host"
    )
    volume = next(row for row in pod["volumes"] if row["name"] == "credentials")
    assert volume == {"name": "credentials", "emptyDir": {"medium": "Memory", "sizeLimit": "1Mi"}}
    init = next(row for row in pod["initContainers"] if row["name"] == "credential-volume")
    assert init["command"][-4:] == [
        "prepare-auth-volume",
        "--enable",
        "--config",
        "/run/session-credentials",
    ]
    assert init["volumeMounts"] == [
        {"name": "credentials", "mountPath": "/run/session-credentials"}
    ]
    assert {"name": "credentials", "mountPath": "/run/session-credentials"} in pod["containers"][0][
        "volumeMounts"
    ]
    assert _GUARD["kubernetes_errors"](auth_docs) == []
    missing_stage = copy.deepcopy(auth_docs)
    altered = next(
        row["spec"]["template"]["spec"]
        for row in missing_stage
        if row["kind"] == "Deployment" and row["metadata"]["name"] == "session-host"
    )
    altered["volumes"] = [row for row in altered["volumes"] if row["name"] != "stage"]
    assert "session-host: staging_volume_unavailable" in _GUARD["kubernetes_errors"](missing_stage)


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is needed for actual render")
def test_explicit_auth_transport_prepares_role_specific_material_before_existing_consumers():
    arguments = [
        "--set",
        "butlersControl.enabled=true",
        "--set",
        "sessionHost.enabled=true",
        "--set",
        "sessionHost.cliAuthEnabled=true",
        "--set",
        "sessionHost.cliAuthTransportEnabled=true",
        "--set",
        "sessionHost.image=registry.invalid/host@sha256:" + "a" * 64,
        "--set",
        "sessionHost.tlsSecretName=host-tls",
        "--set",
        "sessionHost.proofsConfigMap=reviewed-proofs",
        "--set",
        "sessionHost.gatewayServiceIP=10.43.253.2",
        "--set",
        "sessionHost.controlAuthSecretName=control-purpose",
        "--set",
        "sessionHost.dashboardAuthSecretName=dashboard-purpose",
    ]
    docs = _render(*arguments)
    assert _GUARD["kubernetes_errors"](docs) == []
    for role, name in (("control", "butlers-control"), ("dashboard", "dashboard-api")):
        pod = next(
            row["spec"]["template"]["spec"]
            for row in docs
            if row["kind"] == "Deployment" and row["metadata"]["name"] == name
        )
        init = next(row for row in pod["initContainers"] if row["name"] == "session-auth-snapshot")
        assert init["command"][2:] == [
            "butlers.session_host.material",
            role,
            "--source",
            "/auth-source",
            "--destination",
            "/auth-output",
        ]
        assert not init.get("env") and not init.get("envFrom")
        container = pod["containers"][0]
        assert {"name": "BUTLERS_SESSION_HOST_CLI_AUTH", "value": "1"} in container["env"]
        assert {
            "name": "session-auth",
            "mountPath": "/etc/session-host",
            "readOnly": True,
        } in container["volumeMounts"]
        assert all(row["name"] != "session-auth-source" for row in container["volumeMounts"])
        source = next(row for row in pod["volumes"] if row["name"] == "session-auth-source")
        assert source["secret"]["secretName"] == role + "-purpose"
        assert len(source["secret"]["items"]) == (7 if role == "control" else 4)
        assert next(row for row in pod["volumes"] if row["name"] == "session-auth")["emptyDir"] == {
            "medium": "Memory",
            "sizeLimit": "1Mi",
        }
    # Material names are required only on explicit activation, never invented.
    missing = list(arguments)
    missing[missing.index("sessionHost.controlAuthSecretName=control-purpose")] = (
        "sessionHost.controlAuthSecretName="
    )
    with pytest.raises(subprocess.CalledProcessError):
        _render(*missing)


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Compose CLI is needed for actual render"
)
def test_actual_compose_union_keeps_session_host_off_every_domain_network(tmp_path):
    import json
    import os

    inputs = tmp_path / "synthetic.env"
    inputs.write_text(
        "POSTGRES_HOST=db.invalid\nPOSTGRES_PASSWORD=fixture-only\n"
        "SESSION_HOST_IMAGE=registry.invalid/host@sha256:" + "a" * 64 + "\n"
        "SESSION_HOST_PROOFS=/fixture/proofs\nSESSION_HOST_TLS=/fixture/tls\n"
    )
    home = tmp_path / "docker-home"
    home.mkdir()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(inputs),
            "-f",
            str(_ROOT / "docker-compose.yml"),
            "-f",
            str(_ROOT / "docker-compose.session-host.yml"),
            "--profile",
            "control-stage",
            "--profile",
            "session-host",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        env={"HOME": str(home), "PATH": os.defpath},
    )
    rendered = json.loads(result.stdout)
    host = rendered["services"]["session-host"]
    assert set(host["networks"]) == {"session-isolated"}
    assert not host.get("environment") and not host.get("env_file") and not host.get("secrets")
    assert host["entrypoint"][:7] == _GUARD["_PREFIX"]
    assert not host.get("ports") and host.get("privileged", False) is False
    assert host["dns"] == ["172.30.253.2"]
    network = rendered["networks"]["session-isolated"]
    assert network["internal"] is True
    assert network["driver_opts"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert set(rendered["services"]["session-egress"]["networks"]) == {
        "session-isolated",
        "session-control-relay",
        "control-session",
        "egress",
    }
    assert "session-control-relay" in rendered["services"]["butlers-control"]["networks"]
    assert "session-isolated" not in rendered["services"]["butlers-control"]["networks"]
    # The optional material overlay is rendered only; no daemon or container
    # runs, and every path/value below is a synthetic deployment input.
    with inputs.open("a") as stream:
        stream.write(
            "SESSION_CONTROL_AUTH_INPUT=/fixture/control\n"
            "SESSION_DASHBOARD_AUTH_INPUT=/fixture/dashboard\n"
        )
    command = result.args.copy()
    index = command.index("--profile")
    command[index:index] = ["-f", str(_ROOT / "docker-compose.session-host-auth.yml")]
    index = command.index("config")
    command[index:index] = ["--profile", "session-host-auth"]
    configured = json.loads(
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            env={"HOME": str(home), "PATH": os.defpath},
        ).stdout
    )
    services = configured["services"]
    for role, consumer in (("control", "butlers-control"), ("dashboard", "dashboard-api")):
        initializer = "session-" + role + "-auth-init"
        assert services[initializer]["network_mode"] == "none"
        assert services[initializer]["read_only"] is True
        assert services[consumer]["environment"]["BUTLERS_SESSION_HOST_CLI_AUTH"] == "1"
        assert services[consumer]["depends_on"][initializer]["condition"] == (
            "service_completed_successfully"
        )
        assert any(
            mount["target"] == "/etc/session-host" and mount["read_only"]
            for mount in services[consumer]["volumes"]
        )
    assert set(services["session-host"]["networks"]) == {"session-isolated"}
    assert services["session-host"]["tmpfs"] == [
        "/tmp:rw,nosuid,nodev,size=256m",
        "/run/session-credentials:rw,nosuid,nodev,noexec,size=1m,mode=0700",
    ]
