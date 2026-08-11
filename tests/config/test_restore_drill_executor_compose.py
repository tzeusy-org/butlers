"""Compose boundary coverage for the isolated restore-drill executor.

REQ-database-security-006 requires the privileged recovery credential to be
mounted only into the db-only executor. These tests render Compose only; they
never start a stack, read a deployment secret, or mutate a live runtime.
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
from butlers.jobs.restore_drill_executor import load_restore_drill_executor_config
from tests.restore_drill_endpoint_policy import (
    EXECUTOR_DNS_IDENTITIES_ACCEPTED,
    EXECUTOR_NUMERIC_IDENTITIES_REJECTED,
    LEGACY_NUMERIC_IPV4_REJECTED,
    NONCANONICAL_PORT_REJECTED,
    REMOTE_IPV4_ACCEPTED,
    REMOTE_IPV4_REJECTED,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIREWALL = _REPO_ROOT / "scripts" / "restore-drill-firewall.sh"
_FIREWALL_INSTALLER = _REPO_ROOT / "scripts" / "install_restore_drill_firewall_wrapper.sh"
_FIREWALL_SUDOERS = _REPO_ROOT / "scripts" / "restore-drill-firewall.sudoers"
_COMPOSE_LAUNCHER = _REPO_ROOT / "scripts" / "compose.sh"
_INSPECT_HELPER = _REPO_ROOT / "scripts" / "restore-drill-compose-inspect.sh"
_SCRIPTS_README = _REPO_ROOT / "scripts" / "README.md"
_BACKUP_RESTORE_DOC = _REPO_ROOT / "docs" / "operations" / "backup-restore.md"
_DOCKER_DEPLOYMENT_DOC = _REPO_ROOT / "docs" / "operations" / "docker-deployment.md"
_TROUBLESHOOTING_DOC = _REPO_ROOT / "docs" / "operations" / "troubleshooting.md"
_RESTORE_DRILL_CHANGE = _REPO_ROOT / "openspec" / "changes" / "restore-drill-recovery-truthfulness"
_RESTORE_DRILL_DESIGN = _RESTORE_DRILL_CHANGE / "design.md"
_RESTORE_DRILL_DATABASE_SECURITY_SPEC = (
    _RESTORE_DRILL_CHANGE / "specs" / "database-security" / "spec.md"
)
_RESTORE_DRILL_DEPLOYMENT_HARDENING_SPEC = (
    _RESTORE_DRILL_CHANGE / "specs" / "deployment-hardening" / "spec.md"
)
_RESTORE_DRILL_TASKS = _RESTORE_DRILL_CHANGE / "tasks.md"
_FIREWALL_WRAPPER = "/usr/local/libexec/butlers-restore-drill-firewall"
_CA_CONFIG_SOURCE = "restore_drill_executor_ca"
_CA_CONTAINER_PATH = "/run/configs/restore_drill_executor_ca.pem"
_BASE_COMPOSE_FILE = "docker-compose.yml"
_RESTORE_DRILL_COMPOSE_FILE = "docker-compose.restore-drill.yml"
_DRY_RUN_NETWORK_ARGS = [
    "--bridge",
    "br-restore-drill-test",
    "--executor-bridge",
    "br-restore-drill-executor-test",
    "--relay-ip",
    "172.30.0.2",
]


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


def _rendered_compose(*compose_files: str, env_overrides: dict[str, str] | None = None) -> dict:
    """Render selected Compose files without starting any containers."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is required to render this deployment contract")

    command = [docker, "compose"]
    for compose_file in compose_files:
        command.extend(["-f", compose_file])
    command.extend(["config", "--format", "json"])
    environment = {
        **os.environ,
        "POSTGRES_HOST": "10.23.4.5",
        "POSTGRES_PASSWORD": "non-secret-test-password",
        "COMPOSE_PROJECT_NAME": "butlers-test",
        "RESTORE_DRILL_EXECUTOR_DB_HOST": "postgres.example.test",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": "10.23.4.5",
        "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": "/tmp/restore-drill-test-secret",
        "RESTORE_DRILL_EXECUTOR_SSLROOTCERT_SOURCE_FILE": "/tmp/restore-drill-test-ca.pem",
    }
    environment.update(env_overrides or {})
    completed = subprocess.run(
        command,
        check=False,
        cwd=_REPO_ROOT,
        capture_output=True,
        env=environment,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_direct_compose_render_omits_the_privileged_restore_executor() -> None:
    """Bare Compose must not be able to start the credentialed executor unfenced."""
    direct = _rendered_compose(_BASE_COMPOSE_FILE)

    assert direct["services"].get("restore-drill-executor") is None
    assert direct["networks"].get("restore_drill_db") is None
    assert direct.get("secrets", {}).get("restore_drill_executor_password") is None
    assert direct.get("configs", {}).get(_CA_CONFIG_SOURCE) is None


def test_direct_merged_compose_keeps_the_executor_on_an_internal_relay_network() -> None:
    """A direct render proves attachment isolation, not prepared egress policy."""
    merged = _rendered_compose(_BASE_COMPOSE_FILE, _RESTORE_DRILL_COMPOSE_FILE)
    executor = merged["services"]["restore-drill-executor"]
    relay = merged["services"]["restore-drill-postgres-proxy"]

    assert executor["networks"] == {"restore_drill_executor": None}
    assert "restore_drill_db" not in executor["networks"]
    assert merged["networks"]["restore_drill_executor"]["internal"] is True
    internal_members = {
        name
        for name, service in merged["services"].items()
        if "restore_drill_executor" in service.get("networks", {})
    }
    assert internal_members == {"restore-drill-executor", "restore-drill-postgres-proxy"}
    assert "ports" not in executor
    assert "expose" not in executor
    assert "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST" not in executor["environment"]
    assert (
        executor["environment"]["RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE"] == "unprepared"
    )
    assert not any(key.startswith("POSTGRES_") for key in executor["environment"])
    assert "DATABASE_URL" not in executor["environment"]
    assert relay["networks"]["restore_drill_executor"]["aliases"] == ["postgres.example.test"]
    assert "restore_drill_db" in relay["networks"]
    assert "secrets" not in relay
    assert "configs" not in relay
    assert "volumes" not in relay
    assert "ports" not in relay
    assert "expose" not in relay
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in _environment_keys(relay)


def test_supported_launchers_include_the_protected_restore_drill_compose_file() -> None:
    """Only launchers that install the firewall may include the executor overlay."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    protected_command = (
        "CMD=(docker compose -f docker-compose.yml -f docker-compose.restore-drill.yml)"
    )

    assert DEFAULT_COMPOSE_FILES == (_BASE_COMPOSE_FILE, _RESTORE_DRILL_COMPOSE_FILE)
    assert protected_command in launcher
    assert launcher.index(protected_command) < launcher.index(
        '"${CMD[@]}" create restore-drill-postgres-proxy restore-drill-executor'
    )
    assert launcher.index(_FIREWALL_WRAPPER) < launcher.index('"${CMD[@]}" up -d')


def test_operator_guidance_keeps_the_protected_fragment_out_of_direct_compose() -> None:
    """Guidance must retain the fail-closed launch and read-only inspection boundary."""
    compose = (_REPO_ROOT / _BASE_COMPOSE_FILE).read_text(encoding="utf-8")
    scripts_readme = _SCRIPTS_README.read_text(encoding="utf-8")
    backup_restore = _BACKUP_RESTORE_DOC.read_text(encoding="utf-8")
    docker_deployment = _DOCKER_DEPLOYMENT_DOC.read_text(encoding="utf-8")
    troubleshooting = _TROUBLESHOOTING_DOC.read_text(encoding="utf-8")
    endpoint_contract_surfaces = (
        scripts_readme,
        backup_restore,
        _RESTORE_DRILL_DESIGN.read_text(encoding="utf-8"),
        _RESTORE_DRILL_DATABASE_SECURITY_SPEC.read_text(encoding="utf-8"),
        _RESTORE_DRILL_DEPLOYMENT_HARDENING_SPEC.read_text(encoding="utf-8"),
        _RESTORE_DRILL_TASKS.read_text(encoding="utf-8"),
    )

    assert "A bare direct\n# Compose invocation with this non-privileged base file" in compose
    assert "restore-drill-compose-inspect.sh" in scripts_readme
    assert "same-boot manual down/recreate cannot" in backup_restore
    assert "--prepare-executor-capability-v1" in backup_restore
    assert "restore-drill-compose-inspect.sh" in backup_restore
    assert "canonical dotted-decimal remote-unicast" in backup_restore
    assert "canonical ASCII-decimal port" in backup_restore
    assert "inet_aton" in backup_restore
    assert "MUST be an untrimmed DNS hostname" in backup_restore
    for surface in endpoint_contract_surfaces:
        normalized_surface = " ".join(surface.split())
        assert "`KEY=value` or `export KEY=value`" in normalized_surface
        assert "optional leading spaces/tabs" in normalized_surface
        assert (
            "Other Bash command forms are outside this pre-source endpoint-literal grammar"
            in normalized_surface
        )
        assert "without trimming or reinterpretation" in normalized_surface
    assert "two-slot" in backup_restore
    assert "internal network alone does not deny bridge-gateway or host traffic" in backup_restore
    assert (
        "executor bridge policy is default-denied except for\n  its created relay peer"
        in backup_restore
    )
    assert "Rendered config is a\nread-only inspection artifact" in scripts_readme
    assert "executor bridge policy" in scripts_readme
    assert "rendered Compose output is inspection only" in backup_restore
    assert "restore-drill-compose-inspect.sh ps" in docker_deployment
    assert "docker compose logs <butler-name> --tail=100" in troubleshooting
    assert "restore-drill-compose-inspect.sh" not in troubleshooting


def test_restore_drill_inspection_helper_allows_only_read_only_merged_commands(
    tmp_path: Path,
) -> None:
    """Operator inspection must include the overlay but never invoke Compose `up`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    docker_probe = bin_dir / "docker"
    docker_probe.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$RESTORE_DRILL_INSPECT_CALLS"\n',
        encoding="utf-8",
    )
    docker_probe.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RESTORE_DRILL_INSPECT_CALLS": str(calls),
    }

    allowed = [
        subprocess.run(
            [_INSPECT_HELPER, *arguments],
            cwd=_REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        for arguments in (
            ("config", "--services"),
            ("ps",),
            ("logs", "restore-drill-executor", "--tail=100"),
        )
    ]
    rejected = subprocess.run(
        [_INSPECT_HELPER, "up", "-d"],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert all(result.returncode == 0 for result in allowed)
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "compose -f docker-compose.yml -f docker-compose.restore-drill.yml config --services",
        "compose -f docker-compose.yml -f docker-compose.restore-drill.yml ps",
        "compose -f docker-compose.yml -f docker-compose.restore-drill.yml logs restore-drill-executor --tail=100",
    ]
    assert rejected.returncode != 0
    assert "read-only" in rejected.stderr


def test_restore_drill_executor_has_an_internal_relay_network_and_private_secret() -> None:
    """REQ-database-security-006: the recovery credential has one narrow path."""
    compose = _compose(_RESTORE_DRILL_COMPOSE_FILE)
    service = compose["services"]["restore-drill-executor"]
    relay = compose["services"]["restore-drill-postgres-proxy"]

    assert service["networks"] == ["restore_drill_executor"]
    assert compose["networks"]["restore_drill_db"] == {
        "driver": "bridge",
        "enable_ipv6": False,
    }
    assert compose["networks"]["restore_drill_executor"] == {
        "driver": "bridge",
        "internal": True,
        "enable_ipv6": False,
    }
    assert "ports" not in service
    assert "expose" not in service
    assert "privileged" not in service
    assert "cap_add" not in service
    assert "security_opt" not in service
    assert service["restart"] == "no"
    assert "dns" not in service
    assert "extra_hosts" not in service
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
    assert "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST" not in environment
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in environment
    assert environment["RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE"] == _CA_CONTAINER_PATH
    assert environment["RESTORE_DRILL_EXECUTOR_FIREWALL_PROJECT"] == (
        "${COMPOSE_PROJECT_NAME:?Run a supported launcher to prepare the executor firewall}"
    )
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

    assert relay["entrypoint"] == ["python", "/app/scripts/restore_drill_tcp_proxy.py"]
    assert relay["networks"] == {
        "restore_drill_db": None,
        "restore_drill_executor": {
            "aliases": [
                "${RESTORE_DRILL_EXECUTOR_DB_HOST:?Run a supported launcher to provide the PostgreSQL TLS DNS hostname}"
            ]
        },
    }
    relay_environment = _environment(relay)
    assert relay_environment == {
        "RESTORE_DRILL_PROXY_DB_HOST": "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST:?Run a supported launcher to provide the resolved PostgreSQL IPv4 endpoint}",
        "RESTORE_DRILL_PROXY_DB_PORT": "${RESTORE_DRILL_EXECUTOR_DB_PORT:-5432}",
    }
    assert "secrets" not in relay
    assert "configs" not in relay
    assert "volumes" not in relay
    assert "ports" not in relay
    assert "expose" not in relay
    assert not any(key.startswith("POSTGRES_") for key in relay_environment)
    assert "DATABASE_URL" not in relay_environment
    assert {
        "type": "bind",
        "source": "/run/butlers/restore-drill-firewall",
        "target": "/run/butlers/restore-drill-firewall",
        "read_only": True,
        "bind": {"create_host_path": False},
    } in service["volumes"]


def test_rendered_executor_keeps_tls_host_only_through_the_internal_relay() -> None:
    """The TLS identity resolves to the relay, never a direct external route."""
    rendered = _rendered_compose(_BASE_COMPOSE_FILE, _RESTORE_DRILL_COMPOSE_FILE)
    service = rendered["services"]["restore-drill-executor"]
    relay = rendered["services"]["restore-drill-postgres-proxy"]

    assert service["networks"] == {"restore_drill_executor": None}
    assert "dns" not in service
    assert "extra_hosts" not in service
    assert service["restart"] == "no"
    assert "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST" not in service["environment"]
    assert service["environment"]["RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE"] == _CA_CONTAINER_PATH
    rendered_configs = {config["target"]: config for config in service["configs"]}
    assert rendered_configs[_CA_CONTAINER_PATH]["source"] == _CA_CONFIG_SOURCE
    assert relay["networks"] == {
        "restore_drill_db": None,
        "restore_drill_executor": {"aliases": ["postgres.example.test"]},
    }
    assert relay["environment"] == {
        "RESTORE_DRILL_PROXY_DB_HOST": "10.23.4.5",
        "RESTORE_DRILL_PROXY_DB_PORT": "5432",
    }


def test_rendered_numeric_executor_identity_fails_before_the_executor_can_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A numeric Compose alias cannot become an executor direct-route host."""
    rendered = _rendered_compose(
        _BASE_COMPOSE_FILE,
        _RESTORE_DRILL_COMPOSE_FILE,
        env_overrides={"RESTORE_DRILL_EXECUTOR_DB_HOST": "10.23.4.5"},
    )
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    executor_environment = rendered["services"]["restore-drill-executor"]["environment"]
    assert executor_environment["RESTORE_DRILL_EXECUTOR_DB_HOST"] == "10.23.4.5"
    monkeypatch.setenv(
        "RESTORE_DRILL_EXECUTOR_DB_HOST",
        executor_environment["RESTORE_DRILL_EXECUTOR_DB_HOST"],
    )
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))

    with pytest.raises(ValueError, match="DNS hostname"):
        load_restore_drill_executor_config()


def test_rendered_relay_and_executor_keep_a_nondefault_postgres_port_aligned() -> None:
    """The executor's internal TLS dial and relay's remote dial share one valid port."""
    rendered = _rendered_compose(
        _BASE_COMPOSE_FILE,
        _RESTORE_DRILL_COMPOSE_FILE,
        env_overrides={"RESTORE_DRILL_EXECUTOR_DB_PORT": "5544"},
    )

    assert (
        rendered["services"]["restore-drill-executor"]["environment"][
            "RESTORE_DRILL_EXECUTOR_DB_PORT"
        ]
        == "5544"
    )
    assert (
        rendered["services"]["restore-drill-postgres-proxy"]["environment"][
            "RESTORE_DRILL_PROXY_DB_PORT"
        ]
        == "5544"
    )


def test_restore_drill_firewall_dry_run_fences_relay_and_executor_bridges() -> None:
    """REQ-database-security-006: relay egress and executor peers default deny.

    The immutable wrapper's dry-run builds the actual iptables command plan
    without consulting Docker or changing the host.  The two distinct bridge
    inputs model the created relay egress network and the executor-only
    network: the former permits only PostgreSQL, while the latter permits only
    the created relay endpoint and denies host/gateway traffic entirely.
    """
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--dry-run",
            "--bridge",
            "br-restore-drill-test",
            "--executor-bridge",
            "br-restore-drill-executor-test",
            "--relay-ip",
            "172.30.0.2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    rules = completed.stdout
    forward_match = re.search(r"iptables -N (BTRL_RDF_\d+)", rules)
    input_match = re.search(r"iptables -N (BTRL_RDI_\d+)", rules)
    executor_forward_match = re.search(r"iptables -N (BTRL_RDFE_\d+)", rules)
    executor_input_match = re.search(r"iptables -N (BTRL_RDIE_\d+)", rules)
    assert forward_match is not None
    assert input_match is not None
    assert executor_forward_match is not None
    assert executor_input_match is not None
    forward_chain = forward_match.group(1)
    input_chain = input_match.group(1)
    executor_forward_chain = executor_forward_match.group(1)
    executor_input_chain = executor_input_match.group(1)

    accept_lines = [line for line in rules.splitlines() if "-j ACCEPT" in line]
    assert accept_lines == [
        f"iptables -A {forward_chain} -p tcp -d 10.23.4.5 --dport 5432 -j ACCEPT -m comment --comment butlers-restore-drill-postgres-only",
        f"iptables -A {input_chain} -p tcp -d 10.23.4.5 --dport 5432 -j ACCEPT -m comment --comment butlers-restore-drill-postgres-only",
        f"iptables -A {executor_forward_chain} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        f"iptables -A {executor_forward_chain} -p tcp -d 172.30.0.2 --dport 5432 -j ACCEPT -m comment --comment butlers-restore-drill-relay-only",
    ]
    assert f"iptables -A {forward_chain} -j DROP" in rules
    assert f"iptables -I DOCKER-USER 1 -i br-restore-drill-test -j {forward_chain}" in rules
    assert f"iptables -A {input_chain} -j DROP" in rules
    assert f"iptables -I INPUT 1 -i br-restore-drill-test -j {input_chain}" in rules
    assert f"iptables -A {executor_forward_chain} -j DROP" in rules
    assert (
        f"iptables -I DOCKER-USER 1 -i br-restore-drill-executor-test -j {executor_forward_chain}"
        in rules
    )
    assert f"iptables -A {executor_input_chain} -j DROP" in rules
    assert (
        f"iptables -I INPUT 1 -i br-restore-drill-executor-test -j {executor_input_chain}" in rules
    )
    executor_rules = [
        line
        for line in rules.splitlines()
        if executor_forward_chain in line or executor_input_chain in line
    ]
    assert all("10.23.4.5" not in line for line in executor_rules)
    assert all("--dport 53" not in line for line in executor_rules)
    assert "127.0.0.11" not in rules
    assert "--dport 53" not in rules


def test_restore_drill_firewall_writes_capability_only_after_gated_policy_success(
    tmp_path: Path,
) -> None:
    """The real wrapper writes its marker only after every policy command succeeds."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capability_directory = tmp_path / "restore-drill-firewall"
    preparation_path = capability_directory / "restore-drill-test.executor-preparation-v1"
    capability_path = capability_directory / "restore-drill-test.executor-capability-v1"
    firewall_source = _FIREWALL.read_text(encoding="utf-8")
    production_path = "PATH=/usr/sbin:/usr/bin:/sbin:/bin"
    production_capability_directory = (
        'readonly EXECUTOR_CAPABILITY_DIRECTORY="/run/butlers/restore-drill-firewall"'
    )
    assert firewall_source.count(production_path) == 1
    assert firewall_source.count(production_capability_directory) == 1
    firewall = tmp_path / "restore-drill-firewall.sh"
    firewall.write_text(
        firewall_source.replace(
            production_path, f"PATH={fake_bin}:/usr/sbin:/usr/bin:/sbin:/bin"
        ).replace(
            production_capability_directory,
            f'readonly EXECUTOR_CAPABILITY_DIRECTORY="{capability_directory}"',
        ),
        encoding="utf-8",
    )
    firewall.chmod(0o755)

    executor_id = "1" * 64
    relay_id = "2" * 64
    executor_network_id = "3" * 64
    relay_network_id = "4" * 64
    iptables_log = tmp_path / "iptables.log"

    (fake_bin / "install").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1" == -d ]]; then\n'
        '  mkdir -p "${!#}"\n'
        '  chmod 0711 "${!#}"\n'
        "  exit 0\n"
        "fi\n"
        'exec /usr/bin/install "$@"\n',
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "ip").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "iptables").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ -e "$FAKE_CAPABILITY_PATH" ]]; then\n'
        "  echo 'capability existed before policy completed' >&2\n"
        "  exit 91\n"
        "fi\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_IPTABLES_LOG"\n'
        'if [[ "$1" == -nL || "$1" == -C ]]; then exit 1; fi\n'
        'if [[ "${FAKE_IPTABLES_FAIL_FINAL:-}" == 1 && "$*" == *BTRL_RDIE_* && "$*" == *\'-j DROP\'* ]]; then\n'
        "  exit 73\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1" == ps ]]; then\n'
        '  if [[ "$*" == *restore-drill-executor* ]]; then\n'
        "    printf '%s\\n' \"$FAKE_EXECUTOR_ID\"\n"
        "  else\n"
        "    printf '%s\\n' \"$FAKE_RELAY_ID\"\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == network && "$2" == inspect ]]; then\n'
        '  network="$3"\n'
        '  template="$5"\n'
        '  case "$template" in\n'
        '    *bridge.name*) [[ "$network" == *_restore_drill_db ]] && echo br-relay || echo br-executor ;;\n'
        "    *Gateway*) echo 172.30.0.1 ;;\n"
        '    *Id*) [[ "$network" == *_restore_drill_db ]] && echo "$FAKE_RELAY_NETWORK_ID" || echo "$FAKE_EXECUTOR_NETWORK_ID" ;;\n'
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == inspect ]]; then\n'
        '  template="$3"\n'
        '  container_id="$4"\n'
        '  if [[ "$template" == *NetworkSettings.Networks* ]]; then\n'
        '    [[ "$container_id" == "$FAKE_EXECUTOR_ID" ]] && echo 172.30.0.3 || echo 172.30.0.2\n'
        '  elif [[ "$template" == *Config.Env* ]]; then\n'
        '    nonce=$(awk -F= \'$1 == "nonce" { print $2 }\' "$FAKE_PREPARATION_PATH")\n'
        "    printf 'RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE=%s\\n' \"$nonce\"\n"
        "    printf 'RESTORE_DRILL_EXECUTOR_FIREWALL_PROJECT=restore-drill-test\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 81\n",
        encoding="utf-8",
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)

    environment = {
        **os.environ,
        "FAKE_CAPABILITY_PATH": str(capability_path),
        "FAKE_EXECUTOR_ID": executor_id,
        "FAKE_RELAY_ID": relay_id,
        "FAKE_EXECUTOR_NETWORK_ID": executor_network_id,
        "FAKE_RELAY_NETWORK_ID": relay_network_id,
        "FAKE_IPTABLES_LOG": str(iptables_log),
        "FAKE_PREPARATION_PATH": str(preparation_path),
    }
    prepare = subprocess.run(
        [firewall, "--prepare-executor-capability-v1", "--project", "restore-drill-test"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert prepare.returncode == 0, prepare.stderr
    nonce = prepare.stdout.strip()
    assert re.fullmatch(r"[a-f0-9]{64}", nonce)
    assert preparation_path.is_file()
    assert not capability_path.exists()

    failed_apply = subprocess.run(
        [
            firewall,
            "--project",
            "restore-drill-test",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--require-executor-capability-v1",
        ],
        check=False,
        capture_output=True,
        env={**environment, "FAKE_IPTABLES_FAIL_FINAL": "1"},
        text=True,
    )

    assert failed_apply.returncode != 0
    assert not capability_path.exists()
    assert preparation_path.is_file()

    applied = subprocess.run(
        [
            firewall,
            "--project",
            "restore-drill-test",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--require-executor-capability-v1",
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert applied.returncode == 0, applied.stderr
    assert not preparation_path.exists()
    capability = capability_path.read_text(encoding="utf-8")
    assert f"nonce={nonce}" in capability
    assert f"executor_container_id={executor_id}" in capability
    assert f"executor_network_id={executor_network_id}" in capability
    assert "executor_ip=172.30.0.3" in capability
    assert "executor_gateway=172.30.0.1" in capability
    assert f"relay_container_id={relay_id}" in capability
    assert f"relay_network_id={relay_network_id}" in capability
    assert "relay_ip=172.30.0.2" in capability
    assert iptables_log.read_text(encoding="utf-8")


def test_restore_drill_launcher_uses_only_fixed_root_wrapper_before_startup() -> None:
    """No passwordless sudo path may execute checkout-controlled firewall code."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    boundary = launcher[
        launcher.index("Create the relay and executor without starting either") : launcher.index(
            '"${CMD[@]}" up -d'
        )
    ]

    assert boundary.index(
        "create restore-drill-postgres-proxy restore-drill-executor"
    ) < boundary.index(_FIREWALL_WRAPPER)
    assert _FIREWALL.name not in boundary
    assert "sudo -n true" not in boundary
    normalized_boundary = " ".join(boundary.replace("\\\n", " ").split())
    assert f"sudo -n {_FIREWALL_WRAPPER} --project" in normalized_boundary
    assert '--db-host "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST}"' in boundary
    assert '--db-port "${RESTORE_DRILL_EXECUTOR_DB_PORT}"' in boundary
    assert "--require-executor-capability-v1" in boundary


def test_restore_drill_launcher_rejects_an_old_wrapper_before_create_or_up(
    tmp_path: Path,
) -> None:
    """A wrapper that accepts the old three-argument form cannot start the executor."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# ── Swap: stop old containers, start new ones")
    end = launcher.index("# ── Apply egress firewall", start)
    swap_boundary = launcher[start:end]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    compose_probe = bin_dir / "compose-probe"
    compose_probe.write_text(
        '#!/usr/bin/env bash\nprintf \'compose %s\\n\' "$*" >> "$RESTORE_DRILL_LAUNCHER_CALLS"\n',
        encoding="utf-8",
    )
    compose_probe.chmod(0o755)
    sudo_probe = bin_dir / "sudo"
    sudo_probe.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'sudo %s\\n\' "$*" >> "$RESTORE_DRILL_LAUNCHER_CALLS"\n'
        'if [[ "$*" == *"--prepare-executor-capability-v1"* ]]; then exit 42; fi\n',
        encoding="utf-8",
    )
    sudo_probe.chmod(0o755)

    harness = "\n".join(
        [
            "set -euo pipefail",
            "CMD=(compose-probe)",
            "COMPOSE_PROJECT_NAME=restore-drill-test",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5",
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
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "compose down --remove-orphans",
        "sudo -n /usr/local/libexec/butlers-restore-drill-firewall "
        "--prepare-executor-capability-v1 --project restore-drill-test",
    ]


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
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5",
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


@pytest.mark.parametrize("source_name", ("POSTGRES_HOST", "RESTORE_DRILL_EXECUTOR_DB_HOST"))
@pytest.mark.parametrize("numeric_host", EXECUTOR_NUMERIC_IDENTITIES_REJECTED)
def test_restore_drill_launcher_rejects_numeric_executor_identity_before_compose(
    source_name: str, numeric_host: str
) -> None:
    """No numeric literal may bypass Docker DNS and reach the executor."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]

    env = {**os.environ, source_name: numeric_host}
    if source_name != "POSTGRES_HOST":
        env["POSTGRES_HOST"] = "postgres.example.test"
    else:
        env.pop("RESTORE_DRILL_EXECUTOR_DB_HOST", None)
    env.pop("RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST", None)
    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "DNS hostname" in completed.stderr


@pytest.mark.parametrize(
    "connection_host",
    (
        f"{'a' * 64}.example.test",
        f"{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 62}",
    ),
)
def test_restore_drill_launcher_rejects_dns_identities_outside_canonical_lengths(
    connection_host: str,
) -> None:
    """Shell validation must match the executor/deploy DNS label and total bounds."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "POSTGRES_HOST": connection_host,
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": "10.23.4.5",
        },
        text=True,
    )

    assert completed.returncode != 0
    assert "DNS hostname" in completed.stderr


@pytest.mark.parametrize("connection_host", EXECUTOR_DNS_IDENTITIES_ACCEPTED)
@pytest.mark.parametrize("remote_ipv4", REMOTE_IPV4_ACCEPTED)
def test_restore_drill_launcher_accepts_dns_identity_with_a_separate_remote_firewall_target(
    connection_host: str, remote_ipv4: str
) -> None:
    """Only the relay target, not the executor TLS identity, may be numeric."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]
    env = {
        **os.environ,
        "POSTGRES_HOST": connection_host,
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": remote_ipv4,
    }
    env.pop("RESTORE_DRILL_EXECUTOR_DB_HOST", None)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"Restore-drill endpoint: {connection_host}:" in completed.stdout
    assert f"relay firewall IPv4 {remote_ipv4}" in completed.stdout


def test_restore_drill_launcher_rejects_a_dns_host_that_resolves_to_loopback(
    tmp_path: Path,
) -> None:
    """A TLS hostname resolving to localhost must not make the relay recurse."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    getent = bin_dir / "getent"
    getent.write_text("#!/usr/bin/env bash\nprintf '127.0.0.1\\n'\n", encoding="utf-8")
    getent.chmod(0o755)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "POSTGRES_HOST": "localhost",
        },
        text=True,
    )

    assert completed.returncode != 0
    assert "remote IPv4" in completed.stderr or "localhost" in completed.stderr


