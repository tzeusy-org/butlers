"""Real-Postgres regression: the calendar sync deadman's condition-lifecycle
reconciliation (bu-27dxl.6.3).

Exercises butlers.jobs.calendar_sync_deadman.reconcile_calendar_conditions
against a real, fully-migrated Postgres instance (testcontainers) writing
through the actual public.infra_conditions ledger, public.healing_attempts,
and public.audit_log tables -- not just the mocked-reconcile_snapshot unit
tests in tests/jobs/test_calendar_sync_deadman.py (mirroring the split used
for tests/integration/test_infra_conditions_roundtrip.py vs
tests/core/test_infra_conditions.py).

Maps onto this child's acceptance criteria:
  - AC1: an ongoing stale-source condition advances through lifecycle levels
    instead of remaining permanently already-escalated.
  - AC2: a partial fan-out failure (snapshot_complete=False) cannot resolve
    an active condition.
  - AC3: a complete healthy snapshot resolves once; recurrence opens a new
    episode.
  - AC4: L2+ re-escalation creates no further healing attempt.
  - AC5: the L1/L2+ audit rows carry the direct-audit-result attribution
    (result="detected"/"escalated"/"reescalated").
  - Race: concurrent reconciliation of the same provider source claims
    exactly one due escalation and opens exactly one healing_attempts row.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.jobs.calendar_sync_deadman import (
    CalendarSyncDeadmanReport,
    StaleCalendarSource,
    _condition_fingerprint,
    reconcile_calendar_conditions,
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


def _stale(source_key: str, db_butler: str = "general") -> StaleCalendarSource:
    return StaleCalendarSource(
        source_key=source_key, db_butler=db_butler, butler_name=None, last_synced_at=None
    )


def _report(
    stale_sources: tuple[StaleCalendarSource, ...] = (),
    *,
    failed_butlers: tuple[str, ...] = (),
) -> CalendarSyncDeadmanReport:
    return CalendarSyncDeadmanReport(
        checked_at=datetime.now(UTC), stale_sources=stale_sources, failed_butlers=failed_butlers
    )


async def _force_due(pool: asyncpg.Pool, fingerprint: str) -> None:
    await pool.execute(
        """
        UPDATE public.infra_conditions
        SET next_reescalate_at = now() - INTERVAL '1 second'
        WHERE source = 'calendar_sync_deadman' AND fingerprint = $1
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
        stale = _stale("provider:google:l0l1l2")
        fp = _condition_fingerprint(stale)

        opened = await reconcile_calendar_conditions(pool, _report((stale,)))
        assert opened == [{"fingerprint": fp, "transition": "opened"}]
        assert await _healing_attempt_count(pool, fp) == 0

        await _force_due(pool, fp)
        l1 = await reconcile_calendar_conditions(pool, _report((stale,)))
        assert len(l1) == 1
        assert l1[0]["transition"] == "escalation_due"
        assert l1[0]["escalated"] is True
        assert "healing_attempt_id" in l1[0]
        assert await _healing_attempt_count(pool, fp) == 1

        await _force_due(pool, fp)
        l2 = await reconcile_calendar_conditions(pool, _report((stale,)))
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
            ("calendar_sync_deadman_first_detected", "detected"),
            ("calendar_sync_deadman_escalated", "escalated"),
            ("calendar_sync_deadman_reescalated", "reescalated"),
        ]


class TestPartialFailureCannotResolve:
    async def test_partial_fan_out_failure_leaves_active_condition_untouched(
        self, pool: asyncpg.Pool
    ) -> None:
        stale = _stale("provider:google:partial")
        fp = _condition_fingerprint(stale)

        await reconcile_calendar_conditions(pool, _report((stale,)))

        # A partial-failure tick that doesn't even mention this fingerprint
        # (its butler's fan-out failed) must leave it exactly as-is.
        result = await reconcile_calendar_conditions(pool, _report((), failed_butlers=("general",)))
        assert result == []

        row = await pool.fetchrow(
            "SELECT state FROM public.infra_conditions "
            "WHERE source = 'calendar_sync_deadman' AND fingerprint = $1",
            fp,
        )
        assert row is not None
        assert row["state"] == "open"


class TestCompleteRecoveryAndRecurrence:
    async def test_resolves_once_then_recurrence_opens_new_episode(
        self, pool: asyncpg.Pool
    ) -> None:
        stale = _stale("provider:google:recur")
        fp = _condition_fingerprint(stale)

        opened = await reconcile_calendar_conditions(pool, _report((stale,)))
        assert opened[0]["transition"] == "opened"

        resolved = await reconcile_calendar_conditions(pool, _report(()))
        assert resolved == [{"fingerprint": fp, "transition": "resolved"}]

        row = await pool.fetchrow(
            "SELECT state FROM public.infra_conditions "
            "WHERE source = 'calendar_sync_deadman' AND fingerprint = $1 "
            "ORDER BY episode DESC LIMIT 1",
            fp,
        )
        assert row["state"] == "resolved"

        reopened = await reconcile_calendar_conditions(pool, _report((stale,)))
        assert reopened == [{"fingerprint": fp, "transition": "reopened"}]

        episodes = await pool.fetch(
            "SELECT episode, state FROM public.infra_conditions "
            "WHERE source = 'calendar_sync_deadman' AND fingerprint = $1 ORDER BY episode",
            fp,
        )
        assert [(e["episode"], e["state"]) for e in episodes] == [(1, "resolved"), (2, "open")]


class TestOneProviderDoesNotMaskAnother:
    async def test_provider_recovery_resolves_only_its_own_condition(
        self, pool: asyncpg.Pool
    ) -> None:
        a = _stale("provider:google:a-independent")
        b = _stale("provider:google:b-independent")
        fp_a = _condition_fingerprint(a)
        fp_b = _condition_fingerprint(b)

        await reconcile_calendar_conditions(pool, _report((a, b)))

        # b recovers (absent from a complete snapshot); a is still stale.
        result = await reconcile_calendar_conditions(pool, _report((a,)))
        by_fp = {r["fingerprint"]: r for r in result}
        assert by_fp[fp_b]["transition"] == "resolved"
        assert by_fp[fp_a]["transition"] == "confirmed"

        a_row = await pool.fetchrow(
            "SELECT state FROM public.infra_conditions "
            "WHERE source = 'calendar_sync_deadman' AND fingerprint = $1 "
            "ORDER BY episode DESC LIMIT 1",
            fp_a,
        )
        assert a_row["state"] == "open"


class TestConcurrentReconciliationRace:
    async def test_concurrent_ticks_claim_exactly_one_l1_escalation(
        self, pool: asyncpg.Pool
    ) -> None:
        stale = _stale("provider:google:race")
        fp = _condition_fingerprint(stale)

        await reconcile_calendar_conditions(pool, _report((stale,)))
        await _force_due(pool, fp)

        batches = await asyncio.gather(
            *[reconcile_calendar_conditions(pool, _report((stale,))) for _ in range(8)]
        )
        flattened = [r for batch in batches for r in batch]
        escalations = [r for r in flattened if r.get("transition") == "escalation_due"]

        assert len(escalations) == 1
        assert escalations[0]["escalated"] is True
        assert await _healing_attempt_count(pool, fp) == 1
