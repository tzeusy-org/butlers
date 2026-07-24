"""Tests for butlers.jobs.deploy_drift — migration-drift sentinel (bu-9r3hd.1, bu-27dxl.6.3).

Covers:
- _expected_chains_by_schema: resolves core + butler + module chains per schema.
- compute_drift_report: all-aligned -> not drifted; a dark revision -> drifted
  with the correct schema/chain/expected/actual; a check failure (pool
  exception, missing switchboard pool) -> degraded (check_error set), never a
  false all-clear.
- _drift_fingerprint: stable per (schema, chain) regardless of the mutable
  expected_head/actual_revision evidence; different pairs get different
  fingerprints.
- get_drift_escalation_state: aggregates (first_detected_at, escalated)
  across every currently drifted pair's active condition-lifecycle episode.
- reconcile_drift_conditions / _apply_drift_transition: opened/reopened write
  the first-detected marker with result="detected" (preserving the
  bu-27dxl.3.2 / PR #3516 direct-audit-result attribution); the L1
  escalation_due transition opens exactly one terminal healing_attempts case
  and writes the escalated marker with result="escalated"; L2+ due
  transitions write a distinct reescalated marker and do NOT touch
  healing_attempts (AC4); confirmed/resolved transitions have no audit side
  effect of their own.
- run_migration_drift_check: never raises; reconciles even when nothing is
  currently drifted (AC3 -- a clean comparison is what resolves a prior
  episode).

No real database required — pools are faked/mocked; the underlying
reconcile_snapshot lifecycle behavior itself (open/confirm/escalate/resolve,
concurrency) is covered against real Postgres by
tests/core/test_infra_conditions.py and
tests/integration/test_infra_conditions_roundtrip.py (bu-27dxl.6.2). This
producer's own real-Postgres wiring (race and recovery through the actual
ledger + healing_attempts + audit_log tables) is covered by
tests/integration/test_deploy_drift_lifecycle_roundtrip.py; compute_drift_report
against a real migrated schema remains covered by
tests/integration/test_deploy_drift_roundtrip.py (unchanged by this child).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from butlers.api.deps import ButlerConnectionInfo
from butlers.core.infra_conditions import ConditionTransition
from butlers.jobs.deploy_drift import (
    ChainDrift,
    DriftReport,
    _actual_revisions,
    _drift_fingerprint,
    _expected_chains_by_schema,
    compute_drift_report,
    get_drift_escalation_state,
    reconcile_drift_conditions,
    run_migration_drift_check,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeDatabaseManager:
    """Minimal stand-in for DatabaseManager: only .pool() is used by this module."""

    def __init__(self, *, pools: dict[str, object] | None = None):
        self._pools = pools or {}

    def pool(self, name: str):
        try:
            return self._pools[name]
        except KeyError:
            raise KeyError(name) from None


class _FakePool:
    """Fake asyncpg pool: schema-aware .fetch() for alembic_version reads."""

    def __init__(self, *, revisions_by_schema: dict[str, list[str] | Exception]):
        self._revisions_by_schema = revisions_by_schema

    async def fetch(self, sql: str, *args):
        for schema, revisions in self._revisions_by_schema.items():
            if f'"{schema}".alembic_version' in sql:
                if isinstance(revisions, Exception):
                    raise revisions
                return [{"version_num": r} for r in revisions]
        raise AssertionError(f"Unexpected schema query: {sql}")


# ---------------------------------------------------------------------------
# _expected_chains_by_schema
# ---------------------------------------------------------------------------


def test_expected_chains_by_schema_includes_core_butler_and_module_chains(monkeypatch):
    butlers = [
        ButlerConnectionInfo(
            name="finance", port=1, db_schema="finance", modules=frozenset({"finance", "memory"})
        ),
        ButlerConnectionInfo(
            name="messenger", port=2, db_schema=None, modules=frozenset({"messenger"})
        ),
    ]
    monkeypatch.setattr("butlers.jobs.deploy_drift.discover_butlers", lambda: butlers)
    monkeypatch.setattr("butlers.jobs.deploy_drift.get_all_chains", lambda: ["core", "memory"])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.has_butler_chain", lambda name: name == "finance"
    )

    result = _expected_chains_by_schema()

    # finance: core + own butler chain + enabled "memory" module chain.
    assert result["finance"] == ["core", "finance", "memory"]
    # messenger: db_schema=None falls back to the butler name; no butler
    # chain (has_butler_chain=False), and "messenger" module isn't a
    # recognized chain per get_all_chains() above.
    assert result["messenger"] == ["core"]


# ---------------------------------------------------------------------------
# _actual_revisions
# ---------------------------------------------------------------------------


async def test_actual_revisions_returns_rows_for_existing_table():
    pool = _FakePool(revisions_by_schema={"finance": ["core_010", "finance_003"]})
    result = await _actual_revisions(pool, "finance")
    assert result == {"core_010", "finance_003"}


async def test_actual_revisions_treats_missing_table_as_empty_not_a_failure():
    pool = _FakePool(revisions_by_schema={"finance": asyncpg.UndefinedTableError()})
    result = await _actual_revisions(pool, "finance")
    assert result == set()


# ---------------------------------------------------------------------------
# compute_drift_report
# ---------------------------------------------------------------------------


def _patch_chain_helpers(monkeypatch, *, schema_chains, heads, revision_ids):
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift._expected_chains_by_schema", lambda: schema_chains
    )
    monkeypatch.setattr("butlers.jobs.deploy_drift.get_chain_head", lambda chain: heads[chain])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_chain_revision_ids",
        lambda chain: frozenset(revision_ids[chain]),
    )


async def test_compute_drift_report_all_aligned_is_not_drifted(monkeypatch):
    _patch_chain_helpers(
        monkeypatch,
        schema_chains={"finance": ["core", "finance"]},
        heads={"core": "core_005", "finance": "finance_002"},
        revision_ids={
            "core": {"core_001", "core_002", "core_003", "core_004", "core_005"},
            "finance": {"finance_001", "finance_002"},
        },
    )
    pool = _FakePool(revisions_by_schema={"finance": ["core_005", "finance_002"]})
    db = _FakeDatabaseManager(pools={"switchboard": pool})

    report = await compute_drift_report(db)

    assert report.is_available
    assert not report.is_drifted
    assert report.drifted == ()


async def test_compute_drift_report_detects_a_dark_revision(monkeypatch):
    _patch_chain_helpers(
        monkeypatch,
        schema_chains={"finance": ["core", "finance"]},
        heads={"core": "core_005", "finance": "finance_002"},
        revision_ids={
            "core": {"core_001", "core_002", "core_003", "core_004", "core_005"},
            "finance": {"finance_001", "finance_002"},
        },
    )
    # DB is stuck at core_003 -- core_004/core_005 never got deployed (the
    # bu-zhfd0 shape). finance chain is fine.
    pool = _FakePool(revisions_by_schema={"finance": ["core_003", "finance_002"]})
    db = _FakeDatabaseManager(pools={"switchboard": pool})

    report = await compute_drift_report(db)

    assert report.is_available
    assert report.is_drifted
    assert report.drifted == (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )


async def test_compute_drift_report_never_applied_chain_reports_none_actual(monkeypatch):
    _patch_chain_helpers(
        monkeypatch,
        schema_chains={"finance": ["core", "finance"]},
        heads={"core": "core_005", "finance": "finance_002"},
        revision_ids={
            "core": {"core_005"},
            "finance": {"finance_001", "finance_002"},
        },
    )
    pool = _FakePool(revisions_by_schema={"finance": ["core_005"]})  # finance chain never applied
    db = _FakeDatabaseManager(pools={"switchboard": pool})

    report = await compute_drift_report(db)

    assert report.is_drifted
    assert report.drifted[0].chain == "finance"
    assert report.drifted[0].actual_revision is None


async def test_compute_drift_report_is_degraded_not_clean_when_switchboard_pool_missing():
    db = _FakeDatabaseManager(pools={})

    report = await compute_drift_report(db)

    assert not report.is_available
    assert not report.is_drifted  # a degraded check must not fabricate drift either
    assert report.check_error is not None


async def test_compute_drift_report_is_degraded_when_a_schema_query_raises(monkeypatch):
    _patch_chain_helpers(
        monkeypatch,
        schema_chains={"finance": ["core"]},
        heads={"core": "core_005"},
        revision_ids={"core": {"core_005"}},
    )
    pool = _FakePool(revisions_by_schema={"finance": RuntimeError("permission denied")})
    db = _FakeDatabaseManager(pools={"switchboard": pool})

    report = await compute_drift_report(db)

    assert not report.is_available
    assert report.drifted == ()
    assert "permission denied" in (report.check_error or "")


# ---------------------------------------------------------------------------
# _drift_fingerprint
# ---------------------------------------------------------------------------


def test_drift_fingerprint_stable_regardless_of_mutable_evidence():
    """expected_head/actual_revision are evidence, not identity -- the SAME
    (schema, chain) pair must fingerprint identically even as those mutable
    values change tick to tick during one ongoing outage."""
    a = ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    b = ChainDrift(
        schema="finance", chain="core", expected_head="core_006", actual_revision="core_004"
    )

    assert _drift_fingerprint(a) == _drift_fingerprint(b)
    assert len(_drift_fingerprint(a)) == 64  # sha256 hex digest


def test_drift_fingerprint_distinguishes_schema_chain_pairs():
    a = ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    b = ChainDrift(
        schema="health", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    c = ChainDrift(
        schema="finance", chain="finance", expected_head="finance_002", actual_revision=None
    )

    assert _drift_fingerprint(a) != _drift_fingerprint(b)
    assert _drift_fingerprint(a) != _drift_fingerprint(c)


# ---------------------------------------------------------------------------
# get_drift_escalation_state
# ---------------------------------------------------------------------------


async def test_get_drift_escalation_state_no_active_conditions(monkeypatch):
    drifted = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_active_condition", AsyncMock(return_value=None)
    )

    first, escalated = await get_drift_escalation_state(object(), drifted)

    assert first is None
    assert escalated is False


async def test_get_drift_escalation_state_aggregates_earliest_and_any_escalated(monkeypatch):
    a = ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    b = ChainDrift(
        schema="health", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    earlier = datetime(2026, 7, 1, tzinfo=UTC)
    later = datetime(2026, 7, 10, tzinfo=UTC)

    async def _fake_get_active_condition(pool, *, source, fingerprint):
        if fingerprint == _drift_fingerprint(a):
            return {"first_detected_at": later, "escalation_level": "L0"}
        if fingerprint == _drift_fingerprint(b):
            return {"first_detected_at": earlier, "escalation_level": "L2"}
        raise AssertionError("unexpected fingerprint")

    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_active_condition", _fake_get_active_condition
    )

    first, escalated = await get_drift_escalation_state(object(), (a, b))

    assert first == earlier
    assert escalated is True  # b is past L0 even though a is not


# ---------------------------------------------------------------------------
# reconcile_drift_conditions / _apply_drift_transition
# ---------------------------------------------------------------------------


_DRIFT = (
    ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
    ),
)


def _report(drifted: tuple[ChainDrift, ...] = (), *, check_error: str | None = None) -> DriftReport:
    return DriftReport(checked_at=datetime.now(UTC), drifted=drifted, check_error=check_error)


def _transition(
    fingerprint: str, transition: str, escalation_level: str = "L0"
) -> ConditionTransition:
    return ConditionTransition(
        condition_id=uuid4(),
        source="deployment_drift",
        fingerprint=fingerprint,
        episode=1,
        state="open" if transition != "resolved" else "resolved",
        transition=transition,
        escalation_level=escalation_level,
        next_reescalate_at=None,
    )


async def test_reconcile_no_drift_still_calls_lifecycle_for_resolution(monkeypatch):
    """AC3: a clean comparison must still reconcile (with zero observations)
    so a previously active episode can resolve by omission."""
    reconcile_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("butlers.jobs.deploy_drift.reconcile_snapshot", reconcile_mock)

    result = await reconcile_drift_conditions(object(), _report())

    assert result == []
    reconcile_mock.assert_awaited_once()
    assert reconcile_mock.await_args.kwargs["observations"] == []
    assert reconcile_mock.await_args.kwargs["snapshot_complete"] is True


async def test_reconcile_opened_writes_first_detected_marker(monkeypatch):
    fp = _drift_fingerprint(_DRIFT[0])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "opened")]),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await reconcile_drift_conditions(object(), _report(_DRIFT))

    assert result == [{"fingerprint": fp, "transition": "opened"}]
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "migration_drift_first_detected"
    assert append_mock.await_args.kwargs["result"] == "detected"


async def test_reconcile_l1_escalation_due_opens_healing_case(monkeypatch):
    fp = _drift_fingerprint(_DRIFT[0])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "escalation_due", escalation_level="L1")]),
    )
    attempt_id = uuid4()
    create_mock = AsyncMock(return_value=(attempt_id, True))
    update_mock = AsyncMock(return_value=True)
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.create_or_join_attempt", create_mock)
    monkeypatch.setattr("butlers.jobs.deploy_drift.update_attempt_status", update_mock)
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await reconcile_drift_conditions(object(), _report(_DRIFT))

    assert result == [
        {
            "fingerprint": fp,
            "transition": "escalation_due",
            "escalated": True,
            "healing_attempt_id": str(attempt_id),
        }
    ]
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["fingerprint"] == fp
    assert create_mock.await_args.kwargs["exception_type"] == "MigrationDriftDetected"
    update_mock.assert_awaited_once()
    assert update_mock.await_args.args[1] == attempt_id
    assert update_mock.await_args.args[2] == "unfixable"
    assert "human action required" in update_mock.await_args.kwargs["error_detail"]
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "migration_drift_escalated"
    assert append_mock.await_args.kwargs["result"] == "escalated"


async def test_reconcile_l2_reescalation_does_not_open_new_healing_case(monkeypatch):
    """AC4: L2+ due transitions must write a distinct marker WITHOUT creating
    another healing_attempts row."""
    fp = _drift_fingerprint(_DRIFT[0])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "escalation_due", escalation_level="L3")]),
    )
    create_mock = AsyncMock()
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.create_or_join_attempt", create_mock)
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await reconcile_drift_conditions(object(), _report(_DRIFT))

    assert result == [{"fingerprint": fp, "transition": "escalation_due", "escalation_level": "L3"}]
    create_mock.assert_not_awaited()
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "migration_drift_reescalated"
    assert append_mock.await_args.kwargs["result"] == "reescalated"


async def test_reconcile_confirmed_has_no_audit_side_effect(monkeypatch):
    fp = _drift_fingerprint(_DRIFT[0])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "confirmed")]),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await reconcile_drift_conditions(object(), _report(_DRIFT))

    assert result == [{"fingerprint": fp, "transition": "confirmed"}]
    append_mock.assert_not_awaited()


async def test_reconcile_resolved_has_no_audit_side_effect(monkeypatch):
    fp = "some-retired-fingerprint"
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "resolved")]),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await reconcile_drift_conditions(object(), _report())

    assert result == [{"fingerprint": fp, "transition": "resolved"}]
    append_mock.assert_not_awaited()


async def test_reconcile_escalation_failure_degrades_not_crash(monkeypatch):
    fp = _drift_fingerprint(_DRIFT[0])
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "escalation_due", escalation_level="L1")]),
    )
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.create_or_join_attempt",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await reconcile_drift_conditions(object(), _report(_DRIFT))

    assert result == [
        {
            "fingerprint": fp,
            "transition": "escalation_due",
            "escalated": False,
            "reason": "escalation_failed",
        }
    ]


async def test_reconcile_snapshot_complete_follows_report_availability(monkeypatch):
    reconcile_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("butlers.jobs.deploy_drift.reconcile_snapshot", reconcile_mock)

    await reconcile_drift_conditions(object(), _report(_DRIFT, check_error="boom"))

    assert reconcile_mock.await_args.kwargs["snapshot_complete"] is False


# ---------------------------------------------------------------------------
# run_migration_drift_check (end-to-end tick, never raises)
# ---------------------------------------------------------------------------


async def test_run_migration_drift_check_degraded_never_raises(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.compute_drift_report",
        AsyncMock(
            return_value=DriftReport(checked_at=datetime.now(UTC), drifted=(), check_error="boom")
        ),
    )
    result = await run_migration_drift_check(_FakeDatabaseManager())
    assert result == {"available": False, "drifted": False}


async def test_run_migration_drift_check_not_drifted_still_reconciles(monkeypatch):
    """AC3: even a fully clean tick must reconcile -- it's the only path that
    resolves a leftover active condition."""
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.compute_drift_report",
        AsyncMock(return_value=DriftReport(checked_at=datetime.now(UTC), drifted=())),
    )
    reconcile_mock = AsyncMock(return_value=[{"fingerprint": "fp", "transition": "resolved"}])
    monkeypatch.setattr("butlers.jobs.deploy_drift.reconcile_drift_conditions", reconcile_mock)

    result = await run_migration_drift_check(_FakeDatabaseManager(pools={"switchboard": object()}))

    assert result == {
        "available": True,
        "drifted": False,
        "conditions": [{"fingerprint": "fp", "transition": "resolved"}],
    }
    reconcile_mock.assert_awaited_once()


async def test_run_migration_drift_check_drifted_reconciles(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.compute_drift_report",
        AsyncMock(return_value=DriftReport(checked_at=datetime.now(UTC), drifted=_DRIFT)),
    )
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.reconcile_drift_conditions",
        AsyncMock(return_value=[{"fingerprint": "fp", "transition": "opened"}]),
    )
    result = await run_migration_drift_check(_FakeDatabaseManager(pools={"switchboard": object()}))
    assert result == {
        "available": True,
        "drifted": True,
        "conditions": [{"fingerprint": "fp", "transition": "opened"}],
    }
