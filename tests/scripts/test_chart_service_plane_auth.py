"""Service-plane caller authentication is dark in the chart by default (bu-2c1g7v)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed"),
]

_CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "butlers"


def _render(*settings: str) -> list[dict]:
    result = _helm(*settings)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _helm(*settings: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "butlers", str(_CHART)]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    command += [
        "-f",
        str(Path(__file__).resolve().parents[1] / "fixtures/egress-gateway-values.yaml"),
    ]
    for setting in (
        "image.tag=t",
        "frontendImage.tag=t",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={198.51.100.7/32}",
        "database.host=postgres.invalid",
        "butlersControl.enabled=true",
        *settings,
    ):
        command += ["--set", setting]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _pod(docs: list[dict], name: str) -> dict:
    return next(
        d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == name
    )


def _env(pod: dict) -> dict[str, str]:
    return {e["name"]: e.get("value") for c in pod["containers"] for e in c.get("env", [])}


def test_off_by_default_renders_no_accounts_switch_or_token() -> None:
    docs = _render()

    assert not [d for d in docs if d["kind"] == "ServiceAccount"]
    for name in ("butlers-control", "butlers-up", "dashboard-api"):
        pod = _pod(docs, name)
        assert "serviceAccountName" not in pod
        assert "BUTLERS_SERVICE_PLANE_AUTH" not in _env(pod)
        assert all(v["name"] != "workload-token" for v in pod["volumes"])


def test_on_gives_each_workload_its_account_and_its_one_bound_token() -> None:
    docs = _render("servicePlaneAuth.enabled=true")

    accounts = {d["metadata"]["name"] for d in docs if d["kind"] == "ServiceAccount"}
    assert {"butlers-control", "butlers-up"} <= accounts
    control, up = _pod(docs, "butlers-control"), _pod(docs, "butlers-up")
    assert control["serviceAccountName"] == "butlers-control"
    assert up["serviceAccountName"] == "butlers-up"
    assert _env(control)["BUTLERS_SERVICE_PLANE_AUTH"] == "1"
    assert _env(up)["BUTLERS_SERVICE_PLANE_AUTH"] == "1"
    # The dashboard reroutes its model-reachable tool calls under the same switch.
    assert _env(_pod(docs, "dashboard-api"))["BUTLERS_SERVICE_PLANE_AUTH"] == "1"

    def audiences(pod: dict) -> set[str]:
        (volume,) = [v for v in pod["volumes"] if v["name"] == "workload-token"]
        tokens = [source["serviceAccountToken"] for source in volume["projected"]["sources"]]
        assert all(t["audience"] == t["path"] and t["expirationSeconds"] <= 3600 for t in tokens)
        return {t["audience"] for t in tokens}

    # Each workload holds only the audience it presents: control delivers to
    # Messenger; the domain daemons call the relays.
    assert audiences(control) == {"butlers.messenger-delivery.v1"}
    assert audiences(up) == {"butlers.service-relay.v1"}


def _connector_pods(docs: list[dict]) -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"].startswith("connector-")
    }


def test_only_the_owntracks_connector_gets_an_account_and_a_relay_token() -> None:
    # bu-s11n0s.7 S4b-3c: the connector reads Chronicler's ready retention grants through
    # the location_retention.grants_ready relay, so it presents its own workload token.
    docs = _render("servicePlaneAuth.enabled=true")

    accounts = {d["metadata"]["name"] for d in docs if d["kind"] == "ServiceAccount"}
    assert "connector-owntracks" in accounts
    assert "default" not in accounts
    pods = _connector_pods(docs)
    owntracks = pods.pop("connector-owntracks")
    assert owntracks["serviceAccountName"] == "connector-owntracks"
    (volume,) = [v for v in owntracks["volumes"] if v["name"] == "workload-token"]
    (source,) = volume["projected"]["sources"]
    token = source["serviceAccountToken"]
    assert token["audience"] == token["path"] == "butlers.service-relay.v1"
    assert token["expirationSeconds"] <= 3600
    (connector,) = [c for c in owntracks["containers"] if c["name"] == "connector"]
    assert {
        "name": "workload-token",
        "mountPath": "/var/run/secrets/butlers/workload",
        "readOnly": True,
    } in connector["volumeMounts"]
    assert all(c["name"] == "connector" for c in owntracks["containers"] if "volumeMounts" in c)

    assert pods, "the other connectors must still render"
    for name, pod in pods.items():
        assert "serviceAccountName" not in pod, name
        assert all(v["name"] != "workload-token" for v in pod.get("volumes", [])), name


def test_off_the_owntracks_connector_has_no_account_or_token() -> None:
    docs = _render()

    owntracks = _connector_pods(docs)["connector-owntracks"]
    assert "serviceAccountName" not in owntracks
    assert all(v["name"] != "workload-token" for v in owntracks["volumes"])
    (connector,) = [c for c in owntracks["containers"] if c["name"] == "connector"]
    assert all(m["name"] != "workload-token" for m in connector["volumeMounts"])


_PROFILED = (
    "egressProfiles.enabled=true",
    "egressPolicy.enabled=false",
    "connectors.telegram-user.enabled=false",
    "egressProfiles.sites.database.namespace=postgres-site",
    "egressProfiles.sites.otlp.cidr=198.51.100.9/32",
    "egressProfiles.sites.home-assistant.cidr=198.51.100.10/32",
)


def _egress_to(docs: list[dict], workload: str, cidr: str) -> list[dict]:
    policy = next(
        d
        for d in docs
        if d["kind"] == "NetworkPolicy"
        and d["spec"]["podSelector"].get("matchLabels", {}).get("app.kubernetes.io/component")
        == workload
    )
    return [
        port
        for rule in policy["spec"]["egress"]
        if any(peer.get("ipBlock", {}).get("cidr") == cidr for peer in rule.get("to", []))
        for port in rule.get("ports", [])
    ]


def test_under_egress_profiles_the_switch_requires_the_api_server_site() -> None:
    result = _helm("servicePlaneAuth.enabled=true", *_PROFILED)

    assert result.returncode != 0
    assert "egressProfiles.sites.kube-apiserver" in result.stderr


def test_the_api_server_site_admits_only_the_jwks_port_from_both_verifiers() -> None:
    site = "egressProfiles.sites.kube-apiserver.cidr=198.51.100.20/32"
    docs = _render("servicePlaneAuth.enabled=true", *_PROFILED, site)

    # Messenger (butlers-up) verifies delivery tokens; Switchboard (control) verifies relays.
    for workload in ("butlers-up", "butlers-control"):
        assert _egress_to(docs, workload, "198.51.100.20/32") == [{"port": 6443, "protocol": "TCP"}]
    # Without the switch the optional site stays unadmitted unless located.
    assert _helm(*_PROFILED).returncode == 0


def test_profiled_service_plane_verifiers_keep_their_api_account_files() -> None:
    docs = _render(
        "servicePlaneAuth.enabled=true",
        *_PROFILED,
        "egressProfiles.sites.kube-apiserver.cidr=198.51.100.20/32",
    )
    for workload in ("butlers-up", "butlers-control"):
        pod = _pod(docs, workload)
        assert pod["automountServiceAccountToken"] is False
        account = next(v for v in pod["volumes"] if v["name"] == "api-server-account")
        assert account["projected"]["sources"] == [
            {"serviceAccountToken": {"path": "token", "expirationSeconds": 3600}},
            {
                "configMap": {
                    "name": "kube-root-ca.crt",
                    "items": [{"key": "ca.crt", "path": "ca.crt"}],
                }
            },
            {
                "downwardAPI": {
                    "items": [
                        {"path": "namespace", "fieldRef": {"fieldPath": "metadata.namespace"}}
                    ]
                }
            },
        ]
        mounted = [
            (c["name"], mount)
            for c in [*pod["containers"], *pod["initContainers"]]
            for mount in c.get("volumeMounts", [])
            if mount["name"] == account["name"]
        ]
        assert mounted == [
            (
                workload,
                {
                    "name": "api-server-account",
                    "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                    "readOnly": True,
                },
            )
        ]


def test_the_owntracks_connector_runs_its_retention_worker() -> None:
    # bu-s11n0s.75: the worker is on in the chart; Chronicler offers it nothing unless the owner
    # has turned raw expiry on, and without its relay token it is inert.
    for docs in (_render(), _render("servicePlaneAuth.enabled=true")):
        owntracks = _connector_pods(docs)["connector-owntracks"]
        assert _env(owntracks)["OWNTRACKS_RAW_RETENTION_ENABLED"] == "true"
        others = _connector_pods(docs)
        others.pop("connector-owntracks")
        assert all("OWNTRACKS_RAW_RETENTION_ENABLED" not in _env(pod) for pod in others.values())


_SWITCHBOARD_TLS = (
    "servicePlaneAuth.enabled=true",
    "servicePlaneAuth.switchboardTLS.enabled=true",
    "servicePlaneAuth.switchboardTLS.servingSecretName=test-switchboard-identity",
    "servicePlaneAuth.switchboardTLS.caConfigMapName=test-switchboard-ca",
)


def test_switchboard_tls_mounts_public_trust_on_callers_and_serving_material_on_control():
    docs = _render(*_SWITCHBOARD_TLS)
    pods = {
        "butlers-control": _pod(docs, "butlers-control"),
        "butlers-up": _pod(docs, "butlers-up"),
        "dashboard-api": _pod(docs, "dashboard-api"),
        **_connector_pods(docs),
    }
    for name, pod in pods.items():
        assert _env(pod)["SWITCHBOARD_MCP_URL"] == "https://butlers-control:41100/sse"
        assert _env(pod)["BUTLERS_SWITCHBOARD_CA_FILE"] == "/var/run/butlers-switchboard-ca/ca.crt"
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        assert volumes["switchboard-ca"]["configMap"] == {
            "name": "test-switchboard-ca",
            "items": [{"key": "ca.crt", "path": "ca.crt"}],
        }
        assert ("switchboard-serving-tls" in volumes) == (name == "butlers-control")
        container = next(c for c in pod["containers"] if c["name"] in {name, "connector"})
        assert any(
            m["name"] == "switchboard-ca" and m["readOnly"] for m in container["volumeMounts"]
        )
        if name.startswith("connector-"):
            waiter = next(c for c in pod["initContainers"] if c["name"] == "wait-for-butlers-up")
            assert "--cacert /var/run/butlers-switchboard-ca/ca.crt" in waiter["args"][0]
            assert "https://butlers-control:41100/health" in waiter["args"][0]
            assert waiter["volumeMounts"][0]["name"] == "switchboard-ca"
    control = pods["butlers-control"]["containers"][0]
    assert control["args"] == [
        "--service-tls-dir",
        "/var/run/butlers-switchboard-tls",
        "--service-tls-bind",
        "$(BUTLERS_CONTROL_POD_IP)",
    ]
    pod_ip = next(env for env in control["env"] if env["name"] == "BUTLERS_CONTROL_POD_IP")
    assert pod_ip["valueFrom"] == {"fieldRef": {"fieldPath": "status.podIP"}}
    assert control["ports"] == [
        {"name": "service", "containerPort": 41100},
        {"name": "session", "containerPort": 41199},
    ]
    for probe in ("readinessProbe", "startupProbe", "livenessProbe"):
        assert control[probe]["httpGet"] == {
            "path": "/health",
            "port": "service",
            "scheme": "HTTPS",
        }


def test_switchboard_tls_requires_service_plane_auth_and_explicit_trust():
    for override, reason in (
        ("servicePlaneAuth.enabled=false", "requires butlersControl.enabled"),
        ("butlersControl.enabled=false", "requires butlersControl.enabled"),
        ("servicePlaneAuth.switchboardTLS.servingSecretName=", "requires servingSecretName"),
        ("servicePlaneAuth.switchboardTLS.caConfigMapName=", "requires caConfigMapName"),
    ):
        result = _helm(*_SWITCHBOARD_TLS, override)
        assert result.returncode != 0
        assert reason in result.stderr


_HEALTH_TLS = (
    "servicePlaneAuth.enabled=true",
    "servicePlaneAuth.healthTLS.enabled=true",
    "servicePlaneAuth.healthTLS.servingSecretName=test-health-identity",
    "servicePlaneAuth.healthTLS.caConfigMapName=test-health-ca",
)


def test_health_tls_scopes_serving_material_trust_and_tokens():
    docs = _render(*_HEALTH_TLS, *_SWITCHBOARD_TLS)
    ca_path = "/var/run/butlers-health-ca/ca.crt"
    audience = "butlers.health-medication-peer.v1"
    callers = {"butlers-control", "dashboard-api"}
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        name = doc["metadata"]["name"]
        pod = doc["spec"]["template"]["spec"]
        volumes = {volume["name"]: volume for volume in pod.get("volumes", [])}
        env = _env(pod)
        assert ("health-serving-tls" in volumes) == (name == "butlers-up")
        has_ca = name in callers or name == "connector-google-health"
        assert ("health-ca" in volumes) == has_ca
        if has_ca:
            assert volumes["health-ca"]["configMap"] == {
                "name": "test-health-ca",
                "items": [{"key": "ca.crt", "path": "ca.crt"}],
            }
        audiences = {
            token["audience"]: token["path"]
            for volume in volumes.values()
            for source in volume.get("projected", {}).get("sources", [])
            if (token := source.get("serviceAccountToken"))
        }
        assert (audience in audiences) == (name in callers)
        if name in callers:
            assert audiences[audience] == audience
            assert pod["serviceAccountName"] == name
            assert env["HEALTH_MCP_URL"] == "https://butlers-up:41103/mcp"
            assert env["BUTLERS_HEALTH_CA_FILE"] == ca_path
        else:
            assert "HEALTH_MCP_URL" not in env and "BUTLERS_HEALTH_CA_FILE" not in env
        ca_mounts = [
            (container["name"], mount)
            for container in [*pod.get("initContainers", []), *pod["containers"]]
            for mount in container.get("volumeMounts", [])
            if mount["name"] == "health-ca"
        ]
        if has_ca:
            receiver = name if name in callers else "wait-for-health-routes"
            assert ca_mounts == [
                (
                    receiver,
                    {
                        "name": "health-ca",
                        "mountPath": "/var/run/butlers-health-ca",
                        "readOnly": True,
                    },
                )
            ]
        else:
            assert ca_mounts == []

    up_pod = _pod(docs, "butlers-up")
    up = up_pod["containers"][0]
    assert up["command"][-4:] == [
        "--health-tls-dir",
        "/var/run/butlers-health-tls",
        "--health-tls-bind",
        "$(BUTLERS_UP_POD_IP)",
    ]
    pod_ip = next(env for env in up["env"] if env["name"] == "BUTLERS_UP_POD_IP")
    assert pod_ip["valueFrom"] == {"fieldRef": {"fieldPath": "status.podIP"}}
    serving = next(volume for volume in up_pod["volumes"] if volume["name"] == "health-serving-tls")
    assert serving["secret"]["secretName"] == "test-health-identity"
    assert serving["secret"]["items"] == [
        {"key": "tls.crt", "path": "tls.crt"},
        {"key": "tls.key", "path": "tls.key"},
    ]
    assert {
        "name": "health-serving-tls",
        "mountPath": "/var/run/butlers-health-tls",
        "readOnly": True,
    } in up["volumeMounts"]
    dashboard = next(
        doc
        for doc in docs
        if doc["kind"] == "ServiceAccount" and doc["metadata"]["name"] == "dashboard-api"
    )
    assert dashboard["automountServiceAccountToken"] is False
    control = _pod(docs, "butlers-control")
    projections = next(
        volume["projected"] for volume in control["volumes"] if volume["name"] == "workload-token"
    )
    assert {source["serviceAccountToken"]["audience"] for source in projections["sources"]} == {
        audience,
        "butlers.messenger-delivery.v1",
    }
    connectors = _connector_pods(docs)
    for name, pod in connectors.items():
        gates = [c for c in pod["initContainers"] if c["name"] == "wait-for-health-routes"]
        if name == "connector-google-health":
            assert len(gates) == 1
            assert pod["initContainers"][-1] == gates[0]
            assert gates[0]["args"] == [
                "--url",
                "https://butlers-up:41103/internal/control-plane/identity",
                "--butler",
                "health",
                "--ca-file",
                ca_path,
            ]
        else:
            assert gates == []


def test_health_tls_requires_split_service_plane_and_explicit_trust():
    for override, reason in (
        ("servicePlaneAuth.enabled=false", "requires butlersControl.enabled"),
        ("butlersControl.enabled=false", "requires butlersControl.enabled"),
        ("servicePlaneAuth.healthTLS.servingSecretName=", "requires servingSecretName"),
        ("servicePlaneAuth.healthTLS.caConfigMapName=", "requires caConfigMapName"),
    ):
        result = _helm(*_HEALTH_TLS, override)
        assert result.returncode != 0
        assert reason in result.stderr
