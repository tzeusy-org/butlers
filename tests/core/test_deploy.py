"""Unit tests for butlers.core.deploy (bu-9r3hd.3, "butlers deploy").

Every docker/compose/subprocess/httpx boundary is mocked here — the real
ledger write (via butlers.core.deployments.record_deployment) is exercised
against a migrated Postgres in
tests/integration/test_deploy_ledger_roundtrip.py.
"""

from __future__ import annotations

import logging
import socket
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.core.deploy import (
    DEFAULT_COMPOSE_FILES,
    RESTORE_DRILL_FIREWALL_WRAPPER,
    DeployConfig,
    DeployError,
    RestoreDrillEndpoint,
    RestoreDrillFirewallCapability,
    _clean_compose_env,
    _compose_base_args,
    _head_vs_origin_main,
    _resolve_restore_drill_endpoint,
    build_image,
    materialize_beads_export,
    preflight_check,
    prepare_restore_drill_executor,
    recreate_services,
    resolve_git_sha,
    run_deploy,
    run_migrations,
    wait_for_health,
)
from tests.restore_drill_endpoint_policy import (
    EXECUTOR_NUMERIC_IDENTITIES_REJECTED,
    LEGACY_NUMERIC_IPV4_REJECTED,
    NONCANONICAL_PORT_REJECTED,
    REMOTE_IPV4_ACCEPTED,
    REMOTE_IPV4_REJECTED,
)

pytestmark = pytest.mark.unit


def _config(**overrides) -> DeployConfig:
    defaults = dict(repo_root=Path("/repo"))
    defaults.update(overrides)
    return DeployConfig(**defaults)


class TestResolveGitSha:
    def test_runs_git_rev_parse_head_in_repo_root(self, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, capture_output, text, check):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        sha = resolve_git_sha(Path("/repo"))
        assert sha == "abc1234"
        assert captured["cmd"] == ["git", "rev-parse", "HEAD"]
        assert captured["cwd"] == Path("/repo")


class TestComposeArgsAndEnv:
    def test_default_deploy_includes_the_protected_restore_drill_overlay(self):
        """Prod deploy must render the executor only through its prepared Compose input."""
        assert DEFAULT_COMPOSE_FILES == (
            "docker-compose.yml",
            "docker-compose.restore-drill.yml",
        )

    def test_compose_base_args_includes_files_project_and_env_file(self):
        config = _config(compose_files=("docker-compose.yml",), project_name="butlers")
        args = _compose_base_args(config)
        assert args == [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-p",
            "butlers",
            "--env-file",
            ".env.prod",
        ]

    def test_no_profile_flag_appears_by_default(self):
        """Structural guard: a default (prod) deploy must never request a compose profile."""
        args = _compose_base_args(_config())
        assert "--profile" not in args

    def test_explicit_profiles_are_threaded_into_compose_args(self):
        """bu-hmdqz.1: an explicitly-requested profile (e.g. 'dev' for the
        butlers-dev project's profile-gated frontend-dev service) must reach
        the actual compose invocation."""
        args = _compose_base_args(_config(profiles=("dev",)))
        assert "--profile" in args
        assert args[args.index("--profile") + 1] == "dev"

    def test_hotreload_profile_is_rejected_at_config_construction(self):
        """The hotreload profile bind-mounts source instead of the baked
        image -- never allowed, regardless of caller intent."""
        with pytest.raises(ValueError, match="hotreload"):
            _config(profiles=("hotreload",))

    def test_hotreload_profile_rejected_even_alongside_other_profiles(self):
        with pytest.raises(ValueError, match="hotreload"):
            _config(profiles=("dev", "hotreload"))

    def test_clean_compose_env_strips_compose_profiles(self, monkeypatch):
        monkeypatch.setenv("COMPOSE_PROFILES", "hotreload")
        monkeypatch.setenv("SOME_OTHER_VAR", "kept")
        env = _clean_compose_env()
        assert "COMPOSE_PROFILES" not in env
        assert env["SOME_OTHER_VAR"] == "kept"

    def test_clean_compose_env_is_noop_when_unset(self, monkeypatch):
        monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
        env = _clean_compose_env()
        assert "COMPOSE_PROFILES" not in env