def test_restore_drill_launcher_rejects_localhost_with_a_remote_firewall_override() -> None:
    """A safe override cannot turn localhost into an internal TLS relay alias."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]
    env = {
        **os.environ,
        "POSTGRES_HOST": "localhost",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": "10.23.4.5",
    }
    env.pop("RESTORE_DRILL_EXECUTOR_DB_HOST", None)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "localhost" in completed.stderr


@pytest.mark.parametrize(
    "noncanonical_ipv4",
    ["010.23.4.5", "198.022.001.001", "192.037.196.1", *LEGACY_NUMERIC_IPV4_REJECTED],
)
def test_restore_drill_launcher_rejects_noncanonical_numeric_host_before_dns_fallback(
    tmp_path: Path, noncanonical_ipv4: str
) -> None:
    """A dotted numeric literal cannot bypass the parser by becoming a DNS name."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    getent_calls = tmp_path / "getent-calls"
    getent = bin_dir / "getent"
    getent.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$RESTORE_DRILL_GETENT_CALLS"\n'
        "printf '8.23.4.5\\n'\n",
        encoding="utf-8",
    )
    getent.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "POSTGRES_HOST": noncanonical_ipv4,
        "RESTORE_DRILL_GETENT_CALLS": str(getent_calls),
    }
    env.pop("RESTORE_DRILL_EXECUTOR_DB_HOST", None)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "DNS hostname" in completed.stderr
    assert not getent_calls.exists()


