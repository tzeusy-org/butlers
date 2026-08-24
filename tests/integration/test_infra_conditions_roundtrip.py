"""Real-Postgres regression: the infra_conditions ledger and lifecycle service.

Exercises core_182 (bu-27dxl.6.2) against a fully migrated Postgres instance
(testcontainers) through the actual production writer in
``butlers.core.infra_conditions`` — not just the mocked-pool unit tests in
``tests/core/test_infra_conditions.py`` (mirrors the split used for
``tests/integration/test_delegation_ledger_roundtrip.py`` vs
``tests/core/test_delegation_ledger.py``).

AC1-4 from bu-27dxl.6.2 map directly onto this file's test classes:
  - AC1: a complete clean snapshot resolves an active condition exactly once.
  - AC2: a failed/incomplete snapshot cannot resolve it.
  - AC3: recurrence creates a new episode and preserves resolved history.
  - AC4: concurrent observations produce one active condition and one due
    transition per level.

The concurrency assertions here are the reason this module exists at all —
`reconcile_snapshot`'s transaction-scoped advisory lock and the partial
unique index it is backed by cannot be exercised meaningfully against a
mocked pool.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import asyncpg
import pytest

from butlers.core.infra_conditions import (
    Observation,
    compute_fingerprint,
    get_active_condition,
    list_conditions,
    reconcile_snapshot,
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


def _fp(name: str) -> str:
    return compute_fingerprint("deploy_drift", 1, {"probe": name})


# ---------------------------------------------------------------------------
# AC1 — complete clean snapshot resolves an active condition exactly once.
# ---------------------------------------------------------------------------


class TestCompleteSnapshotResolution:
    async def test_opens_then_resolves_on_absence_from_a_complete_snapshot(
        self, pool: asyncpg.Pool
    ) -> None:
        fp = _fp("ac1-basic")
        opened = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[Observation(fingerprint=fp, summary="drift detected")],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert len(opened) == 1
        assert opened[0].transition == "opened"
        assert opened[0].state == "open"
        assert opened[0].episode == 1

        active = await get_active_condition(pool, source="deploy_drift", fingerprint=fp)
        assert active is not None
        assert active["state"] == "open"

        # Next complete snapshot no longer observes it -> resolves exactly once.
        resolved = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        matching = [r for r in resolved if r.fingerprint == fp]
        assert len(matching) == 1
        assert matching[0].transition == "resolved"
        assert matching[0].resolved_at is not None
        assert matching[0].recovered_after_s is not None
        assert matching[0].recovered_after_s >= 0

        assert await get_active_condition(pool, source="deploy_drift", fingerprint=fp) is None

        # A THIRD complete snapshot has nothing active to resolve for this
        # fingerprint again -- it must not re-resolve or duplicate anything.
        again = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert [r for r in again if r.fingerprint == fp] == []


# ---------------------------------------------------------------------------
# AC2 — failed/incomplete snapshots cannot resolve.
# ---------------------------------------------------------------------------


class TestIncompleteSnapshotCannotResolve:
    async def test_snapshot_incomplete_confirms_but_never_resolves_by_omission(
        self, pool: asyncpg.Pool
    ) -> None:
        fp = _fp("ac2-incomplete")
        await reconcile_snapshot(
            pool,
            source="calendar_sync_deadman",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )

        # A degraded/partial run that doesn't even mention this fingerprint
        # must leave it exactly as-is.
        incomplete = await reconcile_snapshot(
            pool,
            source="calendar_sync_deadman",
            observations=[],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        assert incomplete == []

        active = await get_active_condition(pool, source="calendar_sync_deadman", fingerprint=fp)
        assert active is not None
        assert active["state"] == "open"

    async def test_incomplete_snapshot_still_confirms_what_it_did_observe(
        self, pool: asyncpg.Pool
    ) -> None:
        fp = _fp("ac2-confirm-degraded")
        await reconcile_snapshot(
            pool,
            source="calendar_sync_deadman",
            observations=[Observation(fingerprint=fp, summary="v1")],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        before = await get_active_condition(pool, source="calendar_sync_deadman", fingerprint=fp)

        confirmed = await reconcile_snapshot(
            pool,
            source="calendar_sync_deadman",
            observations=[Observation(fingerprint=fp, summary="v2 (degraded run)")],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        assert len(confirmed) == 1
        assert confirmed[0].transition == "confirmed"

        after = await get_active_condition(pool, source="calendar_sync_deadman", fingerprint=fp)
        assert after["summary"] == "v2 (degraded run)"
        assert after["last_confirmed_at"] > before["last_confirmed_at"]
        assert after["state"] == "open"


# ---------------------------------------------------------------------------
# AC3 — recurrence creates a new episode and preserves resolved history.
# ---------------------------------------------------------------------------


class TestRecurrencePreservesHistory:
    async def test_reopen_after_resolution_creates_episode_two(self, pool: asyncpg.Pool) -> None:
        fp = _fp("ac3-recurrence")
        opened = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert opened[0].episode == 1
        assert opened[0].transition == "opened"

        resolved = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert resolved[0].transition == "resolved"
        first_episode_id = resolved[0].condition_id

        reopened = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert len(reopened) == 1
        assert reopened[0].transition == "reopened"
        assert reopened[0].episode == 2
        assert reopened[0].condition_id != first_episode_id

        total, rows = await list_conditions(pool, source="deploy_drift")
        episodes_for_fp = sorted((r["episode"], r["state"]) for r in rows if r["fingerprint"] == fp)
        assert episodes_for_fp == [(1, "resolved"), (2, "open")]

        # The resolved episode-1 row's original evidence is untouched.
        resolved_row = next(r for r in rows if r["fingerprint"] == fp and r["episode"] == 1)
        assert resolved_row["resolved_at"] is not None
        assert resolved_row["recovered_after_s"] is not None


# ---------------------------------------------------------------------------
# AC4 — concurrent observations: one active condition, one due transition
# per level.
# ---------------------------------------------------------------------------


class TestConcurrency:
    async def test_concurrent_first_observations_open_exactly_one_episode(
        self, pool: asyncpg.Pool
    ) -> None:
        fp = _fp("ac4-concurrent-open")

        async def _observe():
            return await reconcile_snapshot(
                pool,
                source="deploy_drift",
                observations=[Observation(fingerprint=fp)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            )

        batches = await asyncio.gather(*[_observe() for _ in range(8)])
        transitions = [r.transition for batch in batches for r in batch]

        assert transitions.count("opened") == 1
        assert transitions.count("confirmed") == 7

        total, rows = await list_conditions(pool, source="deploy_drift")
        matching_rows = [r for r in rows if r["fingerprint"] == fp]
        assert len(matching_rows) == 1
        assert matching_rows[0]["state"] == "open"

    async def test_concurrent_confirmations_claim_exactly_one_due_escalation(
        self, pool: asyncpg.Pool
    ) -> None:
        fp = _fp("ac4-concurrent-escalate")
        # initial_grace_seconds=0 -> immediately due for L1 on the next confirm.
        await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=0,
        )

        async def _confirm():
            return await reconcile_snapshot(
                pool,
                source="deploy_drift",
                observations=[Observation(fingerprint=fp)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            )

        batches = await asyncio.gather(*[_confirm() for _ in range(8)])
        transitions = [r.transition for batch in batches for r in batch]

        assert transitions.count("escalation_due") == 1
        assert transitions.count("confirmed") == 7

        active = await get_active_condition(pool, source="deploy_drift", fingerprint=fp)
        assert active["state"] == "aging"
        assert active["escalation_level"] == "L1"

    async def test_different_sources_do_not_contend(self, pool: asyncpg.Pool) -> None:
        fp_a = _fp("ac4-source-a")
        fp_b = compute_fingerprint("calendar_sync_deadman", 1, {"probe": "ac4-source-b"})

        results = await asyncio.gather(
            reconcile_snapshot(
                pool,
                source="deploy_drift",
                observations=[Observation(fingerprint=fp_a)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            ),
            reconcile_snapshot(
                pool,
                source="calendar_sync_deadman",
                observations=[Observation(fingerprint=fp_b)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            ),
        )
        assert [r.transition for r in results[0]] == ["opened"]
        assert [r.transition for r in results[1]] == ["opened"]


# ---------------------------------------------------------------------------
# Escalation level progression (spec.md "Bounded lifecycle escalation").
# ---------------------------------------------------------------------------


class TestEscalationProgression:
    async def test_l0_through_l3_repeat_progression(self, pool: asyncpg.Pool) -> None:
        fp = _fp("escalation-progression")
        opened = await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=0,
        )
        assert opened[0].escalation_level == "L0"
        assert opened[0].state == "open"

        seen_levels = []
        for _ in range(4):
            result = await reconcile_snapshot(
                pool,
                source="deploy_drift",
                observations=[Observation(fingerprint=fp)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            )
            seen_levels.append((result[0].transition, result[0].escalation_level))

        # Only the very first confirm after open is due (L0->L1, scheduled at
        # open time via initial_grace_seconds=0). L1->L2 is scheduled a full
        # day out, so the next three back-to-back confirms in this test all
        # land well inside that window and read as ordinary confirms at L1 --
        # exercising the full L2/L3/L3-repeat progression needs simulated
        # time, which is out of scope here; this asserts the one
        # guaranteed-due transition and that nothing over-claims early.
        assert seen_levels[0] == ("escalation_due", "L1")
        assert all(level == "L1" for _transition, level in seen_levels[1:])
        assert all(transition == "confirmed" for transition, _level in seen_levels[1:])

        active = await get_active_condition(pool, source="deploy_drift", fingerprint=fp)
        assert active["state"] == "aging"
        assert active["escalation_level"] == "L1"

    async def test_l1_l2_l3_and_l3_repeat_intervals(self, pool: asyncpg.Pool) -> None:
        """Directly forces each level's next_reescalate_at into the past (rather
        than waiting real days) to exercise _ESCALATION_ADVANCE's full mapping:
        L1->L2 (+1d), L2->L3 (+3d), and L3->L3 repeating (+7d) -- spec.md's
        "Bounded lifecycle escalation and recurrence"."""
        fp = _fp("escalation-full-mapping")
        await reconcile_snapshot(
            pool,
            source="deploy_drift",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=0,
        )

        async def _force_due_and_confirm():
            await pool.execute(
                """
                UPDATE public.infra_conditions
                SET next_reescalate_at = now() - INTERVAL '1 second'
                WHERE source = 'deploy_drift' AND fingerprint = $1
                  AND state IN ('open', 'aging')
                """,
                fp,
            )
            result = await reconcile_snapshot(
                pool,
                source="deploy_drift",
                observations=[Observation(fingerprint=fp)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            )
            return result[0]

        l1 = await _force_due_and_confirm()
        assert (l1.transition, l1.escalation_level) == ("escalation_due", "L1")

        l2 = await _force_due_and_confirm()
        assert (l2.transition, l2.escalation_level) == ("escalation_due", "L2")

        l3 = await _force_due_and_confirm()
        assert (l3.transition, l3.escalation_level) == ("escalation_due", "L3")

        l3_repeat = await _force_due_and_confirm()
        assert (l3_repeat.transition, l3_repeat.escalation_level) == ("escalation_due", "L3")

        active = await get_active_condition(pool, source="deploy_drift", fingerprint=fp)
        assert active["state"] == "aging"
        assert active["escalation_level"] == "L3"
        assert active["next_reescalate_at"] > active["last_escalated_at"]


# ---------------------------------------------------------------------------
# Identity-version-bump totality (bu-27dxl.6.2 review-input / bu-rxo0l seam).
# ---------------------------------------------------------------------------


class TestIdentityVersionBumpProvenance:
    async def test_complete_v2_snapshot_marks_v1_absence_as_superseded_and_links_episodes(
        self, pool: asyncpg.Pool
    ) -> None:
        """A declared v2 successor makes the v1 absence honest, not recovery."""
        source = "migration_drift"
        old_fp = compute_fingerprint(source, 1, {"chain": "core"})
        new_fp = compute_fingerprint(source, 2, {"chain": "core"})
        assert old_fp != new_fp

        opened = await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=old_fp, identity_version=1)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert opened[0].transition == "opened"

        # The producer bumps its identity version: its next complete snapshot
        # only ever emits the NEW fingerprint. It never mentions old_fp again.
        migrated = await reconcile_snapshot(
            pool,
            source=source,
            observations=[
                Observation(
                    fingerprint=new_fp,
                    identity_version=2,
                    predecessor_fingerprint=old_fp,
                )
            ],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        by_fp = {r.fingerprint: r for r in migrated}
        assert by_fp[old_fp].transition == "resolved"
        assert by_fp[new_fp].transition == "opened"
        assert by_fp[new_fp].episode == 1

        assert await get_active_condition(pool, source=source, fingerprint=old_fp) is None
        new_active = await get_active_condition(pool, source=source, fingerprint=new_fp)
        assert new_active is not None
        assert new_active["state"] == "open"
        old_episode = await pool.fetchrow(
            "SELECT id, metadata FROM public.infra_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            old_fp,
        )
        assert old_episode is not None
        old_metadata = json.loads(old_episode["metadata"])
        # bu-o4i4j: the terminal reason lands top-level, the same place an
        # explicit resolution writes it; only the successor lineage nests.
        assert old_metadata["resolution_reason"] == "superseded_by_identity_version_bump"
        assert old_metadata["identity_payload"] == {
            "version": 1,
            "successor": {
                "condition_id": str(new_active["id"]),
                "fingerprint": new_fp,
                "version": 2,
            },
        }
        reconfirmed = await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=new_fp, identity_version=2)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert reconfirmed[0].transition == "confirmed"
        new_active = await get_active_condition(pool, source=source, fingerprint=new_fp)
        assert new_active is not None
        assert new_active["metadata"]["identity_payload"] == {
            "version": 2,
            "predecessor": {
                "condition_id": str(old_episode["id"]),
                "fingerprint": old_fp,
                "version": 1,
            },
        }

    async def test_complete_absence_without_version_successor_remains_ordinary_recovery(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "ordinary_recovery"
        fp = compute_fingerprint(source, 1, {"chain": "core"})
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp, identity_version=1)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )

        resolved = await reconcile_snapshot(
            pool,
            source=source,
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )

        assert resolved[0].transition == "resolved"
        row = await pool.fetchrow(
            "SELECT metadata FROM public.infra_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            fp,
        )
        assert row is not None
        assert json.loads(row["metadata"])["identity_payload"] == {"version": 1}

    async def test_incomplete_v2_snapshot_never_resolves_or_links_v1_episode(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "incomplete_version_bump"
        old_fp = compute_fingerprint(source, 1, {"chain": "core"})
        new_fp = compute_fingerprint(source, 2, {"chain": "core"})
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=old_fp, identity_version=1)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )

        transitions = await reconcile_snapshot(
            pool,
            source=source,
            observations=[
                Observation(
                    fingerprint=new_fp,
                    identity_version=2,
                    predecessor_fingerprint=old_fp,
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )

        assert [transition.transition for transition in transitions] == ["opened"]
        old_active = await get_active_condition(pool, source=source, fingerprint=old_fp)
        assert old_active is not None
        assert old_active["metadata"]["identity_payload"] == {"version": 1}