class TestRestoreDrillDeployBoundary:
    """The supported deploy path must install the executor firewall before up."""

    def test_resolves_firewall_ipv4_without_replacing_verify_full_hostname(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / ".env.prod").write_text(
            "POSTGRES_HOST=postgres.example.test\nPOSTGRES_PORT=5433\n",
            encoding="utf-8",
        )
        for name in (
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_PORT",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

        def fake_getaddrinfo(host, port, family, type):
            assert host == "postgres.example.test"
            assert family == socket.AF_INET
            assert type == socket.SOCK_STREAM
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.23.4.5", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

        endpoint = _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

        assert endpoint == RestoreDrillEndpoint(
            connection_host="postgres.example.test",
            firewall_ipv4="10.23.4.5",
            port=5433,
        )
        assert endpoint.compose_environment() == {
            "RESTORE_DRILL_EXECUTOR_DB_HOST": "postgres.example.test",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": "10.23.4.5",
            "RESTORE_DRILL_EXECUTOR_DB_PORT": "5433",
        }

    @pytest.mark.parametrize("unsafe_ipv4", REMOTE_IPV4_REJECTED)
    def test_rejects_non_remote_firewall_ipv4_overrides(self, tmp_path, monkeypatch, unsafe_ipv4):
        """A supported deploy cannot pass a local or special address to the relay."""
        (tmp_path / ".env.prod").write_text(
            "POSTGRES_HOST=postgres.example.test\n", encoding="utf-8"
        )
        monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST", unsafe_ipv4)
        for name in ("POSTGRES_HOST", "RESTORE_DRILL_EXECUTOR_DB_HOST"):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(DeployError, match="remote IPv4|localhost"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    @pytest.mark.parametrize("source_name", ("POSTGRES_HOST", "RESTORE_DRILL_EXECUTOR_DB_HOST"))
    @pytest.mark.parametrize("numeric_host", EXECUTOR_NUMERIC_IDENTITIES_REJECTED)
    def test_rejects_numeric_connection_identities_before_dns_or_firewall_override(
        self, tmp_path, monkeypatch, source_name, numeric_host
    ):
        """The executor TLS identity must use Docker-resolvable DNS, never an IP."""
        (tmp_path / ".env.prod").write_text(
            "POSTGRES_HOST=postgres.example.test\n", encoding="utf-8"
        )
        for name in (
            "POSTGRES_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(source_name, numeric_host)

        def unexpected_dns_resolution(*_args, **_kwargs):
            pytest.fail("numeric executor identity reached DNS resolution")

        monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns_resolution)

        with pytest.raises(DeployError, match="DNS hostname"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    def test_rejects_whitespace_padded_dotenv_connection_host(self, tmp_path, monkeypatch):
        """The deploy dotenv reader must not normalize a protected endpoint literal."""
        (tmp_path / ".env.prod").write_text("POSTGRES_HOST= 10.23.4.5 \n", encoding="utf-8")
        for name in (
            "POSTGRES_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(DeployError, match="DNS hostname"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    @pytest.mark.parametrize("assignment_prefix", ("  ", "\texport "))
    def test_accepts_supported_indented_dotenv_endpoint_assignments(
        self, tmp_path, monkeypatch, assignment_prefix
    ):
        """Deploy must match the launcher for simple indented dotenv assignments."""
        (tmp_path / ".env.prod").write_text(
            "\n".join(
                (
                    f"{assignment_prefix}POSTGRES_HOST=postgres.example.test",
                    f"{assignment_prefix}POSTGRES_PORT=5433",
                    f"{assignment_prefix}RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        for name in (
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_PORT",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

        endpoint = _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

        assert endpoint == RestoreDrillEndpoint(
            connection_host="postgres.example.test",
            firewall_ipv4="10.23.4.5",
            port=5433,
        )

    @pytest.mark.parametrize("remote_ipv4", REMOTE_IPV4_ACCEPTED)
    def test_accepts_every_supported_remote_firewall_ipv4_override(
        self, tmp_path, monkeypatch, remote_ipv4
    ):
        """Keep deploy endpoint validation in parity with all other entry points."""
        (tmp_path / ".env.prod").write_text(
            "POSTGRES_HOST=postgres.example.test\n", encoding="utf-8"
        )
        monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST", remote_ipv4)
        for name in ("POSTGRES_HOST", "RESTORE_DRILL_EXECUTOR_DB_HOST"):
            monkeypatch.delenv(name, raising=False)

        endpoint = _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

        assert endpoint.firewall_ipv4 == remote_ipv4

    def test_rejects_a_dns_identity_that_resolves_only_to_loopback(self, tmp_path, monkeypatch):
        """The TLS/SNI hostname cannot turn the relay into a localhost loop."""
        (tmp_path / ".env.prod").write_text("POSTGRES_HOST=localhost\n", encoding="utf-8")
        for name in (
            "POSTGRES_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

        def loopback_getaddrinfo(host, port, family, type):
            assert host == "localhost"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", loopback_getaddrinfo)

        with pytest.raises(DeployError, match="remote IPv4|localhost"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    def test_rejects_localhost_even_when_a_remote_firewall_override_is_supplied(
        self, tmp_path, monkeypatch
    ):
        """A caller cannot retain a localhost TLS identity beside a safe relay target."""
        (tmp_path / ".env.prod").write_text("POSTGRES_HOST=localhost\n", encoding="utf-8")
        monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST", "10.23.4.5")
        for name in ("POSTGRES_HOST", "RESTORE_DRILL_EXECUTOR_DB_HOST"):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(DeployError, match="localhost"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    @pytest.mark.parametrize(
        "noncanonical_ipv4",
        ["010.23.4.5", "198.022.001.001", "192.037.196.1", *LEGACY_NUMERIC_IPV4_REJECTED],
    )
    def test_rejects_noncanonical_numeric_host_before_dns_or_firewall_override(
        self, tmp_path, monkeypatch, noncanonical_ipv4
    ):
        """A numeric dotted quad is never a DNS/TLS identity fallback."""
        (tmp_path / ".env.prod").write_text(
            f"POSTGRES_HOST={noncanonical_ipv4}\n", encoding="utf-8"
        )
        for name in (
            "POSTGRES_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

        def unexpected_dns_resolution(*_args, **_kwargs):
            pytest.fail("legacy numeric host reached DNS resolution")

        monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns_resolution)

        with pytest.raises(DeployError, match="DNS hostname"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    @pytest.mark.parametrize("port", ["0", "65536", "not-a-port", *NONCANONICAL_PORT_REJECTED])
    def test_rejects_out_of_range_or_invalid_database_ports(self, tmp_path, monkeypatch, port):
        """Deploy must not emit a port the launcher, executor, or relay rejects."""
        (tmp_path / ".env.prod").write_text(
            f"POSTGRES_HOST=postgres.example.test\nRESTORE_DRILL_EXECUTOR_DB_PORT={port}\n",
            encoding="utf-8",
        )
        for name in (
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "RESTORE_DRILL_EXECUTOR_DB_HOST",
            "RESTORE_DRILL_EXECUTOR_DB_PORT",
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(DeployError, match="1..65535"):
            _resolve_restore_drill_endpoint(_config(repo_root=tmp_path))

    def test_stops_creates_firewalls_then_starts_with_one_resolved_endpoint(
        self, tmp_path, monkeypatch
    ):
        calls: list[tuple[list[str], dict[str, str]]] = []

        def fake_run(cmd, cwd, env, capture_output, text):
            calls.append((cmd, env))
            stdout = "a" * 64 + "\n" if "--prepare-executor-capability-v1" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        config = _config(repo_root=tmp_path)
        endpoint = RestoreDrillEndpoint("postgres.example.test", "10.23.4.5", 5432)

        capability = prepare_restore_drill_executor(config, endpoint)
        recreate_services(config, endpoint, capability)

        commands = [command for command, _env in calls]
        protected_compose_prefix = [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.restore-drill.yml",
            "-p",
            "butlers",
            "--env-file",
            ".env.prod",
        ]
        assert commands[0][: len(protected_compose_prefix)] == protected_compose_prefix
        assert commands[1] == [
            "sudo",
            "-n",
            RESTORE_DRILL_FIREWALL_WRAPPER,
            "--prepare-executor-capability-v1",
            "--project",
            "butlers",
        ]
        assert commands[2][: len(protected_compose_prefix)] == protected_compose_prefix
        assert commands[4][: len(protected_compose_prefix)] == protected_compose_prefix
        assert commands[0][-3:] == [
            "stop",
            "restore-drill-postgres-proxy",
            "restore-drill-executor",
        ]
        assert commands[2][-3:] == [
            "create",
            "restore-drill-postgres-proxy",
            "restore-drill-executor",
        ]
        assert commands[3] == [
            "sudo",
            "-n",
            RESTORE_DRILL_FIREWALL_WRAPPER,
            "--project",
            "butlers",
            "--db-host",
            "10.23.4.5",
            "--db-port",
            "5432",
            "--require-executor-capability-v1",
        ]
        assert commands[4][-3:] == ["up", "-d", "--remove-orphans"]
        assert all("restore-drill-firewall.sh" not in command for command in commands[1])
        assert all(str(tmp_path) not in command for command in commands[1])

        for _command, env in (calls[0], calls[2], calls[4]):
            assert env["RESTORE_DRILL_EXECUTOR_DB_HOST"] == "postgres.example.test"
            assert env["RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST"] == "10.23.4.5"
            assert env["RESTORE_DRILL_EXECUTOR_DB_PORT"] == "5432"
            assert env["COMPOSE_PROJECT_NAME"] == "butlers"
        assert calls[0][1]["RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE"] == "unprepared"
        assert calls[2][1]["RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE"] == "a" * 64
        assert calls[4][1]["RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE"] == "a" * 64
        assert calls[1][1] == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
        assert calls[3][1] == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}

    def test_firewall_failure_fails_closed_before_recreate(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, env, capture_output, text):
            calls.append(cmd)
            if cmd[:3] == ["sudo", "-n", RESTORE_DRILL_FIREWALL_WRAPPER]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="sudo denied")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        endpoint = RestoreDrillEndpoint("postgres.example.test", "10.23.4.5", 5432)

        with pytest.raises(DeployError) as exc_info:
            prepare_restore_drill_executor(_config(), endpoint)

        assert exc_info.value.phase == "restore-drill-firewall"
        assert not any(command[-1:] == ["up"] or "up" in command for command in calls)


class TestBuildImage:
    def test_success_builds_with_git_sha_arg(self, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, env, capture_output, text):
            captured["cmd"] = cmd
            captured["env"] = env
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        build_image(_config(), "abc1234")
        cmd = captured["cmd"]
        assert cmd[:2] == ["docker", "build"]
        assert "--build-arg" in cmd
        assert "GIT_SHA=abc1234" in cmd
        assert "butlers-app:latest" in cmd

    def test_failure_raises_deploy_error_with_build_phase(self, monkeypatch):
        def fake_run(cmd, cwd, env, capture_output, text):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no space left on device")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(DeployError) as exc_info:
            build_image(_config(), "abc1234")
        assert exc_info.value.phase == "build"
        assert "no space left on device" in str(exc_info.value)


class TestRunMigrations:
    def test_uses_run_rm_not_up(self, monkeypatch):
        """Never `up -d`-only for migrations — a stale exited container must
        not silently skip a rerun (bu-zhfd0)."""
        captured = {}

        def fake_run(cmd, cwd, env, capture_output, text):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        run_migrations(_config())
        cmd = captured["cmd"]
        assert cmd[-3:] == ["run", "--rm", "migrations"]

    def test_failure_raises_deploy_error_with_migrate_phase(self, monkeypatch):
        def fake_run(cmd, cwd, env, capture_output, text):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="alembic: revision mismatch"
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(DeployError) as exc_info:
            run_migrations(_config())
        assert exc_info.value.phase == "migrate"


class TestMaterializeBeadsExport:
    """bu-hmdqz.6: best-effort `bd export` refresh before recreate_services.

    Uses a real `tmp_path`-backed repo_root (not the module-level `_config()`
    default of the nonexistent `/repo`) because this function now touches
    the filesystem directly (mkdir/touch the placeholder export file) before
    ever shelling out to `bd` -- see the docstring's "Docker bind-mount
    trap" note.
    """

    def test_success_exports_to_repo_root_beads_dir(self, tmp_path, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, capture_output, text, timeout):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = materialize_beads_export(_config(repo_root=tmp_path))
        assert result is True
        expected_path = str(tmp_path / ".beads" / "issues.export.jsonl")
        assert captured["cmd"] == ["bd", "export", "-o", expected_path]
        assert captured["cwd"] == tmp_path

    def test_missing_bd_binary_returns_false_not_raises(self, tmp_path, monkeypatch):
        def fake_run(cmd, cwd, capture_output, text, timeout):
            raise FileNotFoundError("bd not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert materialize_beads_export(_config(repo_root=tmp_path)) is False

    def test_timeout_returns_false_not_raises(self, tmp_path, monkeypatch):
        def fake_run(cmd, cwd, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert materialize_beads_export(_config(repo_root=tmp_path)) is False

    def test_nonzero_exit_returns_false_not_raises(self, tmp_path, monkeypatch):
        def fake_run(cmd, cwd, capture_output, text, timeout):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="dolt: connection refused")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert materialize_beads_export(_config(repo_root=tmp_path)) is False

    def test_creates_placeholder_file_before_shelling_out_to_bd(self, tmp_path, monkeypatch):
        # Regression (PR #3174 review): the export file must exist as a
        # regular file BEFORE `docker compose up` ever runs, regardless of
        # whether `bd export` itself succeeds -- otherwise Docker creates a
        # directory at the missing bind-mount host path, which then makes
        # every future `bd export -o` fail permanently with
        # IsADirectoryError. Simulate "bd export failed" and assert a
        # regular (not missing) file is left behind anyway.
        def fake_run(cmd, cwd, capture_output, text, timeout):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="dolt unreachable")

        monkeypatch.setattr(subprocess, "run", fake_run)
        export_path = tmp_path / ".beads" / "issues.export.jsonl"
        assert not export_path.exists()

        result = materialize_beads_export(_config(repo_root=tmp_path))

        assert result is False
        assert export_path.is_file()

    def test_does_not_clobber_existing_export_before_running_bd(self, tmp_path, monkeypatch):
        # The pre-flight placeholder guard must not truncate an existing,
        # still-valid export before `bd export` gets a chance to refresh it.
        export_path = tmp_path / ".beads" / "issues.export.jsonl"
        export_path.parent.mkdir(parents=True)
        export_path.write_text('{"id": "bu-1"}\n')

        captured = {}

        def fake_run(cmd, cwd, capture_output, text, timeout):
            captured["pre_run_contents"] = export_path.read_text()
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        materialize_beads_export(_config(repo_root=tmp_path))
        assert captured["pre_run_contents"] == '{"id": "bu-1"}\n'


class TestRecreateServices:
    def test_rejects_missing_restore_drill_endpoint_before_compose_up(self, monkeypatch):
        """No direct caller may bypass the prepared endpoint/firewall boundary."""
        calls = []

        def fake_run(cmd, cwd, env, capture_output, text):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(DeployError, match="restore-drill.*endpoint"):
            recreate_services(_config(), None)

        assert calls == []

    def test_up_dash_d_remove_orphans_no_profile(self, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, env, capture_output, text):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        recreate_services(
            _config(),
            RestoreDrillEndpoint("postgres.example.test", "10.23.4.5", 5432),
            RestoreDrillFirewallCapability("a" * 64),
        )
        cmd = captured["cmd"]
        assert "up" in cmd
        assert "-d" in cmd
        assert "--remove-orphans" in cmd
        assert "--profile" not in cmd
        assert "--scale" not in cmd

    def test_failure_raises_deploy_error_with_recreate_phase(self, monkeypatch):
        def fake_run(cmd, cwd, env, capture_output, text):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="port already in use")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(DeployError) as exc_info:
            recreate_services(
                _config(),
                RestoreDrillEndpoint("postgres.example.test", "10.23.4.5", 5432),
                RestoreDrillFirewallCapability("a" * 64),
            )
        assert exc_info.value.phase == "recreate"


class TestWaitForHealth:
    async def test_returns_when_status_ok(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        await wait_for_health(_config(health_timeout_s=1.0, health_poll_interval_s=0.01))

    async def test_retries_on_starting_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        async def fake_get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(
                    503, json={"status": "starting"}, request=httpx.Request("GET", url)
                )
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        await wait_for_health(_config(health_timeout_s=2.0, health_poll_interval_s=0.01))
        assert calls["n"] == 3

    async def test_times_out_and_raises_health_check_deploy_error(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return httpx.Response(
                503, json={"status": "starting"}, request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        with pytest.raises(DeployError) as exc_info:
            await wait_for_health(_config(health_timeout_s=0.05, health_poll_interval_s=0.01))
        assert exc_info.value.phase == "health-check"

    async def test_connection_error_is_retried_not_raised_immediately(self, monkeypatch):
        calls = {"n": 0}

        async def fake_get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        await wait_for_health(_config(health_timeout_s=2.0, health_poll_interval_s=0.01))
        assert calls["n"] == 2


class TestRunDeploy:
    """Orchestration only — record_deployment is mocked here; the real write
    is covered by the integration test."""

    def _patch_phases(self, monkeypatch, *, fail_at: str | None = None):
        calls: list[str] = []

        def make(name):
            def _fn(config, *a):
                calls.append(name)
                if fail_at == name:
                    raise DeployError(name, f"{name} boom")

            return _fn

        def _export_ok(config):
            # Best-effort — never raises, so it is not a valid `fail_at` target.
            calls.append("beads-export")
            return True

        async def _wait_ok(config):
            calls.append("health-check")
            if fail_at == "health-check":
                raise DeployError("health-check", "health-check boom")

        monkeypatch.setattr("butlers.core.deploy.build_image", make("build"))
        monkeypatch.setattr("butlers.core.deploy.run_migrations", make("migrate"))
        monkeypatch.setattr("butlers.core.deploy.materialize_beads_export", _export_ok)
        monkeypatch.setattr(
            "butlers.core.deploy.prepare_restore_drill_executor",
            make("restore-drill-boundary"),
        )
        monkeypatch.setattr("butlers.core.deploy.recreate_services", make("recreate"))
        monkeypatch.setattr(
            "butlers.core.deploy._resolve_restore_drill_endpoint",
            lambda config: RestoreDrillEndpoint("postgres.example.test", "10.23.4.5", 5432),
        )
        monkeypatch.setattr("butlers.core.deploy.wait_for_health", _wait_ok)
        monkeypatch.setattr("butlers.core.deploy.resolve_git_sha", lambda repo_root: "deadbeef")
        # Preflight is exercised directly in TestPreflightCheck against real
        # tmp git repos; the orchestration tests use a fake `/repo` path, so
        # stub it as a clean (no-override) pass here.
        monkeypatch.setattr("butlers.core.deploy.preflight_check", lambda config: ())
        return calls

    async def test_success_records_success_row(self, monkeypatch):
        calls = self._patch_phases(monkeypatch)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.resolve_core_migration_head", AsyncMock(return_value="core_163")
        )

        result = await run_deploy(_config(), pool=pool)

        assert calls == [
            "build",
            "migrate",
            "beads-export",
            "restore-drill-boundary",
            "recreate",
            "health-check",
        ]
        assert result.result == "success"
        assert result.git_sha == "deadbeef"
        assert result.migration_head == "core_163"
        record_mock.assert_awaited_once_with(
            pool,
            git_sha="deadbeef",
            migration_head="core_163",
            result="success",
            source="deploy",
            serving_mode="image",
            serving_worktree=None,
        )
        # Injected pool must not be closed by run_deploy.
        pool.close.assert_not_awaited()

    @pytest.mark.parametrize(
        "fail_at", ["build", "migrate", "restore-drill-boundary", "recreate", "health-check"]
    )
    async def test_failure_at_each_phase_records_failed_row_and_reraises(
        self, monkeypatch, fail_at
    ):
        calls = self._patch_phases(monkeypatch, fail_at=fail_at)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.resolve_core_migration_head", AsyncMock(return_value=None)
        )

        with pytest.raises(DeployError) as exc_info:
            await run_deploy(_config(), pool=pool)

        assert exc_info.value.phase == fail_at
        # Every phase up to and including the failing one ran; nothing after it did.
        assert calls[-1] == fail_at
        record_mock.assert_awaited_once_with(
            pool,
            git_sha="deadbeef",
            migration_head=None,
            result="failed",
            source="deploy",
            serving_mode="image",
            serving_worktree=None,
        )

    async def test_migration_head_read_failure_does_not_block_the_ledger_write(self, monkeypatch):
        """A failing alembic_version read must record an honest null, not crash."""
        self._patch_phases(monkeypatch)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.resolve_core_migration_head",
            AsyncMock(side_effect=Exception("relation does not exist")),
        )

        result = await run_deploy(_config(), pool=pool)

        assert result.migration_head is None
        record_mock.assert_awaited_once_with(
            pool,
            git_sha="deadbeef",
            migration_head=None,
            result="success",
            source="deploy",
            serving_mode="image",
            serving_worktree=None,
        )

    async def test_no_injected_pool_creates_and_closes_its_own(self, monkeypatch):
        self._patch_phases(monkeypatch)
        owned_pool = AsyncMock()
        monkeypatch.setattr("butlers.core.deploy._make_pool", AsyncMock(return_value=owned_pool))
        monkeypatch.setattr(
            "butlers.core.deploy.record_deployment", AsyncMock(return_value="row-1")
        )
        monkeypatch.setattr(
            "butlers.core.deploy.resolve_core_migration_head", AsyncMock(return_value="core_163")
        )

        await run_deploy(_config())

        owned_pool.close.assert_awaited_once()

    async def test_idempotent_rerun_after_a_failure_can_succeed(self, monkeypatch):
        """Re-running the whole pipeline after a failed attempt must work —
        no leftover state from the previous run blocks it."""
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.resolve_core_migration_head", AsyncMock(return_value="core_163")
        )

        self._patch_phases(monkeypatch, fail_at="recreate")
        with pytest.raises(DeployError):
            await run_deploy(_config(), pool=pool)

        self._patch_phases(monkeypatch)  # second run: nothing fails
        result = await run_deploy(_config(), pool=pool)

        assert result.result == "success"
        assert record_mock.await_count == 2

    async def test_preflight_rejection_writes_no_ledger_row(self, monkeypatch):
        """A preflight refusal happens before any build/ledger step — nothing
        is attempted, so no `public.deployments` row is written (a refusal to
        deploy, not a recorded deploy failure)."""
        self._patch_phases(monkeypatch)

        def _reject(config):
            raise DeployError("preflight", "deploy root is a linked git worktree")

        monkeypatch.setattr("butlers.core.deploy.preflight_check", _reject)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)

        with pytest.raises(DeployError) as exc_info:
            await run_deploy(_config(), pool=pool)

        assert exc_info.value.phase == "preflight"
        record_mock.assert_not_awaited()

    async def test_override_reasons_thread_into_result(self, monkeypatch):
        """allow_dirty_root override reasons flow through to DeployResult so the
        CLI can surface them; the (possibly divergent) git_sha is still recorded."""
        self._patch_phases(monkeypatch)
        monkeypatch.setattr(
            "butlers.core.deploy.preflight_check",
            lambda config: ("deploy root /wt is a linked git worktree",),
        )
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.resolve_core_migration_head", AsyncMock(return_value="core_163")
        )

        result = await run_deploy(_config(), pool=pool)

        assert result.overrides == ("deploy root /wt is a linked git worktree",)
        record_mock.assert_awaited_once_with(
            pool,
            git_sha="deadbeef",
            migration_head="core_163",
            result="success",
            source="deploy",
            serving_mode="image",
            serving_worktree=None,
        )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _init_repo_with_origin(tmp_path: Path) -> Path:
    """Create a local bare `origin` + a working `main` checkout pushed to it.

    Returns the path to the canonical main checkout (``.git`` is a directory,
    HEAD == origin/main). All git ops are local (the bare origin lives under
    tmp_path), so the preflight `git fetch origin main` works fully offline.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    main = tmp_path / "main"
    subprocess.run(["git", "clone", str(origin), str(main)], check=True, capture_output=True)
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "Test")
    (main / "README.md").write_text("hi\n")
    _git(main, "add", "README.md")
    _git(main, "commit", "-m", "initial")
    _git(main, "push", "origin", "main")
    return main


class TestPreflightCheck:
    """Preflight guard against deploying a frozen worktree or divergent HEAD
    (bu-5phh8). Uses real tmp git repos, mirroring the issue's test plan."""

    def test_canonical_main_checkout_passes(self, tmp_path):
        main = _init_repo_with_origin(tmp_path)
        assert preflight_check(_config(repo_root=main)) == ()

    def test_linked_worktree_rejected(self, tmp_path):
        main = _init_repo_with_origin(tmp_path)
        wt = tmp_path / "wt"
        _git(main, "worktree", "add", str(wt))
        # Sanity: a linked worktree's .git is a file, not a directory.
        assert (wt / ".git").is_file()
        with pytest.raises(DeployError) as exc_info:
            preflight_check(_config(repo_root=wt))
        assert exc_info.value.phase == "preflight"
        assert "linked git worktree" in str(exc_info.value)
        assert str(wt) in str(exc_info.value)

    def test_diverged_head_rejected(self, tmp_path):
        main = _init_repo_with_origin(tmp_path)
        (main / "extra.txt").write_text("x\n")
        _git(main, "add", "extra.txt")
        _git(main, "commit", "-m", "local unmerged commit")
        with pytest.raises(DeployError) as exc_info:
            preflight_check(_config(repo_root=main))
        assert exc_info.value.phase == "preflight"
        assert "not an ancestor of origin/main" in str(exc_info.value)
        assert "1 commit(s) ahead" in str(exc_info.value)

    def test_allow_dirty_root_downgrades_worktree_to_warning(self, tmp_path, caplog):
        main = _init_repo_with_origin(tmp_path)
        wt = tmp_path / "wt"
        _git(main, "worktree", "add", str(wt))
        with caplog.at_level(logging.WARNING, logger="butlers.core.deploy"):
            overrides = preflight_check(_config(repo_root=wt, allow_dirty_root=True))
        assert any("linked git worktree" in reason for reason in overrides)
        assert any("OVERRIDDEN" in rec.getMessage() for rec in caplog.records)

    def test_allow_dirty_root_downgrades_diverged_head_to_warning(self, tmp_path):
        main = _init_repo_with_origin(tmp_path)
        (main / "extra.txt").write_text("x\n")
        _git(main, "add", "extra.txt")
        _git(main, "commit", "-m", "local unmerged commit")
        overrides = preflight_check(_config(repo_root=main, allow_dirty_root=True))
        assert any("not an ancestor of origin/main" in reason for reason in overrides)

    def test_non_git_directory_rejected_with_clear_message(self, tmp_path):
        """A plain directory with no .git gets an explicit "not a git repository"
        error, not a confusing ancestry message from a failed git subprocess."""
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(DeployError) as exc_info:
            preflight_check(_config(repo_root=plain))
        assert exc_info.value.phase == "preflight"
        assert "not a git repository" in str(exc_info.value)

    def test_ancestry_check_fails_closed_when_git_unavailable(self, monkeypatch):
        """If the git binary cannot execute, ancestry is unconfirmed → fail
        closed (not an ancestor, zeroed counts), never an unhandled traceback."""

        def _boom(*args, **kwargs):
            raise OSError("git: command not found")

        monkeypatch.setattr(subprocess, "run", _boom)
        assert _head_vs_origin_main(Path("/repo")) == (False, 0, 0)
