"""Rendered-chart contract for deploy/helm/butlers-public-mcp (RFC 0039 D6, D8; task 2.4).

REQ-public-mcp-authorization-008 (network confinement) and REQ-public-mcp-projection-014
(the public pod's path to ``postgres-<env>``) are checked on the rendered objects, not on
the templates: allowed traffic to or from a pod is the union of every policy that selects
it, so a test of one policy proves nothing. The union is evaluated here for the one pod.
This is rendered intent, not proof of CNI enforcement; the live checks are
``scripts/k8s/public-mcp-live-checks.sh``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed"),
]

_ROOT = Path(__file__).resolve().parents[2]
_CHART = _ROOT / "deploy" / "helm" / "butlers-public-mcp"
_BUTLERS_CHART = _ROOT / "deploy" / "helm" / "butlers"
_NAMESPACE = "butlers-public-dev"
_POD = {
    "app.kubernetes.io/instance": "butlers-public-mcp",
    "app.kubernetes.io/component": "butlers-public-mcp",
}
_MCP_PORT = 8080
_METRICS_PORT = 9090
_BEARER_DIR = "/etc/butlers-public-mcp/bearer"
_KEYRING_DIR = "/etc/butlers-public-mcp/keyring"
_SWITCHES_DIR = "/etc/butlers-public-mcp/switches"
_DB_DIR = "/etc/butlers-public-mcp/db"
_CANARIES_DIR = "/etc/butlers-public-mcp/canaries"
# Site values the chart requires and never commits (the deploy script supplies them).
_SITE = ("audience=https://mcp.example/mcp", "allowedSubs=owner-sub-1\\,owner-sub-2")


def _render(*settings: str, values: tuple[str, ...] = ("values.dev.yaml",)) -> list[dict]:
    command = ["helm", "template", "butlers-public-mcp", str(_CHART), "-n", "butlers-dev"]
    command += ["-f", str(_CHART / "values.yaml")]
    for value in values:
        command += ["-f", value if Path(value).is_absolute() else str(_CHART / value)]
    for setting in ("image.tag=t", *_SITE, *settings):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return _render()


def _of(docs: list[dict], kind: str) -> list[dict]:
    return [doc for doc in docs if doc["kind"] == kind]


def _one(docs: list[dict], kind: str, name: str | None = None) -> dict:
    found = [d for d in _of(docs, kind) if name in (None, d["metadata"]["name"])]
    assert len(found) == 1, f"{kind} {name}: {[d['metadata']['name'] for d in found]}"
    return found[0]


def _pod(docs: list[dict]) -> dict:
    return _one(docs, "Deployment")["spec"]["template"]["spec"]


def _container(docs: list[dict]) -> dict:
    containers = _pod(docs)["containers"]
    assert len(containers) == 1
    return containers[0]


# --- namespace and workload ---------------------------------------------------------------


def test_the_chart_creates_its_own_namespace_and_places_everything_in_it(docs):
    namespaces = _of(docs, "Namespace")
    assert [n["metadata"]["name"] for n in namespaces] == [_NAMESPACE]
    for doc in docs:
        if doc["kind"] != "Namespace":
            assert doc["metadata"]["namespace"] == _NAMESPACE, doc["metadata"]["name"]
    # The namespace outlives a helm uninstall: deleting it would drop the Secrets with it.
    assert namespaces[0]["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def test_one_replica_that_never_runs_two_at_once(docs):
    deployment = _one(docs, "Deployment")
    assert deployment["spec"]["replicas"] == 1
    # The jti replay set is in memory: a rolling overlap would admit a replay.
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}


def test_pod_and_container_security_context(docs):
    pod = _pod(docs)
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] >= 1000
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    context = _container(docs)["securityContext"]
    assert context["runAsNonRoot"] is True
    assert context["readOnlyRootFilesystem"] is True
    assert context["allowPrivilegeEscalation"] is False
    assert context["capabilities"] == {"drop": ["ALL"]}
    assert context["seccompProfile"] == {"type": "RuntimeDefault"}
    assert "privileged" not in context or context["privileged"] is False


def test_the_only_writable_path_is_a_tmp_emptydir(docs):
    pod, container = _pod(docs), _container(docs)
    writable = [m for m in container["volumeMounts"] if not m.get("readOnly")]
    assert [(m["mountPath"]) for m in writable] == ["/tmp"]
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert "emptyDir" in volumes[writable[0]["name"]]
    for doc_volume in pod["volumes"]:
        assert "hostPath" not in doc_volume


def test_command_image_and_ports(docs):
    container = _container(docs)
    assert container["command"] == ["butlers-public-mcp"]
    assert container["image"] == "registry.invalid/butlers-app:t"
    ports = {p["name"]: p["containerPort"] for p in container["ports"]}
    assert ports == {"mcp": _MCP_PORT, "metrics": _METRICS_PORT}
    for probe in ("livenessProbe", "readinessProbe"):
        assert container[probe]["tcpSocket"] == {"port": _MCP_PORT}


def test_the_image_tag_is_required(docs):
    result = subprocess.run(
        ["helm", "template", "x", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "-f", str(_CHART / "values.dev.yaml")],
        capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode != 0
    assert "image.tag" in result.stderr


# --- environment, secrets and mounts ------------------------------------------------------


def _env(docs: list[dict]) -> dict[str, dict]:
    return {e["name"]: e for e in _container(docs)["env"]}


def test_environment_contract(docs):
    env = _env(docs)
    assert env["MCP_PUBLIC_DB_HOST"]["value"] == "postgres-dev-rw.postgres-dev.svc.cluster.local"
    assert env["MCP_PUBLIC_DB_PORT"]["value"] == "5432"
    assert env["MCP_PUBLIC_DB_NAME"]["value"] == "butlers_public"
    assert env["MCP_PUBLIC_DB_USER"]["value"] == "mcp_public_ro"
    assert env["MCP_PUBLIC_DASHBOARD_BASE"]["value"]
    assert env["MCP_PUBLIC_AUDIENCE"]["value"] == "https://mcp.example/mcp"
    assert env["MCP_PUBLIC_ALLOWED_SUBS"]["value"] == "owner-sub-1,owner-sub-2"
    assert env["HF_HUB_OFFLINE"]["value"] == "1"
    assert env["TRANSFORMERS_OFFLINE"]["value"] == "1"
    # Optional settings are absent unless set: the server defaults apply.
    assert "MCP_PUBLIC_CLIENT_DOMAINS" not in env
    assert "MCP_PUBLIC_REGISTERED_CLIENTS" not in env


def test_optional_client_settings_render_only_when_set():
    docs = _render("clientDomains=claude.ai", "registeredClients=cid-1")
    env = _env(docs)
    assert env["MCP_PUBLIC_CLIENT_DOMAINS"]["value"] == "claude.ai"
    assert env["MCP_PUBLIC_REGISTERED_CLIENTS"]["value"] == "cid-1"


@pytest.mark.parametrize("missing", ["audience", "allowedSubs"])
def test_audience_and_allowed_subs_are_required(missing):
    command = ["helm", "template", "x", str(_CHART), "-f", str(_CHART / "values.yaml")]
    command += ["-f", str(_CHART / "values.dev.yaml"), "--set", "image.tag=t"]
    for setting in _SITE:
        if not setting.startswith(missing + "="):
            command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0
    assert missing in result.stderr


def test_no_secret_is_an_env_var_the_password_is_a_file(docs):
    container = _container(docs)
    assert "envFrom" not in container
    assert not [e for e in container["env"] if "valueFrom" in e]
    assert "MCP_PUBLIC_DB_PASSWORD" not in {e["name"] for e in container["env"]}
    text = yaml.safe_dump(docs)
    for forbidden in ("POSTGRES_PASSWORD", "POSTGRES_USER", "BUTLERS_RUNTIME_DB", "postgres-mcp-"):
        assert forbidden not in text, forbidden
    for other in (
        "MCP_PROPOSALS_READER_DB_PASSWORD",
        "MCP_SUBSCRIPTION_OWNER_DB_PASSWORD",
        "butlers_projection_publisher",
    ):
        assert other not in text


def _external_keys(docs: list[dict]) -> dict[str, dict[str, str]]:
    result = {}
    for secret in _of(docs, "ExternalSecret"):
        assert secret["spec"]["secretStoreRef"] == {
            "name": "bitwarden-secretsmanager-dev",
            "kind": "ClusterSecretStore",
        }
        assert secret["spec"]["target"]["creationPolicy"] == "Owner"
        result[secret["metadata"]["name"]] = {
            d["secretKey"]: d["remoteRef"]["key"] for d in secret["spec"]["data"]
        }
    return result


def test_external_secrets_read_the_dev_store_and_name_the_bitwarden_items(docs):
    assert _external_keys(docs) == {
        "butlers-public-mcp-db": {"password": "MCP_PUBLIC_RO_DB_PASSWORD"},
        "butlers-public-mcp-bearer": {"digest": "MCP_PUBLIC_HOP_BEARER_DIGEST"},
        "butlers-public-mcp-canaries": {"canaries.json": "MCP_PUBLIC_CANARIES"},
    }


def test_a_rotation_overlap_adds_digest_next_from_a_second_item():
    docs = _render("externalSecrets.bearerDigestNextKey=MCP_PUBLIC_HOP_BEARER_DIGEST_NEXT")
    assert _external_keys(docs)["butlers-public-mcp-bearer"] == {
        "digest": "MCP_PUBLIC_HOP_BEARER_DIGEST",
        "digest_next": "MCP_PUBLIC_HOP_BEARER_DIGEST_NEXT",
    }
    volumes = {v["name"]: v for v in _pod(docs)["volumes"]}
    items = volumes["bearer"]["secret"]["items"]
    assert [(i["key"], i["path"]) for i in items] == [
        ("digest", "digest"),
        ("digest_next", "digest_next"),
    ]


def test_files_are_mounted_read_only_where_the_server_reads_them(docs):
    pod, container = _pod(docs), _container(docs)
    mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
    volumes = {v["name"]: v for v in pod["volumes"]}
    config_names = {c["metadata"]["name"] for c in _of(docs, "ConfigMap")}

    def secret_files(path: str, secret: str) -> list[tuple[str, str]]:
        volume = volumes[mounts[path]["name"]]["secret"]
        assert volume["secretName"] == secret
        return [(i["key"], i["path"]) for i in volume["items"]]

    assert secret_files(_BEARER_DIR, "butlers-public-mcp-bearer") == [("digest", "digest")]
    assert secret_files(_DB_DIR, "butlers-public-mcp-db") == [("password", "password")]
    assert secret_files(_CANARIES_DIR, "butlers-public-mcp-canaries") == [
        ("canaries.json", "canaries.json")
    ]
    assert volumes[mounts[_KEYRING_DIR]["name"]]["configMap"]["name"] in config_names
    assert volumes[mounts[_SWITCHES_DIR]["name"]]["configMap"]["name"] in config_names
    for path in (_BEARER_DIR, _KEYRING_DIR, _SWITCHES_DIR, _DB_DIR, _CANARIES_DIR):
        assert mounts[path]["readOnly"] is True
        # A subPath mount never sees a ConfigMap update; the switches are re-read every 10 s.
        assert "subPath" not in mounts[path]


def _config_files(docs: list[dict]) -> dict[str, str]:
    return {n: body for c in _of(docs, "ConfigMap") for n, body in c["data"].items()}


def test_switches_default_disables_nothing_in_the_hosts_format(docs):
    files = _config_files(docs)
    assert set(files) == {"keyring.json", "disabled_domains"}
    entries = [ln.split("#", 1)[0].strip() for ln in files["disabled_domains"].splitlines()]
    assert [e for e in entries if e] == []
    assert files["disabled_domains"].lstrip().startswith("#")


def test_break_glass_lines_render_verbatim(tmp_path):
    extra = tmp_path / "switch.yaml"
    extra.write_text(yaml.safe_dump({"disabledDomains": "finance\nhealth  # incident\n"}))
    docs = _render(values=("values.dev.yaml", str(extra)))
    assert _config_files(docs)["disabled_domains"] == "finance\nhealth  # incident\n"


def test_default_keyring_is_refused_by_the_hosts_parser_not_a_crash(docs):
    """An empty keyring makes the host verify nothing; the parser raises its one error type."""
    from butlers.core.runtime_probe_control.keys import (
        RuntimeProbeControlKeyError,
        parse_verifier_keyring_document,
    )

    raw = _config_files(docs)["keyring.json"].encode()
    with pytest.raises(RuntimeProbeControlKeyError):
        parse_verifier_keyring_document(raw)


def test_keyring_comes_from_values_verbatim(tmp_path):
    keyring = json.dumps({"version": 1, "current": {"kid": "w1"}, "retiring": []})
    extra = tmp_path / "keyring.yaml"
    extra.write_text(yaml.safe_dump({"keyringJson": keyring}), encoding="utf-8")
    docs = _render(values=("values.dev.yaml", str(extra)))
    assert json.loads(_config_files(docs)["keyring.json"])["current"]["kid"] == "w1"


def test_service_is_cluster_internal_on_the_mcp_port_only(docs):
    service = _one(docs, "Service")
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert [p["port"] for p in service["spec"]["ports"]] == [_MCP_PORT]
    assert service["spec"]["selector"] == {
        **_POD,
        "app.kubernetes.io/instance": "butlers-public-mcp",
    }
    assert not _of(docs, "Ingress") and not _of(docs, "IngressRoute")


# --- network policies ---------------------------------------------------------------------


def _selects(policy: dict) -> bool:
    match = policy["spec"]["podSelector"].get("matchLabels", {})
    assert "matchExpressions" not in policy["spec"]["podSelector"]
    return all(_POD.get(k) == v for k, v in match.items())


def _union(docs: list[dict], direction: str) -> tuple[bool, list[dict]]:
    """Whether the pod is isolated for the direction, and the union of allow rules."""
    policies = [p for p in _of(docs, "NetworkPolicy") if _selects(p)]
    types = {t for p in policies for t in p["spec"]["policyTypes"]}
    rules = [r for p in policies if direction in p["spec"]["policyTypes"]
             for r in p["spec"].get(direction.lower(), [])]  # fmt: skip
    return direction in types, rules


def _peers(rule: dict, key: str) -> list[dict]:
    peers = rule[key]
    assert peers, "an empty peer list admits everything"
    return peers


def test_default_deny_selects_every_pod_in_both_directions(docs):
    policies = _of(docs, "NetworkPolicy")
    deny = [p for p in policies if p["spec"]["podSelector"] == {}]
    assert len(deny) == 1
    assert set(deny[0]["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert not deny[0]["spec"].get("ingress") and not deny[0]["spec"].get("egress")


def test_exactly_the_four_allows_and_nothing_wider(docs):
    isolated_in, ingress = _union(docs, "Ingress")
    isolated_out, egress = _union(docs, "Egress")
    assert isolated_in and isolated_out

    def ports(rule):
        return sorted((p["protocol"], p["port"]) for p in rule["ports"])

    ingress_summary = sorted(
        json.dumps({"from": _peers(rule, "from"), "ports": ports(rule)}, sort_keys=True)
        for rule in ingress
    )
    assert ingress_summary == sorted(
        json.dumps(entry, sort_keys=True)
        for entry in (
            {  # the filter-proxy release in `mcp`, on the MCP port only
                "from": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "mcp"}
                        },
                        "podSelector": {"matchLabels": {"app.kubernetes.io/instance": "butlers"}},
                    }
                ],
                "ports": [("TCP", _MCP_PORT)],
            },
            {  # the monitoring namespace, on the metrics port only
                "from": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "lgtm"}
                        },
                    }
                ],
                "ports": [("TCP", _METRICS_PORT)],
            },
        )
    )

    egress_summary = sorted(
        json.dumps({"to": _peers(rule, "to"), "ports": ports(rule)}, sort_keys=True)
        for rule in egress
    )
    assert egress_summary == sorted(
        json.dumps(entry, sort_keys=True)
        for entry in (
            {  # the instance pods of the shared cluster, never the operator or other pods
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "postgres-dev"}
                        },
                        "podSelector": {
                            "matchLabels": {
                                "cnpg.io/cluster": "postgres-dev",
                                "cnpg.io/podRole": "instance",
                            }
                        },
                    }
                ],
                "ports": [("TCP", 5432)],
            },
            {  # kube-dns
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                        },
                        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                    }
                ],
                "ports": [("TCP", 53), ("UDP", 53)],
            },
        )
    )


def test_no_rule_has_an_ipblock_an_empty_selector_or_a_port_range(docs):
    for policy in _of(docs, "NetworkPolicy"):
        text = json.dumps(policy)
        assert "ipBlock" not in text, policy["metadata"]["name"]
        assert "endPort" not in text
        assert "0.0.0.0" not in text
        for rule in policy["spec"].get("ingress", []) + policy["spec"].get("egress", []):
            assert rule.get("ports"), "a rule without ports admits every port"
            for peer in rule.get("from", []) + rule.get("to", []):
                assert "namespaceSelector" in peer, "a bare podSelector means this namespace only"
                assert peer["namespaceSelector"].get("matchLabels")


def test_no_internet_egress_and_no_route_to_the_primary_or_other_namespaces(docs):
    _, egress = _union(docs, "Egress")
    namespaces = {
        peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        for rule in egress
        for peer in rule["to"]
    }
    assert namespaces == {"postgres-dev", "kube-system"}
    ports = {p["port"] for rule in egress for p in rule["ports"]}
    assert ports == {53, 5432}


def _render_raw(*settings: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "butlers-public-mcp", str(_CHART), "-n", "butlers-dev"]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    for setting in ("image.tag=t", *_SITE, *settings):
        command += ["--set", setting]
    return subprocess.run(command, capture_output=True, text=True)


def _policy_fingerprint(docs: list[dict]) -> str:
    policies = sorted(_of(docs, "NetworkPolicy"), key=lambda p: p["metadata"]["name"])
    return json.dumps(policies, sort_keys=True)


@pytest.mark.parametrize(
    "override",
    [
        "networkPolicy.filterProxy.podLabels.app\\.kubernetes\\.io/instance=null",
        "networkPolicy.filterProxy.podLabels=null",
        "networkPolicy.filterProxy.namespace=butlers-dev",
        "networkPolicy.filterProxy.namespace=",
        "networkPolicy.monitoringNamespace=other",
        "networkPolicy.monitoringNamespace=",
        "database.clusterNamespace=butlers-dev",
        "database.clusterNamespace=",
        "database.clusterName=other",
        "database.clusterName=",
        "database.port=1",
        "database.port=65535",
        "database.host=elsewhere.example",
        "ports.mcp=9999",
        "ports.metrics=1",
        "env=dev/x",
        "env=Dev",
    ],
)
def test_no_value_can_widen_or_move_a_network_allow(docs, override):
    """Each override fails the render or leaves every rendered policy exactly as it was."""
    result = _render_raw(override)
    if result.returncode != 0:
        return
    rendered = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert _policy_fingerprint(rendered) == _policy_fingerprint(docs), override


def test_the_allows_are_derived_from_env_not_from_values():
    prod_like = _render("env=stage")
    egress = [r for p in _of(prod_like, "NetworkPolicy") for r in p["spec"].get("egress", [])]
    selectors = [peer for rule in egress for peer in rule["to"]]
    names = {
        s["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for s in selectors
    }
    assert names == {"postgres-stage", "kube-system"}


def test_replicas_cannot_be_overridden_by_values():
    assert _render_raw("replicas=2").returncode != 0
    assert _render_raw("replicas=0").returncode != 0
    ok = _render_raw("replicas=1")
    assert ok.returncode == 0, ok.stderr
    deployment = next(d for d in yaml.safe_load_all(ok.stdout) if d and d["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 1


def test_the_mounts_always_exist_even_when_the_switch_file_is_empty(tmp_path):
    """A missing disabled_domains file disables every domain in the host: always render it."""
    extra = tmp_path / "empty.yaml"
    extra.write_text(yaml.safe_dump({"disabledDomains": ""}))
    docs = _render(values=("values.dev.yaml", str(extra)))
    assert _config_files(docs)["disabled_domains"] == ""
    mounts = {m["mountPath"] for m in _container(docs)["volumeMounts"]}
    assert _SWITCHES_DIR in mounts


def test_the_canaries_secret_and_mount_are_always_present(docs):
    """The chart cannot read the item's content; it guarantees the file, the docs name the rule."""
    assert "butlers-public-mcp-canaries" in _external_keys(docs)
    readme = (_CHART / "README.md").read_text()
    assert "non-empty" in readme
    assert "non-empty" in (_ROOT / "docs/operations/public-mcp-read-surface.md").read_text()


