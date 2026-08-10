"""Unit coverage for the db-only restore-drill executor service."""

from __future__ import annotations

import gzip
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from butlers.jobs.backup_health import RestoreDrillResult, _run_restore_drill_sync
from butlers.jobs.restore_drill_executor import (
    RestoreDrillExecutorConfig,
    _psql_env,
    load_restore_drill_executor_config,
    run_restore_drill_executor_tick,
)

pytestmark = pytest.mark.unit


def _completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _write_gzip_backup(tmp_path: Path) -> Path:
    path = tmp_path / "butlers_2026-08-10T02-00-00.sql.gz"
    with gzip.open(path, "wb") as backup:
        backup.write(b"CREATE TABLE restore_probe (id integer);\n")
    return path


def test_executor_configuration_reads_its_password_from_a_file_not_shared_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("file-backed-test-password\n", encoding="utf-8")
    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("POSTGRES_USER", "shared-dashboard-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "shared-dashboard-password")
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared-dashboard-user@example.test/butlers")

    config = load_restore_drill_executor_config()

    assert config.user == "restore_drill_executor"
    assert config.password == "file-backed-test-password"
    assert config.sslmode is None
    assert _psql_env(config) == {"PGPASSWORD": "file-backed-test-password"}

    monkeypatch.setenv("RESTORE_DRILL_EXECUTOR_SSLMODE", "require")

    dedicated_config = load_restore_drill_executor_config()

    assert dedicated_config.sslmode == "require"
    assert _psql_env(dedicated_config) == {
        "PGPASSWORD": "file-backed-test-password",
        "PGSSLMODE": "require",
    }


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

    async def is_due(self, interval_s: int) -> bool:
        self.due_intervals.append(interval_s)
        return self.due

    async def record_result(
        self, *, backup_name: str, result: str, detail: str, table_count: int | None
    ) -> int:
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
