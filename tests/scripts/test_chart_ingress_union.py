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
_HOST_COMPONENTS = {"dashboard-api", "frontend", "connector-owntracks"}
_CONTROL_RULES = [
    {"from": [{"podSelector": {}}], "ports": [{"protocol": "TCP", "port": 41100}]},
    {
        "from": [
            {"podSelector": {"matchLabels": {"app.kubernetes.io/instance": "butlers",
                                             "app.kubernetes.io/component": component}}}
            for component in ("session-host", "control-session-health")
        ],
        "ports": [{"protocol": "TCP", "port": 41199}],
    },
]  # fmt: skip
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
        if doc["kind"] in {"Pod", "ReplicaSet", "ReplicationController"}:
            raise ValueError(f"pod-bearing kind this evaluator does not read: {doc['kind']}")
        if doc["kind"] == "CronJob":
            pods[doc["metadata"]["name"]] = spec["jobTemplate"]["spec"]["template"]
        elif doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
            pods[doc["metadata"]["name"]] = spec["template"]
    return pods


def _pod_labels(pod: dict) -> dict[str, str]:
    # A CronJob template may carry no labels; a selector still has to reason about it.
    return pod.get("metadata", {}).get("labels", {})


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


def _port_matches(item: dict, port: int) -> bool:
    declared = item.get("port")
    if declared is None:  # A protocol with no port means every port.
        return True
    if not isinstance(declared, int):
        raise ValueError(f"named port needs resolving per pod: {item}")
    return declared <= port <= item.get("endPort", declared)


def _rule_admits(rule: dict, source: Source, port: int, policy_namespace: str) -> bool:
    ports = rule.get("ports")
    if ports and not any(_port_matches(item, port) for item in ports):
        return False
    peers = rule.get("from")
    return not peers or any(_peer_admits(peer, source, policy_namespace) for peer in peers)


def _ingress_policies(docs: list[dict]) -> list[dict]:
    for doc in docs:
        if "NetworkPolicy" in doc["kind"] and doc["kind"] != "NetworkPolicy":
            raise ValueError(f"policy kind this evaluator does not read: {doc['kind']}")
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


def _peer_offence(rule: dict, peer: dict, labels: dict[str, str]) -> str | None:
    """Why a peer is outside the reviewed flows; unlisted shapes offend, so this fails closed."""
    unknown = set(peer) - {"ipBlock", "namespaceSelector", "podSelector"}
    if unknown or not peer:
        raise ValueError(f"unknown peer shape: {peer}")
    component = labels.get("app.kubernetes.io/component")
    if component == "butlers-control" and rule not in _CONTROL_RULES:
        return "not a reviewed butlers-control rule"
    if "ipBlock" in peer:
        if peer != {"ipBlock": {"cidr": f"{_NODE_SOURCE}/32"}}:
            return "ipBlock other than the node's address"
        return None if component in _HOST_COMPONENTS else "node address on a non-host pod"
    if "namespaceSelector" in peer:
        scraper = {"matchLabels": {"kubernetes.io/metadata.name": _MONITORING_NAMESPACE}}
        if peer != {"namespaceSelector": scraper}:
            return "namespace other than exactly the named scraper namespace"
        if rule.get("ports") != [{"protocol": "TCP", "port": _METRICS_PORT}]:
            return "scraper namespace on anything but exactly the metrics port"
        scraped = labels.get("butlers.io/connector-metrics") == "true"
        return None if scraped or component in _HOST_COMPONENTS else "scraper on an unscraped pod"
    return None


