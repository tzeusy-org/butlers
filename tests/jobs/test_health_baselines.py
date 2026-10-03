"""Real-seam tests for the health ``baseline_watch`` job (synthetic values only)."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_NOW = datetime(2026, 10, 3, 7, 0, tzinfo=UTC)
_AS_OF = date(2026, 10, 2)
_ENDPOINT = "google_health:user:owner"
_REPO = Path(__file__).resolve().parents[2]

_CREATE_FACTS = """
CREATE TABLE IF NOT EXISTS public.facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL DEFAULT 'owner',
    predicate TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'global',
    validity TEXT NOT NULL DEFAULT 'active',
    valid_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
)
"""


def _replay(migration_name: str):
    """Run one core migration's ``upgrade`` against the pool's current search_path."""
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
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await pool.execute("CREATE SCHEMA IF NOT EXISTS health")
    await pool.execute(_CREATE_FACTS)
    for name in (
        "core_184_owner_conditions.py",
        "core_203_condition_resolution_reason_top_level.py",
        "core_210_expected_signals.py",
        "core_211_expected_signal_endpoint_identity.py",
        "core_258_personal_baselines.py",
    ):
        for statement in _replay(name):
            await pool.execute(statement)
    await create_insight_tables(pool)
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS public._baseline_test_liveness ("
        "connector_type text, endpoint_identity text, state text, last_heartbeat_at timestamptz)"
    )
    await pool.execute(
        "CREATE OR REPLACE VIEW public.v_qa_connector_state AS "
        "SELECT connector_type, endpoint_identity, state, last_heartbeat_at "
        "FROM public._baseline_test_liveness"
    )


async def _connector(pool, state: str) -> None:
    await pool.execute(
        "INSERT INTO public._baseline_test_liveness VALUES ('google_health', $1, $2, $3)",
        _ENDPOINT,
        state,
        _NOW,
    )


async def _reading(pool, predicate: str, day: date, metadata: dict, *, hour: int = 6) -> None:
    await pool.execute(
        "INSERT INTO public.facts (predicate, scope, valid_at, metadata) "
        "VALUES ($1, 'health', $2, $3)",
        predicate,
        datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
        {"source": "google_health", "source_endpoint_identity": _ENDPOINT, **metadata},
    )


_USUAL = (54.0, 56.0, 58.0)


async def _seed_resting_hr(pool, *, elevated_days: int, window_gap_every: int = 10) -> None:
    """60 window days with 6 gaps (54 observed), then ``elevated_days`` high days."""
    window_last = _AS_OF - timedelta(days=3)
    for offset in range(60):
        if offset % window_gap_every == window_gap_every - 1:
            continue
        await _reading(
            pool,
            "measurement_resting_hr",
            window_last - timedelta(days=offset),
            {"value": _USUAL[offset % 3]},
        )
    for k in range(3):
        day = _AS_OF - timedelta(days=2 - k)
        high = k < 3 and (2 - k) < elevated_days
        await _reading(pool, "measurement_resting_hr", day, {"value": 70.0 if high else 56.0})


async def _candidates(pool):
    return await pool.fetch(
        "SELECT category, dedup_key, message, metadata, premise FROM insight_candidates "
        "WHERE category = 'baseline-deviation'"
    )


async def test_three_elevated_days_open_one_episode_and_one_candidate(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.health_baselines import CONDITION_SOURCE, run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        await _seed_resting_hr(pool, elevated_days=3)

        first = await run_baseline_watch(pool, now=_NOW)

        assert first["episodes_opened"] == 1
        assert first["insights_accepted"] == 1
        rows = await _candidates(pool)
        assert len(rows) == 1
        evidence = rows[0]["metadata"]["baseline_evidence"]
        assert (evidence["n_observed"], evidence["n_expected"]) == (54, 60)
        assert evidence["method_version"] == "mad-v1"
        premise = rows[0]["premise"]
        assert premise["kind"] == "owner_condition" and premise["source"] == CONDITION_SOURCE
        message = rows[0]["message"]
        assert "above your usual range" in message and "54 of 60 days" in message
        assert not any(word in message.lower() for word in ("consider", "should", "doctor"))

        episodes = await pool.fetch("SELECT * FROM metric_deviation_episodes")
        assert [(e["metric_key"], e["status"], e["direction"]) for e in episodes] == [
            ("resting_hr", "open", "above")
        ]
        assert episodes[0]["opened_on"] == _AS_OF - timedelta(days=2)
        assert episodes[0]["insight_proposed_at"] is not None
        condition = await pool.fetchrow(
            "SELECT state FROM public.owner_conditions WHERE source = $1", CONDITION_SOURCE
        )
        assert condition["state"] == "open"

        again = await run_baseline_watch(pool, now=_NOW)

        assert again["episodes_opened"] == 0 and again["insights_accepted"] == 0
        assert again["baselines_written"] == 0
        assert len(await _candidates(pool)) == 1
        assert await pool.fetchval("SELECT count(*) FROM metric_deviation_episodes") == 1
        # Per-schema storage: the tables exist in the butler's schema, not in public.
        assert await pool.fetchval("SELECT to_regclass('public.metric_baselines')") is None
        assert await pool.fetchval("SELECT to_regclass('health.metric_baselines')") is not None


async def test_two_elevated_days_stay_silent(provisioned_postgres_pool) -> None:
    from butlers.jobs.health_baselines import run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        await _seed_resting_hr(pool, elevated_days=2)

        result = await run_baseline_watch(pool, now=_NOW)

        assert result["episodes_opened"] == 0
        assert await _candidates(pool) == []
        band = await pool.fetchrow("SELECT status, n_observed FROM metric_baselines")
        assert (band["status"], band["n_observed"]) == ("ready", 54)


async def test_unhealthy_connector_is_unmeasurable_and_silent(provisioned_postgres_pool) -> None:
    from butlers.jobs.health_baselines import run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "offline")
        await _seed_resting_hr(pool, elevated_days=3)

        result = await run_baseline_watch(pool, now=_NOW)

        assert result["episodes_opened"] == 0
        assert result["states"]["resting_hr"] == "unmeasurable"
        assert await _candidates(pool) == []
        row = await pool.fetchrow(
            "SELECT measurability, status, center FROM metric_baselines"
            " WHERE metric_key = 'resting_hr'"
        )
        assert (row["measurability"], row["status"], row["center"]) == (
            "unmeasurable",
            "unmeasurable",
            None,
        )
        signal = await pool.fetchrow(
            "SELECT measurability FROM public.expected_signals"
            " WHERE signal_key = 'health:baseline:resting_hr'"
        )
        assert signal["measurability"] == "unmeasurable"


async def test_thin_history_is_insufficient_never_within(provisioned_postgres_pool) -> None:
    from butlers.jobs.health_baselines import run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        for offset in range(5):
            await _reading(
                pool, "measurement_resting_hr", _AS_OF - timedelta(days=offset), {"value": 80.0}
            )

        result = await run_baseline_watch(pool, now=_NOW)

        assert result["states"]["resting_hr"] == "insufficient_history"
        assert result["episodes_opened"] == 0 and await _candidates(pool) == []
        assert (
            await pool.fetchval(
                "SELECT status FROM metric_baselines WHERE metric_key = 'resting_hr'"
            )
            == "insufficient_history"
        )
        # Metrics with no readings at all are reported as such, not as a dead instrument.
        assert result["states"]["hrv"] == "no_reading"


async def test_return_to_band_closes_episode_resolves_condition_and_band_ignores_it(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.health_baselines import CONDITION_SOURCE, run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        await _seed_resting_hr(pool, elevated_days=3)
        await run_baseline_watch(pool, now=_NOW)
        center_during = await pool.fetchval("SELECT center FROM metric_baselines")

        next_day = _AS_OF + timedelta(days=1)
        await _reading(pool, "measurement_resting_hr", next_day, {"value": 56.0})
        await pool.execute(
            "UPDATE public._baseline_test_liveness SET last_heartbeat_at = $1",
            _NOW + timedelta(days=1),
        )
        closed = await run_baseline_watch(pool, now=_NOW + timedelta(days=1))

        assert closed["episodes_closed"] == 1 and closed["insights_accepted"] == 0
        episode = await pool.fetchrow("SELECT status, closed_on FROM metric_deviation_episodes")
        assert (episode["status"], episode["closed_on"]) == ("closed", next_day)
        condition = await pool.fetchrow(
            "SELECT state FROM public.owner_conditions WHERE source = $1", CONDITION_SOURCE
        )
        assert condition["state"] == "resolved"
        band = await pool.fetchrow("SELECT center, n_observed, n_expected FROM metric_baselines")
        assert band["center"] == center_during == 56.0
        assert len(await _candidates(pool)) == 1


async def test_concurrent_open_decisions_yield_one_episode(provisioned_postgres_pool) -> None:
    from butlers.core.baselines import EpisodeAction, EpisodeDecision, apply_episode_decision

    async with provisioned_postgres_pool(schema="health", max_pool_size=4) as pool:
        await _setup(pool)
        decision = EpisodeDecision(
            EpisodeAction.OPEN, opened_on=_AS_OF, direction="above", peak_mad=4.0
        )

        results = await asyncio.gather(
            *(apply_episode_decision(pool, "resting_hr", decision, as_of=_AS_OF) for _ in range(3))
        )

        assert sorted(r.action.value for r in results) == ["none", "none", "open"]
        assert await pool.fetchval("SELECT count(*) FROM metric_deviation_episodes") == 1


async def test_broker_rejects_a_deviation_claim_without_baseline_evidence(
    provisioned_postgres_pool,
) -> None:
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        base = dict(
            origin_butler="health",
            priority=50,
            category="baseline-deviation",
            dedup_key="health:baseline:resting_hr:x",
            message="Resting heart rate has been above your usual range.",
            expires_at=_NOW + timedelta(days=3),
            now=_NOW,
        )

        bare = await propose_insight_candidate(pool, **base)
        thin = await propose_insight_candidate(
            pool, **base, metadata={"baseline_evidence": {"n_observed": 54}}
        )

        assert bare["status"] == "error" and "baseline_evidence" in bare["reason"]
        assert thin["status"] == "error"
        assert await _candidates(pool) == []


async def test_dead_instrument_holds_an_open_episode_open(provisioned_postgres_pool) -> None:
    from butlers.jobs.health_baselines import CONDITION_SOURCE, run_baseline_watch

    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        await _seed_resting_hr(pool, elevated_days=3)
        await run_baseline_watch(pool, now=_NOW)

        # A normal reading arrives, but the connector's heartbeat is now a day old.
        await _reading(pool, "measurement_resting_hr", _AS_OF + timedelta(days=1), {"value": 56.0})
        held = await run_baseline_watch(pool, now=_NOW + timedelta(days=1))

        assert held["episodes_closed"] == 0
        assert held["states"]["resting_hr"] == "unmeasurable"
        assert await pool.fetchval("SELECT status FROM metric_deviation_episodes") == "open"
        assert (
            await pool.fetchval(
                "SELECT state FROM public.owner_conditions WHERE source = $1", CONDITION_SOURCE
            )
            == "open"
        )


async def test_sleep_is_summed_by_wake_day_and_short_nights_open_an_episode(
    provisioned_postgres_pool,
) -> None:
    from butlers.jobs.health_baselines import run_baseline_watch

    hour_ms = 3_600_000
    async with provisioned_postgres_pool(schema="health") as pool:
        await _setup(pool)
        await _connector(pool, "healthy")
        window_last = _AS_OF - timedelta(days=3)
        for offset in range(60):
            if offset % 10 == 9:
                continue
            wake_day = window_last - timedelta(days=offset)
            night = 7.0 + (offset % 3) * 0.5
            # Falls asleep the evening before; the whole night belongs to the wake day.
            start = datetime(wake_day.year, wake_day.month, wake_day.day, tzinfo=UTC)
            await pool.execute(
                "INSERT INTO public.facts (predicate, scope, valid_at, metadata)"
                " VALUES ('sleep_session', 'health', $1, $2)",
                start - timedelta(hours=2),
                {
                    "source": "google_health",
                    "source_endpoint_identity": _ENDPOINT,
                    "duration_ms": int((night + 1.0) * hour_ms),
                },
            )
        for k in range(3):
            wake_day = _AS_OF - timedelta(days=k)
            start = datetime(wake_day.year, wake_day.month, wake_day.day, tzinfo=UTC)
            await pool.execute(
                "INSERT INTO public.facts (predicate, scope, valid_at, metadata)"
                " VALUES ('sleep_session', 'health', $1, $2)",
                start - timedelta(hours=1),
                {
                    "source": "google_health",
                    "source_endpoint_identity": _ENDPOINT,
                    "duration_ms": int(4.0 * hour_ms),
                },
            )

        result = await run_baseline_watch(pool, now=_NOW)

        assert result["states"]["sleep_duration"] == "below"
        rows = await _candidates(pool)
        assert [r["dedup_key"].split(":")[2] for r in rows] == ["sleep_duration"]
        assert "below your usual range" in rows[0]["message"] and "hours" in rows[0]["message"]
