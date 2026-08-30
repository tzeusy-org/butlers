"""Rendered Compose contracts for Dashboard runtime-CLI sandbox support.

These checks only render checked-in Compose with synthetic non-secret inputs.
They never create or start a container, read a deployment secret, or change a
live Compose project.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = "docker-compose.yml"
_RESTORE_DRILL_COMPOSE_FILE = "docker-compose.restore-drill.yml"
_SANDBOX_SECCOMP_PROFILE = "deploy/seccomp/dashboard-runtime-cli-sandbox.json"
_DASHBOARD_SERVICES = ("dashboard-api", "dashboard-api-hotreload")


def _rendered_compose(tmp_path: Path) -> dict[str, object]:
    """Render default and hotreload Compose from synthetic, non-secret inputs only."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is required to render this deployment contract")

    env_file = tmp_path / "runtime-cli-sandbox-compose.env"
    env_file.write_text(
        "\n".join(
            (
                "POSTGRES_HOST=10.23.4.5",
                "POSTGRES_PASSWORD=non-secret-test-password",
                "COMPOSE_PROJECT_NAME=butlers-runtime-cli-sandbox-test",
                "",
            )
        ),
        encoding="utf-8",
    )
    docker_home = tmp_path / "docker-home"
    docker_home.mkdir()
    completed = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            _COMPOSE_FILE,
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
    rendered = json.loads(completed.stdout)
    assert isinstance(rendered, dict)
    return rendered


def _volume_references_docker_socket(volume: object) -> bool:
    if isinstance(volume, str):
        return "/var/run/docker.sock" in volume
    if isinstance(volume, dict):
        return volume.get("source") == "/var/run/docker.sock"
    return False


def test_render_helper_uses_only_a_dedicated_synthetic_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The render-only proof never imports ambient deployment environment values."""
    captured: dict[str, object] = {}

    monkeypatch.setenv("POSTGRES_PASSWORD", "ambient-deployment-secret")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout='{"services": {}}', stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert _rendered_compose(tmp_path) == {"services": {}}

    command = captured["command"]
    assert isinstance(command, list)
    env_file = Path(command[command.index("--env-file") + 1])
    assert env_file.read_text(encoding="utf-8") == (
        "POSTGRES_HOST=10.23.4.5\n"
        "POSTGRES_PASSWORD=non-secret-test-password\n"
        "COMPOSE_PROJECT_NAME=butlers-runtime-cli-sandbox-test\n"
    )
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] == {
        "PATH": os.defpath,
        "HOME": str(tmp_path / "docker-home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
    }


def _rendered_seccomp_path(value: str) -> Path:
    """Normalize Compose's relative-or-absolute seccomp render form."""
    assert value.startswith("seccomp:")
    candidate = Path(value.removeprefix("seccomp:"))
    return candidate if candidate.is_absolute() else _REPO_ROOT / candidate


def test_dashboard_variants_render_the_same_namespace_capable_security_policy(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: both Dashboard images can launch the same sandbox only."""
    profile = _REPO_ROOT / _SANDBOX_SECCOMP_PROFILE
    profile_document = json.loads(profile.read_text(encoding="utf-8"))
    rendered = _rendered_compose(tmp_path)
    services = rendered["services"]

    for service_name in _DASHBOARD_SERVICES:
        service = services[service_name]
        security_opt = set(service["security_opt"])
        assert {"apparmor:unconfined", "systempaths=unconfined"} <= security_opt
        seccomp_options = [item for item in security_opt if item.startswith("seccomp:")]
        assert len(seccomp_options) == 1
        assert _rendered_seccomp_path(seccomp_options[0]) == profile
        assert not service.get("privileged", False)
        assert "cap_add" not in service
        assert service.get("pid") != "host"
        assert not any(
            _volume_references_docker_socket(volume) for volume in service.get("volumes", [])
        )

    assert profile_document["defaultAction"] == "SCMP_ACT_ERRNO"
    unconditional_allowed_syscalls = {
        syscall
        for rule in profile_document["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW" and "includes" not in rule and "excludes" not in rule
        for syscall in rule["names"]
    }
    # Bubblewrap performs these exact namespace/bootstrap syscalls before it
    # gains the user-namespace capabilities which Moby's stock profile uses
    # for conditional mount rules.  The repository-owned profile therefore
    # permits this narrow launch set explicitly.
    assert {
        "clone",
        "close_range",
        "mount",
        "pidfd_open",
        "pidfd_send_signal",
        "pivot_root",
        "umount2",
    } <= unconditional_allowed_syscalls
    bootstrap_rules = [
        rule
        for rule in profile_document["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW"
        and "includes" not in rule
        and "excludes" not in rule
        and set(rule["names"]) <= {"clone", "mount", "pivot_root", "umount2", "unshare"}
    ]
    assert bootstrap_rules == [
        {
            "names": ["clone"],
            "action": "SCMP_ACT_ALLOW",
            "args": [{"index": 0, "value": 0x3C020011, "op": "SCMP_CMP_EQ"}],
        },
        {
            "names": ["unshare"],
            "action": "SCMP_ACT_ALLOW",
            "args": [{"index": 0, "value": 0x10000000, "op": "SCMP_CMP_EQ"}],
        },
        {"names": ["mount", "pivot_root", "umount2"], "action": "SCMP_ACT_ALLOW"},
    ]

    for service_name, service in services.items():
        if service_name in _DASHBOARD_SERVICES:
            continue
        security_opt = set(service.get("security_opt", []))
        assert "systempaths=unconfined" not in security_opt
        assert not any(
            item.startswith("seccomp:") and _rendered_seccomp_path(item) == profile
            for item in security_opt
        )


def test_runtime_cli_policy_does_not_expand_the_restore_drill_overlay() -> None:
    """REQ-database-security-006: Dashboard sandbox policy leaves the executor overlay alone."""
    overlay = yaml.safe_load((_REPO_ROOT / _RESTORE_DRILL_COMPOSE_FILE).read_text(encoding="utf-8"))
    services = overlay["services"]

    assert set(services) == {"restore-drill-executor", "restore-drill-postgres-proxy"}
    assert all(_SANDBOX_SECCOMP_PROFILE not in str(service) for service in services.values())
    assert _SANDBOX_SECCOMP_PROFILE not in (_REPO_ROOT / _RESTORE_DRILL_COMPOSE_FILE).read_text(
        encoding="utf-8"
    )
