"""Tests for butlers.jobs.deploy_drift — migration-drift sentinel (bu-9r3hd.1).

Covers:
- _expected_chains_by_schema: resolves core + butler + module chains per schema.
- compute_drift_report: all-aligned -> not drifted; a dark revision -> drifted
  with the correct schema/chain/expected/actual; a check failure (pool
  exception, missing switchboard pool) -> degraded (check_error set), never a
  false all-clear.
- drift_fingerprint: stable regardless of input ordering.
- get_drift_escalation_state: reads the first-detected/escalated debounce
  markers from public.audit_log.
- maybe_escalate_drift: writes first-detected marker on first sight; does not
  escalate within the 24h threshold; escalates exactly once past the
  threshold (creates + immediately closes a public.healing_attempts case);
  never re-escalates once already escalated.

No real database required — pools are faked/mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from butlers.api.deps import ButlerConnectionInfo
from butlers.jobs.deploy_drift import (
    ChainDrift,
    DriftReport,
    _actual_revisions,
    _expected_chains_by_schema,
    compute_drift_report,
    drift_fingerprint,
    get_drift_escalation_state,
    maybe_escalate_drift,
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
# drift_fingerprint
# ---------------------------------------------------------------------------


def test_drift_fingerprint_stable_regardless_of_ordering():
    a = ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    b = ChainDrift(
        schema="health", chain="core", expected_head="core_005", actual_revision="core_003"
    )

    fp1 = drift_fingerprint((a, b))
    fp2 = drift_fingerprint((b, a))

    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex digest


def test_drift_fingerprint_changes_when_composition_changes():
    a = ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
    )
    c = ChainDrift(
        schema="finance", chain="core", expected_head="core_005", actual_revision="core_004"
    )

    assert drift_fingerprint((a,)) != drift_fingerprint((c,))


# ---------------------------------------------------------------------------
# get_drift_escalation_state
# ---------------------------------------------------------------------------


class _FakeAuditPool:
    """Fake pool answering the two audit_log lookups get_drift_escalation_state issues."""

    def __init__(self, *, first_detected_at: datetime | None, escalated: bool):
        self._first_detected_at = first_detected_at
        self._escalated = escalated

    async def fetchrow(self, sql: str, *args):
        if "migration_drift_first_detected" in args:
            return {"ts": self._first_detected_at} if self._first_detected_at is not None else None
        if "migration_drift_escalated" in args:
            return {"dummy": 1} if self._escalated else None
        raise AssertionError(f"Unexpected query: {sql} {args}")


async def test_get_drift_escalation_state_never_detected():
    pool = _FakeAuditPool(first_detected_at=None, escalated=False)
    first, escalated = await get_drift_escalation_state(pool, "fp")
    assert first is None
    assert escalated is False


async def test_get_drift_escalation_state_detected_not_escalated():
    ts = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    pool = _FakeAuditPool(first_detected_at=ts, escalated=False)
    first, escalated = await get_drift_escalation_state(pool, "fp")
    assert first == ts
    assert escalated is False


async def test_get_drift_escalation_state_detected_and_escalated():
    ts = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    pool = _FakeAuditPool(first_detected_at=ts, escalated=True)
    first, escalated = await get_drift_escalation_state(pool, "fp")
    assert first == ts
    assert escalated is True


# ---------------------------------------------------------------------------
# maybe_escalate_drift
# ---------------------------------------------------------------------------


def _report(drifted: tuple[ChainDrift, ...], checked_at: datetime) -> DriftReport:
    return DriftReport(checked_at=checked_at, drifted=drifted)


async def test_maybe_escalate_drift_no_drift_is_a_no_op():
    result = await maybe_escalate_drift(object(), _report((), datetime.now(UTC)))
    assert result == {"escalated": False, "reason": "no_drift"}


async def test_maybe_escalate_drift_first_sighting_writes_marker_no_escalation(monkeypatch):
    drift = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    now = datetime.now(UTC)

    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_drift_escalation_state",
        AsyncMock(return_value=(None, False)),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await maybe_escalate_drift(object(), _report(drift, now))

    assert result["escalated"] is False
    assert result["reason"] == "newly_detected"
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "migration_drift_first_detected"


async def test_maybe_escalate_drift_within_threshold_does_not_escalate(monkeypatch):
    drift = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    now = datetime.now(UTC)
    first_detected = now - timedelta(hours=1)

    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_drift_escalation_state",
        AsyncMock(return_value=(first_detected, False)),
    )
    create_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.create_or_join_attempt", create_mock)

    result = await maybe_escalate_drift(object(), _report(drift, now))

    assert result["escalated"] is False
    assert result["reason"] == "within_threshold"
    create_mock.assert_not_awaited()


async def test_maybe_escalate_drift_past_threshold_escalates_via_healing_attempts(monkeypatch):
    drift = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    now = datetime.now(UTC)
    first_detected = now - timedelta(hours=25)
    attempt_id = uuid4()

    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_drift_escalation_state",
        AsyncMock(return_value=(first_detected, False)),
    )
    create_mock = AsyncMock(return_value=(attempt_id, True))
    update_mock = AsyncMock(return_value=True)
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.create_or_join_attempt", create_mock)
    monkeypatch.setattr("butlers.jobs.deploy_drift.update_attempt_status", update_mock)
    monkeypatch.setattr("butlers.jobs.deploy_drift.audit_router.append", append_mock)

    result = await maybe_escalate_drift(object(), _report(drift, now))

    assert result["escalated"] is True
    assert result["healing_attempt_id"] == str(attempt_id)
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["exception_type"] == "MigrationDriftDetected"
    update_mock.assert_awaited_once()
    assert update_mock.await_args.args[1] == attempt_id
    assert update_mock.await_args.args[2] == "unfixable"
    assert "human action required" in update_mock.await_args.kwargs["error_detail"]
    # Escalation marker written, not the first-detected marker (already exists).
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "migration_drift_escalated"


async def test_maybe_escalate_drift_does_not_re_escalate(monkeypatch):
    drift = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    now = datetime.now(UTC)
    first_detected = now - timedelta(hours=48)

    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_drift_escalation_state",
        AsyncMock(return_value=(first_detected, True)),
    )
    create_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.deploy_drift.create_or_join_attempt", create_mock)

    result = await maybe_escalate_drift(object(), _report(drift, now))

    assert result == {
        "escalated": False,
        "reason": "already_escalated",
        "first_detected_at": first_detected.isoformat(),
    }
    create_mock.assert_not_awaited()


async def test_maybe_escalate_drift_reports_degraded_not_crash_on_escalation_failure(monkeypatch):
    drift = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    now = datetime.now(UTC)
    first_detected = now - timedelta(hours=48)

    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.get_drift_escalation_state",
        AsyncMock(return_value=(first_detected, False)),
    )
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.create_or_join_attempt",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await maybe_escalate_drift(object(), _report(drift, now))

    assert result["escalated"] is False
    assert result["reason"] == "escalation_failed"


# ---------------------------------------------------------------------------
# run_migration_drift_check (end-to-end tick, never raises)
# ---------------------------------------------------------------------------


async def test_run_migration_drift_check_available_no_drift(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.compute_drift_report",
        AsyncMock(return_value=DriftReport(checked_at=datetime.now(UTC), drifted=())),
    )
    result = await run_migration_drift_check(_FakeDatabaseManager())
    assert result == {"available": True, "drifted": False}


async def test_run_migration_drift_check_degraded_check_never_raises(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.compute_drift_report",
        AsyncMock(
            return_value=DriftReport(checked_at=datetime.now(UTC), drifted=(), check_error="boom")
        ),
    )
    result = await run_migration_drift_check(_FakeDatabaseManager())
    assert result == {"available": False, "drifted": False}


async def test_run_migration_drift_check_drifted_runs_escalation(monkeypatch):
    drift = (
        ChainDrift(
            schema="finance", chain="core", expected_head="core_005", actual_revision="core_003"
        ),
    )
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.compute_drift_report",
        AsyncMock(return_value=DriftReport(checked_at=datetime.now(UTC), drifted=drift)),
    )
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift.maybe_escalate_drift",
        AsyncMock(return_value={"escalated": False, "reason": "newly_detected"}),
    )
    pool = object()
    result = await run_migration_drift_check(_FakeDatabaseManager(pools={"switchboard": pool}))
    assert result["available"] is True
    assert result["drifted"] is True
    assert result["reason"] == "newly_detected"
