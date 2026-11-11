"""The collector job: gated on its own writer DSN, one reduction per dependency per tick."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_health.model import Observation
from butlers.capability_health.publish import PublishResult
from butlers.core.capability_dependencies import DEPENDENCY_IDS
from butlers.jobs import capability_health as job

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class Publisher:
    def __init__(self, ok=True) -> None:
        self.ok = ok
        self.reductions = []

    async def publish(self, reduction, now):
        self.reductions.append((reduction, now))
        return PublishResult(self.ok, generation=1 if self.ok else None)


async def gather(now):
    return [
        Observation("google", "down", "oauth_revoked", now),
        Observation("steam", "available", None, now),
    ]


def by_dependency(publisher):
    return {r.dependency: r for r, _ in publisher.reductions}


async def test_one_tick_reduces_every_declared_dependency() -> None:
    publisher = Publisher()
    state = {}
    await job.run_tick(gather, publisher, state, NOW)
    got = by_dependency(publisher)
    assert set(got) == set(DEPENDENCY_IDS)
    assert (got["google"].state, got["google"].cause) == ("down", "oauth_revoked")
    assert got["steam"].state == "available"
    # Nothing measured a dependency: published as unmeasurable, never as available.
    assert got["spotify"].unmeasurable


async def test_an_unchanged_reduction_is_not_republished_before_the_heartbeat() -> None:
    publisher = Publisher()
    state = {}
    await job.run_tick(gather, publisher, state, NOW)
    first = len(publisher.reductions)
    await job.run_tick(gather, publisher, state, NOW + timedelta(seconds=60))
    assert len(publisher.reductions) == first
    await job.run_tick(gather, publisher, state, NOW + timedelta(seconds=301))
    assert len(publisher.reductions) == 2 * first


async def test_a_refused_publish_is_retried_next_tick() -> None:
    publisher = Publisher(ok=False)
    state = {}
    await job.run_tick(gather, publisher, state, NOW)
    first = len(publisher.reductions)
    await job.run_tick(gather, publisher, state, NOW + timedelta(seconds=1))
    assert len(publisher.reductions) == 2 * first


async def test_a_failing_collector_publishes_unmeasurable_not_stale_health() -> None:
    publisher = Publisher()
    state = {}

    async def broken(_now):
        raise RuntimeError("boom")

    await job.run_tick(broken, publisher, state, NOW)
    assert all(r.unmeasurable for r in by_dependency(publisher).values())


def test_no_writer_dsn_means_no_job(monkeypatch, caplog) -> None:
    monkeypatch.setattr(job, "_logged_not_configured", False)
    monkeypatch.delenv(job.WRITER_DSN_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://dashboard@host/db")
    caplog.set_level(logging.INFO)
    assert job.writer_dsn() is None
    assert job.writer_dsn() is None
    assert [r.getMessage() for r in caplog.records].count(job.NOT_CONFIGURED_MESSAGE) == 1
    assert "dashboard@host" not in caplog.text


def test_the_writer_dsn_is_only_read_from_its_own_variable(monkeypatch) -> None:
    monkeypatch.setenv(job.WRITER_DSN_ENV, "postgresql://perception_health_writer@h/db")
    assert job.writer_dsn() == "postgresql://perception_health_writer@h/db"
    monkeypatch.setenv(job.WRITER_DSN_ENV, "   ")
    assert job.writer_dsn() is None


async def test_the_loop_survives_a_failed_tick_and_stops_on_cancel() -> None:
    ticks = []

    async def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("first tick fails")

    task = asyncio.create_task(job.run_loop(tick, interval_s=0.001))
    while len(ticks) < 3:  # noqa: ASYNC110
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_a_non_positive_interval_is_refused() -> None:
    async def tick() -> None: ...

    with pytest.raises(ValueError):
        asyncio.run(job.run_loop(tick, interval_s=0))