@pytest.mark.parametrize("invalid_port", ["0", "65536", "not-a-port", *NONCANONICAL_PORT_REJECTED])
def test_restore_drill_launcher_rejects_invalid_port_before_compose(invalid_port: str) -> None:
    """An invalid executor port must fail before lifecycle commands can begin."""
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# The restore-drill executor has an internal-only network")
    end = launcher.index("# ── Mode-dependent configuration", start)
    endpoint_boundary = launcher[start:end]
    env = {
        **os.environ,
        "POSTGRES_HOST": "postgres.example.test",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": "10.23.4.5",
        "RESTORE_DRILL_EXECUTOR_DB_PORT": invalid_port,
    }
    env.pop("RESTORE_DRILL_EXECUTOR_DB_HOST", None)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "1..65535" in completed.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("POSTGRES_HOST", "10.23.4.5 "),
        ("POSTGRES_PORT", "5432 "),
        ("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test "),
        ("RESTORE_DRILL_EXECUTOR_DB_PORT", "5432 "),
        ("RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST", "10.23.4.5 "),
    ),
)
def test_restore_drill_launcher_rejects_raw_whitespace_endpoint_literals_before_source(
    tmp_path: Path, key: str, value: str
) -> None:
    """Unquoted dotenv whitespace must not normalize before endpoint validation."""
    environment_file = tmp_path / ".env.dev"
    lines = [
        "POSTGRES_HOST=postgres.example.test",
        "POSTGRES_PORT=5432",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5",
    ]
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    environment_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# ── Load environment-specific database config")
    end = launcher.index("# ── Mode-dependent configuration", start)
    bootstrap_and_endpoint_boundary = launcher[start:end]
    env = {**os.environ, "PROJECT_DIR": str(tmp_path), "BUTLERS_MODE": "dev"}
    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "RESTORE_DRILL_EXECUTOR_DB_HOST",
        "RESTORE_DRILL_EXECUTOR_DB_PORT",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
    ):
        env.pop(name, None)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + bootstrap_and_endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "whitespace" in completed.stderr


