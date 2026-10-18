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
  *"rollout status deploy/$FAIL_ROLLOUT"*) [ -n "$FAIL_ROLLOUT" ] && exit 1 ;;
  *" exec "*) [ -n "$FAIL_EXEC" ] && exit 1 ;;
esac
exit 0
"""


@pytest.fixture()
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    stubs = {"helm": '[ -n "$FAIL_HELM" ] && exit 3; exit 0', "kubectl": _KUBECTL}
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
        **{f"FAIL_{key.upper()}": value for key, value in fail.items()},
    }
    script = shutil.copy2(_REPO_ROOT / "scripts/k8s/record-deploy.sh", tmp_path / "rd.sh")
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
    ]
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("deploy/dashboard-api -- butlers deployments record "
                              "--source deploy --result success")  # fmt: skip


def test_failed_release_records_failed_row_and_keeps_exit_status(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, _stderr, calls = _run(tmp_path, bin_dir, helm="1")
    assert code == 3
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")
    # A failed helm upgrade never waits on rollouts.
    assert not any("rollout status" in c for c in calls)

    code, _stderr, calls = _run(tmp_path, bin_dir, rollout="butlers-up")
    assert code == 1
    assert len(_records(calls)) == 1
    assert calls[-1].endswith("--source deploy --result failed --git-sha tag123")


def test_unreachable_pod_never_turns_failure_into_success_or_success_into_failure(
    tmp_path: Path, bin_dir: Path
) -> None:
    code, stderr, _calls = _run(tmp_path, bin_dir, exec="1")
    assert code == 0
    assert "no deployment row was recorded" in stderr

    code, stderr, _calls = _run(tmp_path, bin_dir, exec="1", helm="1")
    assert code == 3
    assert "no deployment row recorded" in stderr
