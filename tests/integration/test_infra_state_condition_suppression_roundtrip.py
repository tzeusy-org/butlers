"""Real-Postgres regression: InfraStateSource condition-ledger reconciliation and
QA dispatch suppression (bu-27dxl.6.4).

Exercises ``InfraStateSource.discover()``'s reconciliation into the shared
``public.infra_conditions`` ledger, and ``core.qa.dispatch.dispatch_qa_investigation``'s
Gate 5.5 suppression, against a real fully-migrated Postgres instance
(testcontainers) writing through the actual ``public.infra_conditions``,
``public.healing_attempts``, and ``public.healing_dispatch_events`` tables --
not just the mocked-pool unit tests in ``tests/core/qa/test_infra_state.py`` /
``tests/core/qa/test_dispatch.py`` (mirroring the split used for
``tests/integration/test_calendar_sync_deadman_roundtrip.py``).

Maps onto this bead's acceptance criteria:
  - AC1: an active InfraState condition produces a decision record and zero
    new healing_attempts rows.
  - AC2: the suppression gate runs before create_or_join_attempt -- proven
    directly by counting healing_attempts rows, not by mocking the call.
  - AC3: a degraded/failed check can never resolve an active condition
    (health-check failure -- unit-tested in test_infra_state.py; the "one
    source recovers, another stays active" partial-snapshot case is
    integration-tested here).
  - AC4: an unconfigured external deadman is durably visible in the ledger
    without ever becoming a QaFinding (so it can never reach QA dispatch).
  - AC6: complete / recovery / reopen / paused / dispatch-no-attempt cases.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.infra_conditions import get_active_condition
from butlers.core.qa.dispatch import QaDispatchConfig, dispatch_qa_investigation
from butlers.core.qa.sources.infra_state import (
    _DEADMAN_UNCONFIGURED_FINGERPRINT,
    SOURCE_NAME,
    InfraStateSource,
)
from butlers.core.qa.triage import TriagedFinding
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=10)
    # The module-scoped DB is shared across every test below; InfraStateSource
    # reads the real switchboard.connector_registry / infra_conditions /
    # healing_* tables (unlike the calendar/deploy roundtrip suites, which
    # only ever call their reconcile function directly against a hand-built
    # report), so a prior test's rows would otherwise leak into the next
    # test's "complete snapshot" and break isolation.
    await p.execute("TRUNCATE switchboard.connector_registry")
    await p.execute("TRUNCATE public.infra_conditions")
    await p.execute("TRUNCATE public.healing_attempts CASCADE")
    await p.execute("TRUNCATE public.healing_dispatch_events")
    yield p
    await p.close()


async def _insert_connector(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    endpoint_identity: str,
    state: str = "error",
    last_heartbeat_at: datetime | None,
    first_seen_at: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at, first_seen_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (connector_type, endpoint_identity) DO UPDATE SET
            state = EXCLUDED.state,
            last_heartbeat_at = EXCLUDED.last_heartbeat_at
        """,
        connector_type,
        endpoint_identity,
        state,
        last_heartbeat_at,
        first_seen_at or (datetime.now(UTC) - timedelta(days=30)),
    )


async def _healing_attempt_count(pool: asyncpg.Pool, fingerprint: str) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM public.healing_attempts WHERE fingerprint = $1", fingerprint
    )


async def _dispatch_events(pool: asyncpg.Pool, fingerprint: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT decision, attempt_id, reason FROM public.healing_dispatch_events "
        "WHERE fingerprint = $1 ORDER BY created_at ASC",
        fingerprint,
    )


def _triaged(finding) -> TriagedFinding:
    return TriagedFinding(finding=finding, dedup_reason=None, finding_id=uuid.uuid4())


