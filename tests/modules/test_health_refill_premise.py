"""Health medication-refill insights are premise-bound (bu-q7vx1q.30).

A refill reminder asserts "this supply runs out soon". The producer binds it to
the ``health``/``medication_refill_still_due`` probe so the broker withdraws it,
unsent, once the medication was refilled, paused or stopped.

Unit tests (no Docker) cover the producer binding and the probe verdicts. The
DB-backed class runs the real definer and ``delivery_cycle`` (CI-only).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.insight_premise import probe_premise
from butlers.tools.switchboard.insight import broker, premises

_docker_available = shutil.which("docker") is not None
_REPO = Path(__file__).resolve().parents[2]
_PINNED_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
_PROBE = ("health", "medication_refill_still_due")
_ARGS = {"subject": "medication:Metformin", "doses_per_day": 1.0, "warning_days": 14}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _health_jobs():
    return _load(_REPO / "roster/health/jobs/health_jobs.py", "_health_jobs_refill_premise")


def _migration():
    return _load(
        _REPO / "alembic/versions/core/core_270_health_refill_premise_probe.py",
        "_core_270_under_test",
    )


class TestRefillProducerBinding:
    async def _scan(self, med: dict) -> list[dict]:
        jobs = _health_jobs()
        pool = MagicMock()

        async def fetch(query, *args):
            if "predicate = 'medication'" in query:
                return [med]
            return []

        pool.fetch = AsyncMock(side_effect=fetch)
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=None)
        propose = AsyncMock(return_value={"status": "accepted"})
        with patch("butlers.tools.switchboard.insight.broker.propose_insight_candidate", propose):
            await jobs.run_insight_scan(pool)
        return [
            c.kwargs for c in propose.await_args_list if c.kwargs["category"] == "medication-refill"
        ]

    def _med(self, **overrides) -> dict:
        med = {
            "id": "11111111-1111-1111-1111-111111111111",
            "subject": "medication:Metformin",
            "name": "Metformin",
            "frequency": "daily",
            "quantity": 10,
            "quantity_updated_at": datetime.now(UTC) - timedelta(days=1),
        }
        med.update(overrides)
        return med

    async def test_a_refill_candidate_carries_the_supply_premise(self):
        (candidate,) = await self._scan(self._med())
        assert candidate["premise"] == probe_premise(
            "health",
            "medication_refill_still_due",
            {"subject": "medication:Metformin", "doses_per_day": 1.0, "warning_days": 14},
        )

    async def test_the_premise_binds_the_stable_subject_not_the_superseded_fact_id(self):
        (candidate,) = await self._scan(self._med(subject=None))
        assert candidate["premise"]["args"]["subject"] == "medication:Metformin"
        assert candidate["dedup_key"].endswith("11111111-1111-1111-1111-111111111111")

    async def test_thresholds_and_priority_are_unchanged(self):
        (candidate,) = await self._scan(self._med(quantity=2))
        assert candidate["priority"] == 90


class TestRefillProbeVerdicts:
    def _pool(self, row):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=row)
        return pool

    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            ({"is_active": True, "days_remaining": 3}, True),
            ({"is_active": True, "days_remaining": 14}, True),
            ({"is_active": True, "days_remaining": 40}, False),
            ({"is_active": False, "days_remaining": 3}, False),
            ({"is_active": False, "days_remaining": None}, False),
            ({"is_active": True, "days_remaining": None}, None),
            (None, None),
        ],
        ids=["due", "edge", "refilled", "stopped", "stopped-no-qty", "no-qty", "unknown"],
    )
    async def test_verdict(self, row, expected):
        premise = probe_premise(*_PROBE, _ARGS)
        assert await premises.check_premise(self._pool(row), premise) is expected

    @pytest.mark.parametrize(
        "args",
        [
            {},
            {**_ARGS, "subject": 7},
            {**_ARGS, "doses_per_day": 0},
            {**_ARGS, "doses_per_day": True},
            {**_ARGS, "warning_days": "14"},
        ],
    )
    async def test_malformed_args_are_unknown_and_never_query(self, args):
        pool = self._pool({"is_active": True, "days_remaining": 1})
        assert await premises.check_premise(pool, probe_premise(*_PROBE, args)) is None
        pool.fetchrow.assert_not_awaited()

    async def test_a_lookup_error_is_unknown_not_false(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
        assert await premises.check_premise(pool, probe_premise(*_PROBE, _ARGS)) is None

    async def test_removing_the_registry_entry_makes_the_premise_unknown(self):
        pool = self._pool({"is_active": False, "days_remaining": None})
        with patch.dict(premises.PREMISE_PROBES):
            del premises.PREMISE_PROBES[_PROBE]
            assert await premises.check_premise(pool, probe_premise(*_PROBE, _ARGS)) is None


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestRefillPremiseAgainstPostgres:
    @pytest.fixture
    async def pool(self, provisioned_postgres_pool):
        migration = _migration()
        async with provisioned_postgres_pool() as pool:
            await broker.create_insight_tables(pool)
            await pool.execute("CREATE SCHEMA IF NOT EXISTS health")
            await pool.execute(
                """
                CREATE TABLE health.facts (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    subject text NOT NULL, predicate text NOT NULL,
                    scope text NOT NULL DEFAULT 'global',
                    validity text NOT NULL DEFAULT 'active',
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    valid_at timestamptz,
                    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )
                """
            )
            await pool.execute(migration.CREATE_REFILL_FN)
            yield pool

    async def _med(self, pool, *, active=True, quantity=10, validity="active", doses=0):
        meta = json.dumps(
            {
                "active": active,
                "quantity": quantity,
                "quantity_updated_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            }
        )
        med_id = await pool.fetchval(
            "INSERT INTO health.facts (subject, predicate, scope, validity, metadata) "
            "VALUES ('medication:Metformin', 'medication', 'health', $1, $2::text::jsonb) RETURNING id",
            validity,
            meta,
        )
        for _ in range(doses):
            await pool.execute(
                "INSERT INTO health.facts (subject, predicate, scope, metadata, valid_at) "
                "VALUES ('dose', 'took_dose', 'health', jsonb_build_object('medication_id', $1::text), now())",
                str(med_id),
            )
        return med_id

    async def _verdict(self, pool):
        return await premises.check_premise(pool, probe_premise(*_PROBE, _ARGS))

    async def test_still_due_then_refilled_paused_or_stopped(self, pool):
        await self._med(pool, quantity=10, doses=3)
        assert await self._verdict(pool) is True  # 7 days left, inside 14

        await pool.execute("UPDATE health.facts SET validity = 'superseded'")
        await self._med(pool, quantity=90)  # refill supersedes the row
        assert await self._verdict(pool) is False

        await pool.execute("UPDATE health.facts SET validity = 'superseded'")
        await self._med(pool, active=False)  # paused
        assert await self._verdict(pool) is False

        await pool.execute("UPDATE health.facts SET validity = 'retracted'")
        assert await self._verdict(pool) is False  # stopped (deleted)

    async def test_an_unrecorded_medication_is_unknown(self, pool):
        assert await self._verdict(pool) is None

    async def test_a_refilled_reminder_is_withdrawn_at_send(self, pool):
        await self._med(pool, quantity=10)
        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'minimal') "
            "ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'"
        )
        result = await broker.propose_insight_candidate(
            pool,
            origin_butler="health",
            priority=60,
            category="medication-refill",
            dedup_key="health:medication-refill:m1",
            message="Metformin supply estimated to run out in 8 day(s)",
            expires_at=_PINNED_NOW + timedelta(days=7),
            premise=probe_premise(*_PROBE, _ARGS),
            now=_PINNED_NOW,
        )
        assert result["status"] == "accepted"
        await pool.execute("UPDATE health.facts SET validity = 'superseded'")
        await self._med(pool, quantity=90)
        notify = AsyncMock()
        await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)
        notify.assert_not_awaited()
        status = await pool.fetchval("SELECT status FROM insight_candidates")
        assert status == "withdrawn"
