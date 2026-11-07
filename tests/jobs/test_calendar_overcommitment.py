"""Calendar overcommitment owner-condition producer (bu-vcjgo8, REQ-owner-condition-ledger-007).

Unit coverage with mocked pools: the pure observation builder (qualifying kinds, identity,
privacy), the identity seam against the real ``query_calendar_conflicts`` read model over a
faked fan-out (task 1.1), and the check's completeness rules. The ledger semantics behind
``reconcile_snapshot`` are proven against real Postgres in
``tests/integration/test_calendar_overcommitment_roundtrip.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace import (
    CalendarConflictScan,
    query_calendar_conflicts,
)
from butlers.core.temporal.conflicts import ConflictEventRef, DetectedIssue
from butlers.jobs import calendar_overcommitment as job
from butlers.testing.db_manager import db_manager

pytestmark = pytest.mark.unit

_DAY = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
_PRIVATE = ("Board offsite", "ceo@example.com", "Room 4B")


def _ref(entry_id: str, start: datetime, minutes: int = 60) -> ConflictEventRef:
    return ConflictEventRef(
        entry_id=entry_id,
        title=_PRIVATE[0],
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        timezone="UTC",
        status="confirmed",
    )


def _overlap(a: str = "a", b: str = "b") -> DetectedIssue:
    return DetectedIssue(
        kind="overlap",
        date="2026-07-01",
        summary=f"“{_PRIVATE[0]}” and “{_PRIVATE[0]}” overlap by 30 min",
        severity="warning",
        events=[_ref(a, _DAY), _ref(b, _DAY + timedelta(minutes=30))],
    )


def _scan(*issues: DetectedIssue, available: bool = True, **keys: str) -> CalendarConflictScan:
    return CalendarConflictScan(
        issues=list(issues), available=available, entry_identity_keys=dict(keys)
    )


def _fingerprints(scan: CalendarConflictScan) -> set[str]:
    return {o.fingerprint for o in job.build_overcommitment_observations(scan)}


# ---------------------------------------------------------------------------
# Observation builder
# ---------------------------------------------------------------------------


def test_overlap_and_overloaded_day_open_while_back_to_back_and_info_never_do() -> None:
    day = [_ref(f"d{i}", _DAY + timedelta(hours=i), minutes=55) for i in range(7)]
    issues = [
        _overlap(),
        DetectedIssue(
            kind="overloaded_day",
            date="2026-07-01",
            summary="6.4 h of meetings in one day",
            severity="warning",
            events=day,
        ),
        # A 3-chain back-to-back is a `warning`: kind, not severity, excludes it.
        DetectedIssue(kind="back_to_back", date="2026-07-01", summary="x", severity="warning"),
        DetectedIssue(kind="overlap", date="2026-07-02", summary="x", severity="info"),
    ]

    observations = job.build_overcommitment_observations(_scan(*issues, a="origin:A", b="origin:B"))

    by_kind = {o.metadata["kind"]: o for o in observations}
    assert set(by_kind) == {"overlap", "overloaded_day"}
    overlap = by_kind["overlap"]
    assert overlap.fingerprint == job.overcommitment_fingerprint(
        {"kind": "overlap", "pair_id": job.stable_pair_id("origin:A", "origin:B")}
    )
    assert overlap.metadata == {
        "kind": "overlap",
        "date": "2026-07-01",
        "severity": "warning",
        "overlap_minutes": 30,
        "entry_ids": ["a", "b"],
    }
    overloaded = by_kind["overloaded_day"]
    assert overloaded.fingerprint == job.overcommitment_fingerprint(
        {"kind": "overloaded_day", "date": "2026-07-01"}
    )
    assert overloaded.metadata == {
        "kind": "overloaded_day",
        "date": "2026-07-01",
        "severity": "warning",
        "meeting_hours": 6.4,
        "event_count": 7,
    }


def test_condition_text_carries_no_title_attendee_or_location() -> None:
    issues = [
        _overlap(),
        DetectedIssue(
            kind="overloaded_day",
            date="2026-07-01",
            summary=f"{_PRIVATE} 7.0 h of meetings in one day",
            severity="warning",
            events=[_ref("x", _DAY, minutes=7 * 60)],
        ),
    ]

    for observation in job.build_overcommitment_observations(_scan(*issues)):
        rendered = f"{observation.summary} {observation.metadata}"
        assert not [value for value in _PRIVATE if value in rendered]


def test_pair_identity_is_order_free_and_independent_of_surviving_copy() -> None:
    first = _scan(_overlap("copy-1", "b"), **{"copy-1": "origin:A", "b": "origin:B"})
    swapped = _scan(_overlap("b", "copy-2"), **{"copy-2": "origin:A", "b": "origin:B"})

    assert _fingerprints(first) == _fingerprints(swapped)
    assert _fingerprints(first) != _fingerprints(_scan(_overlap("copy-1", "b")))


# ---------------------------------------------------------------------------
# Identity seam against the real read model (task 1.1)
# ---------------------------------------------------------------------------


def _row(entry_id: str, origin_ref: str, start: datetime, updated_at: datetime) -> dict:
    return {
        "instance_id": entry_id,
        "origin_instance_ref": f"{origin_ref}:{start.isoformat()}",
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(hours=1),
        "instance_status": "confirmed",
        "instance_metadata": {},
        "instance_updated_at": updated_at,
        "event_id": uuid4(),
        "origin_ref": origin_ref,
        "title": f"{origin_ref} title",
        "description": "",
        "location": "",
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": {"source_type": "provider_event"},
        "source_butler": None,
        "source_session_id": None,
        "source_id": uuid4(),
        "source_key": "provider:google:primary",
        "source_kind": "provider_event",
        "lane": "user",
        "provider": "google",
        "calendar_id": "primary",
        "butler_name": None,
        "display_name": "Primary",
        "writable": True,
        "source_metadata": {},
        "cursor_name": "provider_sync",
        "last_synced_at": start,
        "last_success_at": start,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


def _workspace_db(rows: dict[str, list[dict]], *, down: frozenset[str] = frozenset()) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = sorted(rows)
    db.butlers_with_module = MagicMock(return_value=sorted(rows))

    async def _fan_out(query: str, args=(), butler_names=None):
        found = rows if "FROM calendar_event_instances AS i" in query else {}
        return found, []

    db.fan_out_with_status = AsyncMock(side_effect=_fan_out)
    outage = OSError("dedup store down")
    dedup_pool = MagicMock()
    # fetchrow reads the dedup rules; fetch reads the keep-separate pins.
    dedup_pool.fetchrow = AsyncMock(side_effect=outage if "rules" in down else None)
    dedup_pool.fetch = AsyncMock(side_effect=outage if "pins" in down else None, return_value=[])
    if "rules" not in down:
        dedup_pool.fetchrow.return_value = None
    db.pool = MagicMock(return_value=dedup_pool)
    return db


async def test_overlap_identity_survives_a_resync_that_rotates_the_surviving_copy() -> None:
    """The same provider event lives in every calendar butler's schema, and the
    ``origin_ref`` dedup keeps the most recently synced copy, so the surviving
    ``instance_id`` (and the radar's pair id) rotates across schemas between
    scans. The condition identity must not."""
    early, late = _DAY - timedelta(hours=2), _DAY - timedelta(hours=1)
    other = _row("other", "B", _DAY + timedelta(minutes=30), early)

    async def scan(general_stamp: datetime, travel_stamp: datetime) -> CalendarConflictScan:
        db = _workspace_db(
            {
                "general": [_row("general-copy", "A", _DAY, general_stamp), other],
                "travel": [_row("travel-copy", "A", _DAY, travel_stamp)],
            }
        )
        return await query_calendar_conflicts(db, start=_DAY, end=_DAY + timedelta(days=1))

    before, after = await scan(late, early), await scan(early, late)

    def overlap_entries(result: CalendarConflictScan) -> set[str]:
        (issue,) = [i for i in result.issues if i.kind == "overlap"]
        return {e.entry_id for e in issue.events}

    assert overlap_entries(before) == {"general-copy", "other"}
    assert overlap_entries(after) == {"travel-copy", "other"}
    assert before.dedup_available and after.dedup_available
    assert _fingerprints(before) == _fingerprints(after)


@pytest.mark.parametrize("down", ["rules", "pins"])
async def test_dedup_store_outage_is_reported_on_the_scan(down: str) -> None:
    db = _workspace_db({"general": [_row("a", "A", _DAY, _DAY)]}, down=frozenset({down}))

    result = await query_calendar_conflicts(db, start=_DAY, end=_DAY + timedelta(days=1))

    assert result.available is True
    assert result.dedup_available is False


# ---------------------------------------------------------------------------
# Check: window, completeness, failure isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def check(monkeypatch):
    """Patch the check's three seams; return their mocks."""
    mocks = {
        "settings": AsyncMock(return_value={"timezone": "Asia/Singapore"}),
        "scan": AsyncMock(return_value=_scan(_overlap(), a="origin:A", b="origin:B")),
        "reconcile": AsyncMock(return_value=[]),
    }
    monkeypatch.setattr(job, "load_general_settings", mocks["settings"])
    monkeypatch.setattr(job, "query_calendar_conflicts", mocks["scan"])
    monkeypatch.setattr(job, "reconcile_snapshot", mocks["reconcile"])
    return mocks


async def test_complete_scan_of_the_owner_local_window_resolves_by_omission(check) -> None:
    pool = object()
    now = datetime(2026, 7, 1, 20, 0, tzinfo=UTC)  # 04:00 on 2 July in Singapore

    result = await job.run_calendar_overcommitment_check(
        db_manager({"switchboard": pool}), lookahead_days=3, now=now
    )

    assert result["reconciled"] is True
    sgt = ZoneInfo("Asia/Singapore")
    scan_kwargs = check["scan"].await_args.kwargs
    assert scan_kwargs["start"] == datetime(2026, 7, 2, tzinfo=sgt)
    assert scan_kwargs["end"] == datetime(2026, 7, 5, tzinfo=sgt)
    assert scan_kwargs["display_tz"] == sgt
    (args, kwargs) = check["reconcile"].await_args
    assert args == (pool,)
    assert kwargs["source"] == "calendar:overcommitment"
    assert kwargs["snapshot_complete"] is True
    assert len(kwargs["observations"]) == 1


async def test_partial_scan_confirms_what_it_saw_but_never_resolves(check) -> None:
    check["scan"].return_value = _scan(_overlap(), available=False)

    result = await job.run_calendar_overcommitment_check(db_manager({"switchboard": object()}))

    assert result["reconciled"] is True
    kwargs = check["reconcile"].await_args.kwargs
    assert kwargs["snapshot_complete"] is False
    assert len(kwargs["observations"]) == 1


@pytest.mark.parametrize("failure", ["no_pool", "timezone", "scan_raises", "dedup_unavailable"])
async def test_unmeasurable_tick_never_reconciles(check, failure) -> None:
    pools: dict[str, Any] = {"switchboard": object()}
    if failure == "no_pool":
        pools = {}
    elif failure == "timezone":
        check["settings"].side_effect = OSError("state table unreachable")
    elif failure == "scan_raises":
        check["scan"].side_effect = RuntimeError("fan-out down")
    else:
        check["scan"].return_value = CalendarConflictScan(issues=[], dedup_available=False)

    result = await job.run_calendar_overcommitment_check(db_manager(pools))

    assert result["reconciled"] is False
    check["reconcile"].assert_not_awaited()


async def test_reconciliation_failure_never_kills_the_loop(check, monkeypatch) -> None:
    check["reconcile"].side_effect = [OSError("ledger mid-migration"), []]
    ticks: list[bool] = []

    async def record_tick(*, failed: bool = False) -> None:
        ticks.append(failed)
        if len(ticks) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(job, "record_tick", record_tick)
    monkeypatch.setattr(job.asyncio, "sleep", AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await job.run_calendar_overcommitment_loop(
            db_manager({"switchboard": object()}), interval_s=1, lookahead_days=7
        )

    assert ticks == [True, False]
    assert check["reconcile"].await_count == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 7), ("14", 14), ("1", 1), ("90", 90), ("0", 7), ("91", 7), ("2.5", 7), ("x", 7)],
)
def test_lookahead_days_is_bounded_with_a_safe_default(monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv(job.LOOKAHEAD_DAYS_ENV, raising=False)
    else:
        monkeypatch.setenv(job.LOOKAHEAD_DAYS_ENV, raw)

    assert job.resolve_lookahead_days() == expected
