"""Compose boundary coverage for the isolated restore-drill executor.

REQ-database-security-006 requires the privileged recovery credential to be
mounted only into the db-only executor.  These are configuration tests: they
do not render or start a Compose stack and therefore cannot read a deployment
secret or mutate a live runtime.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from butlers.core.deploy import DEFAULT_COMPOSE_FILES

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIREWALL = _REPO_ROOT / "scripts" / "restore-drill-firewall.sh"
_FIREWALL_INSTALLER = _REPO_ROOT / "scripts" / "install_restore_drill_firewall_wrapper.sh"
_COMPOSE_LAUNCHER = _REPO_ROOT / "scripts" / "compose.sh"
_SCRIPTS_README = _REPO_ROOT / "scripts" / "README.md"
_BACKUP_RESTORE_DOC = _REPO_ROOT / "docs" / "operations" / "backup-restore.md"
_FIREWALL_WRAPPER = "/usr/local/libexec/butlers-restore-drill-firewall"
_CA_CONFIG_SOURCE = "restore_drill_executor_ca"
_CA_CONTAINER_PATH = "/run/configs/restore_drill_executor_ca.pem"
_BASE_COMPOSE_FILE = "docker-compose.yml"
_RESTORE_DRILL_COMPOSE_FILE = "docker-compose.restore-drill.yml"


def _compose(compose_file: str) -> dict:
    return yaml.safe_load((_REPO_ROOT / compose_file).read_text(encoding="utf-8"))


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


def _rendered_compose(*compose_files: str) -> dict:
    """Render selected Compose files without starting any containers."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is required to render this deployment contract")

    command = [docker, "compose"]
    for compose_file in compose_files:
        command.extend(["-f", compose_file])
    command.extend(["config", "--format", "json"])
    completed = subprocess.run(
        command,
        check=False,
        cwd=_REPO_ROOT,
        capture_output=True,
        env={
            **os.environ,
            "POSTGRES_HOST": "198.51.100.42",
            "POSTGRES_PASSWORD": "non-secret-test-password",
            "RESTORE_DRILL_EXECUTOR_DB_HOST": "postgres.example.test",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": "198.51.100.42",
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": "/tmp/restore-drill-test-secret",
            "RESTORE_DRILL_EXECUTOR_SSLROOTCERT_SOURCE_FILE": "/tmp/restore-drill-test-ca.pem",
        },
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _rendered_executor() -> dict:
    """Render the protected executor contract without starting containers."""
    return _rendered_compose(_BASE_COMPOSE_FILE, _RESTORE_DRILL_COMPOSE_FILE)["services"][
        "restore-drill-executor"
    ]


def test_direct_compose_render_omits_the_privileged_restore_executor() -> None:
    """Bare Compose must not be able to start the credentialed executor unfenced."""
    direct = _rendered_compose(_BASE_COMPOSE_FILE)

    assert direct["services"].get("restore-drill-executor") is None
    assert direct["networks"].get("restore_drill_db") is None
    assert direct.get("secrets", {}).get("restore_drill_executor_password") is None
    assert direct.get("configs", {}).get(_CA_CONFIG_SOURCE) is None


def test_supported_launchers_include_the_protected_restore_drill_compose_file() -> None:
    """Only launchers that install the firewall may include the executor overlay."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    protected_command = (
        "CMD=(docker compose -f docker-compose.yml -f docker-compose.restore-drill.yml)"
    )

    assert DEFAULT_COMPOSE_FILES == (_BASE_COMPOSE_FILE, _RESTORE_DRILL_COMPOSE_FILE)
    assert protected_command in launcher
    assert launcher.index(protected_command) < launcher.index(
        '"${CMD[@]}" create restore-drill-executor'
    )
    assert launcher.index(_FIREWALL_WRAPPER) < launcher.index('"${CMD[@]}" up -d')


def test_operator_guidance_keeps_the_protected_fragment_out_of_direct_compose() -> None:
    """Direct Compose guidance must describe the base-only fail-closed path."""
    compose = (_REPO_ROOT / _BASE_COMPOSE_FILE).read_text(encoding="utf-8")
    scripts_readme = _SCRIPTS_README.read_text(encoding="utf-8")
    backup_restore = _BACKUP_RESTORE_DOC.read_text(encoding="utf-8")

    assert "Direct Compose\n# uses this non-privileged base file only" in compose
    assert "Do not compose the protected\nfragment directly." in scripts_readme
    assert "a bare `docker compose up` fails\n  closed by omitting the executor" in backup_restore


def test_restore_drill_executor_has_a_dedicated_database_network_and_private_secret() -> None:
    """REQ-database-security-006: the recovery credential has one narrow path."""
    compose = _compose(_RESTORE_DRILL_COMPOSE_FILE)
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
    assert service["restart"] == "no"
    assert service["dns"] == ["127.0.0.1"]
    assert "butlers_backups:/backups:ro" in service["volumes"]
    assert service["secrets"] == [
        {
            "source": "restore_drill_executor_password",
            "target": "restore_drill_executor_password",
            "mode": 0o400,
        }
    ]
    assert service["configs"] == [
        {
            "source": _CA_CONFIG_SOURCE,
            "target": _CA_CONTAINER_PATH,
            "mode": 0o444,
        }
    ]

    environment = _environment(service)
    assert environment["RESTORE_DRILL_EXECUTOR_DB_HOST"].startswith(
        "${RESTORE_DRILL_EXECUTOR_DB_HOST:?"
    )
    assert "resolved PostgreSQL IPv4" not in environment["RESTORE_DRILL_EXECUTOR_DB_HOST"]
    assert service["extra_hosts"] == [
        "${RESTORE_DRILL_EXECUTOR_DB_HOST:?Run a supported launcher to retain the PostgreSQL TLS hostname}=${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST:?Run a supported launcher to resolve the PostgreSQL IPv4 firewall endpoint}"
    ]
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in environment
    assert environment["RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE"] == _CA_CONTAINER_PATH
    assert not any(key.startswith("POSTGRES_") for key in environment)
    assert "DATABASE_URL" not in environment
    assert service["entrypoint"][-1] == "butlers.jobs.restore_drill_executor"

    secret = compose["secrets"]["restore_drill_executor_password"]
    assert secret["file"].startswith("${RESTORE_DRILL_EXECUTOR_PASSWORD_FILE:")
    ca_config = compose["configs"][_CA_CONFIG_SOURCE]
    assert ca_config["file"] == (
        "${RESTORE_DRILL_EXECUTOR_SSLROOTCERT_SOURCE_FILE:-"
        "./deploy/restore-drill-ca-unconfigured.pem}"
    )


def test_rendered_executor_keeps_tls_host_but_has_only_loopback_dns() -> None:
    """Rendered Compose config cannot delegate resolver traffic off-container.

    This is deliberately a Compose rendering check rather than a text search:
    Docker receives a loopback-only DNS upstream while the PostgreSQL TLS name
    is resolved through the concrete /etc/hosts mapping.
    """
    service = _rendered_executor()

    assert service["dns"] == ["127.0.0.1"]
    assert service["extra_hosts"] == ["postgres.example.test=198.51.100.42"]
    assert service["restart"] == "no"
    assert service["environment"]["RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE"] == _CA_CONTAINER_PATH
    rendered_configs = {config["target"]: config for config in service["configs"]}
    assert rendered_configs[_CA_CONTAINER_PATH]["source"] == _CA_CONFIG_SOURCE


def test_restore_drill_firewall_dry_run_default_denies_forward_and_host_paths() -> None:
    """REQ-database-security-006: only the configured PostgreSQL route survives.

    The immutable wrapper's dry-run builds the actual iptables command plan
    without consulting Docker or changing the host.  This exercises argument
    validation and rule construction while proving that both forwarded traffic
    and executor-to-host/gateway traffic finish in a terminal default deny.
    """
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            "198.51.100.42",
            "--db-port",
            "5432",
            "--dry-run",
            "--bridge",
            "br-restore-drill-test",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    rules = completed.stdout
    forward_match = re.search(r"iptables -N (BTRL_RDF_\d+)", rules)
    input_match = re.search(r"iptables -N (BTRL_RDI_\d+)", rules)
    assert forward_match is not None
    assert input_match is not None
    forward_chain = forward_match.group(1)
    input_chain = input_match.group(1)

    accept_lines = [line for line in rules.splitlines() if "-j ACCEPT" in line]
    assert accept_lines == [
        f"iptables -A {forward_chain} -p tcp -d 198.51.100.42 --dport 5432 -j ACCEPT -m comment --comment butlers-restore-drill-postgres-only",
        f"iptables -A {input_chain} -p tcp -d 198.51.100.42 --dport 5432 -j ACCEPT -m comment --comment butlers-restore-drill-postgres-only",
    ]
    assert f"iptables -A {forward_chain} -j DROP" in rules
    assert f"iptables -I DOCKER-USER 1 -i br-restore-drill-test -j {forward_chain}" in rules
    assert f"iptables -A {input_chain} -j DROP" in rules
    assert f"iptables -I INPUT 1 -i br-restore-drill-test -j {input_chain}" in rules
    assert "127.0.0.11" not in rules
    assert "--dport 53" not in rules


def test_restore_drill_launcher_uses_only_fixed_root_wrapper_before_startup() -> None:
    """No passwordless sudo path may execute checkout-controlled firewall code."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    boundary = launcher[
        launcher.index("Create the executor and its dedicated network") : launcher.index(
            '"${CMD[@]}" up -d'
        )
    ]

    assert boundary.index("create restore-drill-executor") < boundary.index(_FIREWALL_WRAPPER)
    assert _FIREWALL.name not in boundary
    assert "sudo -n true" not in boundary
    normalized_boundary = " ".join(boundary.replace("\\\n", " ").split())
    assert f"sudo -n {_FIREWALL_WRAPPER} --project" in normalized_boundary
    assert '--db-host "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST}"' in boundary
    assert '--db-port "${RESTORE_DRILL_EXECUTOR_DB_PORT}"' in boundary


