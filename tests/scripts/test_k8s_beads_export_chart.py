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
_SITE = ["--set", "trackerEgress.deniedCidrs={192.0.2.1/32,192.0.2.2/32}"]

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
    docs = _render(env, "--set", "beadsExport.enabled=true", *_SITE)
    names = _by_kind_name(docs)
    assert ("CronJob", "butlers-beads-export") not in names
    assert ("PersistentVolumeClaim", "butlers-beads-export") not in names
    for deploy in ("dashboard-api", "butlers-up", *(["butlers-control"] if env == "dev" else [])):
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
    # Existing environment policy remains: dev denied, production setting unchanged.
    assert (("NetworkPolicy", "butlers-tracker-egress") in names) == (env == "dev")
    if env == "prod":
        # Prod's default is off: the explicit flag renders the same as omitting it.
        assert _render(env, *_SITE) == docs


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_butlers_up_runs_the_owner_set_fleet_session_cap(env: str) -> None:
    # The owner total stays five: staged dev partitions it across exactly one
    # control and one domain process; unsplit production retains its old cap.
    names = _by_kind_name(_render(env, *_SITE))
    domains = names[("Deployment", "butlers-up")]
    container_env = {e["name"]: e.get("value") for e in _pod_spec(domains)["containers"][0]["env"]}
    if env == "dev":
        control = names[("Deployment", "butlers-control")]
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
    else:
        assert ("Deployment", "butlers-control") not in names
        assert container_env["BUTLERS_MAX_GLOBAL_SESSIONS"] == "5"


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
    names = _by_kind_name(_render("dev", *_SITE))
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
