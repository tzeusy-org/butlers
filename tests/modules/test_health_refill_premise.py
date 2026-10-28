"""Health medication-refill insights are premise-bound (bu-q7vx1q.30).

A refill reminder asserts "this supply runs out soon". The producer binds it to
the ``health``/``medication_refill_still_due`` probe so the broker withdraws it,
unsent, once the medication was refilled, paused or stopped.

Unit tests (no Docker) cover the producer binding and the probe verdicts. The
DB-backed class runs the real definer and ``delivery_cycle`` (CI-only).
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.insight_premise import probe_premise
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.insight import broker, premises

_docker_available = shutil.which("docker") is not None
_REPO = Path(__file__).resolve().parents[2]
_PINNED_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
_PROBE = ("health", "medication_refill_still_due")
_TELEGRAM_SENT = {
    "notification_id": "n-1",
    "status": "sent",
    "result": {"ok": True, "result": {"message_id": 77, "chat": {"id": 123}}},
}
_ARGS = {"subject": "medication:Metformin", "doses_per_day": 1.0, "warning_days": 14}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _health_jobs():
    return _load(_REPO / "roster/health/jobs/health_jobs.py", "_health_jobs_refill_premise")


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
    def _pool(self, verdict):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=verdict)
        return pool

    @pytest.mark.parametrize(
        ("verdict", "expected"),
        [(True, True), (False, False), (None, None)],
        ids=["due", "not-due", "unknown"],
    )
    async def test_the_definer_verdict_is_passed_through_tri_state(self, verdict, expected):
        premise = probe_premise(*_PROBE, _ARGS)
        assert await premises.check_premise(self._pool(verdict), premise) is expected

    async def test_the_probe_sends_subject_divisor_and_window_and_asks_for_one_verdict(self):
        pool = self._pool(True)
        await premises.check_premise(pool, probe_premise(*_PROBE, _ARGS))
        sql, *params = pool.fetchval.await_args.args
        assert "resolve_health_medication_refill" in sql
        assert params == ["medication:Metformin", 1.0, 14]

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
        pool = self._pool(True)
        assert await premises.check_premise(pool, probe_premise(*_PROBE, args)) is None
        pool.fetchval.assert_not_awaited()

    async def test_a_lookup_error_is_unknown_not_false(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
        assert await premises.check_premise(pool, probe_premise(*_PROBE, _ARGS)) is None

    async def test_removing_the_registry_entry_makes_the_premise_unknown(self):
        pool = self._pool(False)
        with patch.dict(premises.PREMISE_PROBES):
            del premises.PREMISE_PROBES[_PROBE]
            assert await premises.check_premise(pool, probe_premise(*_PROBE, _ARGS)) is None


@pytest.fixture
def refill_db_url(postgres_container) -> str:
    """Migrate a fresh database before entering this case's async fixture loop."""
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestRefillPremiseAgainstPostgres:
    @pytest.fixture
    async def pool(self, refill_db_url: str):
        pool = await asyncpg.create_pool(
            refill_db_url, min_size=1, max_size=3, init=register_jsonb_codec
        )
        try:
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
            yield pool
        finally:
            await pool.close()

    async def _med(
        self,
        pool,
        *,
        subject="medication:Metformin",
        active=True,
        quantity=10,
        validity="active",
        doses=0,
    ):
        # A dict, not json.dumps: the pool's jsonb codec already encodes it.
        meta = {
            "active": active,
            "quantity": quantity,
            "quantity_updated_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        }
        med_id = await pool.fetchval(
            "INSERT INTO health.facts (subject, predicate, scope, validity, metadata) "
            "VALUES ($3, 'medication', 'health', $1, $2::jsonb) RETURNING id",
            validity,
            meta,
            subject,
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

    async def test_a_refilled_reminder_is_withdrawn_at_send_and_a_due_one_is_not(self, pool):
        await self._med(pool, quantity=10)
        lisinopril = {**_ARGS, "subject": "medication:Lisinopril"}
        await self._med(pool, subject="medication:Lisinopril", quantity=10)
        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'minimal') "
            "ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'"
        )
        for key, name, args in (
            ("m1", "Metformin", _ARGS),
            ("m2", "Lisinopril", lisinopril),
        ):
            result = await broker.propose_insight_candidate(
                pool,
                origin_butler="health",
                priority=60,
                category="medication-refill",
                dedup_key=f"health:medication-refill:{key}",
                message=f"{name} supply estimated to run out in 8 day(s)",
                expires_at=_PINNED_NOW + timedelta(days=7),
                premise=probe_premise(*_PROBE, args),
                now=_PINNED_NOW,
            )
            assert result["status"] == "accepted"
        # Metformin is refilled (its fact is superseded by one with a large supply).
        await pool.execute(
            "UPDATE health.facts SET validity = 'superseded' WHERE subject = 'medication:Metformin'"
        )
        await self._med(pool, quantity=90)
        notify = AsyncMock(return_value=_TELEGRAM_SENT)
        cycle = await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)

        assert cycle["withdrawn"] == 1
        statuses = dict(await pool.fetch("SELECT dedup_key, status FROM insight_candidates"))
        assert statuses["health:medication-refill:m1"] == "withdrawn"
        assert statuses["health:medication-refill:m2"] != "withdrawn"
        sent = " ".join(str(c.args[0]) for c in notify.await_args_list)
        assert "Lisinopril" in sent and "Metformin" not in sent
