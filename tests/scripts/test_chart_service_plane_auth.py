"""Service-plane caller authentication is dark in the chart by default (bu-2c1g7v)."""

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


def _render(*settings: str) -> list[dict]:
    command = ["helm", "template", "butlers", str(_CHART)]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    for setting in (
        "image.tag=t",
        "frontendImage.tag=t",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={198.51.100.7/32}",
        "database.host=postgres.invalid",
        "butlersControl.enabled=true",
        *settings,
    ):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _pod(docs: list[dict], name: str) -> dict:
    return next(
        d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == name
    )


def _env(pod: dict) -> dict[str, str]:
    return {e["name"]: e.get("value") for c in pod["containers"] for e in c.get("env", [])}


def test_off_by_default_renders_no_accounts_switch_or_token() -> None:
    docs = _render()

    assert not [d for d in docs if d["kind"] == "ServiceAccount"]
    for name in ("butlers-control", "butlers-up"):
        pod = _pod(docs, name)
        assert "serviceAccountName" not in pod
        assert "BUTLERS_SERVICE_PLANE_AUTH" not in _env(pod)
        assert all(v["name"] != "workload-token" for v in pod["volumes"])


def test_on_gives_each_workload_its_account_and_control_a_bound_delivery_token() -> None:
    docs = _render("servicePlaneAuth.enabled=true")

    accounts = {d["metadata"]["name"] for d in docs if d["kind"] == "ServiceAccount"}
    assert {"butlers-control", "butlers-up"} <= accounts
    control, up = _pod(docs, "butlers-control"), _pod(docs, "butlers-up")
    assert control["serviceAccountName"] == "butlers-control"
    assert up["serviceAccountName"] == "butlers-up"
    assert _env(control)["BUTLERS_SERVICE_PLANE_AUTH"] == "1"
    assert _env(up)["BUTLERS_SERVICE_PLANE_AUTH"] == "1"

    (volume,) = [v for v in control["volumes"] if v["name"] == "workload-token"]
    (source,) = volume["projected"]["sources"]
    token = source["serviceAccountToken"]
    assert token["audience"] == token["path"] == "butlers.messenger-delivery.v1"
    assert token["expirationSeconds"] <= 3600
    # Only the control plane presents a delivery token.
    assert all(v["name"] != "workload-token" for v in up["volumes"])
