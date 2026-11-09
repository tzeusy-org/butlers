"""scripts/k8s/bootstrap-db.sh: rerun init-db.sql before a k8s rollout (bu-8jtq10)."""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALL_LOG"
{extra}
"""
_SQL = "SELECT 'bootstrap fixture';"
# kubectl exec receives the SQL on stdin; the stub records it and its env/psql args.
_KUBECTL = """case "$*" in
  *"get pod"*) printf '%s\\n' $PRIMARIES ;;
  *"get secret"*) printf 'fixture-login' | base64 ;;
  *exec*) cat > "$STDIN_LOG" ;;
esac"""


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts" / "k8s").mkdir(parents=True)
    chart = tmp_path / "deploy" / "helm" / "butlers"
    chart.mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "scripts/k8s/bootstrap-db.sh", tmp_path / "scripts/k8s")
    for name in (
        "runtime-topology-preflight.sh",
        "runtime_topology_preflight.py",
        "record-deploy.sh",
    ):
        shutil.copy2(_REPO_ROOT / "scripts/k8s" / name, tmp_path / "scripts/k8s" / name)
    shutil.copy2(_REPO_ROOT / "deploy/helm/butlers/makefile", chart / "makefile")
    helm_args = tmp_path / "scripts" / "k8s" / "site-helm-args.sh"
    helm_args.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    helm_args.chmod(0o755)
    # The public MCP owner step follows the release; its own tests cover it.
    owner_step = tmp_path / "scripts" / "k8s" / "public-mcp-owner-step.sh"
    owner_step.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    owner_step.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        "kubectl": _KUBECTL,
        "git": f'case "$*" in *show*) echo "{_SQL}" ;; *rev-parse*) echo abc123def456 ;; esac',
        "helm": "",
        "uv": f'while [ "$1" != python ]; do shift; done; shift; exec "{sys.executable}" "$@"',
    }
    for name, extra in stubs.items():
        stub = bin_dir / name
        stub.write_text(_STUB.format(extra=extra), encoding="utf-8")
        stub.chmod(0o755)
    return tmp_path


def _run(repo: Path, *args: str, primaries: str = "pod/postgres-dev-1") -> tuple[int, str, str]:
    log = repo / "calls.log"
    log.touch()
    env = {
        "PATH": f"{repo / 'bin'}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "STDIN_LOG": str(repo / "stdin.log"),
        "PRIMARIES": primaries,
    }
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "k8s" / "bootstrap-db.sh"), *args],
        capture_output=True, text=True, env=env, cwd=repo,
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text(encoding="utf-8")


def test_dev_pipes_the_tags_bootstrap_into_the_primary_as_superuser(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, "dev", "abc123def456")
    assert code == 0, stderr
    assert "git -C" in calls and "show abc123def456:scripts/init-db.sql" in calls
    assert "-l cnpg.io/cluster=postgres-dev,cnpg.io/instanceRole=primary" in calls
    exec_call = next(line for line in calls.splitlines() if " exec " in line)
    assert exec_call.startswith("kubectl -n postgres-dev exec -i pod/postgres-dev-1 -c postgres")
    assert "PGOPTIONS=-c butlers.connecting_user=fixture-login" in exec_call
    assert "psql -X -q -v ON_ERROR_STOP=1 -d butlers -f -" in exec_call
    assert (fake_repo / "stdin.log").read_text(encoding="utf-8").strip() == _SQL


def test_ambiguous_primary_refuses_before_running_sql(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, "dev", primaries="pod/a pod/b")
    assert code == 1
    assert "expected one primary" in stderr
    assert " exec " not in calls


def test_unconfigured_environment_is_a_reminder_not_a_connection(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, "prod")
    assert code == 0
    assert "manually" in stderr
    assert "kubectl" not in calls


def test_deploy_target_bootstraps_the_deployed_tag_before_helm(fake_repo: Path) -> None:
    chart = fake_repo / "deploy" / "helm" / "butlers"
    result = subprocess.run(
        ["make", "-n", "deploy-dev", "TAG=abc123def456"],
        capture_output=True, text=True, cwd=chart,
        env={**os.environ, "PATH": f"{fake_repo / 'bin'}:{os.environ['PATH']}"},
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith('../../../scripts/k8s/runtime-topology-preflight.sh "butlers-dev"')
    bootstrap = lines.index("../../../scripts/k8s/bootstrap-db.sh dev abc123def456")
    release = next(
        index
        for index, line in enumerate(lines)
        if line.startswith(
            '../../../scripts/k8s/record-deploy.sh "butlers-dev" "abc123def456" "butlers" -- '
        )
    )
    assert 0 < bootstrap < release
    assert "--set image.tag=abc123def456 --set frontendImage.tag=abc123def456-dev" in lines[release]

    # Exercise the actual shared admission through make and record-deploy,
    # using real Helm renders and controlled read-only API responses.
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm unavailable; make ordering completed")
    models = {}
    for mode in ("cohost", "split"):
        rendered = subprocess.run(
            [
                helm,
                "template",
                "butlers",
                str(_REPO_ROOT / "deploy/helm/butlers"),
                "-f",
                str(_REPO_ROOT / "deploy/helm/butlers/values.dev.yaml"),
                "--set",
                "trackerEgress.enabled=false",
                "--set",
                "egressPolicy.enabled=false",
                "--set",
                "database.host=db.invalid",
                "--set",
                "image.tag=fixture",
                "--set",
                "frontendImage.tag=fixture",
                "--set",
                "butlersControl.enabled=" + ("true" if mode == "split" else "false"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        import yaml

        models[mode] = {
            doc["metadata"]["name"]: doc
            for doc in yaml.safe_load_all(rendered.stdout)
            if doc and doc["kind"] == "Deployment"
        }
        (fake_repo / (mode + ".yaml")).write_text(rendered.stdout)

    def deployment(mode, role="butlers-up", *, replicas=1):
        value = copy.deepcopy(models[mode][role])
        value["metadata"]["generation"] = 2
        value["spec"]["replicas"] = replicas
        value["status"] = {
            "observedGeneration": 2,
            "replicas": replicas,
            "readyReplicas": replicas,
            "updatedReplicas": replicas,
            "availableReplicas": replicas,
            "terminatingReplicas": 0,
        }
        return value

    def pod(carrier, *, terminating=False):
        value = copy.deepcopy(carrier["spec"]["template"])
        value.update(kind="Pod", status={"phase": "Running"})
        value["metadata"]["name"] = carrier["metadata"]["name"] + "-old-rs-fixture"
        if terminating:
            value["metadata"]["deletionTimestamp"] = "2026-11-04T00:00:00Z"
        return value

    up = deployment("cohost")
    domains = deployment("split")
    control = deployment("split", "butlers-control")
    zero_up = deployment("cohost", replicas=0)
    zero_domains = deployment("split", replicas=0)
    zero_control = deployment("split", "butlers-control", replicas=0)
    stale = copy.deepcopy(zero_domains)
    stale["status"]["observedGeneration"] = 1
    malformed = copy.deepcopy(zero_domains)
    malformed["status"]["replicas"] = False
    missing = copy.deepcopy(zero_domains)
    del missing["status"]["observedGeneration"]
    unknown = copy.deepcopy(zero_up)
    unknown["spec"]["template"]["spec"]["containers"][0]["command"] = ["unknown-runtime"]
    absent_counters = copy.deepcopy(zero_domains)
    absent_counters["status"] = {"observedGeneration": 2}
    same_stale = copy.deepcopy(up)
    same_stale["status"]["observedGeneration"] = 1
    same_missing = copy.deepcopy(up)
    del same_missing["status"]["readyReplicas"]
    draining = copy.deepcopy(zero_domains)
    draining["status"]["replicas"] = 1
    terminating_count = copy.deepcopy(zero_domains)
    terminating_count["status"]["terminatingReplicas"] = 1
    unclassified = pod(up)
    unclassified["metadata"]["labels"]["app.kubernetes.io/component"] = "unknown"
    mixed = copy.deepcopy(up)
    mixed["spec"]["template"]["spec"]["containers"].append(
        copy.deepcopy(control["spec"]["template"]["spec"]["containers"][0])
    )
    mixed_pod = pod(mixed)
    cases = [
        ("fresh", "cohost", None, None, [], True),
        ("same-cohost", "cohost", up, None, [pod(up)], True),
        ("same-topology-stale", "cohost", same_stale, None, [pod(up)], False),
        ("same-topology-missing-counter", "cohost", same_missing, None, [pod(up)], False),
        ("leftover-stopped-control", "cohost", up, zero_control, [pod(up)], True),
        ("same-split", "split", domains, control, [pod(domains), pod(control)], True),
        ("split-to-cohost-stopped", "cohost", zero_domains, zero_control, [], True),
        ("cohost-to-split-stopped", "split", zero_up, None, [], True),
        ("observed-zero-omitted-counters", "cohost", absent_counters, zero_control, [], True),
        ("split-still-active", "cohost", domains, control, [pod(domains), pod(control)], False),
        ("cohost-still-active", "split", up, None, [pod(up)], False),
        ("old-control-pod", "cohost", up, zero_control, [pod(up), pod(control)], False),
        ("overlapping-same-mode-pods", "cohost", up, None, [pod(up), pod(up)], False),
        ("unclassified-runtime-pod", "cohost", up, None, [unclassified], False),
        ("mixed-runtime-carrier", "cohost", mixed, None, [pod(up)], False),
        ("mixed-runtime-pod", "cohost", up, None, [mixed_pod], False),
        ("observed-replicas-draining", "cohost", draining, zero_control, [pod(domains)], False),
        ("terminating-replicas-empty-pods", "cohost", terminating_count, zero_control, [], False),
        ("typed-pod-list", "cohost", up, None, [pod(up)], True),
        ("invalid-pod-kind", "cohost", up, None, [dict(pod(up), kind="Node")], False),
        (
            "terminating-old-pod",
            "cohost",
            zero_domains,
            zero_control,
            [pod(control, terminating=True)],
            False,
        ),
        ("old-replicaset-mode", "cohost", up, None, [pod(domains)], False),
        ("stale-generation", "cohost", stale, zero_control, [], False),
        ("malformed-counter", "cohost", malformed, zero_control, [], False),
        ("missing-observation", "cohost", missing, zero_control, [], False),
        ("unknown-command", "cohost", unknown, None, [], False),
        ("orphan-old-pod", "cohost", None, None, [pod(domains)], False),
        ("deployment-query-error", "cohost", zero_domains, zero_control, [], False),
        ("control-query-error", "cohost", zero_domains, zero_control, [], False),
        ("pods-query-error", "cohost", zero_domains, zero_control, [], False),
        ("render-error", "cohost", zero_domains, zero_control, [], False),
        ("malformed-deployment-json", "cohost", zero_domains, zero_control, [], False),
        ("empty-pod-json", "cohost", zero_domains, zero_control, [], False),
    ]
    state = fake_repo / "state"
    state.mkdir()
    (fake_repo / "scripts/k8s/bootstrap-db.sh").write_text(
        '#!/bin/bash\necho BOOTSTRAP >> "$CALL_LOG"\n'
        'if [ -n "${AFTER_BOOTSTRAP:-}" ]; then cp "$AFTER_BOOTSTRAP/"* "$STATE_DIR/"; fi\n'
    )
    tools = {
        "helm": 'if [ "$1" = template ]; then '
        '[ "$CASE" != render-error ] || { echo private-error-sentinel >&2; exit 1; }; '
        'if [[ "$CASE" = target-* ]]; then cd "$REAL_CHART"; exec "$REAL_HELM" "$@"; fi; '
        'cat "$DESIRED_RENDER"; '
        'else echo HELM_UPGRADE >> "$CALL_LOG"; fi',
        "kubectl": """case "$*" in
