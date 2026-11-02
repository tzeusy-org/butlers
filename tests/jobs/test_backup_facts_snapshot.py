"""Backup facts snapshot: dashboard-api publishes, the QA patrol reads (bu-deqpzd)."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from butlers.core.backup_facts_snapshot import (
    STATE_KEY,
    load_backup_facts_snapshot,
    publish_backup_facts_snapshot,
)
from butlers.jobs import backup_facts_snapshot as job

pytestmark = pytest.mark.unit


class _StatePool:
    """Minimal public.state: one upsert target, one read."""

    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def execute(self, sql: str, key: str, value: object) -> None:
        assert "INSERT INTO public.state" in sql
        self.rows[key] = value

    async def fetchval(self, sql: str, key: str) -> object:
        assert "FROM public.state" in sql
        return self.rows.get(key)


async def test_published_facts_round_trip_without_artifact_names(tmp_path):
    dump = tmp_path / "butlers_20260711.sql.gz"
    dump.write_bytes(b"x" * 512)
    os.utime(dump, (time.time(), time.time()))
    (tmp_path / "last_run.json").write_text(
        '{"result":"failed","reason":"pg_dump_failed","exit_code":1,'
        '"finished_at":"2026-07-12T02:00:11Z","artifact":null}',
        encoding="utf-8",
    )
    pool = _StatePool()

    await publish_backup_facts_snapshot(pool, str(tmp_path), interval_s=600)
    snapshot = await load_backup_facts_snapshot(pool)

    assert snapshot is not None
    assert snapshot.configured and snapshot.reachable and not snapshot.backup_stale
    assert (snapshot.last_run_result, snapshot.last_run_reason) == ("failed", "pg_dump_failed")
    assert not snapshot.is_stale(datetime.now(UTC))
    # Content-free: neither the directory nor any artifact name crosses over.
    assert "butlers_2026" not in str(pool.rows[STATE_KEY])
    assert str(tmp_path) not in str(pool.rows[STATE_KEY])


async def test_unset_dir_publishes_unconfigured_and_absent_row_reads_none():
    pool = _StatePool()
    assert await load_backup_facts_snapshot(pool) is None

    await publish_backup_facts_snapshot(pool, "", interval_s=600)
    snapshot = await load_backup_facts_snapshot(pool)
    assert snapshot is not None and snapshot.configured is False


async def test_loop_writes_through_the_public_schema_owner_pool(monkeypatch):
    """Butler roles only SELECT public; the shared (owner) pool is the sole writer."""
    pool = _StatePool()
    db = MagicMock()
    db.credential_shared_pool.return_value = pool
    sleeps = 0

    async def _sleep(_s):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(job.asyncio, "sleep", _sleep)
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)

    with pytest.raises(asyncio.CancelledError):
        await job.run_backup_facts_snapshot_loop(db, interval_s=600)

    db.pool.assert_not_called()
    assert pool.rows[STATE_KEY]["configured"] is False
