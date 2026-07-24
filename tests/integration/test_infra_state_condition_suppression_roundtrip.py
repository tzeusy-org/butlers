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
