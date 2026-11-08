"""bu-dhloi1.15 / RFC 0038 S7: the additive union of rendered egress policies.

Allowed egress from a pod is the union of every egress policy selecting it, so each
check here evaluates the whole rendered union, not one policy. This is rendered intent
from deploy/egress-profiles.toml; it is not evidence that a CNI enforces it.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from butlers.egress.profiles import load_profiles

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed"),
]

_CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "butlers"
_NS = "butlers-dev"
_TRACKER = "192.0.2.1"
_OTLP = "198.51.100.9"
_HOME_ASSISTANT = "198.51.100.10"
_OFFSITE = "198.51.100.11"
_POLICY_SITE = "10.0.0.7"
_SITES = (
    "egressProfiles.sites.database.namespace=postgres-dev",
    f"egressProfiles.sites.otlp.cidr={_OTLP}/32",
    f"egressProfiles.sites.home-assistant.cidr={_HOME_ASSISTANT}/32",
    f"egressProfiles.sites.offsite-backup.cidr={_OFFSITE}/32",
)
# Every optional workload on, so every profile that can render does.
_EVERYTHING = (
    "egressProfiles.enabled=true",
    "connectors.telegram-user.enabled=false",
    "butlersControl.enabled=true",
    "sessionHost.enabled=true",
    "offsiteCopy.enabled=true",
    "offsiteCopy.host=offsite.invalid",
    "offsiteCopy.repoPath=/repo",
    "offsiteCopy.secretName=offsite",
    *_SITES,
)


@dataclass(frozen=True)
class Dest:
    namespace: str | None = None
    labels: dict[str, str] = field(default_factory=dict, hash=False)
    ip: str | None = None


def _component(name: str) -> Dest:
    return Dest(_NS, {"app.kubernetes.io/instance": "butlers", "app.kubernetes.io/component": name})


_DNS = Dest("kube-system", {"k8s-app": "kube-dns"})
_PUBLIC_IP = "93.184.216.34"
_GATEWAY = _component("egress-gateway")


def _render(*settings: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "butlers", str(_CHART), "--namespace", _NS]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    # Declare test topology/mode rather than inheriting the environment's dev enable.
    # Explicit per-case settings below can select the predecessor policy instead.
    site = (
        "trackerEgress.enabled=true",
        f"trackerEgress.deniedCidrs={{{_TRACKER}/32}}",
        "egressPolicy.enabled=false",
        f"egressPolicy.allowedCidrs={{{_POLICY_SITE}/32}}",
        "database.host=postgres.invalid",
    )
    for setting in ("image.tag=t", "frontendImage.tag=t", *site, *settings):
        command += ["--set", setting]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _docs(*settings: str) -> list[dict]:
    result = _render(*settings)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _pods(docs: list[dict]) -> dict[str, dict[str, str]]:
    pods = {}
    for doc in docs:
        spec = doc.get("spec", {})
        if doc["kind"] == "CronJob":
            template = spec["jobTemplate"]["spec"]["template"]
        elif doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
            template = spec["template"]
        else:
            continue
        pods[doc["metadata"]["name"]] = template.get("metadata", {}).get("labels", {})
    return pods


def _selects(selector: dict, labels: dict[str, str]) -> bool:
    if set(selector) - {"matchLabels", "matchExpressions"}:
        raise ValueError(f"selector shape this evaluator does not read: {selector}")
    for expression in selector.get("matchExpressions", []):
        value, values = labels.get(expression["key"]), expression.get("values", [])
        if expression["operator"] == "In":
            matched = value in values
        elif expression["operator"] == "NotIn":
            matched = value not in values
        else:
            raise ValueError(f"selector operator this evaluator does not read: {expression}")
        if not matched:
            return False
    return all(labels.get(k) == v for k, v in selector.get("matchLabels", {}).items())


def _egress_policies(docs: list[dict]) -> list[dict]:
    return [
        doc
        for doc in docs
        if doc["kind"] == "NetworkPolicy" and "Egress" in doc["spec"].get("policyTypes", [])
    ]


def _peer_admits(peer: dict, dest: Dest) -> bool:
    if set(peer) - {"ipBlock", "namespaceSelector", "podSelector"} or not peer:
        raise ValueError(f"unknown peer shape: {peer}")
    if "ipBlock" in peer:
        if dest.ip is None:
            return False
        address = ipaddress.ip_address(dest.ip)
        block = peer["ipBlock"]
        inside = address in ipaddress.ip_network(block["cidr"])
        return inside and not any(
            address in ipaddress.ip_network(e) for e in block.get("except", [])
        )
    if dest.namespace is None:
        return False
    if "namespaceSelector" in peer:
        if not _selects(peer["namespaceSelector"], {"kubernetes.io/metadata.name": dest.namespace}):
            return False
    elif dest.namespace != _NS:
        return False
    return _selects(peer.get("podSelector", {}), dest.labels)


def _port_admits(rule: dict, port: int, protocol: str) -> bool:
    ports = rule.get("ports")
    if not ports:
        return True
    return any(
        item.get("protocol", "TCP") == protocol
        and ("port" not in item or item["port"] <= port <= item.get("endPort", item["port"]))
        for item in ports
    )


def _admitting(docs, pod: dict[str, str], dest: Dest, port: int, protocol="TCP") -> list[str]:
    selecting = [p for p in _egress_policies(docs) if _selects(p["spec"]["podSelector"], pod)]
    if not selecting:
        return ["<no egress policy selects this pod, so all egress is open>"]
    return [
        policy["metadata"]["name"]
        for policy in selecting
        for rule in policy["spec"].get("egress", [])
        if _port_admits(rule, port, protocol)
        # A rule with no `to` admits every destination.
        and (not rule.get("to") or any(_peer_admits(peer, dest) for peer in rule["to"]))
    ]


def _profile(component: str):
    return load_profiles().workloads[component]


@pytest.fixture(scope="module")
def everything() -> list[dict]:
    return _docs(*_EVERYTHING)


@pytest.mark.parametrize("policy_enabled", [False, True])
def test_profiles_off_renders_no_profile_policy_and_keeps_the_tracker_policy(policy_enabled):
    docs = _docs(
        "egressProfiles.enabled=false", f"egressPolicy.enabled={str(policy_enabled).lower()}"
    )
    names = {d["metadata"]["name"] for d in _egress_policies(docs)}
    assert names == {"butlers-tracker-egress"}
    pod = _pods(docs)["butlers-up"]
    assert not _admitting(docs, pod, Dest(ip=_TRACKER), 3307)
    assert _admitting(docs, pod, Dest(ip="203.0.113.5"), 443)
    assert _admitting(docs, pod, Dest(ip=_POLICY_SITE), 443)
    assert bool(_admitting(docs, pod, Dest(ip="10.0.0.5"), 443)) is (not policy_enabled)


@pytest.mark.parametrize("tracker_enabled", [True, False])
def test_rollback_holds_workloads_and_retains_restrictive_policy_union(tracker_enabled):
    settings = (
        *_EVERYTHING,
        "egressPolicy.enabled=false",
        f"trackerEgress.enabled={str(tracker_enabled).lower()}",
    )
    before = _docs(*settings)
    held = _docs(*settings, "suspend=true")
    deployments = [d for d in held if d["kind"] == "Deployment"]
    cronjobs = [d for d in held if d["kind"] == "CronJob"]
    assert deployments and cronjobs
    assert all(d["spec"]["replicas"] == 0 for d in deployments)
    assert all(d["spec"]["suspend"] is True for d in cronjobs)
    # Suspend does not remove policies: even a lingering/recreated pod stays contained.
    assert _egress_policies(held) == _egress_policies(before)
    forbidden = [(Dest(ip=_PUBLIC_IP), 443), (_component("dashboard-api"), 49999)]
    for name, labels in _pods(held).items():
        gateway = labels["app.kubernetes.io/component"] == "egress-gateway"
        for dest, port in forbidden:
            # Keep workload containment while retaining the gateway's declared
            # public route. Suspend changes replicas, never the policy union.
            expected = gateway and dest.ip == _PUBLIC_IP and port == 443
            assert bool(_admitting(held, labels, dest, port)) is expected, (name, dest, port)
    # Position the unsafe former runbook: turning profiles off widens the union,
    # whether it restores tracker-egress or leaves the pod without an egress policy.
    unsafe = _docs(*settings, "egressProfiles.enabled=false")
    assert any(d["spec"]["replicas"] > 0 for d in unsafe if d["kind"] == "Deployment")
    pod = _pods(unsafe)["butlers-up"]
    assert all(_admitting(unsafe, pod, dest, port) for dest, port in forbidden)
    # Existing positive routes survive in the retained configuration; hold is not a
    # replacement allowlist or evidence that actual workloads have been drained.
    assert _admitting(held, pod, _DNS, 53, "UDP")
    assert _admitting(held, pod, _component("dashboard-api"), 41200)


def test_profiles_replace_the_tracker_policy_and_every_pod_starts_denied(everything):
    policies = _egress_policies(everything)
    names = {p["metadata"]["name"] for p in policies}
    assert "butlers-tracker-egress" not in names
    deny = next(p for p in policies if p["metadata"]["name"] == "butlers-default-deny-egress")
    assert deny["spec"] == {"podSelector": {}, "policyTypes": ["Egress"]}


def test_every_rendered_pod_has_exactly_one_policy_owner_its_own_profile(everything):
    pods = _pods(everything)
    assert len(pods) > 15
    owners_seen = set()
    for name, labels in pods.items():
        component = labels.get("app.kubernetes.io/component")
        assert component, f"{name} has no component label, so no profile can select it"
        allowing = [
            p["metadata"]["name"]
            for p in _egress_policies(everything)
            if p["spec"].get("egress") and _selects(p["spec"]["podSelector"], labels)
        ]
        assert allowing == [f"butlers-egress-{component}"], name
        owners_seen.add(allowing[0])
    profile_policies = {
        p["metadata"]["name"] for p in _egress_policies(everything) if p["spec"].get("egress")
    }
    # The workload list in _helpers.tpl names nothing the chart does not render.
    assert profile_policies == owners_seen


def test_no_pod_reaches_the_tracker_metadata_lan_or_a_public_address_directly(everything):
    private = (_TRACKER, "169.254.169.254", "10.0.0.5", "100.100.100.100", "127.0.0.1")
    for name, labels in _pods(everything).items():
        gateway = labels["app.kubernetes.io/component"] == "egress-gateway"
        for ip in (*private, _PUBLIC_IP):
            for port in (22, 53, 80, 443, 3307, 5432):
                admitted = _admitting(everything, labels, Dest(ip=ip), port)
                # Only the gateway dials public addresses, and only on declared ports.
                expected = gateway and ip == _PUBLIC_IP and port == 443
                assert bool(admitted) is expected, (name, ip, port)


def test_each_workload_reaches_the_gateway_only_on_its_own_port(everything):
    gateway_ports = {
        name: p.gateway_port for name, p in load_profiles().workloads.items() if p.gateway_port
    }
    for name, labels in _pods(everything).items():
        component = labels["app.kubernetes.io/component"]
        for owner, port in gateway_ports.items():
            admitted = bool(_admitting(everything, labels, _GATEWAY, port))
            assert admitted is (owner == component), (name, owner, port)


def test_declared_peers_are_reachable_and_undeclared_ones_are_not(everything):
    pods = _pods(everything)

    def reaches(pod: str, dest: Dest, port: int, protocol: str = "TCP") -> bool:
        return bool(_admitting(everything, pods[pod], dest, port, protocol))

    for pod in pods:
        assert reaches(pod, _DNS, 53, "UDP"), pod
        assert not reaches(pod, Dest("kube-system", {"k8s-app": "other"}), 53, "UDP"), pod
    assert reaches("butlers-up", _component("dashboard-api"), 41200)
    assert reaches("butlers-up", Dest("postgres-dev", {"any": "pod"}), 5432)
    assert reaches("butlers-up", Dest(ip=_OTLP), 4318)
    assert not reaches("butlers-up", Dest(ip=_OTLP), 4317)
    assert not reaches("butlers-up", Dest("postgres-dev", {}), 5433)
    assert not reaches("butlers-up", Dest("other-namespace", {}), 5432)
    # A connector reaches the Switchboard port, never a domain butler's port.
    assert reaches("connector-gmail", _component("butlers-up"), 41100)
    assert not reaches("connector-gmail", _component("butlers-up"), 41101)
    assert not reaches("connector-gmail", _component("dashboard-api"), 41200)
    assert not reaches("connector-gmail", Dest(ip=_HOME_ASSISTANT), 8123)
    assert reaches("connector-home-assistant", Dest(ip=_HOME_ASSISTANT), 8123)
    # The session host reaches the control session listener and nothing internal else.
    assert reaches("session-host", _component("butlers-control"), 41199)
    for dest, port in [
        (_component("butlers-control"), 41100),
        (_component("butlers-up"), 41101),
        (Dest("postgres-dev", {}), 5432),
        (Dest(ip=_OTLP), 4318),
    ]:
        assert not reaches("session-host", dest, port), (dest, port)
    assert reaches("butlers-offsite-copy", Dest(ip=_OFFSITE), 22)
    assert not reaches("butlers-backup", Dest(ip=_OFFSITE), 22)
    for pod in ("frontend", "butlers-log-cleanup"):
        assert not reaches(pod, _component("butlers-up"), 41100), pod


def test_every_declared_internal_peer_renders_across_its_whole_port_range(everything):
    for name, labels in _pods(everything).items():
        if labels["app.kubernetes.io/component"] == "egress-gateway":
            continue  # The gateway has no profile; its policy is checked on its own.
        for peer in _profile(labels["app.kubernetes.io/component"]).internal:
            dest = _component(peer.component)
            for port in (peer.port, peer.end_port):
                assert _admitting(everything, labels, dest, port, peer.protocol), (name, peer)
            assert not _admitting(everything, labels, dest, peer.port - 1), (name, peer)


def test_no_rule_admits_every_destination(everything):
    for policy in _egress_policies(everything):
        for rule in policy["spec"].get("egress", []):
            assert rule.get("to"), policy["metadata"]["name"]
            assert rule.get("ports"), policy["metadata"]["name"]


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (("connectors.extra.module=extra",), "connector-extra renders but"),
        (("connectors.telegram-user.enabled=true",), "connector-telegram-user has a held"),
        (
            ("egressPolicy.enabled=true", "egressPolicy.allowedCidrs={198.51.100.7/32}"),
            "supersedes",
        ),
        (("egressProfiles.sites.otlp=null",), "egressProfiles.sites.otlp is required"),
        (("egressProfiles.sites.otlp.cidr=198.51.100.0/24",), "one IPv4 /32"),
        (("egressProfiles.sites.otlp.cidr=0.0.0.0/0",), "one IPv4 /32"),
        ((f"egressProfiles.sites.otlp.cidr={_TRACKER}/32",), "tracker, metadata"),
        (("egressProfiles.sites.otlp.cidr=169.254.169.254/32",), "tracker, metadata"),
        # Kubernetes reads 192.0.2.01 as the tracker's 192.0.2.1.
        (("egressProfiles.sites.otlp.cidr=192.0.2.01/32",), "one IPv4 /32"),
        (("egressProfiles.sites.otlp.cidr=127.0.0.1/32",), "tracker, metadata"),
        (("egressProfiles.sites.otlp.cidr=224.0.0.1/32",), "tracker, metadata"),
        (("egressProfiles.sites.otlp.cidr=255.255.255.255/32",), "tracker, metadata"),
        (
            ("trackerEgress.enabled=false", "trackerEgress.deniedCidrs=null"),
            "egressProfiles.enabled needs trackerEgress.deniedCidrs",
        ),
        (("egressProfiles.enabled=yes",), "must be true or false"),
        (("egressProfiles.sites.database.namespace=butlers-dev",), "release's namespace"),
        (("egressProfiles.sites.otlp.namespace=lgtm",), "exactly one of namespace or cidr"),
        (("egressProfiles.sites.otlp.port=1",), "unknown key port"),
        (("egressProfiles.sites.database.namespace=Bad_Name",), "valid namespace name"),
        (("egressProfiles.sites.database.podLabels.a\\: b=c",), "not a label key"),
    ],
)
def test_chart_refuses_to_render_an_undeclared_or_widened_egress(settings, message):
    result = _render(*_EVERYTHING, *settings)
    assert result.returncode != 0, settings
    assert message in result.stderr, result.stderr


def test_an_optional_site_left_unset_is_denied_not_required():
    docs = _docs(*(s for s in _EVERYTHING if "home-assistant" not in s),
                 "connectors.home-assistant.enabled=false")  # fmt: skip
    pods = _pods(docs)
    assert not _admitting(docs, pods["butlers-up"], Dest(ip=_HOME_ASSISTANT), 8123)


def _ingress_admitting(docs, pod: dict[str, str], source: Dest, port: int) -> list[str]:
    """Names of ingress policies admitting source to pod:port (empty union: open)."""
    selecting = [
        doc
        for doc in docs
        if doc["kind"] == "NetworkPolicy"
        and "Ingress" in doc["spec"].get("policyTypes", [])
        and _selects(doc["spec"]["podSelector"], pod)
    ]
    assert selecting, "no ingress policy selects this pod, so all ingress is open"
    return [
        policy["metadata"]["name"]
        for policy in selecting
        for rule in policy["spec"].get("ingress", [])
        if _port_admits(rule, port, "TCP")
        and (not rule.get("from") or any(_peer_admits(peer, source) for peer in rule["from"]))
    ]


def test_the_gateway_admits_each_workload_only_on_that_workloads_port(everything):
    gateway = _pods(everything)["egress-gateway"]
    ports = {n: p.gateway_port for n, p in load_profiles().workloads.items() if p.gateway_port}
    for source in [*ports, "frontend", "connector-owntracks", "backup"]:
        for owner, port in ports.items():
            admitted = _ingress_admitting(everything, gateway, _component(source), port)
            assert bool(admitted) is (source == owner), (source, owner, admitted)
        # The health port is for the kubelet only.
        assert not _ingress_admitting(everything, gateway, _component(source), 3100), source
    assert not _ingress_admitting(everything, gateway, Dest("other", {}), ports["butlers-up"])


def test_the_gateway_runs_two_replicas_with_no_service_account_token(everything):
    deployment = next(
        d
        for d in everything
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "egress-gateway"
    )
    assert deployment["spec"]["replicas"] == 2
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "--profiles" in pod["containers"][0]["command"]


def _env(docs: list[dict]) -> dict[str, dict[str, str]]:
    found = {}
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        template = doc["spec"]["template"]
        component = template["metadata"]["labels"]["app.kubernetes.io/component"]
        container = template["spec"]["containers"][0]
        found[component] = {e["name"]: e.get("value") for e in container.get("env", [])}
    return found


def test_each_workload_proxies_https_to_its_own_gateway_port_only(everything):
    profiles = load_profiles().workloads
    env = _env(everything)
    for component, variables in env.items():
        profile = profiles.get(component)
        if profile is not None and profile.gateway_port:
            expected = f"http://egress-gateway:{profile.gateway_port}"
            assert variables.get("HTTPS_PROXY") == expected, component
            assert variables.get("https_proxy") == expected, component
            assert "egress-gateway" not in variables.get("NO_PROXY", ""), component
        else:
            assert "HTTPS_PROXY" not in variables, component
        assert "HTTP_PROXY" not in variables and "http_proxy" not in variables, component


def test_profiles_off_renders_no_gateway_and_no_proxy_env():
    docs = _docs()
    names = [d["metadata"]["name"] for d in docs]
    # Release-prefixed too: butlers-egress-gateway-ingress, butlers-egress-profiles, ...
    assert not [n for n in names if "egress-gateway" in n or "egress-profiles" in n]
    assert not [n for n in names if n.startswith("butlers-egress-")], names
    for component, variables in _env(docs).items():
        assert "HTTPS_PROXY" not in variables, component
    namespace = next(d for d in docs if d["metadata"]["name"] == "butlers-namespace-ingress")
    excluded = namespace["spec"]["podSelector"]["matchExpressions"][0]["values"]
    assert "egress-gateway" not in excluded
