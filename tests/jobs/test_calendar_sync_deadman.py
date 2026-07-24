"""Tests for butlers.jobs.calendar_sync_deadman (bu-hmdqz.10, bu-27dxl.6.3).

Covers:
- compute_calendar_sync_report: fresh sources -> not stale; a source past 2x
  the poll interval -> stale; never-synced provider source -> stale; a
  disabled source (sync_enabled=false) and an internal (non-provider) source
  -> never flagged; a total fan-out failure -> degraded (check_error set,
  snapshot_complete False), never a false all-clear; a PARTIAL fan-out
  failure (failed_butlers non-empty) -> is_available True but
  snapshot_complete False (bu-27dxl.6.3: the degraded-butler list is now
  consumed, where it used to be dropped).
- _condition_fingerprint: stable per (db_butler, source_key) regardless of
  mutable last_synced_at evidence; different providers get different
  fingerprints (so one provider's outage can never mask another's).
- reconcile_calendar_conditions / _apply_calendar_transition: opened/reopened
  write the first-detected marker with result="detected" (preserving the
  bu-27dxl.3.2 / PR #3516 direct-audit-result attribution); the L1
  escalation_due transition opens exactly one terminal healing_attempts case
  and writes the escalated marker with result="escalated"; L2+ due
  transitions write a distinct reescalated marker and do NOT touch
  healing_attempts (AC4); confirmed/resolved transitions have no audit side
  effect of their own.
- run_calendar_sync_deadman_check: never raises; reconciles even when nothing
  is currently stale (AC3 -- a clean snapshot is what resolves a prior
  episode).

No real database required — pools/DatabaseManager are faked/mocked; the
underlying reconcile_snapshot lifecycle behavior itself (open/confirm/
escalate/resolve, concurrency) is covered against real Postgres by
tests/core/test_infra_conditions.py and
tests/integration/test_infra_conditions_roundtrip.py (bu-27dxl.6.2). This
producer's own real-Postgres wiring (partial failure, race, and recovery
through the actual ledger + healing_attempts + audit_log tables) is covered
by tests/integration/test_calendar_sync_deadman_roundtrip.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.api.read_models.calendar_workspace_v1 import CalendarSourceRow
from butlers.core.infra_conditions import ConditionTransition
from butlers.jobs.calendar_sync_deadman import (
    CalendarSyncDeadmanReport,
    StaleCalendarSource,
    _condition_fingerprint,
    compute_calendar_sync_report,
    reconcile_calendar_conditions,
    run_calendar_sync_deadman_check,
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


def _source(
    *,
    source_key: str = "provider:google:primary",
    db_butler: str = "general",
    butler_name: str | None = None,
    last_synced_at: datetime | None,
    source_kind: str = "provider_event",
    source_metadata: object = None,
) -> CalendarSourceRow:
    return CalendarSourceRow(
        source_id=uuid4(),
        source_key=source_key,
        source_kind=source_kind,
        lane="user",
        provider="google",
        calendar_id="primary",
        butler_name=butler_name,
        display_name="Primary",
        writable=True,
        source_metadata=source_metadata,
        cursor_name="provider_sync",
        last_synced_at=last_synced_at,
        last_success_at=last_synced_at,
        last_error_at=None,
        last_error=None,
        full_sync_required=False,
        db_butler=db_butler,
    )


class _FrozenDatetime:
    """Stand-in for the ``datetime`` module-level name exposing only ``.now()``.

    ``compute_calendar_sync_report`` calls only ``datetime.now(UTC)`` (never
    another ``datetime`` constructor), and the module uses
    ``from __future__ import annotations`` (annotations are strings, never
    evaluated at runtime), so swapping the module-level ``datetime`` name for
    this minimal object is safe and avoids the ceremony of subclassing the
    real (immutable) ``datetime`` type.
    """

    def __init__(self, frozen: datetime):
        self._frozen = frozen

    def now(self, tz=None):
        return self._frozen


# ---------------------------------------------------------------------------
# compute_calendar_sync_report
# ---------------------------------------------------------------------------


async def test_compute_report_fresh_source_is_not_stale(monkeypatch):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.datetime",
        _FrozenDatetime(now),
    )
    sources = [_source(last_synced_at=now - timedelta(minutes=1))]
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(return_value=(sources, [])),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert report.is_available
    assert not report.is_stale
    assert report.stale_sources == ()
    assert report.failed_butlers == ()
    assert report.snapshot_complete


async def test_compute_report_flags_source_past_threshold(monkeypatch):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.datetime",
        _FrozenDatetime(now),
    )
    # DEFAULT_SYNC_INTERVAL_MINUTES=5 * PROJECTION_STALENESS_MULTIPLIER=2 -> 10min.
    stale_at = now - timedelta(minutes=96 * 24 * 60)  # 96 days, matches live incident
    sources = [_source(source_key="provider:google:stale", last_synced_at=stale_at)]
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(return_value=(sources, [])),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert report.is_available
    assert report.is_stale
    assert report.stale_sources == (
        StaleCalendarSource(
            source_key="provider:google:stale",
            db_butler="general",
            butler_name=None,
            last_synced_at=stale_at,
        ),
    )


async def test_compute_report_never_synced_provider_source_is_stale(monkeypatch):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.datetime",
        _FrozenDatetime(now),
    )
    sources = [_source(last_synced_at=None)]
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(return_value=(sources, [])),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert report.is_stale
    assert report.stale_sources[0].last_synced_at is None


async def test_compute_report_disabled_source_never_flagged(monkeypatch):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.datetime",
        _FrozenDatetime(now),
    )
    stale_at = now - timedelta(days=200)
    sources = [
        _source(last_synced_at=stale_at, source_metadata={"sync_enabled": False}),
    ]
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(return_value=(sources, [])),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert not report.is_stale


async def test_compute_report_internal_source_never_flagged(monkeypatch):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.datetime",
        _FrozenDatetime(now),
    )
    # Scheduler/reminder sources are internally projected, never provider-synced
    # -- a null cursor there is normal, not a dead poller.
    sources = [_source(last_synced_at=None, source_kind="internal_scheduler")]
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(return_value=(sources, [])),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert not report.is_stale


async def test_compute_report_degraded_on_total_fan_out_failure(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(side_effect=RuntimeError("fan-out down")),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert not report.is_available
    assert not report.is_stale  # a degraded check must not fabricate staleness either
    assert not report.snapshot_complete
    assert "fan-out down" in (report.check_error or "")


async def test_compute_report_partial_fan_out_failure_is_available_but_incomplete(monkeypatch):
    """A partial fan-out failure (some butler schemas unreachable, others fine)
    is NOT a total check_error -- the check ran and returned real data for the
    butlers that succeeded. But it must still be treated as an incomplete
    snapshot (AC2): some enabled provider sources were never observed."""
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.datetime",
        _FrozenDatetime(now),
    )
    sources = [_source(last_synced_at=now - timedelta(minutes=1))]
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(return_value=(sources, ["relationship"])),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert report.is_available
    assert report.failed_butlers == ("relationship",)
    assert not report.snapshot_complete


# ---------------------------------------------------------------------------
# _condition_fingerprint
# ---------------------------------------------------------------------------


def test_condition_fingerprint_stable_regardless_of_mutable_evidence():
    """last_synced_at is evidence, not identity -- the same provider source
    must fingerprint identically no matter what its diagnostic text says."""
    a = StaleCalendarSource(
        source_key="provider:google:primary",
        db_butler="general",
        butler_name=None,
        last_synced_at=None,
    )
    b = StaleCalendarSource(
        source_key="provider:google:primary",
        db_butler="general",
        butler_name=None,
        last_synced_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert _condition_fingerprint(a) == _condition_fingerprint(b)
    assert len(_condition_fingerprint(a)) == 64  # sha256 hex digest


def test_condition_fingerprint_distinguishes_providers():
    """Two different (db_butler, source_key) pairs must never collide -- this
    is what stops one provider's outage from masking another's escalation."""
    a = StaleCalendarSource(
        source_key="provider:google:a", db_butler="general", butler_name=None, last_synced_at=None
    )
    b = StaleCalendarSource(
        source_key="provider:google:b", db_butler="general", butler_name=None, last_synced_at=None
    )
    c = StaleCalendarSource(
        source_key="provider:google:a",
        db_butler="relationship",
        butler_name=None,
        last_synced_at=None,
    )

    assert _condition_fingerprint(a) != _condition_fingerprint(b)
    assert _condition_fingerprint(a) != _condition_fingerprint(c)


# ---------------------------------------------------------------------------
# reconcile_calendar_conditions / _apply_calendar_transition
# ---------------------------------------------------------------------------


_STALE = (
    StaleCalendarSource(
        source_key="provider:google:primary",
        db_butler="general",
        butler_name=None,
        last_synced_at=None,
    ),
)


def _report(
    stale_sources: tuple[StaleCalendarSource, ...] = (),
    *,
    checked_at: datetime | None = None,
    failed_butlers: tuple[str, ...] = (),
) -> CalendarSyncDeadmanReport:
    return CalendarSyncDeadmanReport(
        checked_at=checked_at or datetime.now(UTC),
        stale_sources=stale_sources,
        failed_butlers=failed_butlers,
    )


def _transition(
    fingerprint: str, transition: str, escalation_level: str = "L0"
) -> ConditionTransition:
    return ConditionTransition(
        condition_id=uuid4(),
        source="calendar_sync_deadman",
        fingerprint=fingerprint,
        episode=1,
        state="open" if transition != "resolved" else "resolved",
        transition=transition,
        escalation_level=escalation_level,
        next_reescalate_at=None,
    )


async def test_reconcile_no_stale_sources_still_calls_lifecycle_for_resolution(monkeypatch):
    """AC3: a clean report must still reconcile (with zero observations) so a
    previously active episode can resolve by omission."""
    reconcile_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.reconcile_snapshot", reconcile_mock)

    result = await reconcile_calendar_conditions(object(), _report())

    assert result == []
    reconcile_mock.assert_awaited_once()
    assert reconcile_mock.await_args.kwargs["observations"] == []
    assert reconcile_mock.await_args.kwargs["snapshot_complete"] is True


async def test_reconcile_opened_writes_first_detected_marker(monkeypatch):
    fp = _condition_fingerprint(_STALE[0])
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "opened")]),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    result = await reconcile_calendar_conditions(object(), _report(_STALE))

    assert result == [{"fingerprint": fp, "transition": "opened"}]
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "calendar_sync_deadman_first_detected"
    assert append_mock.await_args.kwargs["result"] == "detected"


async def test_reconcile_l1_escalation_due_opens_healing_case(monkeypatch):
    fp = _condition_fingerprint(_STALE[0])
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "escalation_due", escalation_level="L1")]),
    )
    attempt_id = uuid4()
    create_mock = AsyncMock(return_value=(attempt_id, True))
    update_mock = AsyncMock(return_value=True)
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.create_or_join_attempt", create_mock)
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.update_attempt_status", update_mock)
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    result = await reconcile_calendar_conditions(object(), _report(_STALE))

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
    assert create_mock.await_args.kwargs["exception_type"] == "CalendarSyncDeadman"
    update_mock.assert_awaited_once()
    assert update_mock.await_args.args[1] == attempt_id
    assert update_mock.await_args.args[2] == "unfixable"
    assert "human action required" in update_mock.await_args.kwargs["error_detail"]
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "calendar_sync_deadman_escalated"
    assert append_mock.await_args.kwargs["result"] == "escalated"


async def test_reconcile_l2_reescalation_does_not_open_new_healing_case(monkeypatch):
    """AC4: L2+ due transitions must write a distinct marker WITHOUT creating
    another healing_attempts row."""
    fp = _condition_fingerprint(_STALE[0])
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "escalation_due", escalation_level="L2")]),
    )
    create_mock = AsyncMock()
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.create_or_join_attempt", create_mock)
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    result = await reconcile_calendar_conditions(object(), _report(_STALE))

    assert result == [{"fingerprint": fp, "transition": "escalation_due", "escalation_level": "L2"}]
    create_mock.assert_not_awaited()
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "calendar_sync_deadman_reescalated"
    assert append_mock.await_args.kwargs["result"] == "reescalated"


async def test_reconcile_confirmed_has_no_audit_side_effect(monkeypatch):
    fp = _condition_fingerprint(_STALE[0])
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "confirmed")]),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    result = await reconcile_calendar_conditions(object(), _report(_STALE))

    assert result == [{"fingerprint": fp, "transition": "confirmed"}]
    append_mock.assert_not_awaited()


async def test_reconcile_resolved_has_no_audit_side_effect(monkeypatch):
    fp = "some-retired-fingerprint"
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "resolved")]),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    # No longer-stale source in this report -> resolved transitions come from
    # episodes absent from the observation set, so `stale=()` here is correct.
    result = await reconcile_calendar_conditions(object(), _report())

    assert result == [{"fingerprint": fp, "transition": "resolved"}]
    append_mock.assert_not_awaited()


async def test_reconcile_escalation_failure_degrades_not_crash(monkeypatch):
    fp = _condition_fingerprint(_STALE[0])
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_snapshot",
        AsyncMock(return_value=[_transition(fp, "escalation_due", escalation_level="L1")]),
    )
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.create_or_join_attempt",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await reconcile_calendar_conditions(object(), _report(_STALE))

    assert result == [
        {
            "fingerprint": fp,
            "transition": "escalation_due",
            "escalated": False,
            "reason": "escalation_failed",
        }
    ]


async def test_reconcile_passes_snapshot_complete_through(monkeypatch):
    """A partial fan-out failure must propagate snapshot_complete=False into
    reconcile_snapshot even when there ARE stale sources to confirm."""
    reconcile_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.reconcile_snapshot", reconcile_mock)

    await reconcile_calendar_conditions(object(), _report(_STALE, failed_butlers=("relationship",)))

    assert reconcile_mock.await_args.kwargs["snapshot_complete"] is False
    assert len(reconcile_mock.await_args.kwargs["observations"]) == 1


# ---------------------------------------------------------------------------
# run_calendar_sync_deadman_check (end-to-end tick, never raises)
# ---------------------------------------------------------------------------


async def test_run_check_degraded_never_raises(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.compute_calendar_sync_report",
        AsyncMock(
            return_value=CalendarSyncDeadmanReport(
                checked_at=datetime.now(UTC), stale_sources=(), check_error="boom"
            )
        ),
    )
    result = await run_calendar_sync_deadman_check(_FakeDatabaseManager())
    assert result == {"available": False, "stale": False}


async def test_run_check_not_stale_still_reconciles(monkeypatch):
    """AC3: even a fully clean tick must reconcile -- it's the only path that
    resolves a leftover active condition."""
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.compute_calendar_sync_report",
        AsyncMock(
            return_value=CalendarSyncDeadmanReport(checked_at=datetime.now(UTC), stale_sources=())
        ),
    )
    reconcile_mock = AsyncMock(return_value=[{"fingerprint": "fp", "transition": "resolved"}])
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_calendar_conditions", reconcile_mock
    )

    result = await run_calendar_sync_deadman_check(
        _FakeDatabaseManager(pools={"switchboard": object()})
    )

    assert result == {
        "available": True,
        "stale": False,
        "conditions": [{"fingerprint": "fp", "transition": "resolved"}],
    }
    reconcile_mock.assert_awaited_once()


async def test_run_check_stale_reconciles(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.compute_calendar_sync_report",
        AsyncMock(
            return_value=CalendarSyncDeadmanReport(
                checked_at=datetime.now(UTC), stale_sources=_STALE
            )
        ),
    )
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.reconcile_calendar_conditions",
        AsyncMock(return_value=[{"fingerprint": "fp", "transition": "opened"}]),
    )
    result = await run_calendar_sync_deadman_check(
        _FakeDatabaseManager(pools={"switchboard": object()})
    )
    assert result == {
        "available": True,
        "stale": True,
        "conditions": [{"fingerprint": "fp", "transition": "opened"}],
    }


async def test_run_check_no_switchboard_pool_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.compute_calendar_sync_report",
        AsyncMock(
            return_value=CalendarSyncDeadmanReport(
                checked_at=datetime.now(UTC), stale_sources=_STALE
            )
        ),
    )

    result = await run_calendar_sync_deadman_check(_FakeDatabaseManager(pools={}))

    assert result == {
        "available": True,
        "stale": True,
        "reconciled": False,
        "reason": "no_pool",
    }