async def _make_patrol(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval("INSERT INTO public.qa_patrols DEFAULT VALUES RETURNING id")


async def _configure_healthy_deadman(pool: asyncpg.Pool, monkeypatch) -> None:
    """Configure + satisfy the external deadman so it never contributes its own condition.

    Several scenarios below only care about connector-offline behavior; a
    real EXTERNAL_DEADMAN_URL is normally unset in the test environment, and
    an unset URL intentionally opens its OWN ``ExternalDeadmanUnconfigured``
    condition (AC4) which would otherwise pollute a "no condition at all"
    assertion for an unrelated check.
    """
    from butlers.api.routers import audit as audit_router

    monkeypatch.setenv("EXTERNAL_DEADMAN_URL", "https://example.com/ping/abc")
    await audit_router.append(
        pool,
        "external_deadman",
        "external_deadman_ping_success",
        target="https://example.com/ping/abc",
        result="success",
    )


async def _dispatch(pool: asyncpg.Pool, finding) -> object:
    """Run dispatch_qa_investigation stopping cleanly before any spawner/worktree work.

    Every scenario here only needs to observe Gate 5.5's decision (suppressed
    or not) and, when NOT suppressed, that dispatch reached the real Gate 6
    novelty claim -- never that a full investigation agent actually spawns.
    Patching resolve_model to return None makes an un-suppressed call stop at
    Gate 10 ("no_model") deterministically, after exercising every real
    Postgres-backed gate in between.
    """
    patrol_id = await _make_patrol(pool)
    with patch("butlers.core.qa.dispatch.resolve_model", new_callable=AsyncMock, return_value=None):
        return await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=_triaged(finding),
            patrol_id=patrol_id,
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/nonexistent-repo"),
            spawner=MagicMock(),
            gh_token=None,
        )


class TestCompleteSnapshotOpensConditionAndSuppressesDispatch:
    async def test_offline_connector_opens_condition_and_gate_5_5_suppresses(
        self, pool: asyncpg.Pool
    ) -> None:
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="suppress@example.com",
            last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
        )

        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.source_type == SOURCE_NAME

        condition = await get_active_condition(
            pool, source=SOURCE_NAME, fingerprint=finding.fingerprint
        )
        assert condition is not None
        assert condition["state"] == "open"

        # AC1/AC2/AC6 (dispatch no-attempt): suppressed BEFORE create_or_join_attempt,
        # so zero healing_attempts rows are ever created for this fingerprint.
        result = await _dispatch(pool, finding)
        assert result.accepted is False
        assert result.reason == "infra_condition_open"
        assert result.attempt_id is None
        assert await _healing_attempt_count(pool, finding.fingerprint) == 0

        events = await _dispatch_events(pool, finding.fingerprint)
        assert len(events) == 1
        assert events[0]["decision"] == "infra_condition_open"
        assert events[0]["attempt_id"] is None


class TestRecoveryResolvesConditionAndUnsuppressesDispatch:
    async def test_connector_recovery_resolves_condition_then_dispatch_proceeds(
        self, pool: asyncpg.Pool
    ) -> None:
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="recover@example.com",
            last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        finding = findings[0]

        # Recovery: connector heartbeats fresh again -> absent from the next
        # complete snapshot -> resolves (AC1 of infra_conditions itself).
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="recover@example.com",
            state="healthy",
            last_heartbeat_at=datetime.now(UTC),
        )
        no_findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert no_findings == []

        condition = await get_active_condition(
            pool, source=SOURCE_NAME, fingerprint=finding.fingerprint
        )
        assert condition is None

        row = await pool.fetchrow(
            "SELECT state FROM public.infra_conditions WHERE source = $1 AND fingerprint = $2 "
            "ORDER BY episode DESC LIMIT 1",
            SOURCE_NAME,
            finding.fingerprint,
        )
        assert row["state"] == "resolved"

        # Not suppressed: Gate 5.5 is a no-op once resolved -- dispatch reaches
        # the real novelty gate (a healing_attempts row is created, then
        # dropped cleanly at the patched no-model gate).
        result = await _dispatch(pool, finding)
        assert result.reason == "no_model"
        assert await _healing_attempt_count(pool, finding.fingerprint) == 0  # deleted (orphaned)


class TestReopenCreatesNewEpisodeAndSuppressesAgain:
    async def test_recurrence_after_recovery_reopens_and_suppresses(
        self, pool: asyncpg.Pool
    ) -> None:
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="reopen@example.com",
            last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        finding = findings[0]

        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="reopen@example.com",
            state="healthy",
            last_heartbeat_at=datetime.now(UTC),
        )
        await InfraStateSource(pool=pool).discover(lookback_minutes=15)

        # Recur: goes offline again.
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="reopen@example.com",
            state="error",
            last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        findings2 = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert findings2[0].fingerprint == finding.fingerprint  # same identity, new episode

        episodes = await pool.fetch(
            "SELECT episode, state FROM public.infra_conditions "
            "WHERE source = $1 AND fingerprint = $2 ORDER BY episode",
            SOURCE_NAME,
            finding.fingerprint,
        )
        assert [(e["episode"], e["state"]) for e in episodes] == [(1, "resolved"), (2, "open")]

        result = await _dispatch(pool, findings2[0])
        assert result.reason == "infra_condition_open"
        assert await _healing_attempt_count(pool, finding.fingerprint) == 0


