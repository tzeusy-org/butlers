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
_GATEWAY = _component("egress-gateway")


def _render(*settings: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "butlers", str(_CHART), "--namespace", _NS]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    site = (f"trackerEgress.deniedCidrs={{{_TRACKER}/32}}", "database.host=postgres.invalid")
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
    if set(selector) - {"matchLabels"}:
        raise ValueError(f"selector shape this evaluator does not read: {selector}")
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


def test_profiles_off_renders_no_profile_policy_and_keeps_the_tracker_policy():
    names = {d["metadata"]["name"] for d in _egress_policies(_docs())}
    assert names == {"butlers-tracker-egress"}


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
    forbidden = (_TRACKER, "169.254.169.254", "10.0.0.5", "100.100.100.100", "203.0.113.5")
    for name, labels in _pods(everything).items():
        for ip in forbidden:
            for port in (22, 53, 80, 443, 3307, 5432):
                assert _admitting(everything, labels, Dest(ip=ip), port) == [], (name, ip, port)


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
