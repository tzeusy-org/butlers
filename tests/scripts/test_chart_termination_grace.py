"""The pod's termination grace outlasts the process's shutdown budget (bu-dhloi1.153).

Kubernetes sends SIGKILL when ``terminationGracePeriodSeconds`` (30s unless set) runs out. The
`butlers up` and control processes unwind their daemons within ``BUTLERS_SHUTDOWN_BUDGET_S``, so
the grace must exceed that by a margin for pool close and interpreter exit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed"),
]

_CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "butlers"


def _deployments(env: str, *settings: str) -> dict[str, dict]:
    command = ["helm", "template", "butlers", str(_CHART)]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / f"values.{env}.yaml")]
    for setting in (
        "image.tag=t",
        "frontendImage.tag=t",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={198.51.100.7/32}",
        "database.host=postgres.invalid",
        *settings,
    ):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}


def _budget(pod: dict) -> int:
    env = {e["name"]: e.get("value") for c in pod["spec"]["containers"] for e in c.get("env", [])}
    return int(env["BUTLERS_SHUTDOWN_BUDGET_S"])


@pytest.mark.parametrize("env", ["dev", "prod"])
@pytest.mark.parametrize("settings", [(), ("butlersControl.enabled=true",)])
def test_a_daemon_pod_has_grace_beyond_its_shutdown_budget(env, settings) -> None:
    deployments = _deployments(env, *settings)
    up = deployments["butlers-up"]["spec"]["template"]
    grace = up["spec"]["terminationGracePeriodSeconds"]
    assert grace >= _budget(up) + 20, "butlers-up: margin for pool close and exit"

    if settings:
        # One daemon, bounded by its own 30s default timeout.
        control = deployments["butlers-control"]["spec"]["template"]
        assert control["spec"]["terminationGracePeriodSeconds"] >= 30 + 20


def test_the_control_pod_grace_follows_the_daemon_timeout_value() -> None:
    deployments = _deployments(
        "dev",
        "butlersControl.enabled=true",
        "shutdown.daemonTimeoutSeconds=50",
        "shutdown.graceMarginSeconds=25",
    )
    control = deployments["butlers-control"]["spec"]["template"]

    assert control["spec"]["terminationGracePeriodSeconds"] == 75


def test_the_budget_and_margin_come_from_values() -> None:
    deployments = _deployments(
        "dev", "shutdown.budgetSeconds=100", "shutdown.graceMarginSeconds=40"
    )
    pod = deployments["butlers-up"]["spec"]["template"]

    assert _budget(pod) == 100
    assert pod["spec"]["terminationGracePeriodSeconds"] == 140