class TestPausedConnectorNeverEntersLedger:
    @pytest.mark.pg_clock
    async def test_paused_connector_creates_no_condition(
        self, pool: asyncpg.Pool, monkeypatch
    ) -> None:
        # Keep the deadman check healthy+configured so its own
        # ExternalDeadmanUnconfigured condition can't confound "no condition
        # at all was created for this paused connector".
        await _configure_healthy_deadman(pool, monkeypatch)
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="paused@example.com",
            state="paused",
            last_heartbeat_at=datetime.now(UTC) - timedelta(days=10),
        )
        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert findings == []

        row = await pool.fetchrow(
            "SELECT 1 FROM public.infra_conditions WHERE source = $1", SOURCE_NAME
        )
        assert row is None


class TestOneConditionRecoveringDoesNotMaskAnother:
    async def test_partial_recovery_leaves_the_other_condition_active_and_suppressible(
        self, pool: asyncpg.Pool
    ) -> None:
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="a@example.com",
            last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="b@example.com",
            last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        by_identity = {f.call_site: f for f in findings}
        fp_a = by_identity["connector:gmail/a@example.com"].fingerprint
        fp_b = by_identity["connector:gmail/b@example.com"].fingerprint

        # b recovers; a is still offline.
        await _insert_connector(
            pool,
            connector_type="gmail",
            endpoint_identity="b@example.com",
            state="healthy",
            last_heartbeat_at=datetime.now(UTC),
        )
        findings2 = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert [f.call_site for f in findings2] == ["connector:gmail/a@example.com"]

        assert await get_active_condition(pool, source=SOURCE_NAME, fingerprint=fp_a) is not None
        assert await get_active_condition(pool, source=SOURCE_NAME, fingerprint=fp_b) is None

        result_a = await _dispatch(pool, findings2[0])
        assert result_a.reason == "infra_condition_open"


class TestExternalDeadmanUnconfiguredIsDurableWithoutAFinding:
    async def test_unconfigured_deadman_opens_a_condition_but_never_a_finding(
        self, pool: asyncpg.Pool, monkeypatch
    ) -> None:
        monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)
        monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)

        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert findings == []  # AC4: never a QA finding

        condition = await get_active_condition(
            pool, source=SOURCE_NAME, fingerprint=_DEADMAN_UNCONFIGURED_FINGERPRINT
        )
        assert condition is not None
        assert condition["state"] == "open"


# ---------------------------------------------------------------------------
# Independent fleet controller and QA patrol assurance (bu-fvw4ap.2)
# REQ-butler-control-plane-liveness-005, REQ-staffer-qa-007/008
# ---------------------------------------------------------------------------


def _cycle(complete: bool, unready: dict[str, str], *, expected: int = 13, paused=()):
    from butlers.core.control_plane_identity import ShadowCycle, UnreadyDaemon

    daemons = tuple(
        UnreadyDaemon(name, category, "paused" if name in paused else "active")
        for name, category in unready.items()
    )
    recorded = expected if complete else expected - 1
    return ShadowCycle(complete, expected, recorded, 0, expected - len(daemons), daemons)


async def _seed_legacy_liveness(pool: asyncpg.Pool, butler: str) -> str:
    from butlers.core.infra_conditions import Observation, reconcile_snapshot

    fingerprint = uuid.uuid4().hex * 2
    await reconcile_snapshot(
        pool,
        source=SOURCE_NAME,
        observations=[
            Observation(
                fingerprint=fingerprint,
                summary=f"Butler '{butler}' heartbeat is stale",
                metadata={
                    "exception_type": "ButlerHeartbeatStale",
                    "source_butler": butler,
                    "call_site": f"butler_heartbeat:{butler}",
                },
            )
        ],
        snapshot_complete=False,
        initial_grace_seconds=3600,
    )
    return fingerprint


