"""Render only synthetic opaque Secret references; never provision a monitor."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
_CHART = Path(__file__).resolve().parents[2] / "deploy/helm/butlers"


def _render(*settings: str) -> subprocess.CompletedProcess:
    command = [
        "helm",
        "template",
        "butlers",
        str(_CHART),
        "--set",
        "image.tag=test",
        "--set",
        "frontendImage.tag=test",
        "--set",
        "database.host=postgres.invalid",
    ]
    for setting in settings:
        command.extend(["--set", setting])
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


@pytest.mark.parametrize("enabled", [False, True])
def test_deadman_optional_secret_reference_rendering(enabled):
    settings = ["dashboardApi.externalDeadman.enabled=true"] if enabled else []
    if enabled:
        settings += [
            "dashboardApi.externalDeadman.urlSecretRef.name=synthetic-monitor",
            "dashboardApi.externalDeadman.urlSecretRef.key=target",
            "dashboardApi.externalDeadman.intervalSeconds=600",
        ]
    rendered = _render(*settings)
    assert rendered.returncode == 0, rendered.stderr
    deployment = next(
        d
        for d in yaml.safe_load_all(rendered.stdout)
        if d and d.get("kind") == "Deployment" and d["metadata"]["name"] == "dashboard-api"
    )
    env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    monitor = [e for e in env if e["name"] == "EXTERNAL_DEADMAN_URL"]
    if enabled:
        assert monitor == [
            {
                "name": "EXTERNAL_DEADMAN_URL",
                "valueFrom": {
                    "secretKeyRef": {"name": "synthetic-monitor", "key": "target"},
                },
            }
        ]
        assert {"name": "EXTERNAL_DEADMAN_CHECK_INTERVAL_S", "value": "600"} in env
    else:
        assert monitor == []
        assert all(e["name"] != "EXTERNAL_DEADMAN_CHECK_INTERVAL_S" for e in env)


def test_enabled_deadman_requires_complete_secret_reference():
    rendered = _render("dashboardApi.externalDeadman.enabled=true")
    assert rendered.returncode != 0
    assert "externalDeadman.urlSecretRef.name is required" in rendered.stderr
