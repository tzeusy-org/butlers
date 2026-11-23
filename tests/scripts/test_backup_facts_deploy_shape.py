"""Deploy-shape guard: only dashboard-api mounts the backups volume (bu-deqpzd).

The backups volume holds full all-schema ``pg_dump`` artifacts. butlers-up
hosts every butler's runtime CLI (shell and file access), so mounting the
volume there would let one prompt-injected session read every schema. The QA
patrol (hosted in butlers-up) gets backup recency from dashboard-api's
``public.state`` snapshot instead (``butlers.core.backup_facts_snapshot``).

This pins both halves: dashboard-api keeps its read-only mount plus
``BUTLERS_BACKUP_DIR`` (it reads the dumps and publishes the snapshot), and the
butler daemon services get neither.
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

_COMPOSE_READERS = ("dashboard-api", "dashboard-api-hotreload")
_COMPOSE_DAEMONS = (
    "butlers-up",
    "butlers-up-hotreload",
    "butlers-control",
    "butlers-control-hotreload",
)


@pytest.fixture(scope="module")
def compose_services() -> dict:
    compose = yaml.safe_load((_REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]


def _compose_mounts_backups(service: dict) -> bool:
    return any(str(v).startswith("butlers_backups:") for v in service.get("volumes", []))


@pytest.mark.parametrize("service_name", _COMPOSE_READERS)
def test_compose_dashboard_keeps_env_and_readonly_backups_mount(
    compose_services: dict, service_name: str
) -> None:
    service = compose_services[service_name]
    backup_dir = service["environment"].get(_ENV)
    assert backup_dir, f"{service_name} must set {_ENV}"
    assert f"butlers_backups:{backup_dir}:ro" in service["volumes"]


@pytest.mark.parametrize("service_name", _COMPOSE_DAEMONS)
def test_compose_daemon_never_sees_backups(compose_services: dict, service_name: str) -> None:
    service = compose_services[service_name]
    assert not _compose_mounts_backups(service)
    assert _ENV not in service.get("environment", {})


@pytest.fixture(scope="module")
def helm_pods() -> dict[str, dict]:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
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
            "--set",
            "butlersControl.enabled=true",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    return {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in yaml.safe_load_all(rendered.stdout)
        if d and d.get("kind") == "Deployment"
    }


def _backup_claims(pod: dict) -> set[str]:
    return {
        v["name"]
        for v in pod.get("volumes", [])
        if v.get("persistentVolumeClaim", {}).get("claimName") == "butlers-backups"
    }


def test_helm_dashboard_keeps_env_and_readonly_backups_mount(helm_pods: dict) -> None:
    pod = helm_pods["dashboard-api"]
    container = pod["containers"][0]
    backup_dir = next((e.get("value") for e in container["env"] if e["name"] == _ENV), None)
    assert backup_dir, f"dashboard-api must set {_ENV}"
    mount = next(m for m in container["volumeMounts"] if m["mountPath"] == backup_dir)
    assert mount["name"] in _backup_claims(pod)
    assert mount.get("readOnly") is True


def test_helm_only_dashboard_mounts_backups(helm_pods: dict) -> None:
    assert "butlers-up" in helm_pods
    mounting = {name for name, pod in helm_pods.items() if _backup_claims(pod)}
    assert mounting == {"dashboard-api"}
    for name, pod in helm_pods.items():
        if name != "dashboard-api":
            for container in pod["containers"]:
                assert all(e["name"] != _ENV for e in container.get("env", [])), name
