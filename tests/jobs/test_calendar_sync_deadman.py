"""Tests for butlers.jobs.calendar_sync_deadman (bu-hmdqz.10).

Covers:
- compute_calendar_sync_report: fresh sources -> not stale; a source past 2x
  the poll interval -> stale; never-synced provider source -> stale; a
  disabled source (sync_enabled=false) and an internal (non-provider) source
  -> never flagged; a fan-out failure -> degraded (check_error set), never a
  false all-clear.
- _fingerprint: stable regardless of input ordering, changes with composition.
- get_deadman_escalation_state: reads the first-detected/escalated debounce
  markers from public.audit_log.
- maybe_escalate_calendar_sync_deadman: writes first-detected marker on first
  sight; does not escalate within the grace tick; escalates exactly once past
  the grace tick (creates + immediately closes a public.healing_attempts
  case); never re-escalates once already escalated.

No real database required — pools/DatabaseManager are faked/mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.api.read_models.calendar_workspace_v1 import CalendarSourceRow
from butlers.jobs.calendar_sync_deadman import (
    CALENDAR_DEADMAN_ESCALATION_DELAY_S,
    CalendarSyncDeadmanReport,
    StaleCalendarSource,
    _fingerprint,
    compute_calendar_sync_report,
    get_deadman_escalation_state,
    maybe_escalate_calendar_sync_deadman,
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


async def test_compute_report_degraded_on_fan_out_failure(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.query_calendar_sources",
        AsyncMock(side_effect=RuntimeError("fan-out down")),
    )

    report = await compute_calendar_sync_report(_FakeDatabaseManager())

    assert not report.is_available
    assert not report.is_stale  # a degraded check must not fabricate staleness either
    assert "fan-out down" in (report.check_error or "")


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
# _fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_stable_regardless_of_ordering():
    a = StaleCalendarSource(
        source_key="provider:google:a", db_butler="general", butler_name=None, last_synced_at=None
    )
    b = StaleCalendarSource(
        source_key="provider:google:b",
        db_butler="relationship",
        butler_name=None,
        last_synced_at=None,
    )

    fp1 = _fingerprint((a, b))
    fp2 = _fingerprint((b, a))

    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex digest


def test_fingerprint_stable_when_source_key_collides_across_butlers():
    """Two different db_butlers can share the same source_key (e.g. both run
    ``provider:google:primary``). Sorting by source_key alone leaves their
    relative order dependent on input order (Python sort is stable); the
    fingerprint must sort by (db_butler, source_key) to stay deterministic.
    """
    a = StaleCalendarSource(
        source_key="provider:google:primary",
        db_butler="general",
        butler_name=None,
        last_synced_at=None,
    )
    b = StaleCalendarSource(
        source_key="provider:google:primary",
        db_butler="relationship",
        butler_name=None,
        last_synced_at=None,
    )

    fp1 = _fingerprint((a, b))
    fp2 = _fingerprint((b, a))

    assert fp1 == fp2


def test_fingerprint_changes_when_composition_changes():
    a = StaleCalendarSource(
        source_key="provider:google:a", db_butler="general", butler_name=None, last_synced_at=None
    )
    b = StaleCalendarSource(
        source_key="provider:google:b", db_butler="general", butler_name=None, last_synced_at=None
    )

    assert _fingerprint((a,)) != _fingerprint((a, b))


# ---------------------------------------------------------------------------
# get_deadman_escalation_state
# ---------------------------------------------------------------------------


class _FakeAuditPool:
    """Fake pool answering the two audit_log lookups get_deadman_escalation_state issues."""

    def __init__(self, *, first_detected_at: datetime | None, escalated: bool):
        self._first_detected_at = first_detected_at
        self._escalated = escalated

    async def fetchrow(self, sql: str, *args):
        if "calendar_sync_deadman_first_detected" in args:
            return {"ts": self._first_detected_at} if self._first_detected_at is not None else None
        if "calendar_sync_deadman_escalated" in args:
            return {"dummy": 1} if self._escalated else None
        raise AssertionError(f"Unexpected query: {sql} {args}")


async def test_get_deadman_escalation_state_never_detected():
    pool = _FakeAuditPool(first_detected_at=None, escalated=False)
    first, escalated = await get_deadman_escalation_state(pool, "fp")
    assert first is None
    assert escalated is False


async def test_get_deadman_escalation_state_detected_and_escalated():
    ts = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    pool = _FakeAuditPool(first_detected_at=ts, escalated=True)
    first, escalated = await get_deadman_escalation_state(pool, "fp")
    assert first == ts
    assert escalated is True


# ---------------------------------------------------------------------------
# maybe_escalate_calendar_sync_deadman
# ---------------------------------------------------------------------------


def _report(
    stale_sources: tuple[StaleCalendarSource, ...], checked_at: datetime
) -> CalendarSyncDeadmanReport:
    return CalendarSyncDeadmanReport(checked_at=checked_at, stale_sources=stale_sources)


_STALE = (
    StaleCalendarSource(
        source_key="provider:google:primary",
        db_butler="general",
        butler_name=None,
        last_synced_at=None,
    ),
)


async def test_maybe_escalate_no_stale_sources_is_a_no_op():
    result = await maybe_escalate_calendar_sync_deadman(object(), _report((), datetime.now(UTC)))
    assert result == {"escalated": False, "reason": "no_stale_sources"}


async def test_maybe_escalate_first_sighting_writes_marker_no_escalation(monkeypatch):
    now = datetime.now(UTC)

    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.get_deadman_escalation_state",
        AsyncMock(return_value=(None, False)),
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    result = await maybe_escalate_calendar_sync_deadman(object(), _report(_STALE, now))

    assert result["escalated"] is False
    assert result["reason"] == "newly_detected"
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "calendar_sync_deadman_first_detected"


async def test_maybe_escalate_within_grace_does_not_escalate(monkeypatch):
    now = datetime.now(UTC)
    first_detected = now - timedelta(seconds=CALENDAR_DEADMAN_ESCALATION_DELAY_S - 1)

    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.get_deadman_escalation_state",
        AsyncMock(return_value=(first_detected, False)),
    )
    create_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.create_or_join_attempt", create_mock)

    result = await maybe_escalate_calendar_sync_deadman(object(), _report(_STALE, now))

    assert result["escalated"] is False
    assert result["reason"] == "within_grace"
    create_mock.assert_not_awaited()


async def test_maybe_escalate_past_grace_escalates_via_healing_attempts(monkeypatch):
    now = datetime.now(UTC)
    first_detected = now - timedelta(seconds=CALENDAR_DEADMAN_ESCALATION_DELAY_S + 1)
    attempt_id = uuid4()

    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.get_deadman_escalation_state",
        AsyncMock(return_value=(first_detected, False)),
    )
    create_mock = AsyncMock(return_value=(attempt_id, True))
    update_mock = AsyncMock(return_value=True)
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.create_or_join_attempt", create_mock)
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.update_attempt_status", update_mock)
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.audit_router.append", append_mock)

    result = await maybe_escalate_calendar_sync_deadman(object(), _report(_STALE, now))

    assert result["escalated"] is True
    assert result["healing_attempt_id"] == str(attempt_id)
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["exception_type"] == "CalendarSyncDeadman"
    update_mock.assert_awaited_once()
    assert update_mock.await_args.args[1] == attempt_id
    assert update_mock.await_args.args[2] == "unfixable"
    assert "human action required" in update_mock.await_args.kwargs["error_detail"]
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "calendar_sync_deadman_escalated"


async def test_maybe_escalate_does_not_re_escalate(monkeypatch):
    now = datetime.now(UTC)
    first_detected = now - timedelta(seconds=CALENDAR_DEADMAN_ESCALATION_DELAY_S * 10)

    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.get_deadman_escalation_state",
        AsyncMock(return_value=(first_detected, True)),
    )
    create_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.calendar_sync_deadman.create_or_join_attempt", create_mock)

    result = await maybe_escalate_calendar_sync_deadman(object(), _report(_STALE, now))

    assert result == {
        "escalated": False,
        "reason": "already_escalated",
        "first_detected_at": first_detected.isoformat(),
    }
    create_mock.assert_not_awaited()


async def test_maybe_escalate_reports_degraded_not_crash_on_escalation_failure(monkeypatch):
    now = datetime.now(UTC)
    first_detected = now - timedelta(seconds=CALENDAR_DEADMAN_ESCALATION_DELAY_S * 10)

    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.get_deadman_escalation_state",
        AsyncMock(return_value=(first_detected, False)),
    )
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.create_or_join_attempt",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await maybe_escalate_calendar_sync_deadman(object(), _report(_STALE, now))

    assert result["escalated"] is False
    assert result["reason"] == "escalation_failed"


# ---------------------------------------------------------------------------
# run_calendar_sync_deadman_check (end-to-end tick, never raises)
# ---------------------------------------------------------------------------


async def test_run_check_available_not_stale(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.compute_calendar_sync_report",
        AsyncMock(
            return_value=CalendarSyncDeadmanReport(checked_at=datetime.now(UTC), stale_sources=())
        ),
    )
    result = await run_calendar_sync_deadman_check(_FakeDatabaseManager())
    assert result == {"available": True, "stale": False}


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


async def test_run_check_stale_runs_escalation(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.compute_calendar_sync_report",
        AsyncMock(
            return_value=CalendarSyncDeadmanReport(
                checked_at=datetime.now(UTC), stale_sources=_STALE
            )
        ),
    )
    monkeypatch.setattr(
        "butlers.jobs.calendar_sync_deadman.maybe_escalate_calendar_sync_deadman",
        AsyncMock(return_value={"escalated": False, "reason": "newly_detected"}),
    )
    pool = object()
    result = await run_calendar_sync_deadman_check(
        _FakeDatabaseManager(pools={"switchboard": pool})
    )
    assert result["available"] is True
    assert result["stale"] is True
    assert result["reason"] == "newly_detected"


async def test_run_check_stale_no_switchboard_pool_degrades_gracefully(monkeypatch):
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
        "escalated": False,
        "reason": "no_pool",
    }
