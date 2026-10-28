"""scripts/k8s/bootstrap-db.sh: rerun init-db.sql before a k8s rollout (bu-8jtq10)."""

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
    shutil.copy2(_REPO_ROOT / "deploy/helm/butlers/makefile", chart / "makefile")
    helm_args = tmp_path / "scripts" / "k8s" / "site-helm-args.sh"
    helm_args.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    helm_args.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        "kubectl": _KUBECTL,
        "git": f'case "$*" in *show*) echo "{_SQL}" ;; *rev-parse*) echo abc123def456 ;; esac',
        "helm": "",
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
    assert lines[0] == "../../../scripts/k8s/bootstrap-db.sh dev abc123def456"
    assert lines[1].startswith(
        '../../../scripts/k8s/record-deploy.sh "butlers-dev" "abc123def456" "butlers" -- '
    )
    assert "--set image.tag=abc123def456 --set frontendImage.tag=abc123def456-dev" in lines[1]
