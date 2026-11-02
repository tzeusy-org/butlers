"""Deploy-shape guard: every reader of backup facts can actually see the backups.

Two processes read ``butlers.core.backup_facts`` from ``BUTLERS_BACKUP_DIR``:

- dashboard-api, for ``GET /api/system/backups``;
- the butler daemon hosting the QA butler, whose ``infra_state`` patrol source
  raises ``BackupStale`` / ``BackupRunFailed`` (bu-deqpzd).

``InfraStateSource`` treats an unset ``BUTLERS_BACKUP_DIR`` as "backups not
enabled" and skips silently, so a daemon without the env var and the backups
volume never fires those findings -- and nothing else notices. Each reader must
get the env var pointing at a read-only mount of the backups volume.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_CHART = _REPO / "deploy/helm/butlers"
_ENV = "BUTLERS_BACKUP_DIR"

# The QA butler runs in butlers-up (butlers-control hosts only switchboard).
_COMPOSE_READERS = (
    "dashboard-api",
    "dashboard-api-hotreload",
    "butlers-up",
    "butlers-up-hotreload",
)
_HELM_READERS = ("dashboard-api", "butlers-up")


@pytest.fixture(scope="module")
def compose_services() -> dict:
    compose = yaml.safe_load((_REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]


def test_qa_patrol_reads_backup_facts() -> None:
    """Pin the premise: the QA butler (hosted by butlers-up) runs infra_state."""
    qa = (_REPO / "roster/qa/butler.toml").read_text(encoding="utf-8")
    assert '"infra_state"' in qa


@pytest.mark.parametrize("service_name", _COMPOSE_READERS)
def test_compose_backup_reader_has_env_and_readonly_volume(
    compose_services: dict, service_name: str
) -> None:
    service = compose_services[service_name]
    backup_dir = service["environment"].get(_ENV)
    assert backup_dir, f"{service_name} must set {_ENV}"
    assert f"butlers_backups:{backup_dir}:ro" in service["volumes"]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("deployment_name", _HELM_READERS)
def test_helm_backup_reader_has_env_and_readonly_volume(deployment_name: str) -> None:
    rendered = subprocess.run(
        [
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
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    pod = next(
        d["spec"]["template"]["spec"]
        for d in yaml.safe_load_all(rendered.stdout)
        if d and d.get("kind") == "Deployment" and d["metadata"]["name"] == deployment_name
    )
    container = pod["containers"][0]
    backup_dir = next((e.get("value") for e in container["env"] if e["name"] == _ENV), None)
    assert backup_dir, f"{deployment_name} must set {_ENV}"

    claims = {
        v["name"]: v["persistentVolumeClaim"]["claimName"]
        for v in pod["volumes"]
        if "persistentVolumeClaim" in v
    }
    mount = next(m for m in container["volumeMounts"] if m["mountPath"] == backup_dir)
    assert claims.get(mount["name"]) == "butlers-backups"
    assert mount.get("readOnly") is True
