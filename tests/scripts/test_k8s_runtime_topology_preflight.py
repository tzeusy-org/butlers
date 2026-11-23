"""The dev topology preflight is self-sufficient in a fresh checkout and tolerates a stuck rollout.

bu-dhloi1.156: ``uv run --no-sync`` in a fresh worktree found an empty venv, so
``import yaml`` failed and ``make deploy-dev`` stopped before Helm. And with ``butlers-up``
stuck unready (``status`` carries replicas/updated/unavailable but no ready/available counters,
which Kubernetes omits when zero) the preflight refused ``invalid-runtime-status``, blocking a
redeploy exactly when a rollout had failed.
"""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO_ROOT / "scripts/k8s/runtime-topology-preflight.sh"
_MODULE = _REPO_ROOT / "scripts/k8s/runtime_topology_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("runtime_topology_preflight", _MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _deployment(desired: int, status: dict) -> dict:
    return {
        "metadata": {"generation": 3},
        "spec": {"replicas": desired},
        "status": {"observedGeneration": 3, **status},
    }


def test_a_stuck_rollout_with_absent_ready_counters_is_not_refused():
    preflight = _load()
    stuck = _deployment(1, {"replicas": 1, "updatedReplicas": 1, "unavailableReplicas": 1})
    assert preflight._observed(stuck) is False  # running, so not quiescent, but admitted


def test_absent_counters_still_read_as_zero_after_scale_to_zero():
    preflight = _load()
    assert preflight._observed(_deployment(0, {})) is True


@pytest.mark.parametrize(
    "status, reason",
    [
        ({"replicas": -1}, "invalid-runtime-status"),
        ({"replicas": "1"}, "invalid-runtime-status"),
        ({"replicas": 1, "readyReplicas": 2}, "inconsistent-runtime-status"),
        ({"replicas": 1, "terminatingReplicas": 1}, "terminating-runtime-replicas"),
    ],
)
def test_malformed_or_inconsistent_status_is_still_refused(status, reason):
    preflight = _load()
    with pytest.raises(preflight.Refused, match=reason):
        preflight._observed(_deployment(1, status))


def _stub_uv(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "uv.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n', encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return bin_dir, log


def _run(tmp_path: Path, namespace: str) -> tuple[int, list[str]]:
    bin_dir, log = _stub_uv(tmp_path)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(_WRAPPER), namespace, "tag", "rel", "--"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    return result.returncode, (log.read_text().splitlines() if log.exists() else [])


def test_dev_preflight_does_not_depend_on_the_project_environment(tmp_path):
    code, calls = _run(tmp_path, "butlers-dev")
    assert code == 0 and len(calls) == 1
    (call,) = calls
    # Not the project venv (empty in a fresh worktree), and no project sync of every dependency:
    # an isolated environment holding exactly the one module the script imports.
    assert "--no-project" in call and "--no-sync" not in call
    assert re.search(r"--with pyyaml==\S+", call), call


def test_the_pyyaml_pin_matches_the_lockfile():
    locked = re.search(
        r'^name = "pyyaml"\nversion = "([^"]+)"', (_REPO_ROOT / "uv.lock").read_text(), re.M
    )
    assert locked is not None
    assert f"pyyaml=={locked.group(1)}" in _WRAPPER.read_text()


def test_other_namespaces_never_touch_uv(tmp_path):
    code, calls = _run(tmp_path, "butlers-prod")
    assert code == 0 and calls == []
