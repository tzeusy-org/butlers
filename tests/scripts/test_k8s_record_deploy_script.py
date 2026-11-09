"""scripts/k8s/record-deploy.sh: each Helm release records a deploy row (bu-viat6h.5)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALL_LOG"
{extra}
"""
# kubectl dispatches on its arguments: `rollout status deploy/<name>` vs `exec`.
_KUBECTL = """
case "$*" in
  *" get deployment butlers-control "*)
    [ -n "$FAIL_QUERY" ] && exit 7
    [ -n "$CONTROL_PRESENT" ] && echo deployment.apps/butlers-control
    exit 0 ;;
  *"rollout status deploy/$FAIL_ROLLOUT"*) [ -n "$FAIL_ROLLOUT" ] && exit 1 ;;
  *"deployments await-ready"*) [ -n "$FAIL_READY" ] && exit 1 ;;
  *" exec "*) [ -n "$FAIL_EXEC" ] && exit 1 ;;
esac
exit 0
"""


@pytest.fixture()
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    stubs = {
        "helm": '[ -n "$FAIL_HELM" ] && exit 3; exit 0',
        "kubectl": _KUBECTL,
        "uv": '[ -n "$FAIL_CHECK" ] && exit 2; exit 0',
    }
    for name, extra in stubs.items():
        stub = path / name
        stub.write_text(_STUB.format(extra=extra), encoding="utf-8")
        stub.chmod(0o755)
    return path


