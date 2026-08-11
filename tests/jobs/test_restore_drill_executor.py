"""Unit coverage for the db-only restore-drill executor service."""

from __future__ import annotations

import asyncio
import gzip
import logging
import ssl
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import butlers.jobs.restore_drill_executor as restore_drill_executor
from butlers.jobs.backup_health import RestoreDrillResult, _run_restore_drill_sync
from butlers.jobs.restore_drill_executor import (
    RestoreDrillExecutorConfig,
    _asyncpg_ssl_context,
    _psql_env,
    _read_executor_password,
    load_restore_drill_executor_config,
    run_restore_drill_executor_loop,
    run_restore_drill_executor_tick,
)
from tests.restore_drill_endpoint_policy import (
    EXECUTOR_NUMERIC_IDENTITIES_REJECTED,
    NONCANONICAL_PORT_REJECTED,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _prepared_firewall_capability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a current root-marker shape to unrelated config tests."""
    project = "butlers"
    nonce = "a" * 64
    executor_id = "1" * 64
    relay_id = "2" * 64
    executor_network_id = "3" * 64
    relay_network_id = "4" * 64
    capability_directory = tmp_path / "restore-drill-firewall"
    capability_directory.mkdir()
    monkeypatch.setattr(
        restore_drill_executor,
        "_FIREWALL_CAPABILITY_DIRECTORY",
        capability_directory,
    )
    monkeypatch.setattr(
        restore_drill_executor,
        "_read_host_boot_id",
        lambda: "test-boot-id",
    )
    monkeypatch.setattr(
        restore_drill_executor,
        "_read_current_container_identity",
        lambda: executor_id,
    )
    monkeypatch.setattr(
        restore_drill_executor,
        "_read_current_executor_ipv4",
        lambda: "172.30.0.3",
    )
    monkeypatch.setattr(
        restore_drill_executor,
        "_current_executor_network_contains",
        lambda executor_ip, gateway: executor_ip == "172.30.0.3" and gateway == "172.30.0.1",
    )
    monkeypatch.setattr(
        restore_drill_executor,
        "_resolve_internal_relay_ipv4",
        lambda _host, _port: "172.30.0.2",
    )
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_FIREWALL_PROJECT", project)
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE", nonce)
    capability_path = capability_directory / f"{project}.executor-capability-v1"
    capability_path.write_text(
        "butlers-restore-drill-firewall-v1\n"
        "project=butlers\n"
        "port=5432\n"
        "boot_id=test-boot-id\n"
        f"nonce={nonce}\n"
        f"executor_container_id={executor_id}\n"
        f"executor_network_id={executor_network_id}\n"
        "executor_ip=172.30.0.3\n"
        "executor_gateway=172.30.0.1\n"
        f"relay_container_id={relay_id}\n"
        f"relay_network_id={relay_network_id}\n"
        "relay_ip=172.30.0.2\n",
        encoding="utf-8",
    )
    capability_path.chmod(0o400)


def _completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _write_gzip_backup(tmp_path: Path) -> Path:
    path = tmp_path / "butlers_2026-08-10T02-00-00.sql.gz"
    with gzip.open(path, "wb") as backup:
        backup.write(b"CREATE TABLE restore_probe (id integer);\n")
    return path


def _system_ca_bundle() -> Path:
    """Return the host trust bundle without embedding certificate material in tests."""
    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate is not None and Path(candidate).is_file():
            return Path(candidate)
    pytest.skip("a system CA bundle is required for restore-drill TLS coverage")


def test_executor_configuration_reads_its_password_from_a_file_not_shared_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("POSTGRES_USER", "shared-dashboard-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "shared-dashboard-password")
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared-dashboard-user@example.test/butlers")

    config = load_restore_drill_executor_config()

    assert config.user == "restore_drill_executor"
    assert config.password == "file-backed-test-password"
    assert config.sslmode is None
    assert config.sslrootcert_file is None
    assert _psql_env(config) == {"PGPASSWORD": "file-backed-test-password"}

    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLMODE", "require")

    dedicated_config = load_restore_drill_executor_config()

    assert dedicated_config.sslmode == "require"
    assert _psql_env(dedicated_config) == {
        "PGPASSWORD": "file-backed-test-password",
        "PGSSLMODE": "require",
    }
    assert dedicated_config.sslrootcert_file is None
    require_context = _asyncpg_ssl_context(dedicated_config)
    assert isinstance(require_context, ssl.SSLContext)
    assert require_context.check_hostname is False
    assert require_context.verify_mode == ssl.CERT_NONE


def test_executor_rejects_missing_prepared_firewall_capability_before_reading_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct merged Compose start must fail before using its credential."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setattr(
        restore_drill_executor,
        "_FIREWALL_CAPABILITY_DIRECTORY",
        tmp_path / "missing-capabilities",
        raising=False,
    )
    monkeypatch.setattr(
        restore_drill_executor,
        "_read_host_boot_id",
        lambda: "test-boot-id",
        raising=False,
    )
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_PORT", "5432")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_FIREWALL_PROJECT", "butlers")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))

    with pytest.raises(ValueError, match="prepared firewall capability"):
        load_restore_drill_executor_config()


def test_executor_rejects_a_stale_capability_from_another_container_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual down/recreate cannot replay a prior boot/project/port marker."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_PORT", "5432")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setattr(
        restore_drill_executor,
        "_read_current_container_identity",
        lambda: "9" * 64,
    )

    with pytest.raises(ValueError, match="prepared firewall capability"):
        load_restore_drill_executor_config()


@pytest.mark.parametrize(
    "tamper",
    (
        "nonce",
        "executor_container_id",
        "executor_network_id",
        "executor_ip",
        "executor_gateway",
        "relay_container_id",
        "relay_network_id",
        "relay_ip",
        "relay_alias",
    ),
)
def test_executor_rejects_tampered_capability_topology_before_reading_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    """Every executor-observable capability field fails closed before secret I/O."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_PORT", "5432")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    capability_path = tmp_path / "restore-drill-firewall" / "butlers.executor-capability-v1"

    replacements = {
        "nonce": ("nonce=" + "a" * 64, "nonce=" + "b" * 64),
        "executor_container_id": (
            "executor_container_id=" + "1" * 64,
            "executor_container_id=" + "9" * 64,
        ),
        "executor_network_id": ("executor_network_id=" + "3" * 64, "executor_network_id=bad"),
        "executor_ip": ("executor_ip=172.30.0.3", "executor_ip=172.30.0.9"),
        "executor_gateway": ("executor_gateway=172.30.0.1", "executor_gateway=172.30.0.9"),
        "relay_container_id": ("relay_container_id=" + "2" * 64, "relay_container_id=bad"),
        "relay_network_id": ("relay_network_id=" + "4" * 64, "relay_network_id=bad"),
        "relay_ip": ("relay_ip=172.30.0.2", "relay_ip=172.30.0.9"),
    }
    if tamper == "relay_alias":
        monkeypatch.setattr(
            restore_drill_executor,
            "_resolve_internal_relay_ipv4",
            lambda _host, _port: "172.30.0.9",
        )
    else:
        before, after = replacements[tamper]
        content = capability_path.read_text(encoding="utf-8")
        assert before in content
        capability_path.chmod(0o600)
        capability_path.write_text(content.replace(before, after), encoding="utf-8")
        capability_path.chmod(0o400)

    def password_must_not_be_read(_path: Path) -> str:
        pytest.fail("capability rejection read the restore-drill password")

    monkeypatch.setattr(
        restore_drill_executor, "_read_executor_password", password_must_not_be_read
    )

    with pytest.raises(ValueError, match="prepared firewall capability"):
        load_restore_drill_executor_config()