def _offenders(docs: list[dict], env: str) -> list[str]:
    """Every rule reaching beyond the reviewed flows, read from its shape, not sampled sources.

    Sampling source labels, addresses and ports misses any selector the samples happen not
    to match, so each peer must instead be a shape this file lists as reviewed.
    """
    policies = _ingress_policies(docs)
    pods = _pods(docs)
    found = []
    for policy in policies:
        # Runtime-added labels (pod-template-hash, job-name) reach pods no template shows, so
        # a rule whose selector matches no rendered template cannot be reasoned about here.
        selector = policy["spec"]["podSelector"]
        rules = policy["spec"].get("ingress", [])
        if rules and not any(_selects(selector, _pod_labels(pod)) for pod in pods.values()):
            found.append(
                f"{policy['metadata']['name']}: {rules} -> {selector}: selects no rendered pod"
            )
    for name, pod in pods.items():
        labels = _pod_labels(pod)
        target = f"{name} ({labels.get('app.kubernetes.io/component', '<unlabelled>')})"
        selecting = [p for p in policies if _selects(p["spec"]["podSelector"], labels)]
        if not selecting:
            found.append(f"<no policy selects this pod, so all ingress is open> -> {target}")
        for policy in selecting:
            policy_name = policy["metadata"]["name"]
            for rule in policy["spec"].get("ingress", []):
                ports = rule.get("ports", "all ports")
                if not rule.get("from"):
                    found.append(f"{policy_name}: any source -> {target} {ports}")
                for peer in rule.get("from", []):
                    offence = _peer_offence(rule, peer, labels)
                    if offence:
                        found.append(f"{policy_name}: {peer} -> {target} {ports}: {offence}")
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
    assert admitted("frontend", node, 8080)
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


_BUTLERS_UP = {"matchLabels": {"app.kubernetes.io/component": "butlers-up"}}
_MCP = {"matchLabels": {"kubernetes.io/metadata.name": "mcp"}}
_SCRAPER = {"matchLabels": {"kubernetes.io/metadata.name": _MONITORING_NAMESPACE}}
_METRICS_RULE_PORTS = [{"protocol": "TCP", "port": _METRICS_PORT}]


def _widening(peer: dict | None, ports: list[dict] | None = None, selector: dict | None = None):
    rule: dict = {} if peer is None else {"from": [peer]}
    if ports is not None:
        rule["ports"] = ports
    spec = {"podSelector": _BUTLERS_UP if selector is None else selector}
    return {
        "kind": "NetworkPolicy",
        "metadata": {"name": "widening"},
        "spec": {**spec, "policyTypes": ["Ingress"], "ingress": [rule]},
    }


@pytest.mark.parametrize(
    ("peer", "ports"),
    [
        pytest.param(None, None, id="any-source"),
        pytest.param(None, [{"protocol": "TCP", "port": 41103}], id="any-source-one-daemon-port"),
        pytest.param({"namespaceSelector": {}}, None, id="all-namespaces"),
        pytest.param(
            {"namespaceSelector": {}, "podSelector": {"matchLabels": {"app": "gateway"}}},
            None,
            id="all-namespaces-with-pod-label",
        ),
        pytest.param({"namespaceSelector": _MCP}, None, id="forbidden-namespace"),
        pytest.param(
            {"namespaceSelector": _MCP, "podSelector": {"matchLabels": {"app": "gateway"}}},
            None,
            id="forbidden-namespace-with-pod-label",
        ),
        pytest.param(
            {"namespaceSelector": _MCP},
            [{"protocol": "TCP", "port": 41103}],
            id="forbidden-namespace-one-unprobed-port",
        ),
        pytest.param(
            {"namespaceSelector": {"matchLabels": {"team": "platform"}}},
            None,
            id="namespace-by-non-name-label",
        ),
        pytest.param(
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
            None,
            id="namespace-by-broad-expression",
        ),
        pytest.param({"ipBlock": {"cidr": "0.0.0.0/0"}}, None, id="all-addresses"),
        pytest.param({"ipBlock": {"cidr": "10.42.0.128/25"}}, None, id="pod-range-block"),
        pytest.param({"ipBlock": {"cidr": f"{_NODE_SOURCE}/32"}}, None, id="node-on-a-daemon"),
        pytest.param({"namespaceSelector": _SCRAPER}, [{"protocol": "TCP"}], id="protocol-only"),
        pytest.param(
            {"namespaceSelector": _SCRAPER},
            [{"protocol": "TCP", "port": "daemon"}],
            id="named-port",
        ),
        pytest.param(
            {"namespaceSelector": _SCRAPER},
            [{"protocol": "TCP", "port": 41000, "endPort": 42000}],
            id="port-range",
        ),
        pytest.param({"namespaceSelector": _SCRAPER}, _METRICS_RULE_PORTS, id="scraper-unscraped"),
    ],
)
def test_a_widening_policy_fails_naming_the_selector_and_source(peer, ports):
    found = _offenders([*_docs("dev"), _widening(peer, ports)], "dev")
    assert len(found) == 1
    assert found[0].startswith("widening: ")
    assert "butlers-up (butlers-up)" in found[0]
    assert ("any source" if peer is None else str(peer)) in found[0]


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(
            {"matchExpressions": [{"key": "pod-template-hash", "operator": "Exists"}]},
            id="deployment-runtime-label",
        ),
        pytest.param(
            {"matchExpressions": [{"key": "job-name", "operator": "Exists"}]},
            id="cronjob-runtime-label",
        ),
        pytest.param({"matchLabels": {"tier": "daemon"}}, id="label-on-no-template"),
    ],
)
def test_a_policy_selecting_no_rendered_pod_fails(selector):
    policy = _widening({"namespaceSelector": _MCP}, selector=selector)
    found = _offenders([*_docs("dev"), policy], "dev")
    assert len(found) == 1
    assert found[0].startswith("widening: ") and "selects no rendered pod" in found[0]
    assert "'mcp'" in found[0] and str(selector) in found[0]


