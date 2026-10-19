"""Real-Postgres read-back of what ``baseline_watch`` stores (bu-q7vx1q.77).

Docker-gated like ``test_health_baselines.py``, whose seeding helpers it reuses.
"""

from __future__ import annotations

import shutil
from datetime import timedelta

import pytest

from tests.jobs.test_health_baselines import (
    _NOW,
    _connector,
    _seed_resting_hr,
    _setup,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


async def test_read_baseline_returns_what_the_job_stored(provisioned_postgres_pool) -> None:
    from butlers.core.baselines import read_baseline
    from butlers.jobs.health_baselines import run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        await _seed_resting_hr(pool, elevated_days=2)
        await run_baseline_watch(pool, now=_NOW)

        stored = await pool.fetchrow(
            "SELECT center, dispersion, k_mad, computed_at FROM metric_baselines"
            " WHERE metric_key = 'resting_hr'"
        )
        reading = await read_baseline(pool, "resting_hr", now=_NOW)

        assert reading.state == "ready"
        assert reading.stale is False
        assert (reading.n_observed, reading.n_expected) == (54, 60)
        assert reading.coverage == pytest.approx(0.9)
        spread = stored["k_mad"] * stored["dispersion"]
        assert reading.band == pytest.approx((stored["center"] - spread, stored["center"] + spread))
        assert reading.computed_at == stored["computed_at"]


async def test_read_baseline_goes_stale_past_twice_the_refresh_interval(
    provisioned_postgres_pool,
) -> None:
    from butlers.core.baselines import read_baseline
    from butlers.jobs.health_baselines import run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        await _seed_resting_hr(pool, elevated_days=2)
        await run_baseline_watch(pool, now=_NOW)

        fresh = await read_baseline(pool, "resting_hr", now=_NOW + timedelta(days=2))
        stale = await read_baseline(pool, "resting_hr", now=_NOW + timedelta(days=2, minutes=1))

        assert fresh.state == "ready"
        assert stale.state == "stale" and stale.stale is True and stale.band is None


async def test_unknown_key_and_unmeasurable_instrument_read_as_words(
    provisioned_postgres_pool,
) -> None:
    from butlers.core.baselines import read_baseline
    from butlers.jobs.health_baselines import run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "offline")
        await _seed_resting_hr(pool, elevated_days=2)
        await run_baseline_watch(pool, now=_NOW)

        unknown = await read_baseline(pool, "no_such_metric", now=_NOW)
        gap = await read_baseline(pool, "resting_hr", now=_NOW)

        assert unknown.state == "no_baseline" and unknown.band is None
        assert gap.state == "unmeasurable" and gap.band is None
