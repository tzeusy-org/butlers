"""The dashboard services take their AppArmor profile from an env var (bu-1x2mc9).

Operators on hosts that restrict unprivileged user namespaces set
``BUTLERS_DASHBOARD_APPARMOR_PROFILE`` instead of editing the tracked Compose file, whose
explicit ``-f`` lists never load override files.  Render-only, synthetic non-secret inputs; no
container is created.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_SERVICES = ("dashboard-api", "dashboard-api-hotreload")
_PROFILE = "butlers-dashboard-api"


def _security_opts(tmp_path: Path, *extra_env: str) -> dict[str, list[str]]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is required to render this deployment contract")
    env_file = tmp_path / "apparmor-profile.env"
    env_file.write_text(
        "\n".join(
            (
                "POSTGRES_HOST=10.23.4.5",
                "POSTGRES_PASSWORD=non-secret-test-password",
                "COMPOSE_PROJECT_NAME=butlers-apparmor-profile-test",
                *extra_env,
                "",
            )
        ),
        encoding="utf-8",
    )
    docker_home = tmp_path / "docker-home"
    docker_home.mkdir(exist_ok=True)
    completed = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            "docker-compose.yml",
            "--profile",
            "hotreload",
            "config",
            "--format",
            "json",
        ],
        check=False,
        cwd=_REPO_ROOT,
        capture_output=True,
        env={
            "PATH": os.defpath,
            "HOME": str(docker_home),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        },
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    services = json.loads(completed.stdout)["services"]
    return {name: service.get("security_opt") for name, service in services.items()}


def test_unset_variable_keeps_apparmor_unconfined_on_both_dashboard_services(tmp_path) -> None:
    options = _security_opts(tmp_path)

    for service in _DASHBOARD_SERVICES:
        assert options[service][0] == "apparmor:unconfined"


def test_empty_variable_is_treated_as_unset(tmp_path) -> None:
    options = _security_opts(tmp_path, "BUTLERS_DASHBOARD_APPARMOR_PROFILE=")

    for service in _DASHBOARD_SERVICES:
        assert options[service][0] == "apparmor:unconfined"


def test_variable_selects_the_scoped_profile_on_both_dashboard_services_only(tmp_path) -> None:
    default = _security_opts(tmp_path)
    selected = _security_opts(tmp_path, f"BUTLERS_DASHBOARD_APPARMOR_PROFILE={_PROFILE}")

    for service in _DASHBOARD_SERVICES:
        assert selected[service][0] == f"apparmor:{_PROFILE}"
        assert "systempaths=unconfined" in selected[service]
        assert any(item.startswith("seccomp:") for item in selected[service])
    others = set(default) - set(_DASHBOARD_SERVICES)
    assert others == set(selected) - set(_DASHBOARD_SERVICES)
    assert {name: selected[name] for name in others} == {name: default[name] for name in others}