def test_restore_drill_launcher_stops_if_down_fails_before_create_wrapper_or_up(
    tmp_path: Path,
) -> None:
    """A failed stop leaves the credentialed executor untouched and unstarted."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# ── Swap: stop old containers, start new ones")
    end = launcher.index("# ── Apply egress firewall", start)
    swap_boundary = launcher[start:end]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    compose_probe = bin_dir / "compose-probe"
    compose_probe.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'compose %s\\n\' "$*" >> "$RESTORE_DRILL_LAUNCHER_CALLS"\n'
        'if [[ "$1" == down ]]; then exit 17; fi\n',
        encoding="utf-8",
    )
    compose_probe.chmod(0o755)
    sudo_probe = bin_dir / "sudo"
    sudo_probe.write_text(
        '#!/usr/bin/env bash\nprintf \'sudo %s\\n\' "$*" >> "$RESTORE_DRILL_LAUNCHER_CALLS"\n',
        encoding="utf-8",
    )
    sudo_probe.chmod(0o755)

    harness = "\n".join(
        [
            "set -euo pipefail",
            "CMD=(compose-probe)",
            "COMPOSE_PROJECT_NAME=restore-drill-test",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=198.51.100.42",
            "RESTORE_DRILL_EXECUTOR_DB_PORT=5432",
            "SCALE_ARGS=()",
            swap_boundary,
        ]
    )
    completed = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "RESTORE_DRILL_LAUNCHER_CALLS": str(calls),
        },
    )

    assert completed.returncode != 0
    assert calls.read_text(encoding="utf-8").splitlines() == ["compose down --remove-orphans"]


def test_firewall_wrapper_install_contract_is_root_owned_and_fixed_path() -> None:
    """The installer cannot be redirected to a checkout-controlled sudo target."""
    completed = subprocess.run(
        [_FIREWALL_INSTALLER, "--print-install-plan"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert _FIREWALL_WRAPPER in completed.stdout
    assert "root:root" in completed.stdout
    assert "0755" in completed.stdout


def test_restore_drill_firewall_rejects_unresolved_firewall_hostname_without_rules() -> None:
    """No DNS exception can silently widen this PostgreSQL-only network."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            "still-a-hostname.example.test",
            "--db-port",
            "5432",
            "--dry-run",
            "--bridge",
            "br-restore-drill-test",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "db-host" in completed.stderr


def test_restore_drill_firewall_rejects_untrusted_wrapper_arguments() -> None:
    """The elevated wrapper has no shell, environment, or project-name escape hatch."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test; id",
            "--db-host",
            "198.51.100.42",
            "--db-port",
            "5432",
            "--dry-run",
            "--bridge",
            "br-restore-drill-test",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "project" in completed.stderr


def test_private_executor_secret_is_absent_from_every_normal_runtime_service() -> None:
    """The secret mount belongs only to the deterministic executor service."""
    compose = _compose(_BASE_COMPOSE_FILE)

    for name, service in compose["services"].items():
        assert "restore_drill_executor_password" not in repr(service.get("secrets", []))
        assert _CA_CONFIG_SOURCE not in repr(service.get("configs", []))
        assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in _environment_keys(service)