*"get deployment butlers-up --ignore"*)
  [ "$CASE" != deployment-query-error ] || { echo private-error-sentinel >&2; exit 1; }
  cat "$STATE_DIR/up" ;;
*"get deployment butlers-control --ignore"*)
  [ "$CASE" != control-query-error ] || exit 1
  cat "$STATE_DIR/control" ;;
*"get pods "*)
  [ "$CASE" != pods-query-error ] || exit 1
  cat "$STATE_DIR/pods" ;;
*) exit 0 ;;
esac""",
    }
    for name, extra in tools.items():
        (fake_repo / "bin" / name).write_text(_STUB.format(extra=extra))
    log = fake_repo / "calls.log"
    for case, target, current_up, current_control, pods, allowed in cases:
        log.write_text("")
        (state / "up").write_text(json.dumps(current_up) if current_up is not None else "")
        (state / "control").write_text(
            json.dumps(current_control) if current_control is not None else ""
        )
        list_kind = "PodList" if case == "typed-pod-list" else "List"
        (state / "pods").write_text(json.dumps({"kind": list_kind, "items": pods}))
        if case == "malformed-deployment-json":
            (state / "up").write_text("private-error-sentinel")
        if case == "empty-pod-json":
            (state / "pods").write_text("{}")
        environment = {
            **os.environ,
            "PATH": f"{fake_repo / 'bin'}:{os.environ['PATH']}",
            "CALL_LOG": str(log),
            "STATE_DIR": str(state),
            "CASE": case,
            "DESIRED_RENDER": str(fake_repo / (target + ".yaml")),
        }
        run = subprocess.run(
            ["make", "deploy-dev", "TAG=fixture"],
            cwd=chart,
            env=environment,
            capture_output=True,
            text=True,
        )
        calls = log.read_text()
        assert (run.returncode == 0) is allowed, case + run.stderr
        assert ("BOOTSTRAP" in calls) is allowed, case
        assert ("HELM_UPGRADE" in calls) is allowed, case
        assert "private-error-sentinel" not in run.stdout + run.stderr
        assert " scale " not in calls and " delete " not in calls

    # These are actual Helm parser forms, using a synthetic inherited context.
    # Helm-only cluster overrides must never diverge from kubectl's target.
    kubeconfig = fake_repo / "kubeconfig-fixture"
    kubeconfig.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [{"name": "fixture", "cluster": {"server": "https://192.0.2.1"}}],
                "contexts": [
                    {"name": "fixture", "context": {"cluster": "fixture", "user": "fixture"}}
                ],
                "current-context": "fixture",
                "users": [{"name": "fixture", "user": {}}],
            }
        )
    )
    kubeconfig.chmod(0o600)
    target_cases = [
        ("short-attached-same", ["-nbutlers-dev"], {}, None),
        ("short-equals-same", ["-n=butlers-dev"], {}, None),
        ("short-separated-same", ["-n", "butlers-dev"], {}, None),
        ("long-equals-same", ["--namespace=butlers-dev"], {}, None),
        ("short-attached-other", ["-nbutlers-other"], {}, "namespace-mismatch"),
        ("short-equals-other", ["-n=butlers-other"], {}, "namespace-mismatch"),
        ("kube-context", ["--kube-context", "fixture"], {}, "helm-target-args-unsupported"),
        ("kube-context-equals", ["--kube-context=fixture"], {}, "helm-target-args-unsupported"),
        ("kubeconfig", ["--kubeconfig", str(kubeconfig)], {}, "helm-target-args-unsupported"),
        (
            "kubeconfig-equals",
            ["--kubeconfig=" + str(kubeconfig)],
            {},
            "helm-target-args-unsupported",
        ),
        (
            "kube-apiserver",
            ["--kube-apiserver=https://192.0.2.2"],
            {},
            "helm-target-args-unsupported",
        ),
        (
            "kube-token",
            ["--kube-token=private-target-sentinel"],
            {},
            "helm-target-args-unsupported",
        ),
        ("env-context", [], {"HELM_KUBECONTEXT": "fixture"}, "helm-target-env-unsupported"),
        (
            "env-apiserver",
            [],
            {"HELM_KUBEAPISERVER": "https://192.0.2.2"},
            "helm-target-env-unsupported",
        ),
        (
            "env-token",
            [],
            {"HELM_KUBETOKEN": "private-target-sentinel"},
            "helm-target-env-unsupported",
        ),
        ("env-empty", [], {"HELM_KUBEAPISERVER": ""}, "helm-target-env-unsupported"),
    ]
    failures = []
    (state / "up").write_text("")
    (state / "control").write_text("")
    (state / "pods").write_text(json.dumps({"kind": "List", "items": []}))
    normal_environment = {
        key: value for key, value in environment.items() if not key.startswith("HELM_KUBE")
    }
    for case, flags, overrides, refusal in target_cases:
        log.write_text("")
        arguments = [
            *flags,
            "-f",
            "values.yaml",
            "-f",
            "values.dev.yaml",
            "--set",
            "database.host=db.invalid",
            "--set",
            "trackerEgress.enabled=false",
            "--set",
            "egressPolicy.enabled=false",
            "--set",
            "image.tag=fixture",
            "--set",
            "frontendImage.tag=fixture-dev",
        ]
        target_environment = {
            **normal_environment,
            **overrides,
            "CASE": "target-" + case,
            "KUBECONFIG": str(kubeconfig),
            "REAL_HELM": helm,
            "REAL_CHART": str(_REPO_ROOT / "deploy/helm/butlers"),
        }
        run = subprocess.run(
            ["make", "deploy-dev", "TAG=fixture", "helm_args=" + shlex.join(arguments)],
            cwd=chart,
            env=target_environment,
            capture_output=True,
            text=True,
        )
        calls = log.read_text()
        allowed = refusal is None
        evidence = {
            "case": case,
            "admitted": run.returncode == 0,
            "bootstrap": "BOOTSTRAP" in calls,
            "helm_upgrade": "HELM_UPGRADE" in calls,
            "fixed_refusal": refusal is None or refusal in run.stderr,
            "private_output": "private-target-sentinel" in run.stdout + run.stderr,
        }
        if (
            any(evidence[key] is not allowed for key in ("admitted", "bootstrap", "helm_upgrade"))
            or not evidence["fixed_refusal"]
            or evidence["private_output"]
        ):
            failures.append(evidence)
    assert not failures, failures

    # A direct record wrapper must recheck the same gate after bootstrap too.
    log.write_text("")
    (state / "up").write_text(json.dumps(domains))
    (state / "control").write_text(json.dumps(control))
    (state / "pods").write_text(json.dumps({"kind": "List", "items": [pod(domains), pod(control)]}))
    environment.update(DESIRED_RENDER=str(fake_repo / "cohost.yaml"), CASE="direct")
    run = subprocess.run(
        [
            "bash",
            str(fake_repo / "scripts/k8s/record-deploy.sh"),
            "butlers-dev",
            "fixture",
            "butlers",
            "--",
        ],
        cwd=chart,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "HELM_UPGRADE" not in log.read_text()

    # The first guard admits a stopped transition, then bootstrap's controlled
    # fixture changes the observation. The immediate pre-Helm recheck refuses.
    after = fake_repo / "after-bootstrap"
    after.mkdir()
    for name in ("up", "control", "pods"):
        shutil.copy2(state / name, after / name)
    (state / "up").write_text(json.dumps(zero_domains))
    (state / "control").write_text(json.dumps(zero_control))
    (state / "pods").write_text(json.dumps({"kind": "List", "items": []}))
    environment.update(AFTER_BOOTSTRAP=str(after))
    log.write_text("")
    run = subprocess.run(
        ["make", "deploy-dev", "TAG=fixture"],
        cwd=chart,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "BOOTSTRAP" in log.read_text()
    assert "HELM_UPGRADE" not in log.read_text()
    assert log.read_text().count("helm template") == 2
