"""Compose boundary coverage for the isolated restore-drill executor.

REQ-database-security-006 requires the privileged recovery credential to be
mounted only into the db-only executor.  These are configuration tests: they
do not render or start a Compose stack and therefore cannot read a deployment
secret or mutate a live runtime.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIREWALL = _REPO_ROOT / "scripts" / "restore-drill-firewall.sh"
_COMPOSE_LAUNCHER = _REPO_ROOT / "scripts" / "compose.sh"


def _compose() -> dict:
    return yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _environment(service: dict) -> dict:
    environment = service.get("environment", {})
    assert isinstance(environment, dict)
    return environment


def _environment_keys(service: dict) -> set[str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return set(environment)
    assert isinstance(environment, list)
    return {entry.split("=", 1)[0] for entry in environment}


def test_restore_drill_executor_has_a_dedicated_database_network_and_private_secret() -> None:
    """REQ-database-security-006: the recovery credential has one narrow path."""
    compose = _compose()
    service = compose["services"]["restore-drill-executor"]

    assert service["networks"] == ["restore_drill_db"]
    assert compose["networks"]["restore_drill_db"] == {
        "driver": "bridge",
        "enable_ipv6": False,
    }
    assert "ports" not in service
    assert "expose" not in service
    assert "privileged" not in service
    assert "cap_add" not in service
    assert "security_opt" not in service
    assert "butlers_backups:/backups:ro" in service["volumes"]
    assert service["secrets"] == [
        {
            "source": "restore_drill_executor_password",
            "target": "restore_drill_executor_password",
            "mode": 0o400,
        }
    ]

    environment = _environment(service)
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in environment
    assert not any(key.startswith("POSTGRES_") for key in environment)
    assert "DATABASE_URL" not in environment
    assert service["entrypoint"][-1] == "butlers.jobs.restore_drill_executor"

    secret = compose["secrets"]["restore_drill_executor_password"]
    assert secret["file"].startswith("${RESTORE_DRILL_EXECUTOR_PASSWORD_FILE:")


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_restore_drill_firewall_default_denies_outbound_except_postgres(
    tmp_path: Path,
) -> None:
    """REQ-database-security-006: executor bridge traffic is allowlist-only."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    iptables_log = tmp_path / "iptables.log"

    _write_executable(
        bin_dir / "docker",
        "#!/bin/sh\nprintf '%s\\n' br-restore-drill-test\n",
    )
    _write_executable(
        bin_dir / "ip",
        "#!/bin/sh\nexit 0\n",
    )
    _write_executable(
        bin_dir / "iptables",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$IPTABLES_LOG"\n'
        "case \" $* \" in *' -nL '*) exit 1 ;; *' -C '*) exit 1 ;; esac\nexit 0\n",
    )

    env = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": "test",
        "RESTORE_DRILL_EXECUTOR_DB_HOST": "198.51.100.42",
        "RESTORE_DRILL_EXECUTOR_DB_PORT": "5432",
        "IPTABLES_LOG": str(iptables_log),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    completed = subprocess.run(
        [_FIREWALL],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    rules = iptables_log.read_text(encoding="utf-8")
    chain_match = re.search(r"-N (BTRL_RD_\d+)", rules)
    assert chain_match is not None
    chain = chain_match.group(1)
    assert f"-A {chain} -p tcp -d 198.51.100.42 --dport 5432 -j ACCEPT" in rules
    assert f"-A {chain} -j DROP" in rules
    assert f"-I DOCKER-USER 1 -i br-restore-drill-test -j {chain}" in rules

    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    assert launcher.index("create restore-drill-executor") < launcher.index(_FIREWALL.name)
    assert launcher.index(_FIREWALL.name) < launcher.index('"${CMD[@]}" up -d')
    assert "restore-drill executor remains stopped" in launcher


def test_restore_drill_firewall_rejects_unresolved_hostname_without_installing_rules(
    tmp_path: Path,
) -> None:
    """No DNS exception can silently widen this PostgreSQL-only network."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    iptables_called = tmp_path / "iptables-called"
    _write_executable(bin_dir / "docker", "#!/bin/sh\nexit 99\n")
    _write_executable(bin_dir / "ip", "#!/bin/sh\nexit 99\n")
    _write_executable(
        bin_dir / "iptables",
        '#!/bin/sh\ntouch "$IPTABLES_CALLED"\nexit 99\n',
    )

    completed = subprocess.run(
        [_FIREWALL],
        check=False,
        env={
            **os.environ,
            "RESTORE_DRILL_EXECUTOR_DB_HOST": "postgres.example.test",
            "IPTABLES_CALLED": str(iptables_called),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert not iptables_called.exists()
    assert "resolved IPv4" in completed.stderr


def test_private_executor_secret_is_absent_from_every_normal_runtime_service() -> None:
    """The secret mount belongs only to the deterministic executor service."""
    compose = _compose()

    for name, service in compose["services"].items():
        if name == "restore-drill-executor":
            continue
        assert "restore_drill_executor_password" not in repr(service.get("secrets", []))
        assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in _environment_keys(service)
