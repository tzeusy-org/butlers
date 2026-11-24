"""The session-host seccomp profiles allow exactly what each run needs (bu-kiuh8v.9).

Two profiles, so a syscall only the proof needs never reaches the pod:

- ``session-host-sandbox.json`` (the pod: compose and Helm) is the dashboard sandbox
  profile (a pinned Moby default plus Bubblewrap's bootstrap) with one delta: the one
  namespace ``clone`` mask adds ``CLONE_NEWNET``, because the session host's Bubblewrap
  plan passes ``--unshare-net``. It allows no ``setns`` at all.
- ``session-host-proof.json`` (the exact-image proof only) is the pod profile plus
  exactly two ``setns`` rules, for ``CLONE_NEWUSER`` and ``CLONE_NEWNET``, which the
  proof's helper uses to listen inside the domain's network namespace.

No container runs here (the agent host has no Docker); the owner's exact-image run is
the behavioural check. These tests pin the structure.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from tests.scripts.test_session_host_env_guard import (
    _ENABLED,
    _render,
    _session_host_docs,
    needs_helm,
    pod_violations,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECCOMP = _REPO_ROOT / "deploy" / "seccomp"
_PROFILE = _SECCOMP / "session-host-sandbox.json"
_PROOF = _SECCOMP / "session-host-proof.json"
_DASHBOARD = _SECCOMP / "dashboard-runtime-cli-sandbox.json"
_BUBBLEWRAP = _REPO_ROOT / "src" / "butlers" / "session_host" / "bubblewrap.py"

SIGCHLD = 17
CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
CLONE_NEWCGROUP = 0x02000000
_UNSHARE_FLAGS = {
    "--unshare-user": CLONE_NEWUSER,
    "--unshare-pid": CLONE_NEWPID,
    "--unshare-ipc": CLONE_NEWIPC,
    "--unshare-uts": CLONE_NEWUTS,
    "--unshare-net": CLONE_NEWNET,
    "--unshare-cgroup": CLONE_NEWCGROUP,
}
SESSION_HOST_CLONE = (
    CLONE_NEWNS
    | CLONE_NEWUTS
    | CLONE_NEWIPC
    | CLONE_NEWUSER
    | CLONE_NEWPID
    | CLONE_NEWNET
    | SIGCHLD
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def profile() -> dict:
    return _load(_PROFILE)


@pytest.fixture(scope="module")
def proof() -> dict:
    return _load(_PROOF)


_PROOF_SETNS = [
    {
        "names": ["setns"],
        "action": "SCMP_ACT_ALLOW",
        "args": [{"index": 1, "value": nstype, "op": "SCMP_CMP_EQ"}],
    }
    for nstype in (CLONE_NEWUSER, CLONE_NEWNET)
]


def _unconditional(profile: dict, name: str) -> list[dict]:
    """Rules that allow ``name`` whatever the container's capabilities or architecture."""
    return [
        rule
        for rule in profile["syscalls"]
        if name in rule["names"]
        and rule["action"] == "SCMP_ACT_ALLOW"
        and "includes" not in rule
        and "excludes" not in rule
    ]


def test_the_pod_profile_is_the_dashboard_profile_with_only_the_clone_value_changed(
    profile,
) -> None:
    expected = copy.deepcopy(_load(_DASHBOARD))
    clone = expected["syscalls"][0]
    assert clone["names"] == ["clone"] and clone["args"][0]["value"] == SESSION_HOST_CLONE & ~(
        CLONE_NEWNET
    )
    clone["args"][0]["value"] = SESSION_HOST_CLONE
    assert profile == expected


def test_the_pod_profile_never_allows_setns_unconditionally(profile) -> None:
    # Moby's own CAP_SYS_ADMIN-gated rule stays; the pod never holds that capability.
    assert _unconditional(profile, "setns") == []


def test_the_proof_profile_is_the_pod_profile_plus_exactly_two_setns_rules(profile, proof) -> None:
    expected = copy.deepcopy(profile)
    expected["syscalls"][2:2] = copy.deepcopy(_PROOF_SETNS)
    assert proof == expected


@pytest.mark.parametrize("path", [_PROFILE, _PROOF])
def test_default_deny_is_kept(path) -> None:
    assert _load(path)["defaultAction"] == "SCMP_ACT_ERRNO"


