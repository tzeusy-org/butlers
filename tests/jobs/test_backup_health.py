"""Tests for butlers.jobs.backup_health — weekly restore drill (bu-9r3hd.5).

Covers:
- _run_restore_drill_sync: pass path (createdb/psql/integrity-check/dropdb all
  succeed) and every failure branch (createdb fails, corrupt gzip, restore
  fails, timeout, zero-tables, unparseable integrity output, missing
  postgresql-client binary) — all via mocked subprocess.run, no real Postgres.
- run_restore_drill_check: no backup file -> skipped, no audit write; a
  drill result (pass or fail) is recorded to public.audit_log via
  audit_router.append.
- get_last_restore_drill: reads the latest recorded result, or None.
- run_restore_drill_loop: checks due-time on boot (no sleep-first) and every
  check_interval_s thereafter (bu-hmdqz.1); unconfigured BUTLERS_BACKUP_DIR
  and a missing switchboard pool are both skipped ticks, not crashes; a tick
  exception never kills the loop; a drill only actually runs when overdue.
- _restore_drill_overdue: never-run and stale-past-interval are overdue;
  recent is not.

No real database or real Postgres client tools required — subprocess and
pool are faked/mocked throughout.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.api.db import DatabaseManager
from butlers.jobs.backup_health import (
    RestoreDrillResult,
    _restore_drill_overdue,
    _run_restore_drill_sync,
    get_last_restore_drill,
    run_restore_drill_check,
    run_restore_drill_loop,
)

pytestmark = pytest.mark.unit

_DB_PARAMS = {"host": "localhost", "port": 5432, "user": "butlers", "password": "hunter2"}


def _completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _write_gzip_backup(tmp_path: Path, name: str = "butlers_2026-05-07T02-00-00.sql.gz") -> Path:
    import gzip

    path = tmp_path / name
    with gzip.open(path, "wb") as f:
        f.write(b"CREATE TABLE t (id int);\n")
    return path


# ---------------------------------------------------------------------------
# _run_restore_drill_sync
# ---------------------------------------------------------------------------


def test_run_restore_drill_sync_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backup = _write_gzip_backup(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql" and "-tAc" in cmd:
            return _completed(0, stdout=b"3\n")
        if cmd[0] == "psql":
            return _completed(0)
        if cmd[0] == "dropdb":
            return _completed(0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is True
    assert result.table_count == 3
    # dropdb runs both before (cleanup of a stale scratch DB) and after (finally).
    assert calls[0][0] == "dropdb"
    assert calls[-1][0] == "dropdb"


def test_run_restore_drill_sync_createdb_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backup = _write_gzip_backup(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "createdb":
            return _completed(1, stderr=b"permission denied to create database")
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "permission denied" in result.detail


def test_run_restore_drill_sync_missing_client_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backup = _write_gzip_backup(tmp_path)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("createdb")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "postgresql-client" in result.detail


def test_run_restore_drill_sync_psql_restore_invoke_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """createdb/dropdb succeed but the psql restore call itself raises OSError
    (e.g. a partially-broken client install) -- a recorded fail, not an
    uncaught exception out of _run_restore_drill_sync."""
    backup = _write_gzip_backup(tmp_path)
    dropdb_calls = 0

    def fake_run(cmd, **kwargs):
        nonlocal dropdb_calls
        if cmd[0] == "dropdb":
            dropdb_calls += 1
            return _completed(0)
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql":
            raise OSError("psql: text file busy")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "failed to invoke psql" in result.detail
    # Teardown still ran even though the restore call itself raised.
    assert dropdb_calls == 2


def test_run_restore_drill_sync_corrupt_backup_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backup = tmp_path / "butlers_2026-05-07T02-00-00.sql.gz"
    backup.write_bytes(b"not gzip data")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "createdb":
            return _completed(0)
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "unreadable/corrupt" in result.detail


def test_run_restore_drill_sync_restore_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backup = _write_gzip_backup(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql" and "-tAc" not in cmd:
            return _completed(1, stderr=b"syntax error near CREATE")
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "restore failed" in result.detail


def test_run_restore_drill_sync_restore_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backup = _write_gzip_backup(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql" and "-tAc" not in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1800)
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "timed out" in result.detail


def test_run_restore_drill_sync_zero_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backup = _write_gzip_backup(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql" and "-tAc" in cmd:
            return _completed(0, stdout=b"0\n")
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert result.table_count == 0
    assert "zero tables" in result.detail


def test_run_restore_drill_sync_unparseable_integrity_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backup = _write_gzip_backup(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql" and "-tAc" in cmd:
            return _completed(0, stdout=b"not-a-number\n")
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    assert "unparseable" in result.detail


def test_run_restore_drill_sync_always_drops_scratch_db_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Even when the restore fails partway through, cleanup (dropdb) still runs."""
    backup = _write_gzip_backup(tmp_path)
    dropdb_calls = 0

    def fake_run(cmd, **kwargs):
        nonlocal dropdb_calls
        if cmd[0] == "dropdb":
            dropdb_calls += 1
            return _completed(0)
        if cmd[0] == "createdb":
            return _completed(0)
        if cmd[0] == "psql":
            return _completed(1, stderr=b"boom")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_restore_drill_sync(backup, db_params=_DB_PARAMS)
    assert result.ok is False
    # Once for the pre-drill stale-DB cleanup, once for the post-failure teardown.
    assert dropdb_calls == 2


