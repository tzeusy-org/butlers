"""Off-node restic copy CronJob rendering (bu-ivuf9u).

Renders only synthetic values and Secret *names*: the chart must ship the job
disabled, demand the owner-supplied device and Secret, and never carry a
secret value.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
_CHART = Path(__file__).resolve().parents[2] / "deploy/helm/butlers"
_ENABLED = [
    "offsiteCopy.enabled=true",
    "offsiteCopy.host=device.invalid",
    "offsiteCopy.repoPath=/srv/restic/butlers",
    "offsiteCopy.secretName=synthetic-offsite",
]


def _render(*settings: str) -> subprocess.CompletedProcess:
    command = ["helm", "template", "butlers", str(_CHART)]
    for setting in ("image.tag=test", "frontendImage.tag=test", "database.host=pg.invalid"):
        command.extend(["--set", setting])
    for setting in settings:
        command.extend(["--set", setting])
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _docs(rendered: subprocess.CompletedProcess) -> list[dict]:
    assert rendered.returncode == 0, rendered.stderr
    return [d for d in yaml.safe_load_all(rendered.stdout) if d]


def _dashboard_env(docs: list[dict]) -> list[dict]:
    api = next(
        d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "dashboard-api"
    )
    return api["spec"]["template"]["spec"]["containers"][0]["env"]


def test_offsite_copy_ships_disabled():
    docs = _docs(_render())

    assert not [d for d in docs if d["metadata"]["name"].endswith("offsite-copy")]
    assert all(e["name"] != "BUTLERS_OFFSITE_COPY_ENABLED" for e in _dashboard_env(docs))


def test_enabled_copy_reads_dumps_and_secrets_only_by_reference():
    docs = _docs(_render(*_ENABLED))

    job = next(
        d
        for d in docs
        if d["kind"] == "CronJob" and d["metadata"]["name"] == "butlers-offsite-copy"
    )
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["OFFSITE_HOST"] == "device.invalid"
    assert env["OFFSITE_REPO_PATH"] == "/srv/restic/butlers"
    assert env["OFFSITE_INIT_REPOSITORY"] == "false"
    # Secrets arrive as files from a projected Secret, never as values.
    secret_volume = next(v for v in pod["volumes"] if "secret" in v)
    assert secret_volume["secret"]["secretName"] == "synthetic-offsite"
    assert {i["key"] for i in secret_volume["secret"]["items"]} == {
        "restic-password",
        "ssh-private-key",
        "known-hosts",
    }
    assert all("valueFrom" not in e for e in container["env"])
    backups = next(v for v in pod["volumes"] if v.get("persistentVolumeClaim"))
    assert backups["persistentVolumeClaim"]["claimName"] == "butlers-backups"
    # The dashboard reads the receipt only where the job exists.
    assert {"name": "BUTLERS_OFFSITE_COPY_ENABLED", "value": "1"} in _dashboard_env(docs)


@pytest.mark.parametrize(
    ("drop", "message"),
    [
        ("offsiteCopy.host", "offsiteCopy.host is required"),
        ("offsiteCopy.repoPath", "offsiteCopy.repoPath is required"),
        ("offsiteCopy.secretName", "offsiteCopy.secretName is required"),
    ],
)
def test_enabled_copy_requires_owner_inputs(drop, message):
    rendered = _render(*[s for s in _ENABLED if not s.startswith(f"{drop}=")])

    assert rendered.returncode != 0
    assert message in rendered.stderr


def test_copy_requires_the_backup_job_it_copies():
    rendered = _render(*_ENABLED, "backup.enabled=false")

    assert rendered.returncode != 0
    assert "offsiteCopy requires backup.enabled" in rendered.stderr