class TestIndependentFleetCondition:
    async def test_common_fault_is_one_condition_and_only_complete_recovery_resolves(
        self, pool: asyncpg.Pool
    ) -> None:
        from butlers.core.fleet_conditions import (
            FLEET_FINGERPRINT,
            FLEET_SOURCE,
            reconcile_fleet_condition,
        )

        names = [f"butler{i:02d}" for i in range(13)]
        roster = frozenset([*names, "parked"])
        stale = {name: "timeout" for name in names}
        await reconcile_fleet_condition(pool, _cycle(True, stale, expected=14), roster)
        rows = await pool.fetch(
            "SELECT metadata FROM public.infra_conditions WHERE source = $1", FLEET_SOURCE
        )
        assert len(rows) == 1  # thirteen stale daemons, one common-cause episode
        condition = await get_active_condition(
            pool, source=FLEET_SOURCE, fingerprint=FLEET_FINGERPRINT
        )
        assert condition["metadata"]["affected_count"] == 13

        # A failed/incomplete scan never infers the missing members healthy.
        await reconcile_fleet_condition(pool, _cycle(False, {}, expected=14), roster)
        partial = {names[0]: "connection", "parked": "connection"}
        await reconcile_fleet_condition(
            pool, _cycle(True, partial, expected=14, paused={"parked"}), roster
        )
        condition = await get_active_condition(
            pool, source=FLEET_SOURCE, fingerprint=FLEET_FINGERPRINT
        )
        assert condition is not None
        assert [d["name"] for d in condition["metadata"]["affected"]] == [names[0]]

        # An owner-paused daemon is an intentional exclusion, not an outage.
        await reconcile_fleet_condition(
            pool, _cycle(True, {"parked": "connection"}, expected=14, paused={"parked"}), roster
        )
        assert (
            await get_active_condition(pool, source=FLEET_SOURCE, fingerprint=FLEET_FINGERPRINT)
            is None
        )

    async def test_handoff_links_legacy_episodes_and_suppresses_one_fleet_finding(
        self, pool: asyncpg.Pool, monkeypatch
    ) -> None:
        from butlers.core.fleet_conditions import FLEET_FINGERPRINT, reconcile_fleet_condition

        await _configure_healthy_deadman(pool, monkeypatch)
        monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
        monkeypatch.setenv("BUTLERS_FLEET_CONDITION_HANDOFF", "1")
        roster = frozenset({"health", "finance", "general"})
        health_fp = await _seed_legacy_liveness(pool, "health")
        finance_fp = await _seed_legacy_liveness(pool, "finance")

        # finance is observed healthy by a complete snapshot; health is not.
        await reconcile_fleet_condition(
            pool, _cycle(True, {"health": "timeout"}, expected=3), roster
        )
        assert await get_active_condition(pool, source=SOURCE_NAME, fingerprint=finance_fp) is None
        fleet = await get_active_condition(
            pool, source="control_plane_fleet", fingerprint=FLEET_FINGERPRINT
        )
        assert [link["butler"] for link in fleet["metadata"]["linked_legacy_conditions"]] == [
            "health",
            "finance",
        ]

        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)
        assert [f.fingerprint for f in findings] == [FLEET_FINGERPRINT]
        assert "health" in findings[0].event_summary
        # QA's own complete snapshot carries the legacy episode instead of
        # resolving it by omission, and never re-files the fleet under itself.
        assert await get_active_condition(pool, source=SOURCE_NAME, fingerprint=health_fp)
        assert (
            await get_active_condition(pool, source=SOURCE_NAME, fingerprint=FLEET_FINGERPRINT)
            is None
        )

        result = await _dispatch(pool, findings[0])
        assert result.reason == "infra_condition_open"
        assert await _healing_attempt_count(pool, FLEET_FINGERPRINT) == 0

        await reconcile_fleet_condition(pool, _cycle(False, {}, expected=3), roster)
        assert await get_active_condition(pool, source=SOURCE_NAME, fingerprint=health_fp)
        await reconcile_fleet_condition(pool, _cycle(True, {}, expected=3), roster)
        resolved = await pool.fetchrow(
            "SELECT state, metadata->>'resolution_reason' AS reason "
            "FROM public.infra_conditions WHERE fingerprint = $1",
            health_fp,
        )
        assert tuple(resolved) == ("resolved", "complete_receiver_snapshot_healthy")
        assert await InfraStateSource(pool=pool).discover(lookback_minutes=15) == []

    async def test_carry_forward_never_reopens_an_episode_resolved_after_its_read(
        self, pool: asyncpg.Pool, monkeypatch
    ) -> None:
        """QA reads legacy episodes before the ledger lock; the controller may win the race."""
        from butlers.core.fleet_conditions import reconcile_fleet_condition

        await _configure_healthy_deadman(pool, monkeypatch)
        monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
        monkeypatch.setenv("BUTLERS_FLEET_CONDITION_HANDOFF", "1")
        finance_fp = await _seed_legacy_liveness(pool, "finance")
        read_carried = InfraStateSource._carry_forward_legacy_liveness

        async def read_then_controller_resolves(source):
            carried = await read_carried(source)
            await reconcile_fleet_condition(pool, _cycle(True, {}, expected=1), {"finance"})
            return carried

        monkeypatch.setattr(
            InfraStateSource, "_carry_forward_legacy_liveness", read_then_controller_resolves
        )
        await InfraStateSource(pool=pool).discover(lookback_minutes=15)

        states = await pool.fetch(
            "SELECT state FROM public.infra_conditions WHERE fingerprint = $1 ORDER BY episode",
            finance_fp,
        )
        assert [row["state"] for row in states] == ["resolved"]