# ---------------------------------------------------------------------------
# run_restore_drill_check
# ---------------------------------------------------------------------------


async def test_run_restore_drill_check_skips_when_no_backup_present(tmp_path: Path):
    pool = MagicMock()
    pool.execute = AsyncMock()
    summary = await run_restore_drill_check(pool, tmp_path)
    assert summary == {"skipped": True, "reason": "no backup file present"}
    pool.execute.assert_not_called()


async def test_run_restore_drill_check_records_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_gzip_backup(tmp_path)
    monkeypatch.setattr(
        "butlers.jobs.backup_health._run_restore_drill_sync",
        lambda *a, **kw: RestoreDrillResult(ok=True, detail="restored 3 tables", table_count=3),
    )
    append_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("butlers.jobs.backup_health.audit_router.append", append_mock)

    pool = MagicMock()
    summary = await run_restore_drill_check(pool, tmp_path)

    assert summary == {"ok": True, "detail": "restored 3 tables", "recorded": True}
    append_mock.assert_awaited_once()
    _, kwargs = append_mock.call_args
    assert kwargs["result"] == "pass"
    assert kwargs["error"] is None
    assert kwargs["metadata"]["table_count"] == 3


async def test_run_restore_drill_check_records_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_gzip_backup(tmp_path)
    monkeypatch.setattr(
        "butlers.jobs.backup_health._run_restore_drill_sync",
        lambda *a, **kw: RestoreDrillResult(ok=False, detail="createdb failed: permission denied"),
    )
    append_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("butlers.jobs.backup_health.audit_router.append", append_mock)

    pool = MagicMock()
    summary = await run_restore_drill_check(pool, tmp_path)

    assert summary["ok"] is False
    assert "permission denied" in summary["detail"]
    _, kwargs = append_mock.call_args
    assert kwargs["result"] == "fail"
    assert kwargs["error"] == "createdb failed: permission denied"