def test_the_namespace_clone_mask_is_exact(profile) -> None:
    # One argument-filtered rule, compared for equality: no other namespace set passes.
    assert _unconditional(profile, "clone") == [
        {
            "names": ["clone"],
            "action": "SCMP_ACT_ALLOW",
            "args": [{"index": 0, "value": SESSION_HOST_CLONE, "op": "SCMP_CMP_EQ"}],
        }
    ]


def test_the_clone_mask_is_the_session_host_bubblewrap_plan() -> None:
    """Every --unshare-* the plan passes, plus the mount namespace and SIGCHLD bwrap adds."""
    source = _BUBBLEWRAP.read_text(encoding="utf-8")
    flags = set(re.findall(r'"(--unshare-[a-z]+)"', source))
    assert flags == {
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
    }
    mask = CLONE_NEWNS | SIGCHLD
    for flag in flags:
        mask |= _UNSHARE_FLAGS[flag]
    assert mask == SESSION_HOST_CLONE


def test_only_the_nested_user_namespace_may_be_unshared(profile) -> None:
    assert _unconditional(profile, "unshare") == [
        {
            "names": ["unshare"],
            "action": "SCMP_ACT_ALLOW",
            "args": [{"index": 0, "value": CLONE_NEWUSER, "op": "SCMP_CMP_EQ"}],
        }
    ]


def test_the_proof_setns_joins_only_a_user_or_network_namespace(proof) -> None:
    rules = _unconditional(proof, "setns")
    assert all(rule["names"] == ["setns"] for rule in rules)
    assert sorted(rule["args"][0]["value"] for rule in rules) == [CLONE_NEWUSER, CLONE_NEWNET]
    for rule in rules:
        (arg,) = rule["args"]
        assert (arg["index"], arg["op"]) == (1, "SCMP_CMP_EQ")


@pytest.mark.parametrize("path", [_PROFILE, _PROOF])
@pytest.mark.parametrize("name", ["clone3", "chroot", "setns", "unshare", "clone"])
def test_nothing_else_is_allowed_without_an_argument_filter(path, name) -> None:
    assert all(rule.get("args") for rule in _unconditional(_load(path), name))


def test_compose_runs_the_session_host_under_the_pod_profile() -> None:
    raw = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    service = compose["services"]["session-host"]
    assert service["security_opt"] == ["seccomp:./deploy/seccomp/session-host-sandbox.json"]
    # The dashboard keeps its own profile; neither service borrows the other's.
    for name, other in compose["services"].items():
        if name != "session-host":
            assert "session-host-sandbox.json" not in json.dumps(other)
    # The proof-only profile is never deployed.
    assert "session-host-proof.json" not in raw


def test_no_deployment_names_the_proof_profile() -> None:
    deploy = _REPO_ROOT / "deploy" / "helm"
    for path in deploy.rglob("*"):
        if path.is_file():
            assert "session-host-proof.json" not in path.read_text(encoding="utf-8"), path


def _container(*settings: str) -> dict:
    docs = _session_host_docs(_render(*_ENABLED, *settings))
    (deployment,) = [d for d in docs if d["kind"] == "Deployment"]
    assert pod_violations(deployment) == []
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    return container


@needs_helm
def test_helm_applies_the_profile_only_when_the_node_has_it_installed() -> None:
    """Localhost profiles live under the kubelet's seccomp root; a missing file stops the pod.

    So the chart sets it only when sessionHost.seccompLocalhostProfile names the installed
    path, and leaves the pod as it was otherwise.
    """
    values = yaml.safe_load((_REPO_ROOT / "deploy/helm/butlers/values.yaml").read_text())
    assert values["sessionHost"]["seccompLocalhostProfile"] == ""
    assert "securityContext" not in _container()
    installed = "butlers/session-host-sandbox.json"
    assert _container(f"sessionHost.seccompLocalhostProfile={installed}")["securityContext"] == {
        "seccompProfile": {"type": "Localhost", "localhostProfile": installed}
    }


def test_clone3_still_falls_back_to_clone(profile) -> None:
    # Without CAP_SYS_ADMIN clone3 answers ENOSYS, so libc retries with the filtered clone.
    assert {
        "names": ["clone3"],
        "action": "SCMP_ACT_ERRNO",
        "errnoRet": 38,
        "excludes": {"caps": ["CAP_SYS_ADMIN"]},
    } in profile["syscalls"]
