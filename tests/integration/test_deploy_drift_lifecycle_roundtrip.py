"""Real-Postgres regression: the migration-drift sentinel's condition-lifecycle
reconciliation (bu-27dxl.6.3).

Exercises butlers.jobs.deploy_drift.reconcile_drift_conditions against a
real, fully-migrated Postgres instance (testcontainers) writing through the
actual public.infra_conditions ledger, public.healing_attempts, and
public.audit_log tables -- not just the mocked-reconcile_snapshot unit tests
in tests/jobs/test_deploy_drift.py (mirroring the split used for
tests/integration/test_infra_conditions_roundtrip.py vs
tests/core/test_infra_conditions.py). compute_drift_report's own three-way
comparison against a real migrated schema remains covered, unchanged, by
tests/integration/test_deploy_drift_roundtrip.py.

Maps onto this child's acceptance criteria:
  - AC1: an ongoing drifted (schema, chain) pair advances through lifecycle
    levels instead of remaining permanently already-escalated.
  - AC2: a degraded/failed comparison (snapshot_complete=False) cannot
    resolve an active condition.
  - AC3: a complete clean comparison resolves once; recurrence opens a new
    episode.
  - AC4: L2+ re-escalation creates no further healing attempt.
  - AC5: the L1/L2+ audit rows carry the direct-audit-result attribution
    (result="detected"/"escalated"/"reescalated").
  - Mutable revisions remain evidence: identity survives expected/actual
    revision values changing mid-episode (spec.md "Mutable drift revisions
    remain evidence").
  - Race: concurrent reconciliation of the same (schema, chain) pair claims
    exactly one due escalation and opens exactly one healing_attempts row.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.jobs.deploy_drift import (
    ChainDrift,
    DriftReport,
    _drift_fingerprint,
    reconcile_drift_conditions,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=10)
    yield p
    await p.close()


def _drift(
    schema: str,
    chain: str = "core",
    expected_head: str = "core_005",
    actual: str | None = "core_003",
) -> ChainDrift:
    return ChainDrift(
        schema=schema, chain=chain, expected_head=expected_head, actual_revision=actual
    )


def _report(drifted: tuple[ChainDrift, ...] = (), *, check_error: str | None = None) -> DriftReport:
    return DriftReport(checked_at=datetime.now(UTC), drifted=drifted, check_error=check_error)


async def _force_due(pool: asyncpg.Pool, fingerprint: str) -> None:
    await pool.execute(
        """
        UPDATE public.infra_conditions
        SET next_reescalate_at = now() - INTERVAL '1 second'
        WHERE source = 'deployment_drift' AND fingerprint = $1
          AND state IN ('open', 'aging')
        """,
        fingerprint,
    )


async def _healing_attempt_count(pool: asyncpg.Pool, fingerprint: str) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM public.healing_attempts WHERE fingerprint = $1", fingerprint
    )


async def _audit_actions(pool: asyncpg.Pool, fingerprint: str) -> list[tuple[str, str | None]]:
    rows = await pool.fetch(
        "SELECT action, result FROM public.audit_log WHERE target = $1 ORDER BY ts ASC",
        fingerprint,
    )
    return [(r["action"], r["result"]) for r in rows]


class TestLifecycleProgressionAndAuditAttribution:
    async def test_l0_opens_l1_escalates_l2_reescalates_without_new_attempt(
        self, pool: asyncpg.Pool
    ) -> None:
        drift = _drift("finance-l0l1l2")
        fp = _drift_fingerprint(drift)

        opened = await reconcile_drift_conditions(pool, _report((drift,)))
        assert opened == [{"fingerprint": fp, "transition": "opened"}]
        assert await _healing_attempt_count(pool, fp) == 0

        await _force_due(pool, fp)
        l1 = await reconcile_drift_conditions(pool, _report((drift,)))
        assert len(l1) == 1
        assert l1[0]["transition"] == "escalation_due"
        assert l1[0]["escalated"] is True
        assert "healing_attempt_id" in l1[0]
        assert await _healing_attempt_count(pool, fp) == 1

        await _force_due(pool, fp)
        l2 = await reconcile_drift_conditions(pool, _report((drift,)))
        assert len(l2) == 1
        assert l2[0] == {
            "fingerprint": fp,
            "transition": "escalation_due",
            "escalation_level": "L2",
        }
        # AC4: L2 re-escalation must NOT create another healing_attempts row.
        assert await _healing_attempt_count(pool, fp) == 1

        actions = await _audit_actions(pool, fp)
        assert actions == [
            ("migration_drift_first_detected", "detected"),
            ("migration_drift_escalated", "escalated"),
            ("migration_drift_reescalated", "reescalated"),
        ]


class TestMutableRevisionsRemainEvidence:
    async def test_revision_change_confirms_same_episode_not_a_new_one(
        self, pool: asyncpg.Pool
    ) -> None:
        """spec.md 'Mutable drift revisions remain evidence': the SAME
        episode continues (and its escalation clock is undisturbed) even as
        expected_head/actual_revision change tick to tick."""
        first = _drift("finance-mutable-evidence", expected_head="core_005", actual="core_003")
        fp = _drift_fingerprint(first)

        opened = await reconcile_drift_conditions(pool, _report((first,)))
        assert opened[0]["transition"] == "opened"

        changed = _drift("finance-mutable-evidence", expected_head="core_006", actual="core_004")
        assert _drift_fingerprint(changed) == fp  # same identity despite different revisions

        confirmed = await reconcile_drift_conditions(pool, _report((changed,)))
        assert confirmed == [{"fingerprint": fp, "transition": "confirmed"}]

        row = await pool.fetchrow(
            "SELECT episode, metadata FROM public.infra_conditions "
            "WHERE source = 'deployment_drift' AND fingerprint = $1",
            fp,
        )
        assert row["episode"] == 1  # still the same episode, not a new one


class TestDegradedComparisonCannotResolve:
    async def test_check_error_leaves_active_condition_untouched(self, pool: asyncpg.Pool) -> None:
        drift = _drift("finance-degraded")
        fp = _drift_fingerprint(drift)

        await reconcile_drift_conditions(pool, _report((drift,)))

        # A degraded tick that doesn't even mention this fingerprint must
        # leave it exactly as-is.
        result = await reconcile_drift_conditions(pool, _report((), check_error="pool down"))
        assert result == []

        row = await pool.fetchrow(
            "SELECT state FROM public.infra_conditions "
            "WHERE source = 'deployment_drift' AND fingerprint = $1",
            fp,
        )
        assert row is not None
        assert row["state"] == "open"


class TestCompleteRecoveryAndRecurrence:
    async def test_resolves_once_then_recurrence_opens_new_episode(
        self, pool: asyncpg.Pool
    ) -> None:
        drift = _drift("finance-recur")
        fp = _drift_fingerprint(drift)

        opened = await reconcile_drift_conditions(pool, _report((drift,)))
        assert opened[0]["transition"] == "opened"

        resolved = await reconcile_drift_conditions(pool, _report(()))
        assert resolved == [{"fingerprint": fp, "transition": "resolved"}]

        reopened = await reconcile_drift_conditions(pool, _report((drift,)))
        assert reopened == [{"fingerprint": fp, "transition": "reopened"}]

        episodes = await pool.fetch(
            "SELECT episode, state FROM public.infra_conditions "
            "WHERE source = 'deployment_drift' AND fingerprint = $1 ORDER BY episode",
            fp,
        )
        assert [(e["episode"], e["state"]) for e in episodes] == [(1, "resolved"), (2, "open")]


class TestConcurrentReconciliationRace:
    async def test_concurrent_ticks_claim_exactly_one_l1_escalation(
        self, pool: asyncpg.Pool
    ) -> None:
        drift = _drift("finance-race")
        fp = _drift_fingerprint(drift)

        await reconcile_drift_conditions(pool, _report((drift,)))
        await _force_due(pool, fp)

        batches = await asyncio.gather(
            *[reconcile_drift_conditions(pool, _report((drift,))) for _ in range(8)]
        )
        flattened = [r for batch in batches for r in batch]
        escalations = [r for r in flattened if r.get("transition") == "escalation_due"]

        assert len(escalations) == 1
        assert escalations[0]["escalated"] is True
        assert await _healing_attempt_count(pool, fp) == 1
