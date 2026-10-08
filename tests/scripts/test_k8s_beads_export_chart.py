"""Helm render and script guard for the beads tracker bridge (bu-viat6h.1, bu-ckkpz.3)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART = _REPO_ROOT / "deploy" / "helm" / "butlers"
_SCRIPT = _CHART / "files" / "beads_export.sh"
_CYCLE = _CHART / "files" / "beads_cycle.sh"
# What scripts/k8s/site-helm-args.sh supplies on a real deploy.
_SITE = [
    "--set", "beadsExport.imageRepository=registry.invalid/butlers-beads",
    "--set", "beadsExport.doltHost=dolt.invalid",
    "--set", "beadsExport.doltEgressCidrs={192.0.2.1/32,192.0.2.2/32}",
]  # fmt: skip
_ENABLE = [
    "--set", "beadsExport.enabled=true",
    *_SITE,
    "--set", "beadsExport.credentialSecretName=beads-export-dolt",
]  # fmt: skip

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
def test_disabled_renders_no_beads_export_and_no_bead_mounts(env: str) -> None:
    docs = _render(env, "--set", "beadsExport.enabled=false", *_SITE)
    names = _by_kind_name(docs)
    assert ("CronJob", "butlers-beads-export") not in names
    assert ("PersistentVolumeClaim", "butlers-beads-export") not in names
    for deploy in ("dashboard-api", "butlers-up"):
        spec = _pod_spec(names[("Deployment", deploy)])
        assert all(v["name"] != "beads-export" for v in spec["volumes"])
        assert all(
            m["mountPath"] != "/app/.beads"
            for c in spec["containers"]
            for m in c.get("volumeMounts", [])
        )
    assert ("ExternalSecret", "butlers-beads-dolt") not in names
    # Rolling the bridge back keeps the tracker closed on dev; prod never opts in.
    assert (("NetworkPolicy", "butlers-tracker-egress") in names) == (env == "dev")
    if env == "prod":
        # Prod's default is off: the explicit flag renders the same as omitting it.
        assert _render(env, *_SITE) == docs


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_enabled_mounts_pvc_read_only_as_directory_for_runtime_pods(env: str) -> None:
    names = _by_kind_name(_render(env, *_ENABLE))
    assert ("CronJob", "butlers-beads-export") in names
    assert ("PersistentVolumeClaim", "butlers-beads-export") in names
    for deploy in ("dashboard-api", "butlers-up"):
        spec = _pod_spec(names[("Deployment", deploy)])
        mounts = [
            m for c in spec["containers"] for m in c.get("volumeMounts", [])
            if m["mountPath"] == "/app/.beads"
        ]  # fmt: skip
        assert mounts == [{"name": "beads-export", "mountPath": "/app/.beads", "readOnly": True}]
        (volume,) = [v for v in spec["volumes"] if v["name"] == "beads-export"]
        assert volume["persistentVolumeClaim"]["claimName"] == "butlers-beads-export"


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_enabled_keeps_dolt_credential_and_host_paths_out_of_runtime_pods(env: str) -> None:
    docs = _render(env, *_ENABLE)
    for doc in docs:
        if doc["kind"] not in ("Deployment", "CronJob"):
            continue
        is_exporter = doc["metadata"]["name"] == "butlers-beads-export"
        spec = _pod_spec(doc)
        text = yaml.safe_dump(spec)
        assert (
            "beads-export-dolt" in text or "BEADS_DOLT" in text or "DOLT" in text
        ) == is_exporter, doc["metadata"]["name"]
        assert ".beads-credential-key" not in text
        assert all("hostPath" not in v for v in spec.get("volumes", []))


@needs_helm
@pytest.mark.parametrize("missing", [1, 2])
def test_enabled_requires_every_site_value(missing: int) -> None:
    site = [
        arg for i, pair in enumerate(zip(_SITE[::2], _SITE[1::2])) if i != missing for arg in pair
    ]
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _render("dev", *site)
    assert "beadsExport." in exc.value.stderr


def _bridge_image(*args: str) -> str:
    names = _by_kind_name(_render("dev", "--set", "image.tag=abc123", *args))
    return _pod_spec(names[("CronJob", "butlers-beads-export")])["containers"][0]["image"]


@needs_helm
def test_bridge_image_derives_from_the_app_image_registry() -> None:
    # A `--reset-then-reuse-values` upgrade carries image.repository but no bridge value.
    site = _SITE[2:]
    assert _bridge_image("--set", "image.repository=reg.example/butlers-app", *site) == (
        "reg.example/butlers-beads:abc123"
    )
    assert _bridge_image(*_SITE) == "registry.invalid/butlers-beads:abc123"


@needs_helm
def test_bridge_image_is_required_when_the_app_image_is_not_derivable() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _bridge_image("--set", "image.repository=reg.example/custom", *_SITE[2:])
    assert "beadsExport.imageRepository" in exc.value.stderr


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
def test_dev_enables_the_bridge_with_its_own_credential_secret() -> None:
    names = _by_kind_name(_render("dev", *_SITE))
    cron = names[("CronJob", "butlers-beads-export")]
    assert cron["spec"]["schedule"] == "*/5 * * * *"
    (container,) = _pod_spec(cron)["containers"]
    assert container["image"] == "registry.invalid/butlers-beads:t"
    env = {e["name"]: e for e in container["env"]}
    assert env["APPLY_DECISIONS"]["value"] == "1"
    assert env["BD_ACTOR"]["value"] == "butlers-decision-desk"
    assert env["BEADS_DOLT_SERVER_HOST"]["value"] == "dolt.invalid"
    for key in ("BEADS_DOLT_SERVER_USER", "BEADS_DOLT_PASSWORD"):
        assert env[key]["valueFrom"]["secretKeyRef"]["name"] == "butlers-beads-dolt"
    # The applier reaches the intent store with the runtime's existing credential.
    assert env["POSTGRES_USER"]["valueFrom"]["secretKeyRef"]["name"] == "butlers-bws"

    secret = names[("ExternalSecret", "butlers-beads-dolt")]
    assert secret["spec"]["target"]["name"] == "butlers-beads-dolt"
    assert {d["secretKey"]: d["remoteRef"]["key"] for d in secret["spec"]["data"]} == {
        "BEADS_DOLT_SERVER_USER": "BUTLERS_RUNTIME_BEADS_DOLT_USER",
        "BEADS_DOLT_PASSWORD": "BUTLERS_RUNTIME_BEADS_DOLT_PASSWORD",
    }
    bws = names[("ExternalSecret", "butlers-bws")]
    assert "DOLT" not in yaml.safe_dump(bws)

    # Routing stays off on dev until the owner consents to live prompts.
    up_env = {
        e["name"]: e.get("value")
        for c in _pod_spec(names[("Deployment", "butlers-up")])["containers"]
        for e in c.get("env", [])
    }
    assert up_env["BUTLERS_DECISION_ROUTING_ENABLED"] == "0"


@needs_helm
def test_tracker_egress_policy_covers_every_pod_but_the_bridge() -> None:
    names = _by_kind_name(_render("dev", *_SITE))
    policy = names[("NetworkPolicy", "butlers-tracker-egress")]["spec"]
    assert policy["podSelector"] == {
        "matchExpressions": [
            {
                "key": "app.kubernetes.io/component",
                "operator": "NotIn",
                "values": ["beads-export"],
            }
        ]
    }
    assert policy["policyTypes"] == ["Egress"]
    assert policy["egress"] == [
        {"to": [{"namespaceSelector": {}}]},
        {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["192.0.2.1/32", "192.0.2.2/32"]}}]},
    ]
    template = names[("CronJob", "butlers-beads-export")]["spec"]["jobTemplate"]["spec"]
    assert template["template"]["metadata"]["labels"]["app.kubernetes.io/component"] == (
        "beads-export"
    )
    for kind, name in names:
        if kind == "Deployment":
            labels = names[(kind, name)]["spec"]["template"]["metadata"]["labels"]
            assert labels.get("app.kubernetes.io/component") != "beads-export", name


def _run_exporter(tmp_path: Path, stub_body: str) -> subprocess.CompletedProcess[str]:
    export_dir = tmp_path / "export"
    export_dir.mkdir(exist_ok=True)
    stub = tmp_path / "bd"
    stub.write_text("#!/bin/sh\n" + stub_body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        ["sh", str(_SCRIPT)],
        env={**os.environ, "EXPORT_DIR": str(export_dir), "BD_BIN": str(stub)},
        capture_output=True, text=True,
    )  # fmt: skip


def test_exporter_writes_final_file_via_rename(tmp_path: Path) -> None:
    # The stub asserts the output is a temp name, never the final one.
    body = 'case "$3" in *issues.export.jsonl) exit 9;; esac\nprintf \'{"id":"a"}\\n\' > "$3"\n'
    result = _run_exporter(tmp_path, body)
    assert result.returncode == 0, result.stderr
    export_dir = tmp_path / "export"
    assert (export_dir / "issues.export.jsonl").read_text() == '{"id":"a"}\n'
    assert [p.name for p in export_dir.iterdir()] == ["issues.export.jsonl"]


@pytest.mark.parametrize(
    "stub_body",
    ['printf partial > "$3"\nexit 3\n', "exit 0\n", ': > "$3"\n'],
    ids=["nonzero-after-partial-write", "no-output", "empty-output"],
)
def test_exporter_failure_keeps_previous_file_byte_identical(
    tmp_path: Path, stub_body: str
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    final = export_dir / "issues.export.jsonl"
    final.write_bytes(b'{"id":"old"}\n')
    result = _run_exporter(tmp_path, stub_body)
    assert result.returncode != 0
    assert final.read_bytes() == b'{"id":"old"}\n'
    assert [p.name for p in export_dir.iterdir()] == ["issues.export.jsonl"]


def _run_cycle(tmp_path: Path, *, applier_rc: int, export_rc: int, **env: str):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "beads_decision_applier.py").write_text(
        f"import os, sys\nopen(os.environ['TRACE'], 'a').write('apply\\n')\nsys.exit({applier_rc})\n"
    )
    (scripts / "beads_export.sh").write_text(
        f'cat "$BEADS_DIR/metadata.json" >> "$TRACE"\necho export >> "$TRACE"\nexit {export_rc}\n'
    )
    trace = tmp_path / "trace"
    result = subprocess.run(
        ["sh", str(_CYCLE)],
        env={**os.environ, "BEADS_SCRIPT_DIR": str(scripts), "BEADS_WORKSPACE": str(tmp_path / "ws"),
             "TRACE": str(trace), "PYTHON": sys.executable, **env},
        capture_output=True, text=True,
    )  # fmt: skip
    return result, trace.read_text().splitlines()


def test_cycle_applies_then_exports_from_a_scratch_workspace(tmp_path: Path) -> None:
    result, trace = _run_cycle(tmp_path, applier_rc=0, export_rc=0, BEADS_DOLT_DATABASE="beads")
    assert result.returncode == 0, result.stderr
    assert trace[0] == "apply"
    assert json.loads(trace[1]) == {
        "database": "dolt",
        "backend": "dolt",
        "dolt_mode": "server",
        "dolt_database": "beads",
    }
    assert trace[2] == "export"


@pytest.mark.parametrize(("applier_rc", "export_rc"), [(1, 0), (0, 1), (1, 1)])
def test_cycle_always_exports_and_fails_if_either_step_failed(
    tmp_path: Path, applier_rc: int, export_rc: int
) -> None:
    result, trace = _run_cycle(tmp_path, applier_rc=applier_rc, export_rc=export_rc)
    assert result.returncode == 1
    assert trace[0] == "apply" and trace[-1] == "export"


def test_cycle_can_skip_the_applier(tmp_path: Path) -> None:
    result, trace = _run_cycle(tmp_path, applier_rc=1, export_rc=0, APPLY_DECISIONS="0")
    assert result.returncode == 0
    assert "apply" not in trace
