"""Real-Postgres regression: the calendar overcommitment producer (bu-vcjgo8).

Drives ``run_calendar_overcommitment_check`` against a migrated ``public.owner_conditions``
ledger, faking only the cross-schema conflict scan and the owner timezone read. The unit tests
in ``tests/jobs/test_calendar_overcommitment.py`` mock ``reconcile_snapshot``; this file proves
the producer's actual ledger outcomes: open, resolve by omission, a partial scan that never
resolves, and concurrent scans that confirm one episode.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.api.read_models.calendar_workspace import CalendarConflictScan
from butlers.core.temporal.conflicts import ConflictEventRef, DetectedIssue
from butlers.jobs import calendar_overcommitment as job

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_DAY = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


class _Db:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def pool(self, name: str) -> Any:
        if name != "switchboard":
            raise KeyError(name)
        return self._pool


def _ref(entry_id: str, start: datetime) -> ConflictEventRef:
    return ConflictEventRef(
        entry_id=entry_id,
        title="private",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone="UTC",
        status="confirmed",
    )


def _overlap() -> DetectedIssue:
    return DetectedIssue(
        kind="overlap",
        date="2026-07-01",
        summary="private",
        severity="warning",
        events=[_ref("a", _DAY), _ref("b", _DAY + timedelta(minutes=30))],
    )


def _scan(*issues: DetectedIssue, available: bool = True) -> CalendarConflictScan:
    return CalendarConflictScan(
        issues=list(issues),
        available=available,
        entry_identity_keys={"a": "origin:A", "b": "origin:B"},
    )


@pytest.fixture
def scans(monkeypatch) -> AsyncMock:
    monkeypatch.setattr(job, "load_general_settings", AsyncMock(return_value={"timezone": "UTC"}))
    scan = AsyncMock()
    monkeypatch.setattr(job, "query_calendar_conflicts", scan)
    return scan


async def _episodes(pool: asyncpg.Pool) -> list[tuple[int, str]]:
    rows = await pool.fetch(
        "SELECT episode, state FROM public.owner_conditions "
        "WHERE source = 'calendar:overcommitment' ORDER BY episode"
    )
    return [(r["episode"], r["state"]) for r in rows]


async def test_open_then_partial_scan_keeps_then_complete_scan_resolves(
    db_pool: asyncpg.Pool, scans: AsyncMock
) -> None:
    db = _Db(db_pool)

    scans.return_value = _scan(_overlap())
    assert (await job.run_calendar_overcommitment_check(db))["transitions"] == ["opened"]

    scans.return_value = _scan(available=False)
    assert (await job.run_calendar_overcommitment_check(db))["transitions"] == []
    assert await _episodes(db_pool) == [(1, "open")]

    scans.return_value = _scan()
    assert (await job.run_calendar_overcommitment_check(db))["transitions"] == ["resolved"]
    assert await _episodes(db_pool) == [(1, "resolved")]


async def test_concurrent_scans_of_one_calendar_keep_one_active_episode(
    db, scans: AsyncMock
) -> None:
    pool = await db("core", min_size=2, max_size=10)
    scans.return_value = _scan(_overlap())

    results = await asyncio.gather(
        *[job.run_calendar_overcommitment_check(_Db(pool)) for _ in range(8)]
    )

    transitions = sorted(t for r in results for t in r["transitions"])
    assert transitions == ["confirmed"] * 7 + ["opened"]
    assert await _episodes(pool) == [(1, "open")]
