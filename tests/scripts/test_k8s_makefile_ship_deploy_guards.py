"""`make ship-*` stops on a failed build and `deploy-*` refuses an empty TAG (bu-s3heqm).

Runs the real makefile in a scratch tree whose build-push.sh, helm and kubectl are stubs, so no
cluster or registry is touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STUB = '#!/usr/bin/env bash\necho "$(basename "$0") $*" >> "$CALL_LOG"\n{body}\n'


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    chart = tmp_path / "deploy/helm/butlers"
    scripts = tmp_path / "scripts/k8s"
    chart.mkdir(parents=True)
    scripts.mkdir(parents=True)
    shutil.copy2(_ROOT / "deploy/helm/butlers/makefile", chart / "makefile")
    shutil.copy2(_ROOT / "scripts/k8s/record-deploy.sh", scripts / "record-deploy.sh")
    for name, body in {
        "build-push.sh": '[ -n "$FAIL_BUILD" ] && exit 5; echo "${BUILD_TAG-abc123}"',
        "site-helm-args.sh": "exit 0",
    }.items():
        (scripts / name).write_text(_STUB.format(body=body), encoding="utf-8")
        (scripts / name).chmod(0o755)
    (chart / "values.yaml").write_text("", encoding="utf-8")
    (chart / "values.dev.yaml").write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("helm", "kubectl"):
        (bin_dir / name).write_text(_STUB.format(body="exit 0"), encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    return tmp_path


def _make(tree: Path, *args: str, **env: str) -> tuple[int, str, list[str]]:
    log = tree / "calls.log"
    log.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["make", "-C", str(tree / "deploy/helm/butlers"), *args],
        capture_output=True, text=True, cwd=tree,
        env={"PATH": f"{tree / 'bin'}:{os.environ['PATH']}", "CALL_LOG": log.as_posix(), **env},
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text(encoding="utf-8").splitlines()


def _helm_calls(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith("helm ")]


def test_a_failed_build_stops_ship_before_any_deploy(tree: Path) -> None:
    code, stderr, calls = _make(tree, "ship-dev", FAIL_BUILD="1")

    assert code != 0, stderr
    assert _helm_calls(calls) == []
    assert not any(call.startswith("kubectl") for call in calls)


def test_a_build_that_prints_no_tag_stops_ship_with_a_clear_message(tree: Path) -> None:
    code, stderr, calls = _make(tree, "ship-dev", BUILD_TAG="")

    assert code != 0
    assert "printed no image tag" in stderr
    assert _helm_calls(calls) == []


def test_a_good_build_deploys_the_printed_tag(tree: Path) -> None:
    code, stderr, calls = _make(tree, "ship-dev")

    assert code == 0, stderr
    (helm,) = _helm_calls(calls)
    assert "image.tag=abc123" in helm


def test_deploy_with_an_empty_tag_is_refused_without_helm(tree: Path) -> None:
    code, stderr, calls = _make(tree, "deploy-dev", "TAG=")

    assert code != 0
    assert "TAG is empty" in stderr
    assert _helm_calls(calls) == []
    assert not any(call.startswith("kubectl") for call in calls)
