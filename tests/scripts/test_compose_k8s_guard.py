"""scripts/compose.sh refuses to start a stack the k3s release already serves."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_launcher(
    tmp_path: Path, *, args: tuple[str, ...], replicas: str, override: bool = False
) -> tuple[subprocess.CompletedProcess[str], str]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "scripts" / "compose.sh", repo / "scripts" / "compose.sh")
    for mode in ("dev", "prod"):
        (repo / f".env.{mode}").write_text(
            "POSTGRES_HOST=db.invalid\nPOSTGRES_PASSWORD=x\n", encoding="utf-8"
        )

    calls = tmp_path / "calls"
    calls.touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "kubectl",
        "#!/usr/bin/env bash\n"
        'printf "kubectl %s\\n" "$*" >> "$TEST_CALLS"\n'
        '[[ -n "$TEST_K8S_REPLICAS" ]] || exit 1\n'
        'printf "%s" "$TEST_K8S_REPLICAS"\n',
    )
    # Anything after the guard fails fast; these tests only cover the guard.
    _write_executable(
        fake_bin / "docker",
        '#!/usr/bin/env bash\nprintf "docker %s\\n" "$*" >> "$TEST_CALLS"\nexit 1\n',
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TEST_CALLS": str(calls),
        "TEST_K8S_REPLICAS": replicas,
    }
    environment.pop("BUTLERS_ALLOW_COMPOSE_WITH_K8S", None)
    if override:
        environment["BUTLERS_ALLOW_COMPOSE_WITH_K8S"] = "1"
    result = subprocess.run(
        [str(repo / "scripts" / "compose.sh"), "--skip-tailscale-check", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, calls.read_text(encoding="utf-8")


@pytest.mark.parametrize(("args", "namespace"), [((), "butlers-dev"), (("--prod",), "butlers")])
def test_refuses_while_release_has_replicas(tmp_path, args, namespace):
    result, calls = _run_launcher(tmp_path, args=args, replicas="1")

    assert result.returncode == 1
    assert f"namespace {namespace}" in result.stderr
    assert f"kubectl -n {namespace} get deploy butlers-up" in calls
    assert "docker" not in calls


@pytest.mark.parametrize("replicas", ["0", ""])
def test_proceeds_when_release_is_absent_or_scaled_down(tmp_path, replicas):
    result, calls = _run_launcher(tmp_path, args=(), replicas=replicas)

    assert "served by Kubernetes" not in result.stderr
    assert "kubectl -n butlers-dev get deploy butlers-up" in calls


def test_override_skips_the_cluster_check(tmp_path):
    result, calls = _run_launcher(tmp_path, args=(), replicas="1", override=True)

    assert "served by Kubernetes" not in result.stderr
    assert "kubectl" not in calls