@pytest.mark.parametrize(
    ("kind", "match"),
    [
        ("Pod", "pod-bearing kind"),
        ("ReplicaSet", "pod-bearing kind"),
        ("CiliumNetworkPolicy", "policy kind"),
    ],
)
def test_a_kind_the_evaluator_cannot_read_refuses(kind, match):
    extra = {"kind": kind, "metadata": {"name": "extra"}, "spec": {}}
    with pytest.raises(ValueError, match=match):
        _offenders([*_docs("dev"), extra], "dev")


@pytest.mark.parametrize(("env", "settings"), _RENDERS)
def test_no_pod_opens_a_path_that_network_policy_cannot_see(env, settings):
    docs = _docs(env, *settings)
    for name, pod in _pods(docs).items():
        assert not pod["spec"].get("hostNetwork"), name
        for container in pod["spec"]["containers"]:
            assert not any("hostPort" in port for port in container.get("ports", [])), name
    for service in (d for d in docs if d["kind"] == "Service"):
        if service["spec"].get("type") == "NodePort":
            component = service["spec"]["selector"]["app.kubernetes.io/component"]
            assert component in _HOST_COMPONENTS, service["metadata"]["name"]


def test_a_rule_widening_an_existing_policy_fails_too():
    docs = _docs("dev")
    host = next(d for d in docs if d["metadata"]["name"] == "butlers-host-ingress")
    host["spec"]["ingress"][0]["from"].append({"ipBlock": {"cidr": "0.0.0.0/0"}})
    found = _offenders(docs, "dev")
    assert any(f.startswith("butlers-host-ingress: ") and "dashboard-api" in f for f in found)


def test_a_namespace_only_allow_covering_the_control_session_port_fails():
    docs = _docs("dev", "butlersControl.enabled=true")
    found = _offenders([*docs, _widening({"podSelector": {}}, selector={})], "dev")
    assert any(f.startswith("widening: ") and "butlers-control" in f for f in found)


@pytest.mark.parametrize(
    ("item", "port", "expected"),
    [
        ({"protocol": "TCP"}, 41103, True),
        ({"protocol": "TCP", "port": 41000, "endPort": 42000}, 41103, True),
        ({"protocol": "TCP", "port": 41000, "endPort": 41050}, 41103, False),
        ({"protocol": "TCP", "port": 9464}, 9464, True),
        ({"protocol": "TCP", "port": 9464}, 9465, False),
    ],
)
def test_port_matching_follows_kubernetes_semantics(item, port, expected):
    assert _port_matches(item, port) is expected


def test_a_named_port_refuses_rather_than_matching_nothing():
    with pytest.raises(ValueError, match="named port"):
        _port_matches({"protocol": "TCP", "port": "daemon"}, 41100)


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
        (('connectorMetrics.sourceNamespace="mcp"',), "sourceNamespace"),
        (("connectorMetrics.sourceNamespace='mcp'",), "sourceNamespace"),
        (("connectorMetrics.sourceNamespace=mcp ",), "sourceNamespace"),
        (("connectorMetrics.sourceNamespace= mcp",), "sourceNamespace"),
        (("connectorMetrics.sourceNamespace=~",), "sourceNamespace"),
        (("connectorMetrics.sourceNamespace=lgtm\nextra: label",), "sourceNamespace"),
        (("hostIngress.nodeSourceCidr=0.0.0.0/0",), "nodeSourceCidr"),
        (("hostIngress.nodeSourceCidr=10.42.0.128/25",), "nodeSourceCidr"),
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
