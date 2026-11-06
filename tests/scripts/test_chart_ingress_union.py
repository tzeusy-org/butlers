"""REQ-deployment-hardening-013: the additive union of rendered ingress policies.

Allowed ingress to a pod is the union of every policy that selects it, so a test of
any one policy proves nothing. This evaluates the rendered chart's actual union for
each rendered pod template. It is rendered intent, not proof of CNI enforcement.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed"),
]

_CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "butlers"
_NAMESPACE = {"dev": "butlers-dev", "prod": "butlers"}
_MONITORING_NAMESPACE = "lgtm"
_METRICS_PORT = 9464
_NODE_SOURCE = "10.42.0.1"
_NARROWER_COMPONENTS = {"dashboard-api", "frontend", "connector-owntracks", "butlers-control"}
_RENDERS = [("dev", ()), ("prod", ()), ("dev", ("butlersControl.enabled=true",))]


@dataclass(frozen=True)
class Source:
    namespace: str
    labels: dict[str, str] = field(default_factory=dict, hash=False)
    ip: str | None = None


def _render(env: str, *settings: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "butlers", str(_CHART), "--namespace", _NAMESPACE[env]]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / f"values.{env}.yaml")]
    site = ("trackerEgress.deniedCidrs={192.0.2.1/32}", "database.host=postgres.invalid")
    for setting in ("image.tag=t", "frontendImage.tag=t", *site, *settings):
        command += ["--set", setting]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _docs(env: str, *settings: str) -> list[dict]:
    result = _render(env, *settings)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _pods(docs: list[dict]) -> dict[str, dict]:
    pods = {}
    for doc in docs:
        spec = doc.get("spec", {})
        if doc["kind"] == "CronJob":
            pods[doc["metadata"]["name"]] = spec["jobTemplate"]["spec"]["template"]
        elif doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
            pods[doc["metadata"]["name"]] = spec["template"]
    return pods


def _pod_labels(pod: dict) -> dict[str, str]:
    # A CronJob template may carry no labels; a selector still has to reason about it.
    return pod.get("metadata", {}).get("labels", {})


def _pod_ports(pod: dict) -> set[int]:
    containers = pod["spec"]["containers"]
    return {port["containerPort"] for c in containers for port in c.get("ports", [])}


def _selects(selector: dict, labels: dict[str, str]) -> bool:
    unknown = set(selector) - {"matchLabels", "matchExpressions"}
    if unknown:
        raise ValueError(f"unknown selector fields: {sorted(unknown)}")
    if any(labels.get(key) != value for key, value in selector.get("matchLabels", {}).items()):
        return False
    for expression in selector.get("matchExpressions", []):
        key, operator = expression["key"], expression["operator"]
        values = expression.get("values", [])
        if operator == "In":
            matched = labels.get(key) in values
        elif operator == "NotIn":
            matched = labels.get(key) not in values
        elif operator == "Exists":
            matched = key in labels
        elif operator == "DoesNotExist":
            matched = key not in labels
        else:
            raise ValueError(f"unknown selector operator: {operator}")
        if not matched:
            return False
    return True


def _peer_admits(peer: dict, source: Source, policy_namespace: str) -> bool:
    unknown = set(peer) - {"ipBlock", "namespaceSelector", "podSelector"}
    if unknown or not peer:
        raise ValueError(f"unknown peer shape: {peer}")
    if "ipBlock" in peer:
        if set(peer) != {"ipBlock"}:
            raise ValueError(f"unknown peer shape: {peer}")
        block = peer["ipBlock"]
        if block.get("except"):
            raise ValueError(f"unknown peer shape: {peer}")
        if source.ip is None:
            return False
        return ipaddress.ip_address(source.ip) in ipaddress.ip_network(block["cidr"])
    if "namespaceSelector" in peer:
        namespace_labels = {"kubernetes.io/metadata.name": source.namespace}
        if not _selects(peer["namespaceSelector"], namespace_labels):
            return False
    elif source.namespace != policy_namespace:
        return False
    return _selects(peer.get("podSelector", {}), source.labels)


def _rule_admits(rule: dict, source: Source, port: int, policy_namespace: str) -> bool:
    ports = rule.get("ports")
    if ports and not any(item.get("port") == port for item in ports):
        return False
    peers = rule.get("from")
    return not peers or any(_peer_admits(peer, source, policy_namespace) for peer in peers)


def _ingress_policies(docs: list[dict]) -> list[dict]:
    return [
        doc
        for doc in docs
        if doc["kind"] == "NetworkPolicy"
        and "Ingress" in doc["spec"].get("policyTypes", ["Ingress"])
    ]


def _admitting(docs: list[dict], pod: dict, source: Source, port: int, ns: str) -> list[str]:
    """Name each policy in the union admitting source to pod:port; no policy means open."""
    labels = _pod_labels(pod)
    selecting = [p for p in _ingress_policies(docs) if _selects(p["spec"]["podSelector"], labels)]
    if not selecting:
        return ["<no policy selects this pod, so all ingress is open>"]
    return [
        policy["metadata"]["name"]
        for policy in selecting
        if any(_rule_admits(r, source, port, ns) for r in policy["spec"].get("ingress", []))
    ]


def _offenders(docs: list[dict], env: str) -> list[str]:
    """Every outside-namespace or cross-identity flow the reviewed flows do not include."""
    ns = _NAMESPACE[env]
    forbidden = ["mcp", "mcp-gateway", f"butlers-public-{env}"]
    outsiders = [Source(name, {"app": "probe"}) for name in forbidden + ["unrelated"]]
    monitor = Source(_MONITORING_NAMESPACE, {"app": "scraper"})
    insider = Source(ns, {"app.kubernetes.io/component": "unreviewed-pod"})
    found = []
    for name, pod in _pods(docs).items():
        component = _pod_labels(pod).get("app.kubernetes.io/component", "<unlabelled>")
        selector = f"{name} ({component})"
        for port in sorted(_pod_ports(pod) | {41100, 41101, 41199, _METRICS_PORT}):
            for source in outsiders:
                for policy in _admitting(docs, pod, source, port, ns):
                    found.append(f"{policy}: {source.namespace} -> {selector}:{port}")
            if port != _METRICS_PORT:
                for policy in _admitting(docs, pod, monitor, port, ns):
                    found.append(f"{policy}: {monitor.namespace} -> {selector}:{port}")
            if component == "butlers-control" and port != 41100:
                for policy in _admitting(docs, pod, insider, port, ns):
                    found.append(f"{policy}: unreviewed in-namespace -> {selector}:{port}")
    return found


def _pod_named(docs: list[dict], name: str) -> dict:
    return _pods(docs)[name]


@pytest.mark.parametrize(("env", "settings"), _RENDERS)
def test_no_outside_namespace_source_reaches_any_pod_beyond_the_reviewed_flows(env, settings):
    docs = _docs(env, *settings)
    assert _pods(docs), "the render must contain pods for the union to mean anything"
    assert _offenders(docs, env) == []


@pytest.mark.parametrize(("env", "settings"), _RENDERS)
def test_every_rendered_pod_is_selected_by_the_empty_default_deny(env, settings):
    docs = _docs(env, *settings)
    deny = next(p for p in _ingress_policies(docs) if not p["spec"].get("ingress"))
    assert deny["spec"]["podSelector"] == {}
    assert deny["spec"]["policyTypes"] == ["Ingress"]


def test_reviewed_flows_keep_working():
    docs = _docs("dev")
    ns = _NAMESPACE["dev"]

    def admitted(pod: str, source: Source, port: int) -> bool:
        return bool(_admitting(docs, _pod_named(docs, pod), source, port, ns))

    dashboard = Source(ns, {"app.kubernetes.io/component": "dashboard-api"})
    daemon = Source(ns, {"app.kubernetes.io/component": "butlers-up"})
    connector = Source(ns, {"app.kubernetes.io/component": "connector-gmail"})
    node = Source("<host>", ip=_NODE_SOURCE)
    monitor = Source(_MONITORING_NAMESPACE, {"app": "scraper"})

    # The dashboard API and butlers reach each other's MCP endpoints in the namespace.
    assert admitted("butlers-up", dashboard, 41101)
    assert admitted("butlers-up", daemon, 41101)
    assert admitted("butlers-up", connector, 41100)
    assert admitted("dashboard-api", daemon, 41200)
    # Host ingress is unchanged: the node and namespace pods reach what the host serves.
    assert admitted("dashboard-api", node, 41200)
    assert admitted("frontend", node, 80)
    assert admitted("connector-owntracks", node, 40086)
    assert not admitted("butlers-up", node, 41101)
    # The configured scraper reaches declared metrics ports, on both kinds of connector.
    assert admitted("connector-gmail", monitor, _METRICS_PORT)
    assert admitted("connector-owntracks", monitor, _METRICS_PORT)
    assert not admitted("butlers-up", monitor, _METRICS_PORT)


def test_control_session_reachability_is_unchanged():
    docs = _docs("dev", "butlersControl.enabled=true")
    ns = _NAMESPACE["dev"]
    control = _pod_named(docs, "butlers-control")
    session_host = Source(ns, {"app.kubernetes.io/instance": "butlers",
                               "app.kubernetes.io/component": "session-host"})  # fmt: skip
    other = Source(ns, {"app.kubernetes.io/instance": "butlers",
                        "app.kubernetes.io/component": "dashboard-api"})  # fmt: skip

    assert _admitting(docs, control, session_host, 41199, ns) == ["butlers-control-ingress"]
    assert _admitting(docs, control, other, 41199, ns) == []
    assert _admitting(docs, control, other, 41100, ns) == ["butlers-control-ingress"]


def test_no_new_allow_policy_selects_a_pod_with_a_narrower_reviewed_policy():
    docs = _docs("dev", "butlersControl.enabled=true")
    reviewed = {"butlers-host-ingress", "butlers-control-ingress", "butlers-default-deny-ingress"}
    for policy in _ingress_policies(docs):
        if policy["metadata"]["name"] in reviewed:
            continue
        for component in _NARROWER_COMPONENTS:
            # The union carries owntracks' metrics rule in host ingress, not here.
            labels = {"app.kubernetes.io/component": component,
                      "butlers.io/connector-metrics": "true"}  # fmt: skip
            assert not _selects(policy["spec"]["podSelector"], labels), (
                policy["metadata"]["name"],
                component,
            )


def _widening(from_rule: dict | None, selector: dict | None = None) -> dict:
    rule = {} if from_rule is None else {"from": [from_rule]}
    return {
        "kind": "NetworkPolicy",
        "metadata": {"name": "widening"},
        "spec": {"podSelector": selector or {}, "policyTypes": ["Ingress"], "ingress": [rule]},
    }


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (_widening(None), "widening: mcp -> butlers-up (butlers-up):41101"),
        (
            _widening({"namespaceSelector": {}}),
            "widening: mcp -> butlers-up (butlers-up):41101",
        ),
        (
            _widening(
                {
                    "namespaceSelector": {
                        "matchExpressions": [
                            {
                                "key": "kubernetes.io/metadata.name",
                                "operator": "NotIn",
                                "values": ["kube-system"],
                            }
                        ]
                    }
                },
                {
                    "matchExpressions": [
                        {"key": "app.kubernetes.io/component", "operator": "Exists"}
                    ]
                },
            ),
            "widening: mcp-gateway -> butlers-up (butlers-up):41101",
        ),
        (
            _widening(
                {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "mcp"}}}
            ),
            "widening: mcp -> frontend (frontend):41100",
        ),
    ],
)
def test_a_widening_policy_fails_naming_the_selector_and_source(policy, expected):
    assert expected in _offenders([*_docs("dev"), policy], "dev")


def test_a_namespace_only_allow_covering_the_control_session_port_fails():
    docs = _docs("dev", "butlersControl.enabled=true")
    found = _offenders([*docs, _widening({"podSelector": {}})], "dev")
    assert "widening: unreviewed in-namespace -> butlers-control (butlers-control):41199" in found


def test_an_unknown_peer_shape_refuses_instead_of_being_ignored():
    docs = _docs("dev")
    with pytest.raises(ValueError, match="unknown peer shape"):
        _offenders([*docs, _widening({"serviceAccountSelector": {}})], "dev")


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (("connectorMetrics.sourceNamespace=",), "sourceNamespace"),
        (("connectorMetrics.sourceNamespace=mcp",), "may not scrape"),
        (("connectorMetrics.sourceNamespace=mcp-gateway",), "may not scrape"),
        (("connectorMetrics.sourceNamespace=butlers-public-dev",), "may not scrape"),
        (("connectorMetrics.allowFrom[0].ipBlock.cidr=0.0.0.0/0",), "allowFrom"),
        (("hostIngress.restrict=false",), "restrict"),
    ],
)
def test_chart_refuses_an_empty_all_source_or_forbidden_monitoring_source(settings, message):
    result = _render("dev", *settings)
    assert result.returncode != 0
    assert message in result.stderr


def test_disabling_metrics_renders_no_metrics_allow_and_needs_no_source():
    docs = _docs("dev", "connectorMetrics.enabled=false", "connectorMetrics.sourceNamespace=")
    assert _offenders(docs, "dev") == []
    ns = _NAMESPACE["dev"]
    monitor = Source(_MONITORING_NAMESPACE, {"app": "scraper"})
    gmail = _pod_named(docs, "connector-gmail")
    assert _admitting(docs, gmail, monitor, _METRICS_PORT, ns) == []