def test_executor_attestation_accepts_an_internal_connected_route_without_default_route() -> None:
    """Docker `internal` networks deliberately omit a default route."""
    routes = "\n".join(
        (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask",
            "eth0\t00001EAC\t00000000\t0001\t0\t0\t0\t0000FFFF",
        )
    )

    assert restore_drill_executor._has_connected_route_for(  # noqa: SLF001
        "172.30.0.3", "172.30.0.1", routes
    )


def test_executor_keeps_dns_tls_identity_for_verify_full_and_strips_one_terminal_lf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    ca_bundle = _system_ca_bundle()
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLMODE", "verify-full")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE", str(ca_bundle))

    config = load_restore_drill_executor_config()

    assert config.host == "postgres.example.test"
    assert config.cli_db_params()["host"] == "postgres.example.test"
    assert config.sslmode == "verify-full"
    assert config.sslrootcert_file == ca_bundle
    assert _psql_env(config)["PGSSLMODE"] == "verify-full"
    assert _psql_env(config)["PGSSLROOTCERT"] == str(ca_bundle)
    context = _asyncpg_ssl_context(config)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port", *NONCANONICAL_PORT_REJECTED])
def test_executor_rejects_out_of_range_or_invalid_database_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    """The executor must never accept a port its relay/firewall cannot serve."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_PORT", port)

    with pytest.raises(ValueError, match="positive integer|1..65535"):
        load_restore_drill_executor_config()


@pytest.mark.parametrize(
    ("sslmode", "expected_hostname_check"),
    [("verify-ca", False), ("verify-full", True)],
)
def test_executor_verification_modes_require_a_valid_dedicated_ca_root_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sslmode: str,
    expected_hostname_check: bool,
) -> None:
    """Verification modes fail closed; ordinary ``require`` does not need this file."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", "postgres.example.test")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLMODE", sslmode)
    monkeypatch.delenv("RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE", raising=False)

    with pytest.raises(ValueError, match="CA root"):
        load_restore_drill_executor_config()

    invalid_ca = tmp_path / "invalid-ca.pem"
    invalid_ca.write_text("not a PEM certificate", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE", str(invalid_ca))

    with pytest.raises(ValueError, match="CA root"):
        load_restore_drill_executor_config()

    ca_bundle = _system_ca_bundle()
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE", str(ca_bundle))
    config = load_restore_drill_executor_config()

    assert config.sslrootcert_file == ca_bundle
    assert _psql_env(config)["PGSSLROOTCERT"] == str(ca_bundle)
    context = _asyncpg_ssl_context(config)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is expected_hostname_check


@pytest.mark.parametrize("host", EXECUTOR_NUMERIC_IDENTITIES_REJECTED)
def test_executor_rejects_numeric_connection_identities_before_opening_a_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """Only Docker-resolvable DNS identities can reach the internal relay alias."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_DB_HOST", host)

    with pytest.raises(ValueError, match="DNS hostname"):
        load_restore_drill_executor_config()


@pytest.mark.parametrize("secret", ["one\ntwo", "one\r", "one\n\n", "\x00"])
def test_executor_password_rejects_ambiguous_or_nul_secret_content(
    tmp_path: Path, secret: str
) -> None:
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text(secret, encoding="utf-8")

    with pytest.raises(ValueError, match="password file"):
        _read_executor_password(password_file)


def test_executor_password_rejects_invalid_utf8_without_leaking_contents(tmp_path: Path) -> None:
    password_file = tmp_path / "restore-drill-password"
    password_file.write_bytes(b"\xff")

    with pytest.raises(ValueError, match="unreadable"):
        _read_executor_password(password_file)


@pytest.mark.asyncio
async def test_executor_pool_uses_the_hostname_as_verify_full_tls_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The firewall IPv4 must never replace the hostname passed to asyncpg."""
    config = RestoreDrillExecutorConfig(
        host="postgres.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
        sslmode="verify-full",
        sslrootcert_file=_system_ca_bundle(),
    )
    captured: dict[str, object] = {}
    pool = AsyncMock()

    async def fake_create_pool(**kwargs: object):
        captured.update(kwargs)
        return pool

    monkeypatch.setattr("butlers.jobs.restore_drill_executor.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "butlers.jobs.restore_drill_executor.run_restore_drill_executor_tick",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_executor_loop(config)

    assert captured["host"] == "postgres.example.test"
    context = captured["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    pool.close.assert_awaited_once()


def test_executor_names_the_maintenance_database_for_create_and_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        calls.append(command)
        if command[0] == "psql" and "-tAc" in command:
            return _completed(stdout=b"1\n")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _run_restore_drill_sync(
        backup,
        db_params={
            "host": "db.example.test",
            "port": 5432,
            "user": "restore_drill_executor",
            "password": "file-backed-test-password",
        },
        maintenance_db="postgres",
    )

    assert result.ok is True
    for command in (command for command in calls if command[0] in {"createdb", "dropdb"}):
        assert command[command.index("--maintenance-db") + 1] == "postgres"


@dataclass
class _FakeExecutorPersistence:
    due: bool
    due_intervals: list[int] = field(default_factory=list)
    recorded: list[tuple[str, str, str, int | None]] = field(default_factory=list)
    record_error: Exception | None = None

    async def is_due(self, interval_s: int) -> bool:
        self.due_intervals.append(interval_s)
        return self.due

    async def record_result(
        self, *, backup_name: str, result: str, detail: str, table_count: int | None
    ) -> int:
        if self.record_error is not None:
            raise self.record_error
        self.recorded.append((backup_name, result, detail, table_count))
        return 1


@pytest.mark.asyncio
async def test_executor_tick_uses_its_narrow_persistence_boundary_for_due_and_result(
    tmp_path: Path,
) -> None:
    """REQ-database-security-006: only the executor owns the CLI lifecycle."""
    backup = _write_gzip_backup(tmp_path)
    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    persistence = _FakeExecutorPersistence(due=True)
    launched: list[Path] = []

    def runner(path: Path, **kwargs: object):
        launched.append(path)
        assert kwargs["process_env"] == {"PGPASSWORD": "file-backed-test-password"}
        return RestoreDrillResult(ok=True, detail="restored 1 table", table_count=1)

    summary = await run_restore_drill_executor_tick(config, persistence, runner=runner)

    assert summary == {"ok": True, "recorded": True, "backup_file": backup.name}
    assert launched == [backup]
    assert persistence.due_intervals == [604800]
    assert persistence.recorded == [(backup.name, "pass", "restored 1 table", 1)]


@pytest.mark.asyncio
async def test_executor_persistence_boundary_withholds_untrusted_result_detail(
    tmp_path: Path,
) -> None:
    """Audit/API readers receive bounded safe detail even if a runner misbehaves."""
    backup = _write_gzip_backup(tmp_path)
    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    persistence = _FakeExecutorPersistence(due=True)

    def runner(_path: Path, **_kwargs: object) -> RestoreDrillResult:
        return RestoreDrillResult(
            ok=False,
            detail="postgresql://restore:top-secret@db.example.test/postgres COPY private_data",
        )

    summary = await run_restore_drill_executor_tick(config, persistence, runner=runner)

    assert summary == {"ok": False, "recorded": True, "backup_file": backup.name}
    assert persistence.recorded == [
        (backup.name, "fail", "restore drill diagnostic withheld", None)
    ]


@pytest.mark.asyncio
async def test_executor_result_persistence_failure_logs_only_fixed_safe_diagnostic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A persistence exception can carry a DSN but must not reach logs or the summary."""
    backup = _write_gzip_backup(tmp_path)
    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    hostile_marker = "record-result-private-dsn-marker"
    persistence = _FakeExecutorPersistence(
        due=True,
        record_error=RuntimeError(f"postgresql://executor:{hostile_marker}@db.example.test"),
    )

    def runner(_path: Path, **_kwargs: object) -> RestoreDrillResult:
        return RestoreDrillResult(ok=True, detail="restored 1 table", table_count=1)

    caplog.set_level(logging.WARNING, logger="butlers.jobs.restore_drill_executor")
    summary = await run_restore_drill_executor_tick(config, persistence, runner=runner)

    assert summary == {"ok": True, "recorded": False, "backup_file": backup.name}
    assert "restore drill executor result persistence failed" in caplog.text
    assert hostile_marker not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_executor_due_check_failure_logs_only_fixed_safe_diagnostic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The due boundary must not log exception text before a scratch lifecycle starts."""

    class FailingDuePersistence:
        async def is_due(self, _interval_s: int) -> bool:
            raise RuntimeError("postgresql://executor:due-check-private-marker@db.example.test")

        async def record_result(
            self, *, backup_name: str, result: str, detail: str, table_count: int | None
        ) -> int:
            raise AssertionError("a failed due check must not persist a result")

    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    caplog.set_level(logging.WARNING, logger="butlers.jobs.restore_drill_executor")

    summary = await run_restore_drill_executor_tick(config, FailingDuePersistence())

    assert summary == {"skipped": True, "reason": "due check unavailable"}
    assert "restore drill executor due check failed" in caplog.text
    assert "due-check-private-marker" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_executor_tick_failure_logs_only_fixed_safe_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unexpected tick failures are safe even when their exception embeds a DSN."""
    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    pool = AsyncMock()

    async def fake_create_pool(**_kwargs: object):
        return pool

    async def cancel_after_first_sleep(_delay: int) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("butlers.jobs.restore_drill_executor.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "butlers.jobs.restore_drill_executor.run_restore_drill_executor_tick",
        AsyncMock(
            side_effect=RuntimeError("postgresql://executor:tick-private-marker@db.example.test")
        ),
    )
    monkeypatch.setattr(
        "butlers.jobs.restore_drill_executor.asyncio.sleep", cancel_after_first_sleep
    )
    caplog.set_level(logging.WARNING, logger="butlers.jobs.restore_drill_executor")

    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_executor_loop(config)

    assert "restore drill executor tick failed" in caplog.text
    assert "tick-private-marker" not in caplog.text
    assert "Traceback" not in caplog.text
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_tick_does_not_launch_a_scratch_lifecycle_before_it_is_due(
    tmp_path: Path,
) -> None:
    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    persistence = _FakeExecutorPersistence(due=False)

    def fail_if_called(*_args: object, **_kwargs: object) -> RestoreDrillResult:
        raise AssertionError("scratch lifecycle must not launch before it is due")

    summary = await run_restore_drill_executor_tick(config, persistence, runner=fail_if_called)

    assert summary == {"skipped": True, "reason": "not due"}
    assert persistence.recorded == []


@pytest.mark.asyncio
async def test_executor_tick_skips_without_a_backup_and_does_not_persist_a_result(
    tmp_path: Path,
) -> None:
    config = RestoreDrillExecutorConfig(
        host="db.example.test",
        port=5432,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password="file-backed-test-password",
        backup_dir=tmp_path,
        drill_interval_s=604800,
        check_interval_s=3600,
    )
    persistence = _FakeExecutorPersistence(due=True)

    summary = await run_restore_drill_executor_tick(config, persistence)

    assert summary == {"skipped": True, "reason": "no backup file present"}
    assert persistence.recorded == []