async def test_run_restore_drill_check_survives_audit_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A ledger-write failure is swallowed -- the tick result is still returned."""
    _write_gzip_backup(tmp_path)
    monkeypatch.setattr(
        "butlers.jobs.backup_health._run_restore_drill_sync",
        lambda *a, **kw: RestoreDrillResult(ok=True, detail="restored 1 table", table_count=1),
    )
    monkeypatch.setattr(
        "butlers.jobs.backup_health.audit_router.append",
        AsyncMock(side_effect=RuntimeError("audit_log unavailable")),
    )

    pool = MagicMock()
    summary = await run_restore_drill_check(pool, tmp_path)
    assert summary["ok"] is True
    assert summary["recorded"] is False


# ---------------------------------------------------------------------------
# get_last_restore_drill
# ---------------------------------------------------------------------------


class _FakeAuditPool:
    def __init__(self, *, row: dict | None):
        self._row = row

    async def fetchrow(self, sql: str, *args):
        return self._row


async def test_get_last_restore_drill_none_when_never_run():
    pool = _FakeAuditPool(row=None)
    result = await get_last_restore_drill(pool)
    assert result is None


async def test_get_last_restore_drill_returns_latest_row():
    ts = datetime(2026, 7, 10, 2, 0, tzinfo=UTC)
    pool = _FakeAuditPool(row={"ts": ts, "result": "pass", "error": None})
    result = await get_last_restore_drill(pool)
    assert result == {"checked_at": ts.isoformat(), "result": "pass", "detail": None}


async def test_get_last_restore_drill_naive_timestamp_treated_as_utc():
    ts = datetime(2026, 7, 10, 2, 0)  # naive
    pool = _FakeAuditPool(row={"ts": ts, "result": "fail", "error": "boom"})
    result = await get_last_restore_drill(pool)
    assert result["checked_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# _restore_drill_overdue
# ---------------------------------------------------------------------------


async def test_restore_drill_overdue_when_never_run():
    pool = _FakeAuditPool(row=None)
    assert await _restore_drill_overdue(pool, interval_s=3600.0) is True


async def test_restore_drill_overdue_when_older_than_interval():
    stale_ts = datetime(2020, 1, 1, tzinfo=UTC)
    pool = _FakeAuditPool(row={"ts": stale_ts, "result": "pass", "error": None})
    assert await _restore_drill_overdue(pool, interval_s=3600.0) is True


async def test_restore_drill_not_overdue_when_recent():
    from datetime import timedelta

    recent_ts = datetime.now(UTC) - timedelta(seconds=10)
    pool = _FakeAuditPool(row={"ts": recent_ts, "result": "pass", "error": None})
    assert await _restore_drill_overdue(pool, interval_s=3600.0) is False


# ---------------------------------------------------------------------------
# run_restore_drill_loop
# ---------------------------------------------------------------------------


async def test_run_restore_drill_loop_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        await run_restore_drill_loop(MagicMock(spec=DatabaseManager), interval_s=0)


async def test_run_restore_drill_loop_rejects_non_positive_check_interval():
    with pytest.raises(ValueError):
        await run_restore_drill_loop(
            MagicMock(spec=DatabaseManager), interval_s=1.0, check_interval_s=0
        )


async def test_run_restore_drill_loop_skips_tick_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()  # stop after the first tick

    monkeypatch.setattr("butlers.jobs.backup_health.asyncio.sleep", fake_sleep)
    check_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.backup_health.run_restore_drill_check", check_mock)

    db = MagicMock(spec=DatabaseManager)
    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_loop(db, interval_s=1.0, check_interval_s=1.0)
    check_mock.assert_not_called()


async def test_run_restore_drill_loop_skips_tick_when_pool_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", "/backups")

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr("butlers.jobs.backup_health.asyncio.sleep", fake_sleep)
    check_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.backup_health.run_restore_drill_check", check_mock)

    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("switchboard")
    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_loop(db, interval_s=1.0, check_interval_s=1.0)
    check_mock.assert_not_called()


async def test_run_restore_drill_loop_runs_immediately_when_never_run(
    monkeypatch: pytest.MonkeyPatch,
):
    """bu-hmdqz.1: on boot, a never-run drill must fire on the FIRST tick --
    no sleep-first wait for the full weekly interval."""
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", "/backups")

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()  # stop right after the first tick

    monkeypatch.setattr("butlers.jobs.backup_health.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "butlers.jobs.backup_health.get_last_restore_drill", AsyncMock(return_value=None)
    )
    check_mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("butlers.jobs.backup_health.run_restore_drill_check", check_mock)

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_loop(db, interval_s=3600.0, check_interval_s=1.0)
    check_mock.assert_awaited_once()


async def test_run_restore_drill_loop_skips_when_not_overdue(monkeypatch: pytest.MonkeyPatch):
    """A recently-completed drill must not re-run just because the hourly
    check tick fired -- only an overdue drill actually runs."""
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", "/backups")

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr("butlers.jobs.backup_health.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "butlers.jobs.backup_health.get_last_restore_drill",
        AsyncMock(return_value={"checked_at": datetime.now(UTC).isoformat(), "result": "pass"}),
    )
    check_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.backup_health.run_restore_drill_check", check_mock)

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_loop(db, interval_s=3600.0, check_interval_s=1.0)
    check_mock.assert_not_called()


async def test_run_restore_drill_loop_swallows_tick_exception(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", "/backups")
    sleep_calls = 0

    async def fake_sleep(seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr("butlers.jobs.backup_health.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "butlers.jobs.backup_health.get_last_restore_drill",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await run_restore_drill_loop(db, interval_s=1.0, check_interval_s=1.0)
    assert sleep_calls == 2