def test_policies_are_confined_to_the_public_namespace(docs):
    for policy in _of(docs, "NetworkPolicy"):
        assert policy["metadata"]["namespace"] == _NAMESPACE


def test_prod_values_are_absent_so_prod_cannot_be_rendered_by_accident():
    assert not (_CHART / "values.prod.yaml").exists()
    result = subprocess.run(
        ["helm", "template", "x", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "--set", "image.tag=t"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode != 0, "values.yaml alone must not render a deployable release"


# --- custody ------------------------------------------------------------------------------


def test_the_db_password_reaches_only_this_chart():
    """The butlers chart renders no reference to the mcp_public_ro credential (task 1.8)."""
    command = ["helm", "template", "butlers", str(_BUTLERS_CHART), "-n", "butlers-dev"]
    command += ["-f", str(_BUTLERS_CHART / "values.yaml")]
    command += ["-f", str(_BUTLERS_CHART / "values.dev.yaml")]
    for setting in (
        "image.tag=t",
        "frontendImage.tag=t",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={192.0.2.2/32}",
    ):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for name in ("MCP_PUBLIC_RO_DB_PASSWORD", "mcp_public_ro", "MCP_PUBLIC_HOP_BEARER_DIGEST",
                 "MCP_PUBLIC_CANARIES"):  # fmt: skip
        assert name not in result.stdout, name
    chart_text = "\n".join(p.read_text() for p in _CHART.rglob("*.yaml"))
    assert "MCP_PUBLIC_RO_DB_PASSWORD" in chart_text


def test_the_public_chart_holds_no_secret_value(docs):
    assert not _of(docs, "Secret")
    for doc in docs:
        if doc["kind"] == "ExternalSecret":
            for data in doc["spec"]["data"]:
                assert set(data) == {"secretKey", "remoteRef"}


def test_the_public_chart_is_not_a_butlers_chart_dependency():
    chart = yaml.safe_load((_BUTLERS_CHART / "Chart.yaml").read_text())
    assert "dependencies" not in chart
    assert not (_CHART / "templates" / "tracker-egress.yaml").exists()


def test_the_egress_profile_row_declares_exactly_what_the_policies_allow():
    """bu-dhloi1.15: the reviewed inventory matches the chart's egress, nothing external."""
    import tomllib

    profiles = tomllib.loads((_ROOT / "deploy/egress-profiles.toml").read_text())
    row = profiles["workloads"]["butlers-public-mcp"]
    assert row["sites"] == [{"site": "database", "port": 5432}]
    assert not row.get("external") and not row.get("internal") and "gateway_port" not in row
