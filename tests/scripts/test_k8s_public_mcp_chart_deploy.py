"""scripts/k8s/public-mcp-chart-deploy.sh: the public chart upgrade at the butlers TAG (task 2.4).

A stub `helm` records its arguments; nothing reaches a cluster.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts/k8s/public-mcp-chart-deploy.sh"
_CHART = _ROOT / "deploy/helm/butlers-public-mcp"
_TAG = "abc123def456"


def _run(tmp_path: Path, *args: str, helm_rc: int = 0, **env: str) -> tuple[int, str, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "helm"
    stub.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{tmp_path}/helm.log"\nexit {helm_rc}\n')
    stub.chmod(0o755)
    log = tmp_path / "helm.log"
    log.write_text("")
    result = subprocess.run(
        ["bash", str(_SCRIPT), *args], capture_output=True, text=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BUTLERS_SITE_ENV": str(tmp_path / "absent.env"),
            "HOME": str(tmp_path),
            **env,
        },
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text().splitlines()


def test_dev_on_upgrades_the_chart_at_the_tag_from_the_butlers_namespace(tmp_path: Path) -> None:
    code, stderr, calls = _run(
        tmp_path, "dev", _TAG, "on",
        BUTLERS_IMAGE_REGISTRY="registry.example", BUTLERS_PUBLIC_HOST="host.example",
    )  # fmt: skip
    assert code == 0, stderr
    assert calls == [
        f"upgrade --install butlers-public-mcp {_CHART} -n butlers-dev "
        f"-f {_CHART}/values.yaml -f {_CHART}/values.dev.yaml --set image.tag={_TAG} "
        "--wait --timeout 5m --set image.repository=registry.example/butlers-app "
        "--set dashboardBase=https://host.example/butlers-dev"
    ]


@pytest.mark.parametrize("args", [("dev", _TAG, "off"), ("dev", _TAG), ("prod", _TAG, "on")])
def test_off_default_and_prod_skip_without_calling_helm(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    code, stderr, calls = _run(tmp_path, *args)
    assert code == 0, stderr
    assert calls == []
    assert "skipped" in stderr


def test_a_helm_failure_is_loud_and_non_zero(tmp_path: Path) -> None:
    code, stderr, _calls = _run(tmp_path, "dev", _TAG, "on", helm_rc=1)
    assert code == 1
    assert "public-mcp chart FAILED" in stderr


@pytest.mark.parametrize("args", [(), ("dev",), ("dev", "bad tag", "on"), ("dev", _TAG, "maybe")])
def test_bad_arguments_fail_before_helm(tmp_path: Path, args: tuple[str, ...]) -> None:
    code, _stderr, calls = _run(tmp_path, *args)
    assert code != 0
    assert calls == []


def test_the_go_live_live_checks_script_parses_and_never_dumps_a_secret() -> None:
    """The live checks run at go-live, not in CI: keep them parseable and secret-free."""
    script = _ROOT / "scripts/k8s/public-mcp-live-checks.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text()
    for banned in ("get secret", "printenv", "jsonpath='{.data"):
        assert banned not in text
