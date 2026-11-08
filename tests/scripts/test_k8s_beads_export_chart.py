"""Retired bridge stays absent; tracker isolation and fleet capacity remain enforced."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART = _REPO_ROOT / "deploy" / "helm" / "butlers"
# Site CIDRs are documentation addresses, never real host or credential values.
_SITE = [
    "--set",
    "trackerEgress.deniedCidrs={192.0.2.1/32,192.0.2.2/32}",
    "--set",
    "egressPolicy.allowedCidrs={198.51.100.7/32}",
]

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "-f", str(_CHART / f"values.{env}.yaml"), "--set", "image.tag=t",
         "--set", "frontendImage.tag=t", *extra],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _by_kind_name(docs: list[dict]) -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d for d in docs}


def _pod_spec(doc: dict) -> dict:
    if doc["kind"] == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return doc["spec"]["template"]["spec"]


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_retired_bridge_cannot_render_or_mount_even_with_legacy_values(env: str) -> None:
    modes = [(), ("--set", "butlersControl.enabled=true")] if env == "dev" else [()]
    for extra in modes:
        docs = _render(env, "--set", "beadsExport.enabled=true", *_SITE, *extra)
        names = _by_kind_name(docs)
        split = bool(extra)
        assert (("Deployment", "butlers-control") in names) == split
        assert ("CronJob", "butlers-beads-export") not in names
        assert ("PersistentVolumeClaim", "butlers-beads-export") not in names
        for deploy in ("dashboard-api", "butlers-up", *(["butlers-control"] if split else [])):
            spec = _pod_spec(names[("Deployment", deploy)])
            assert all(v["name"] != "beads-export" for v in spec["volumes"])
            assert all(
                m["mountPath"] != "/app/.beads"
                for c in spec["containers"]
                for m in c.get("volumeMounts", [])
            )
        assert ("ExternalSecret", "butlers-beads-dolt") not in names
        for doc in docs:
            if doc["kind"] in {"Deployment", "CronJob"}:
                spec = _pod_spec(doc)
                text = yaml.safe_dump(spec)
                assert "DOLT" not in text
                assert "beads-dolt" not in text
                assert ".beads-credential-key" not in text
                assert all("hostPath" not in v for v in spec.get("volumes", []))
        # Existing environment policy remains: dev denied, production unchanged.
        assert (("NetworkPolicy", "butlers-tracker-egress") in names) == (env == "dev")
        if env == "prod":
            # Prod's default is off: the explicit flag equals omitting it.
            assert _render(env, *_SITE) == docs


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_butlers_up_runs_the_owner_set_fleet_session_cap(env: str) -> None:
    # Both defaults cohost with five slots. Explicit split dev still partitions
    # that total across exactly one control and one domain process.
    names = _by_kind_name(_render(env, *_SITE))
    domains = names[("Deployment", "butlers-up")]
    container_env = {e["name"]: e.get("value") for e in _pod_spec(domains)["containers"][0]["env"]}
    assert ("Deployment", "butlers-control") not in names
    assert container_env["BUTLERS_MAX_GLOBAL_SESSIONS"] == "5"
    if env == "dev":
        split = _by_kind_name(_render(env, *_SITE, "--set", "butlersControl.enabled=true"))
        domains = split[("Deployment", "butlers-up")]
        control = split[("Deployment", "butlers-control")]
        container_env = {
            e["name"]: e.get("value") for e in _pod_spec(domains)["containers"][0]["env"]
        }
        control_env = {
            e["name"]: e.get("value") for e in _pod_spec(control)["containers"][0]["env"]
        }
        capacities = [
            int(container_env["BUTLERS_MAX_GLOBAL_SESSIONS"]),
            int(control_env["BUTLERS_MAX_GLOBAL_SESSIONS"]),
        ]
        assert capacities == [4, 1]
        assert all(capacity > 0 for capacity in capacities)
        assert sum(capacities) == 5
        for deployment in (domains, control):
            assert deployment["spec"]["replicas"] == 1
            assert deployment["spec"]["strategy"]["type"] == "Recreate"


@needs_helm
def test_dev_ships_review_jobs_on_and_one_tap_routing_off() -> None:
    # Owner decision 2026-10-08 (bu-ckkpz): digest + P1 escalation on in dev;
    # one-tap prompts stay off until bu-6es2sp.
    names = _by_kind_name(_render("dev", *_SITE))
    env = {
        e["name"]: e.get("value")
        for e in _pod_spec(names[("Deployment", "butlers-up")])["containers"][0]["env"]
    }
    assert env["BUTLERS_DECISION_REVIEW_ENABLED"] == "1"
    assert env["BUTLERS_DECISION_ROUTING_ENABLED"] == "0"


@needs_helm
def test_tracker_egress_policy_covers_every_pod_without_a_bridge_exception() -> None:
    names = _by_kind_name(_render("dev", *_SITE, "--set", "egressPolicy.enabled=false"))
    policy = names[("NetworkPolicy", "butlers-tracker-egress")]["spec"]
    assert policy["podSelector"] == {}
    assert policy["policyTypes"] == ["Egress"]
    assert policy["egress"] == [
        {"to": [{"namespaceSelector": {}}]},
        {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["192.0.2.1/32", "192.0.2.2/32"]}}]},
    ]


@needs_helm
def test_tracker_isolation_refuses_missing_site_cidrs() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _render("dev")
    assert "trackerEgress.deniedCidrs is required" in exc.value.stderr


_PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"]
_ALLOW = [
    "--set",
    "egressPolicy.enabled=true",
    "--set",
    "egressPolicy.allowedCidrs={198.51.100.7/32}",
]


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_egress_policy_excludes_private_ranges_and_admits_the_allowlist(env: str) -> None:
    docs = _render(env, *_SITE, *_ALLOW)
    policies = [
        d for d in docs if d["kind"] == "NetworkPolicy" and "Egress" in d["spec"]["policyTypes"]
    ]
    assert [p["metadata"]["name"] for p in policies] == ["butlers-tracker-egress"]
    assert policies[0]["spec"]["podSelector"] == {}
    egress = policies[0]["spec"]["egress"]
    assert {"to": [{"namespaceSelector": {}}]} in egress
    assert {"to": [{"ipBlock": {"cidr": "198.51.100.7/32"}}]} in egress
    dns = [r for r in egress if r.get("ports")]
    assert [p["port"] for p in dns[0]["ports"]] == [53, 53]
    public = [r for r in egress if r["to"][0].get("ipBlock", {}).get("cidr") == "0.0.0.0/0"]
    assert public[0]["to"][0]["ipBlock"]["except"] == ["192.0.2.1/32", "192.0.2.2/32", *_PRIVATE]


@needs_helm
def test_egress_policy_refuses_an_empty_allowlist_unless_opted_out() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _render("dev", *_SITE, "--set", "egressPolicy.allowedCidrs=null")
    assert "egressPolicy.allowedCidrs is required" in exc.value.stderr
    docs = _render(
        "dev", *_SITE, "--set", "egressPolicy.allowedCidrs=null",
        "--set", "egressPolicy.allowNoAllowlist=true",
    )  # fmt: skip
    assert ("NetworkPolicy", "butlers-tracker-egress") in _by_kind_name(docs)


@needs_helm
def test_egress_policy_renders_without_the_dolt_deny_in_prod() -> None:
    names = _by_kind_name(_render("prod", *_ALLOW))
    public = names[("NetworkPolicy", "butlers-tracker-egress")]["spec"]["egress"][-1]
    assert public["to"][0]["ipBlock"]["except"] == _PRIVATE