class TestQaPatrolAssurance:
    @pytest.fixture
    async def qa_pool(self, pool: asyncpg.Pool):
        await pool.execute("TRUNCATE public.qa_patrols CASCADE")
        await pool.execute(
            "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
            "VALUES ('qa', 'http://qa:41110/mcp') ON CONFLICT (name) DO NOTHING"
        )
        await self._set_policy(pool, "active", "none")
        return pool

    @staticmethod
    async def _set_policy(pool: asyncpg.Pool, state: str, provenance: str) -> None:
        await pool.execute(
            "UPDATE switchboard.butler_registry_control_plane "
            "SET policy_state = $1, policy_provenance = $2 WHERE name = 'qa'",
            state,
            provenance,
        )

    @staticmethod
    async def _active(pool: asyncpg.Pool) -> set[str]:
        from butlers.core.fleet_conditions import (
            QA_PATROL_OVERDUE_FINGERPRINT,
            QA_PATROL_SOURCE,
        )

        rows = await pool.fetch(
            "SELECT fingerprint FROM public.infra_conditions "
            "WHERE source = $1 AND state IN ('open', 'aging')",
            QA_PATROL_SOURCE,
        )
        return {
            "overdue" if row["fingerprint"] == QA_PATROL_OVERDUE_FINGERPRINT else "stopped"
            for row in rows
        }

    async def test_only_complete_current_config_scheduled_patrols_renew_age(
        self, qa_pool: asyncpg.Pool, migrated_db_url: str, monkeypatch
    ) -> None:
        from types import SimpleNamespace

        from butlers.api.routers.qa import SyntheticFindingCreate, create_synthetic_finding
        from butlers.core.control_plane_identity import DashboardProbeRoleView
        from butlers.core.fleet_conditions import reconcile_qa_patrol_assurance
        from butlers.core.qa.patrol_provenance import QaPatrolContract
        from butlers.modules.qa import QaModule

        pool = qa_pool
        module = QaModule()
        contract = QaPatrolContract(tuple(sorted(module._config.enabled_sources)), 10)
        every_source = list(module._config.enabled_sources)

        async def patrol(status: str, polled: list[str], error: str | None = None) -> uuid.UUID:
            patrol_id = await module._create_patrol_record(pool)
            await module._complete_patrol_record(pool, patrol_id, status, 1, 1, 0, polled, error)
            return patrol_id

        # None of these may renew age: error, a missing source, skipped
        # overlap, a still-running row, a synthetic suppressed placeholder, a
        # legacy row with no provenance, and a row from another configuration.
        errored = await patrol("error", every_source, "source infra_state failed: boom")
        partial = await patrol("suppressed", every_source[:-1])
        assert not await pool.fetchval(
            "SELECT bool_or(discovery_complete) FROM public.qa_patrols WHERE id = ANY($1)",
            [errored, partial],
        )
        await module._record_patrol_skip(pool)
        await module._create_patrol_record(pool)
        monkeypatch.setenv("QA_ALLOW_SYNTHETIC_FINDINGS", "true")
        # The dashboard's shared pool registers the JSONB codec.
        api_pool = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=1, init=register_jsonb_codec
        )
        try:
            await create_synthetic_finding(
                body=SyntheticFindingCreate(),
                db=SimpleNamespace(credential_shared_pool=lambda: api_pool),
            )
        finally:
            await api_pool.close()
        await pool.execute(
            "INSERT INTO public.qa_patrols (status, completed_at) VALUES ('clean', now())"
        )
        await pool.execute(
            "INSERT INTO public.qa_patrols (status, completed_at, origin, "
            "enabled_sources_snapshot, enabled_sources_config_digest, discovery_complete) "
            "VALUES ('clean', now(), 'scheduled', '{log_scanner}', 'sha256:old', true)"
        )
        synthetic = await pool.fetchrow(
            "SELECT origin, discovery_complete FROM public.qa_patrols WHERE status = 'suppressed' "
            "AND error_detail LIKE 'Synthetic%'"
        )
        assert tuple(synthetic) == ("operator_synthetic", False)
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO public.qa_patrols (status, origin, discovery_complete) "
                "VALUES ('clean', 'operator_synthetic', true)"
            )

        reader = DashboardProbeRoleView(pool)
        await reconcile_qa_patrol_assurance(pool, reader, contract)
        assert await self._active(pool) == {"overdue"}

        # A genuine suppressed patrol filtered by cooldown still proved discovery.
        genuine = await patrol("suppressed", every_source)
        assert await pool.fetchval(
            "SELECT discovery_complete FROM public.qa_patrols WHERE id = $1", genuine
        )
        await reconcile_qa_patrol_assurance(pool, reader, contract)
        assert await self._active(pool) == set()

    async def test_absence_is_overdue_or_policy_stopped_until_a_qualifying_patrol(
        self, qa_pool: asyncpg.Pool
    ) -> None:
        from butlers.core.control_plane_identity import DashboardProbeRoleView
        from butlers.core.fleet_conditions import reconcile_qa_patrol_assurance
        from butlers.core.qa.patrol_provenance import QaPatrolContract, enabled_sources_digest

        pool = qa_pool
        contract = QaPatrolContract(("infra_state", "log_scanner"), 10)
        reader = DashboardProbeRoleView(pool)

        async def qualifying(age_minutes: int, status: str = "clean") -> None:
            await pool.execute(
                "INSERT INTO public.qa_patrols (status, started_at, completed_at, origin, "
                "enabled_sources_snapshot, enabled_sources_config_digest, discovery_complete) "
                "VALUES ($4, now() - make_interval(mins => $1), "
                "now() - make_interval(mins => $1), 'scheduled', $2, $3, true)",
                age_minutes,
                list(contract.enabled_sources),
                enabled_sources_digest(contract.enabled_sources),
                status,
            )

        # QA is down: its last qualifying patrol is older than twice its cadence.
        await qualifying(25)
        await reconcile_qa_patrol_assurance(pool, reader, contract)
        assert await self._active(pool) == {"overdue"}

        # An owner pause is a distinct, intentional stop.
        await self._set_policy(pool, "paused", "operator")
        await reconcile_qa_patrol_assurance(pool, reader, contract)
        assert await self._active(pool) == {"stopped"}

        # An unreadable policy cannot tell overdue from stopped: it adds
        # evidence and resolves neither identity.
        unreadable = AsyncMock()
        unreadable.fetchrow.side_effect = asyncpg.InsufficientPrivilegeError("denied")
        await reconcile_qa_patrol_assurance(pool, unreadable, contract)
        assert await self._active(pool) == {"overdue", "stopped"}
        assert (
            await pool.fetchval(
                "SELECT metadata->>'policy_known' FROM public.infra_conditions "
                "WHERE source = 'qa_patrol_assurance' AND state = 'open' "
                "ORDER BY last_confirmed_at DESC LIMIT 1"
            )
            == "false"
        )

        await self._set_policy(pool, "active", "operator")
        await qualifying(1, status="findings_dispatched")
        await reconcile_qa_patrol_assurance(pool, reader, contract)
        assert await self._active(pool) == set()
