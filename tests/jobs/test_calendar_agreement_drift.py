"""Changed since you said yes: the drift reconciler (bu-5ii4gj.10 slice A-S2). Synthetic data."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs import calendar_agreement_drift as job
from butlers.jobs.calendar_agreement_drift import Agreed, Head
from butlers.modules.calendar.agreements import attendees_digest
from butlers.testing.db_manager import db_manager

_T0 = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)
_OBS = datetime(2026, 11, 20, tzinfo=UTC)
_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")


def _agreed(butler="relationship", ref="evt-1", **over: Any) -> Agreed:
    base: dict[str, Any] = {
        "butler": butler,
        "event_id": "00000000-0000-0000-0000-000000000001",
        "origin_ref": ref,
        "occurrence_start": _T0,
        "agreed_start": _T0,
        "agreed_end": _T0 + timedelta(hours=1),
        "agreed_location": "loc",
        "agreed_attendees": attendees_digest(["a@x.test"]),
        "current_start": _T0,
        "current_end": _T0 + timedelta(hours=1),
        "current_location": "loc",
        "current_attendees": attendees_digest(["a@x.test"]),
        "current_observed_at": _OBS,
    }
    return Agreed(**{**base, **over})


@pytest.mark.parametrize(
    ("over", "reasons", "minutes"),
    [
        ({}, [], 0),
        ({"current_start": _T0 + timedelta(minutes=14)}, [], 14),
        ({"current_start": _T0 + timedelta(minutes=15)}, ["moved"], 15),
        ({"current_start": _T0 - timedelta(hours=1)}, ["moved"], 60),
        ({"current_location": "other"}, ["relocated"], 0),
        ({"current_location": None}, ["relocated"], 0),
        ({"current_attendees": attendees_digest([])}, ["attendees_removed"], 0),
        ({"current_attendees": attendees_digest(["a@x.test", "b@x.test"])}, [], 0),
    ],
)
def test_what_counts_as_a_material_change(over, reasons, minutes) -> None:
    assert job.material_reasons(_agreed(**over), None) == (reasons, minutes)


def test_a_newer_journal_head_overrides_the_projection_and_a_cancelled_head_ends_it() -> None:
    item = _agreed()
    moved = Head(
        "confirmed", _T0 + timedelta(hours=2), _T0 + timedelta(hours=3), _OBS + timedelta(1)
    )
    stale = Head("confirmed", _T0 + timedelta(hours=2), None, _OBS - timedelta(1))
    cancelled = Head("cancelled", None, None, _OBS + timedelta(1))

    assert job.material_reasons(item, moved) == (["moved"], 120)
    assert job.material_reasons(item, stale) == ([], 0)
    moved_item = _agreed(current_start=_T0 + timedelta(hours=1))
    assert job.material_reasons(moved_item, None) == (["moved"], 60)
    assert job.material_reasons(moved_item, cancelled) == ([], 0)


def test_copies_across_schemas_are_one_condition_and_observations_hold_no_text() -> None:
    moved = {"current_start": _T0 + timedelta(hours=1)}
    items = [
        _agreed("travel", **moved),
        _agreed("general", **moved),
        _agreed("health", ref="evt-2"),
    ]

    observations, checked = job.build_observations(items, {})

    assert checked == 2 and len(observations) == 1
    assert observations[0].metadata["butler"] == "general"
    assert observations[0].fingerprint == job.fingerprint("evt-1", _T0)
    assert "title" not in repr(observations[0]).lower()


@pytest.fixture
def tick(monkeypatch):
    report = MagicMock(is_available=True, is_stale=False, failed_butlers=())
    mocks = {
        "report": AsyncMock(return_value=report),
        "reconcile": AsyncMock(return_value=[]),
        "heads": AsyncMock(return_value={}),
    }
    monkeypatch.setattr(job, "compute_calendar_sync_report", mocks["report"])
    monkeypatch.setattr(job, "reconcile_snapshot", mocks["reconcile"])
    monkeypatch.setattr(job, "read_journal_heads", mocks["heads"])
    mocks["incomplete"] = MagicMock()
    monkeypatch.setattr(job, "mark_tick_incomplete", mocks["incomplete"])
    return mocks


def _db(rows=None, failed=()):
    db = db_manager({"switchboard": object()}, modules={"relationship": ["calendar"]})
    db.fan_out_with_status = AsyncMock(return_value=({"relationship": rows or []}, list(failed)))
    return db


def _row(**over):
    item = _agreed(**over)
    return {
        "event_id": item.event_id,
        "origin_ref": item.origin_ref,
        "occurrence_start": item.occurrence_start,
        "agreed_starts_at": item.agreed_start,
        "agreed_ends_at": item.agreed_end,
        "location_digest": item.agreed_location,
        "attendees_digest": item.agreed_attendees,
        "current_starts_at": item.current_start,
        "current_ends_at": item.current_end,
        "current_location_digest": item.current_location,
        "current_attendees_digest": item.current_attendees,
        "current_observed_at": item.current_observed_at,
    }


async def test_a_moved_event_opens_one_condition_and_a_complete_tick_may_resolve(tick) -> None:
    rows = [_row(current_start=_T0 + timedelta(hours=1)), _row(ref="evt-2")]

    result = await job.run_calendar_agreement_drift_check(_db(rows))

    assert result["reconciled"] and result["checked"] == 2 and result["observed"] == 1
    kwargs = tick["reconcile"].await_args.kwargs
    assert kwargs["source"] == "calendar:changed_since_accepted"
    assert kwargs["snapshot_complete"] is True and len(kwargs["observations"]) == 1
    tick["incomplete"].assert_not_called()


async def test_a_title_only_change_opens_nothing(tick) -> None:
    result = await job.run_calendar_agreement_drift_check(_db([_row()]))

    assert result["observed"] == 0
    assert tick["reconcile"].await_args.kwargs["observations"] == []


async def test_an_unreadable_journal_reconciles_nothing_and_marks_the_tick_incomplete(tick) -> None:
    tick["heads"].side_effect = RuntimeError("connectors schema unreadable")

    result = await job.run_calendar_agreement_drift_check(_db([_row()]))

    assert result == {"reconciled": False, "reason": "journal_unavailable"}
    tick["reconcile"].assert_not_awaited()
    tick["incomplete"].assert_called_once()


@pytest.mark.parametrize("why", ["stale", "unreadable", "no_pool", "read_raised"])
async def test_other_unmeasurable_ticks_reconcile_nothing(tick, why) -> None:
    db = _db([_row()])
    if why == "stale":
        tick["report"].return_value.is_stale = True
    elif why == "unreadable":
        tick["report"].return_value.is_available = False
    elif why == "no_pool":
        db = db_manager({})
    else:
        db.fan_out_with_status.side_effect = RuntimeError("down")

    result = await job.run_calendar_agreement_drift_check(db)

    assert result["reconciled"] is False
    tick["reconcile"].assert_not_awaited()
    tick["incomplete"].assert_called()


async def test_a_partial_fan_out_confirms_but_never_resolves(tick) -> None:
    rows = [_row(current_start=_T0 + timedelta(hours=1))]

    result = await job.run_calendar_agreement_drift_check(_db(rows, failed=["health"]))

    assert result["reconciled"] and result["complete"] is False
    assert tick["reconcile"].await_args.kwargs["snapshot_complete"] is False
    tick["incomplete"].assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_the_journal_definer_returns_only_the_newest_times_and_status(db) -> None:
    pool = await db("core", schema="relationship")
    ref = f"evt-{uuid.uuid4()}"
    for etag, status, hours in (("e1", "confirmed", 1), ("e2", "cancelled", 2)):
        await pool.execute(
            "INSERT INTO connectors.calendar_event_revisions (endpoint_identity, calendar_id, "
            "event_id, etag, status, starts_at, observation, observed_at, raw) "
            "VALUES ('ep', 'cal', $5, $1, $2, $3, 'change', $4, '{\"summary\": \"Secret\"}')",
            etag,
            status,
            _T0,
            _OBS + timedelta(hours=hours),
            ref,
        )

    heads = await job.read_journal_heads(pool, [ref, "missing"])

    assert list(heads) == [ref] and heads[ref].status == "cancelled"
    columns = await pool.fetch("SELECT * FROM public.read_calendar_event_heads($1::text[])", [ref])
    assert set(columns[0].keys()) == {"event_id", "status", "starts_at", "ends_at", "observed_at"}
    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', "
        "'public.read_calendar_event_heads(text[])', 'EXECUTE')"
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_the_agreement_query_feeds_the_job_from_a_real_projection(db) -> None:
    from tests.modules.test_calendar_owner_agreements import _attendees, _world
    from tests.modules.test_calendar_participants import _event, _module, _project

    pool, source = await _world(db)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    event = _event(attendees=_attendees()).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )
    await _project(_module(pool), source, event)

    quiet, _ = job.build_observations(
        [job._agreed("relationship", dict(r)) for r in await pool.fetch(job._AGREEMENTS_SQL)], {}
    )
    moved = event.model_copy(
        update={"start_at": start + timedelta(hours=2), "end_at": start + timedelta(hours=3)}
    )
    await _project(_module(pool), source, moved)
    rows = [job._agreed("relationship", dict(r)) for r in await pool.fetch(job._AGREEMENTS_SQL)]
    observations, checked = job.build_observations(rows, {})

    assert quiet == [] and checked == 1
    assert [o.metadata["reasons"] for o in observations] == [["moved"]]
    assert observations[0].metadata["moved_minutes"] == 120
