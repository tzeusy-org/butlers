"""Tests for shared restore-drill mechanics and dashboard result reading.

The dedicated executor owns scheduling and privileged subprocess launch. These
tests retain only the pure scratch lifecycle and the dashboard's read-only
result reader; executor persistence and timing live in
``test_restore_drill_executor.py``.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.jobs.backup_health import (
    RestoreDrillResult,
    _run_restore_drill_sync,
    get_last_restore_drill,
)

pytestmark = pytest.mark.unit

_DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "user": "restore_drill_executor",
    "password": "test-only-password",
}


def _completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _write_gzip_backup(tmp_path: Path, name: str = "butlers_2026-05-07T02-00-00.sql.gz") -> Path:
    import gzip

    path = tmp_path / name
    with gzip.open(path, "wb") as backup:
        backup.write(b"CREATE TABLE t (id int);\n")
    return path


def test_run_restore_drill_sync_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup = _write_gzip_backup(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        calls.append(command)
        if command[0] == "psql" and "-tAc" in command:
            return _completed(stdout=b"3\n")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result == RestoreDrillResult(ok=True, detail="restored 3 tables", table_count=3)
    assert calls[0][0] == "dropdb"
    assert calls[-1][0] == "dropdb"


def test_run_restore_drill_sync_createdb_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "createdb":
            return _completed(1, stderr=b"permission denied to create database")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "permission denied" in result.detail


def test_run_restore_drill_sync_missing_client_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)

    def fake_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("createdb")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "postgresql-client" in result.detail


def test_run_restore_drill_sync_psql_restore_invoke_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)
    dropdb_calls = 0

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        nonlocal dropdb_calls
        if command[0] == "dropdb":
            dropdb_calls += 1
            return _completed()
        if command[0] == "psql":
            raise OSError("psql unavailable")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "failed to invoke psql" in result.detail
    assert dropdb_calls == 2


def test_run_restore_drill_sync_corrupt_backup_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / "butlers_2026-05-07T02-00-00.sql.gz"
    backup.write_bytes(b"not gzip data")

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _completed())
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "unreadable/corrupt" in result.detail


def test_run_restore_drill_sync_restore_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "psql" and "-tAc" not in command:
            return _completed(1, stderr=b"syntax error near CREATE")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "restore failed" in result.detail


def test_run_restore_drill_sync_restore_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "psql" and "-tAc" not in command:
            raise subprocess.TimeoutExpired(cmd=command, timeout=1800)
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "timed out" in result.detail


def test_run_restore_drill_sync_zero_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "psql" and "-tAc" in command:
            return _completed(stdout=b"0\n")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result == RestoreDrillResult(
        ok=False, detail="restore produced zero tables", table_count=0
    )


def test_run_restore_drill_sync_unparseable_integrity_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "psql" and "-tAc" in command:
            return _completed(stdout=b"not-a-number\n")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert "unparseable" in result.detail


def test_run_restore_drill_sync_always_drops_scratch_db_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _write_gzip_backup(tmp_path)
    dropdb_calls = 0

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        nonlocal dropdb_calls
        if command[0] == "dropdb":
            dropdb_calls += 1
            return _completed()
        if command[0] == "psql":
            return _completed(1, stderr=b"boom")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert dropdb_calls == 2


def test_run_restore_drill_sync_post_cleanup_failure_never_reports_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-deployment-hardening-007: a leftover scratch database invalidates a pass."""
    backup = _write_gzip_backup(tmp_path)
    dropdb_calls = 0

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        nonlocal dropdb_calls
        if command[0] == "dropdb":
            dropdb_calls += 1
            if dropdb_calls == 2:
                return _completed(
                    1,
                    stderr=b"postgresql://restore:top-secret@db.example.test/postgres COPY private_data",
                )
            return _completed()
        if command[0] == "psql" and "-tAc" in command:
            return _completed(stdout=b"3\n")
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert result.table_count == 3
    assert "scratch cleanup failed" in result.detail
    assert "top-secret" not in result.detail
    assert "postgresql://" not in result.detail
    assert "COPY private_data" not in result.detail
    assert len(result.detail) <= 512
    assert dropdb_calls == 2


def test_run_restore_drill_sync_never_persists_raw_client_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client stdout/stderr may contain credentials or dump content, never audit detail."""
    backup = _write_gzip_backup(tmp_path)
    raw_client_output = (
        b"postgresql://restore:top-secret@db.example.test/postgres "
        b"COPY owner_private_data FROM stdin;\n" + b"x" * 4_000
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "createdb":
            return _completed(1, stderr=raw_client_output)
        return _completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)

    assert result.ok is False
    assert result.detail == "createdb failed: PostgreSQL client reported an error"
    assert len(result.detail) <= 512
    assert "top-secret" not in result.detail
    assert "postgresql://" not in result.detail
    assert "COPY owner_private_data" not in result.detail


class _FakeRestoreDrillAuthorityPool:
    def __init__(self, *, row: dict | None):
        self._row = row
        self.query: str | None = None

    async def fetchrow(self, sql: str, *_args: object) -> dict | None:
        self.query = sql
        return self._row


@pytest.mark.asyncio
async def test_get_last_restore_drill_none_when_never_run() -> None:
    pool = _FakeRestoreDrillAuthorityPool(row=None)

    assert await get_last_restore_drill(pool) is None
    assert pool.query is not None
    assert "restore_drill_executor.latest_result()" in pool.query
    assert "public.audit_log" not in pool.query


@pytest.mark.asyncio
async def test_get_last_restore_drill_returns_latest_row() -> None:
    ts = datetime(2026, 7, 10, 2, 0, tzinfo=UTC)
    row = {"checked_at": ts, "result": "pass", "detail": None}

    result = await get_last_restore_drill(_FakeRestoreDrillAuthorityPool(row=row))

    assert result == {"checked_at": ts.isoformat(), "result": "pass", "detail": None}


@pytest.mark.asyncio
async def test_get_last_restore_drill_naive_timestamp_treated_as_utc() -> None:
    result = await get_last_restore_drill(
        _FakeRestoreDrillAuthorityPool(
            row={"checked_at": datetime(2026, 7, 10, 2, 0), "result": "fail", "detail": "boom"}
        )
    )

    assert result is not None
    assert result["checked_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_get_last_restore_drill_withholds_legacy_raw_client_output() -> None:
    result = await get_last_restore_drill(
        _FakeRestoreDrillAuthorityPool(
            row={
                "checked_at": datetime(2026, 7, 10, 2, 0, tzinfo=UTC),
                "result": "fail",
                "detail": "postgresql://restore:top-secret@db.example.test/postgres COPY private_data",
            }
        )
    )

    assert result is not None
    assert result["detail"] == "restore drill diagnostic withheld"