def _run(tmp_path: Path, bin_dir: Path, **fail: str) -> tuple[int, str, list[str]]:
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALL_LOG": log.as_posix(),
        "FAIL_HELM": "",
        "FAIL_ROLLOUT": "",
        "FAIL_EXEC": "",
        "FAIL_QUERY": "",
        "FAIL_READY": "",
        "FAIL_CHECK": "",
        "CONTROL_PRESENT": "1",
        **{
            (
                key.upper()
                if key in {"control_present", "readiness_timeout"}
                else f"FAIL_{key.upper()}"
            ): value
            for key, value in fail.items()
        },
    }
    script = shutil.copy2(_REPO_ROOT / "scripts/k8s/record-deploy.sh", tmp_path / "rd.sh")
    shutil.copy2(_REPO_ROOT / "scripts/k8s/runtime-topology-preflight.sh", tmp_path)
    result = subprocess.run(
        ["bash", str(script), "ns-x", "tag123", "rel", "--", "--set", "image.tag=tag123"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text(encoding="utf-8").splitlines()


def _records(calls: list[str]) -> list[str]:
    return [call for call in calls if "deployments record" in call]


def test_success_records_one_success_row_after_both_rollouts(tmp_path: Path, bin_dir: Path) -> None:
    code, stderr, calls = _run(tmp_path, bin_dir)
    assert code == 0, stderr
    assert calls[0] == "helm upgrade --install rel . --create-namespace --set image.tag=tag123"
    assert [c for c in calls if "rollout status" in c] == [
        "kubectl -n ns-x rollout status deploy/dashboard-api --timeout=10m",
        "kubectl -n ns-x rollout status deploy/butlers-up --timeout=10m",
        "kubectl -n ns-x rollout status deploy/butlers-control --timeout=10m",
    ]
    assert len(_records(calls)) == 1
    # Rollout is not completion: the sustained semantic readiness wait runs in the
    # API pod after every rollout and before the success row (REQ-dashboard-api-064).
    assert calls[-2] == ("kubectl -n ns-x exec --pod-running-timeout=30s deploy/dashboard-api -- "
                         "/app/.venv/bin/butlers deployments await-ready")  # fmt: skip
    assert calls[-1].endswith("deploy/dashboard-api -- /app/.venv/bin/butlers deployments record "
                              "--source deploy --result success")  # fmt: skip


def test_unsustained_readiness_records_failed_row_and_fails_the_release(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, _stderr, calls = _run(tmp_path, bin_dir, ready="1")
    assert code == 1
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")

    code, _stderr, calls = _run(tmp_path, bin_dir, rollout="dashboard-api")
    assert not any("await-ready" in c for c in calls)


def test_helm_failure_records_failed_row_and_skips_rollout_waits(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, _stderr, calls = _run(tmp_path, bin_dir, helm="1")
    assert code == 3
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")
    assert not any("rollout status" in c for c in calls)


def test_rollout_timeout_records_failed_row_and_keeps_exit_status(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, _stderr, calls = _run(tmp_path, bin_dir, rollout="dashboard-api")
    assert code == 1
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")
    assert not any("deploy/butlers-up" in c for c in calls)

    code, _stderr, calls = _run(tmp_path, bin_dir, rollout="butlers-up")
    assert code == 1
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")

    code, _stderr, calls = _run(tmp_path, bin_dir, rollout="butlers-control")
    assert code == 1
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")


def test_optional_control_absence_and_query_failure_are_distinguished(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, stderr, calls = _run(tmp_path, bin_dir, control_present="")
    assert code == 0, stderr
    assert not any("rollout status deploy/butlers-control" in c for c in calls)
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result success")

    code, _stderr, calls = _run(tmp_path, bin_dir, query="1")
    assert code == 7
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")
    assert not any("rollout status deploy/butlers-control" in c for c in calls)


def test_unreachable_pod_never_turns_failure_into_success_or_success_into_failure(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, stderr, _calls = _run(tmp_path, bin_dir, exec="1")
    assert code == 0
    assert "no deployment row was recorded" in stderr

    code, stderr, _calls = _run(tmp_path, bin_dir, exec="1", helm="1")
    assert code == 3
    assert "no deployment row recorded" in stderr


@pytest.mark.parametrize("empty", ["ns", "tag", "release"])
def test_an_empty_argument_is_refused_before_helm_runs(
    tmp_path: Path, bin_dir: Path, empty: str
) -> None:
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": log.as_posix()}
    args = {"ns": "ns-x", "tag": "tag123", "release": "rel"} | {empty: ""}
    result = subprocess.run(
        ["bash", str(_REPO_ROOT / "scripts/k8s/record-deploy.sh"), *args.values(), "--", "--set", "x=1"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )  # fmt: skip

    assert result.returncode == 2
    assert "non-empty" in result.stderr
    assert log.read_text(encoding="utf-8") == ""

    chart = tmp_path / "deploy" / "helm" / "butlers"
    chart.mkdir(parents=True)
    scripts = tmp_path / "scripts" / "k8s"
    scripts.mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "deploy/helm/butlers/makefile", chart / "makefile")
    shutil.copy2(_REPO_ROOT / "scripts/k8s/record-deploy.sh", scripts / "record-deploy.sh")
    for name in (
        "bootstrap-db.sh",
        "site-helm-args.sh",
        "runtime-topology-preflight.sh",
        "public-mcp-owner-step.sh",
    ):
        stub = scripts / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    variables = {"NAMESPACE_dev": args["ns"], "TAG": args["tag"], "RELEASE_NAME": args["release"]}
    recipe = subprocess.run(
        ["make", "deploy-dev", *(f"{key}={value}" for key, value in variables.items())],
        cwd=chart,
        env=env,
        capture_output=True,
        text=True,
    )
    assert recipe.returncode == 2
    assert "non-empty" in recipe.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_a_failed_record_after_a_failed_release_does_not_blame_reachability(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, stderr, _calls = _run(tmp_path, bin_dir, helm="1", exec="1")

    assert code == 3
    assert "no deployment row recorded" in stderr
    assert "unreachable" not in stderr


def test_readiness_timeout_override_is_validated_before_helm_changes_the_cluster(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, stderr, calls = _run(tmp_path, bin_dir, readiness_timeout="60", check="1")
    assert code == 2
    assert "refused before helm upgrade" in stderr
    assert [c.split()[0] for c in calls] == ["uv"]  # no helm, rollout, wait, or record

    code, stderr, calls = _run(tmp_path, bin_dir, readiness_timeout="7200")
    assert code == 0, stderr
    assert calls[0].startswith("uv run --project ")
    assert "await-ready --check-only --timeout 7200" in calls[0]
    assert calls[1].startswith("helm upgrade")
    assert any("await-ready --timeout 7200" in c for c in calls)

    _code, _stderr, calls = _run(tmp_path, bin_dir)
    assert not any(c.startswith("uv ") for c in calls)  # no override, no local check
