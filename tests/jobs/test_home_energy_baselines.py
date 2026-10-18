"""Real-seam tests for the Home energy week-bucket baselines (bu-q7vx1q.78, synthetic values)."""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_AS_OF = date(2026, 10, 12)
_NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
_REPO = Path(__file__).resolve().parents[2]
_DEVICE = "sensor.dryer_energy"
_DEVICE_KEY = f"energy.device:{_DEVICE}"

_CREATE_FACTS = """
CREATE TABLE IF NOT EXISTS public.facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL DEFAULT 'overall',
    predicate TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    validity TEXT NOT NULL DEFAULT 'active',
    content_authority TEXT,
    source_butler TEXT,
    tags JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _replay(migration_name: str) -> list[str]:
    path = _REPO / "alembic/versions/core" / migration_name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(module, "op", mocked_op):
        module.upgrade()
    return statements


async def _setup(pool) -> None:
    await pool.execute("CREATE SCHEMA IF NOT EXISTS home")
    await pool.execute(_CREATE_FACTS)
    for statement in _replay("core_258_personal_baselines.py"):
        await pool.execute(statement)


def _history(values: list[float]) -> dict[date, float]:
    """Weekly totals for the weeks before ``_AS_OF``, most recent first."""
    return {_AS_OF - timedelta(days=7 * (i + 1)): v for i, v in enumerate(values)}


async def _score(pool, *, history: list[float], this_week: float):
    from butlers.jobs.home_baselines import score_energy_week

    devices = [{"entity_id": _DEVICE, "friendly_name": "Dryer", "weekly_kwh": this_week}]
    return await score_energy_week(
        pool,
        devices=devices,
        total_kwh=this_week,
        history={_DEVICE: _history(history)},
        as_of=_AS_OF,
        now=_NOW,
    )


async def test_a_seventh_week_above_six_stored_weeks_is_one_anomaly_with_its_band(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.home_baselines import anomalies_from

    async with provisioned_postgres_pool(schema="home") as pool:
        await _setup(pool)

        findings, changed = await _score(pool, history=[20, 22, 21, 19, 23, 21], this_week=40.0)

        (anomaly,) = anomalies_from(findings)
        assert anomaly["entity_id"] == _DEVICE
        assert anomaly["baseline_kwh"] == 21
        assert anomaly["usual_low"] < 21 < anomaly["usual_high"] < 40.0
        assert (anomaly["n_observed"], anomaly["n_expected"]) == (6, 8)
        assert changed is True
        row = await pool.fetchrow(
            "SELECT status, center, n_observed, n_expected FROM metric_baselines "
            "WHERE metric_key = $1",
            _DEVICE_KEY,
        )
        assert row["status"] == "ready" and row["center"] == 21
        assert (row["n_observed"], row["n_expected"]) == (6, 8)


async def test_three_weeks_of_history_is_not_enough_weeks_yet_and_no_anomaly(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.home_baselines import anomalies_from, unjudged_names

    async with provisioned_postgres_pool(schema="home") as pool:
        await _setup(pool)

        findings, _ = await _score(pool, history=[20, 22, 21], this_week=90.0)

        assert anomalies_from(findings) == []
        assert unjudged_names(findings) == ["Dryer"]
        row = await pool.fetchrow(
            "SELECT status, center FROM metric_baselines WHERE metric_key = $1", _DEVICE_KEY
        )
        assert row["status"] == "insufficient_history" and row["center"] is None


async def test_within_the_band_is_no_anomaly(provisioned_postgres_pool) -> None:
    from butlers.jobs.home_baselines import anomalies_from

    async with provisioned_postgres_pool(schema="home") as pool:
        await _setup(pool)

        findings, _ = await _score(pool, history=[20, 22, 21, 19, 23, 21], this_week=22.0)

        assert anomalies_from(findings) == []


async def test_a_rerun_for_the_same_week_changes_no_band_and_opens_no_second_episode(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.home_baselines import anomalies_from

    async with provisioned_postgres_pool(schema="home") as pool:
        await _setup(pool)
        history = [20, 22, 21, 19, 23, 21]
        first, _ = await _score(pool, history=history, this_week=40.0)
        before = await pool.fetch(
            "SELECT metric_key, input_digest, computed_at FROM metric_baselines ORDER BY 1"
        )

        second, changed = await _score(pool, history=history, this_week=40.0)

        after = await pool.fetch(
            "SELECT metric_key, input_digest, computed_at FROM metric_baselines ORDER BY 1"
        )
        assert changed is False
        assert [dict(r) for r in after] == [dict(r) for r in before]
        assert len(anomalies_from(first)) == len(anomalies_from(second)) == 1
        episodes = await pool.fetchval(
            "SELECT count(*) FROM metric_deviation_episodes WHERE metric_key = $1", _DEVICE_KEY
        )
        assert episodes == 1


async def test_an_anomalous_week_is_left_out_of_the_next_weeks_band(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="home") as pool:
        await _setup(pool)
        await _score(pool, history=[20, 22, 21, 19, 23, 21], this_week=40.0)
        next_week = _AS_OF + timedelta(days=7)
        from butlers.jobs.home_baselines import score_energy_week

        history = {_AS_OF: 40.0, **_history([20, 22, 21, 19, 23, 21])}
        await score_energy_week(
            pool,
            devices=[{"entity_id": _DEVICE, "friendly_name": "Dryer", "weekly_kwh": 21.0}],
            total_kwh=21.0,
            history={_DEVICE: history},
            as_of=next_week,
            now=_NOW + timedelta(days=7),
        )

        row = await pool.fetchrow(
            "SELECT center, n_observed, n_expected FROM metric_baselines WHERE metric_key = $1",
            _DEVICE_KEY,
        )
        # The 40 kWh week is an open episode: neither observed nor expected, centre stays 21.
        assert row["center"] == 21
        assert (row["n_observed"], row["n_expected"]) == (6, 7)


async def _fact(pool, *, predicate: str, authority: str, source: str, tags: list[str]) -> str:
    return str(
        await pool.fetchval(
            "INSERT INTO public.facts (predicate, content_authority, source_butler, tags) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            predicate,
            authority,
            source,
            tags,
        )
    )


async def test_only_the_digest_jobs_own_prose_facts_are_retracted(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.home_baselines import retire_prose_energy_baselines

    async def retract(pool, memory_type, memory_id, **_kwargs) -> bool:
        await pool.execute(
            "UPDATE public.facts SET validity = 'retracted' WHERE id = $1", memory_id
        )
        return True

    job_tags = ["energy", "baseline", "weekly"]
    async with provisioned_postgres_pool(schema="home") as pool:
        await _setup(pool)
        job_overall = await _fact(
            pool, predicate="energy_baseline", authority="system", source="home", tags=job_tags
        )
        job_device = await _fact(
            pool,
            predicate="energy_baseline",
            authority="system",
            source="home",
            tags=[*job_tags, "per-device"],
        )
        owner = await _fact(
            pool, predicate="energy_baseline", authority="owner", source="home", tags=job_tags
        )
        owner_device = await _fact(
            pool,
            predicate="energy_baseline",
            authority="owner_device",
            source="home",
            tags=job_tags,
        )
        other = await _fact(
            pool, predicate="energy_spike", authority="system", source="home", tags=job_tags
        )

        with patch("butlers.modules.memory.storage.forget_memory", AsyncMock(side_effect=retract)):
            first = await retire_prose_energy_baselines(pool)
            second = await retire_prose_energy_baselines(pool)

        validity = {
            str(r["id"]): r["validity"]
            for r in await pool.fetch("SELECT id, validity FROM public.facts")
        }
        assert (first, second) == (2, 0)
        assert validity[job_overall] == validity[job_device] == "retracted"
        assert validity[owner] == validity[owner_device] == validity[other] == "active"