@pytest.mark.parametrize(
    "indented_assignment",
    (
        "  RESTORE_DRILL_EXECUTOR_DB_PORT=5432 ",
        "\texport RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5 ",
    ),
)
def test_restore_drill_launcher_rejects_indented_raw_whitespace_before_source(
    tmp_path: Path, indented_assignment: str
) -> None:
    """Leading indentation must not let Bash normalize endpoint literals."""
    environment_file = tmp_path / ".env.dev"
    environment_file.write_text(
        "\n".join(
            (
                "POSTGRES_HOST=postgres.example.test",
                "POSTGRES_PORT=5432",
                "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5",
                indented_assignment,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# ── Load environment-specific database config")
    end = launcher.index("# ── Mode-dependent configuration", start)
    bootstrap_and_endpoint_boundary = launcher[start:end]
    env = {**os.environ, "PROJECT_DIR": str(tmp_path), "BUTLERS_MODE": "dev"}
    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "RESTORE_DRILL_EXECUTOR_DB_HOST",
        "RESTORE_DRILL_EXECUTOR_DB_PORT",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
    ):
        env.pop(name, None)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + bootstrap_and_endpoint_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "whitespace" in completed.stderr


@pytest.mark.parametrize("invalid_port", ["0", "65536", "not-a-port", *NONCANONICAL_PORT_REJECTED])
def test_restore_drill_firewall_rejects_noncanonical_or_invalid_port_without_rules(
    invalid_port: str,
) -> None:
    """The elevated wrapper must not reinterpret a reviewed decimal port."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            invalid_port,
            "--dry-run",
            *_DRY_RUN_NETWORK_ARGS,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "db-port" in completed.stderr


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


def test_firewall_sudoers_requires_the_versioned_prepare_and_apply_forms() -> None:
    """The documented privilege gate must reject an installed pre-attestation wrapper."""
    sudoers = _FIREWALL_SUDOERS.read_text(encoding="utf-8")

    assert f"{_FIREWALL_WRAPPER} --prepare-executor-capability-v1 --project *" in sudoers
    assert (
        f"{_FIREWALL_WRAPPER} --project * --db-host * --db-port * "
        "--require-executor-capability-v1" in sudoers
    )


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
            *_DRY_RUN_NETWORK_ARGS,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "db-host" in completed.stderr


@pytest.mark.parametrize("unsafe_ipv4", REMOTE_IPV4_REJECTED)
def test_restore_drill_firewall_rejects_non_remote_ipv4_without_rules(unsafe_ipv4: str) -> None:
    """The elevated wrapper must not accept loopback or special relay targets."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            unsafe_ipv4,
            "--db-port",
            "5432",
            "--dry-run",
            *_DRY_RUN_NETWORK_ARGS,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "remote IPv4" in completed.stderr


@pytest.mark.parametrize("remote_ipv4", REMOTE_IPV4_ACCEPTED)
def test_restore_drill_firewall_accepts_every_supported_remote_ipv4(remote_ipv4: str) -> None:
    """The elevated wrapper must agree with deploy, launcher, and relay policy."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            remote_ipv4,
            "--db-port",
            "5432",
            "--dry-run",
            *_DRY_RUN_NETWORK_ARGS,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"-d {remote_ipv4} --dport 5432 -j ACCEPT" in completed.stdout


def test_restore_drill_firewall_rejects_untrusted_wrapper_arguments() -> None:
    """The elevated wrapper has no shell, environment, or project-name escape hatch."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test; id",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--dry-run",
            *_DRY_RUN_NETWORK_ARGS,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "project" in completed.stderr


def test_restore_drill_firewall_derives_executor_topology_outside_test_dry_run() -> None:
    """The sudo path cannot accept a caller-selected executor peer or bridge."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--executor-bridge",
            "br-attacker-selected-executor",
            "--relay-ip",
            "172.30.0.2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "valid only with --dry-run" in completed.stderr


def test_restore_drill_firewall_requests_untruncated_container_ids_for_capability_binding() -> None:
    """Docker's default short IDs cannot authenticate an exact created generation."""
    firewall = _FIREWALL.read_text(encoding="utf-8")

    assert "docker ps --all --quiet --no-trunc" in firewall


@pytest.mark.parametrize("relay_ip", ("0.0.0.0", "127.0.0.1", "169.254.1.1", "224.0.0.1"))
def test_restore_drill_firewall_dry_run_rejects_non_peer_relay_ip(relay_ip: str) -> None:
    """Even unprivileged planning cannot portray a host or special IP as the relay."""
    completed = subprocess.run(
        [
            _FIREWALL,
            "--project",
            "test",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--dry-run",
            "--bridge",
            "br-restore-drill-test",
            "--executor-bridge",
            "br-restore-drill-executor-test",
            "--relay-ip",
            relay_ip,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "relay-ip" in completed.stderr


def test_private_executor_secret_is_absent_from_every_normal_runtime_service() -> None:
    """The secret mount belongs only to the deterministic executor service."""
    compose = _compose(_BASE_COMPOSE_FILE)

    for name, service in compose["services"].items():
        assert "restore_drill_executor_password" not in repr(service.get("secrets", []))
        assert _CA_CONFIG_SOURCE not in repr(service.get("configs", []))
        assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in _environment_keys(service)
