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
        "state": item.state,
        "synced_at": item.synced_at,
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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_a_declined_occurrence_is_not_read_as_an_agreement(db) -> None:
    from butlers.modules.calendar.models import AttendeeResponseStatus
    from tests.modules.test_calendar_owner_agreements import _attendees, _world
    from tests.modules.test_calendar_participants import _event, _module, _project

    pool, source = await _world(db)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    event = _event(attendees=_attendees()).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )
    await _project(_module(pool), source, event)
    assert len(await pool.fetch(job._AGREEMENTS_SQL)) == 1

    declined = event.model_copy(update={"attendees": _attendees(AttendeeResponseStatus.declined)})
    await _project(_module(pool), source, declined)

    assert [r["state"] for r in await pool.fetch(job._AGREEMENTS_SQL)] == ["declined"]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_a_lost_seat_is_ended_not_declined_and_the_cursor_is_the_last_success(db) -> None:
    from tests.modules.test_calendar_owner_agreements import _attendees, _world
    from tests.modules.test_calendar_participants import _event, _module, _project

    pool, source = await _world(db)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    event = _event(attendees=_attendees()).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )
    await _project(_module(pool), source, event)
    # Attempts keep advancing (auth failures); the last success is two days old.
    await pool.execute(
        "INSERT INTO calendar_sync_cursors (source_id, last_synced_at, last_success_at) "
        "VALUES ($1, now(), now() - interval '2 days') ON CONFLICT (source_id, cursor_name) "
        "DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at, "
        "last_success_at = EXCLUDED.last_success_at",
        source,
    )

    (row,) = await pool.fetch(job._AGREEMENTS_SQL)
    assert datetime.now(UTC) - row["synced_at"] > timedelta(days=1)

    # The owner is dropped from the guest list: the seat is lost, which is not a decline.
    await _project(_module(pool), source, event.model_copy(update={"attendees": []}))

    assert [r["state"] for r in await pool.fetch(job._AGREEMENTS_SQL)] == ["ended"]


def test_the_freshest_copy_is_reported_then_the_butler_name() -> None:
    later = _OBS + timedelta(hours=1)
    moved = {"current_start": _T0 + timedelta(hours=1)}
    stale_move = _agreed("aaa", current_observed_at=_OBS, **moved)
    fresh_unchanged = _agreed("zzz", current_observed_at=later)
    # A newer copy that shows no change wins over an older copy that does.
    assert job.build_observations([stale_move, fresh_unchanged], {})[0] == []

    tie_b = _agreed("bbb", current_observed_at=later, **moved)
    tie_z = _agreed("zzz", current_observed_at=later, **moved)
    observations, _ = job.build_observations([tie_z, tie_b], {})
    assert [o.metadata["butler"] for o in observations] == ["bbb"]


def test_a_journal_head_is_compared_with_the_sync_cursor_not_the_last_change() -> None:
    synced = _OBS + timedelta(days=2)
    # The projection last changed long ago, but it was confirmed by a sync after the head was seen.
    item = _agreed(current_observed_at=_OBS, synced_at=synced)
    older_head = Head(
        "confirmed", _T0 + timedelta(hours=2), _T0 + timedelta(hours=3), synced - timedelta(hours=1)
    )
    assert job.material_reasons(item, older_head) == ([], 0)

    newer_head = Head(
        "confirmed", _T0 + timedelta(hours=2), _T0 + timedelta(hours=3), synced + timedelta(hours=1)
    )
    assert job.material_reasons(item, newer_head)[0] == ["moved"]


def test_resolution_causes_name_why_each_unchanged_occurrence_ended() -> None:
    items = [
        _agreed(ref="rev"),
        _agreed(ref="dec", state="declined"),
        _agreed(ref="can", state="cancelled"),
        _agreed(ref="sta", state="started"),
        _agreed(ref="jcan"),
        _agreed(ref="moved", current_start=_T0 + timedelta(hours=1)),
    ]
    causes = job.resolution_causes(items, {"jcan": Head("cancelled", None, None, _OBS)})
    by_ref = {r: causes[job.fingerprint(r, _T0)] for r in ("rev", "dec", "can", "sta", "jcan")}
    assert by_ref == {
        "rev": "reverted",
        "dec": "declined",
        "can": "cancelled",
        "sta": "started",
        "jcan": "cancelled",
    }
    assert job.fingerprint("moved", _T0) not in causes  # still changed: nothing resolves


def test_ended_agreements_are_never_observed_or_counted() -> None:
    observations, checked = job.build_observations(
        [_agreed(state="declined", current_start=_T0 + timedelta(hours=1))], {}
    )
    assert observations == [] and checked == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_end_to_end_a_move_opens_a_condition_and_moving_back_resolves_it_as_reverted(
    db, monkeypatch
) -> None:
    from butlers.core.owner_conditions import create_owner_conditions_table
    from butlers.modules.calendar.models import AttendeeResponseStatus
    from tests.modules.test_calendar_owner_agreements import _attendees, _world
    from tests.modules.test_calendar_participants import _event, _module, _project

    pool, source = await _world(db)
    await create_owner_conditions_table(pool)
    await pool.execute("TRUNCATE TABLE public.owner_conditions")
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    event = _event(attendees=_attendees()).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )
    report = MagicMock(is_available=True, is_stale=False, failed_butlers=())
    monkeypatch.setattr(job, "compute_calendar_sync_report", AsyncMock(return_value=report))
    monkeypatch.setattr(job, "read_journal_heads", AsyncMock(return_value={}))
    db_mgr = db_manager({"switchboard": pool}, modules={"relationship": ["calendar"]})

    async def fan_out(sql, args, butler_names=None):
        return {"relationship": await pool.fetch(sql)}, []

    db_mgr.fan_out_with_status = fan_out

    async def move(by):
        await _project(
            _module(pool),
            source,
            event.model_copy(
                update={"start_at": start + by, "end_at": start + by + timedelta(hours=1)}
            ),
        )

    async def state():
        return await pool.fetchrow(
            "SELECT state, metadata FROM public.owner_conditions ORDER BY episode DESC LIMIT 1"
        )

    await _project(_module(pool), source, event)
    assert (await job.run_calendar_agreement_drift_check(db_mgr))["observed"] == 0

    await move(timedelta(hours=2))
    assert (await job.run_calendar_agreement_drift_check(db_mgr))["transitions"] == ["opened"]
    assert (await state())["state"] != "resolved"

    await move(timedelta(0))
    result = await job.run_calendar_agreement_drift_check(db_mgr)
    assert result["complete"] and result["transitions"] == ["resolved"]
    row = await state()
    assert row["state"] == "resolved" and row["metadata"]["resolution_cause"] == "reverted"

    # A fresh move, then declining: resolved as declined, never "reverted".
    await move(timedelta(hours=4))
    await job.run_calendar_agreement_drift_check(db_mgr)
    declined = event.model_copy(
        update={
            "start_at": start + timedelta(hours=4),
            "end_at": start + timedelta(hours=5),
            "attendees": _attendees(AttendeeResponseStatus.declined),
        }
    )
    await _project(_module(pool), source, declined)
    await job.run_calendar_agreement_drift_check(db_mgr)
    assert (await state())["metadata"]["resolution_cause"] == "declined"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_a_broker_failure_loses_no_candidate_and_a_second_move_is_a_new_one(
    db, monkeypatch
) -> None:
    from butlers.core.owner_conditions import create_owner_conditions_table
    from butlers.tools.switchboard.insight import broker
    from tests.modules.test_calendar_owner_agreements import _attendees, _world
    from tests.modules.test_calendar_participants import _event, _module, _project

    pool, source = await _world(db)
    await create_owner_conditions_table(pool)
    await broker.create_insight_tables(pool)
    await pool.execute("TRUNCATE TABLE public.owner_conditions")
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    event = _event(attendees=_attendees()).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )
    report = MagicMock(is_available=True, is_stale=False, failed_butlers=())
    monkeypatch.setattr(job, "compute_calendar_sync_report", AsyncMock(return_value=report))
    monkeypatch.setattr(job, "read_journal_heads", AsyncMock(return_value={}))
    db_mgr = db_manager({"switchboard": pool}, modules={"relationship": ["calendar"]})

    async def fan_out(sql, args, butler_names=None):
        return {"relationship": await pool.fetch(sql)}, []

    db_mgr.fan_out_with_status = fan_out

    async def move(by):
        await _project(
            _module(pool),
            source,
            event.model_copy(
                update={"start_at": start + by, "end_at": start + by + timedelta(hours=1)}
            ),
        )

    async def keys():
        return [
            r["dedup_key"]
            for r in await pool.fetch(
                "SELECT dedup_key FROM insight_candidates WHERE category = $1 ORDER BY created_at",
                job.CANDIDATE_CATEGORY,
            )
        ]

    real = broker.propose_insight_candidate

    async def down(*args, **kwargs):
        raise RuntimeError("broker down")

    await _project(_module(pool), source, event)
    monkeypatch.setattr(broker, "propose_insight_candidate", down)
    await move(timedelta(hours=2))
    assert (await job.run_calendar_agreement_drift_check(db_mgr))["transitions"] == ["opened"]
    assert await keys() == []  # the condition is durable, its candidate was lost

    monkeypatch.setattr(broker, "propose_insight_candidate", real)
    result = await job.run_calendar_agreement_drift_check(db_mgr)  # a later complete tick
    assert result["queued"] == 1 and len(await keys()) == 1
    await job.run_calendar_agreement_drift_check(db_mgr)
    assert len(await keys()) == 1  # and exactly once

    await move(timedelta(0))
    await job.run_calendar_agreement_drift_check(db_mgr)  # resolved
    await move(timedelta(hours=2))
    await job.run_calendar_agreement_drift_check(db_mgr)  # a new episode

    found = await keys()
    assert len(found) == 2 and found[0] != found[1]


async def test_a_change_to_an_event_that_has_started_is_not_announced() -> None:
    item = _agreed(current_start=_T0)
    broker = _Broker()

    queued = await job.propose_changes(
        object(), _opened(item), [item], {}, now=_T0 + timedelta(minutes=5), broker=broker
    )

    assert queued == 0 and broker.calls == []


async def test_a_digest_line_expires_when_the_event_starts() -> None:
    item = _agreed(current_start=_T0 + timedelta(days=3))
    broker = _Broker()

    await job.propose_changes(
        object(), _opened(item), [item], {}, now=_T0 - timedelta(days=5), broker=broker
    )

    assert broker.calls[0]["expires_at"] == _T0 + timedelta(days=3)


async def test_only_a_revert_is_amended_after_delivery(tick) -> None:
    from types import SimpleNamespace

    rows = [_row(ref="rev"), _row(ref="dec", state="declined"), _row(ref="can", state="cancelled")]
    await job.run_calendar_agreement_drift_check(_db(rows))
    gate = tick["reconcile"].await_args.kwargs["amend_resolved"]

    def verdict(ref):
        return gate(SimpleNamespace(fingerprint=job.fingerprint(ref, _T0)))

    assert verdict("rev") is True
    assert verdict("dec") is False and verdict("can") is False
    assert verdict("unknown") is False


class _Broker:
    def __init__(self, timed_status="accepted"):
        self.calls: list[dict[str, Any]] = []
        self.timed_status = timed_status

    async def propose_insight_candidate(self, pool, **kw):
        self.calls.append(kw)
        if kw.get("lane") == "timed":
            return {"status": self.timed_status}
        return {"status": "accepted"}


def _opened(item):
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            fingerprint=job.fingerprint(item.origin_ref, item.occurrence_start), transition="opened"
        )
    ]


async def test_an_opened_condition_proposes_one_premised_digest_candidate() -> None:
    item = _agreed(title="Board review", current_start=_T0 + timedelta(days=3))
    broker = _Broker()

    queued = await job.propose_changes(
        object(), _opened(item), [item], {}, now=_T0 - timedelta(days=5), broker=broker
    )

    assert queued == 1
    (call,) = broker.calls
    assert call.get("lane", "digest") == "digest"
    assert call["premise"] == {
        "kind": "owner_condition",
        "source": job.CONDITION_SOURCE,
        "fingerprint": job.fingerprint("evt-1", _T0),
    }
    assert call["message"].startswith("Board review changed since you accepted it: moved")


async def test_a_change_starting_within_a_day_uses_the_timed_lane_then_falls_back() -> None:
    item = _agreed(current_start=_T0 + timedelta(hours=1))
    now = _T0 - timedelta(hours=3)

    timed = _Broker()
    await job.propose_changes(object(), _opened(item), [item], {}, now=now, broker=timed)
    assert [c.get("lane") for c in timed.calls] == ["timed"]

    dark = _Broker(timed_status="error")
    await job.propose_changes(object(), _opened(item), [item], {}, now=now, broker=dark)
    assert [c.get("lane") for c in dark.calls] == ["timed", None]  # never notify directly


async def test_only_opened_or_reopened_conditions_are_proposed() -> None:
    from types import SimpleNamespace

    item = _agreed(current_start=_T0 + timedelta(hours=2))
    fp = job.fingerprint(item.origin_ref, item.occurrence_start)
    broker = _Broker()
    for kind in ("confirmed", "no_change", "resolved", "escalation_due"):
        await job.propose_changes(
            object(),
            [SimpleNamespace(fingerprint=fp, transition=kind)],
            [item],
            {},
            now=_T0,
            broker=broker,
        )
    assert broker.calls == []


async def test_third_party_text_is_defanged_in_the_line() -> None:
    item = _agreed(title="Sync https://evil.test/x", current_start=_T0 + timedelta(hours=2))
    line = job.change_line(item, ["moved"], 120)
    assert "https://" not in line and "evil[.]test" in line


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_an_episode_older_than_the_candidate_retention_is_not_recovered(
    db, monkeypatch
) -> None:
    from butlers.core.owner_conditions import create_owner_conditions_table
    from butlers.tools.switchboard.insight import broker
    from tests.modules.test_calendar_owner_agreements import _attendees, _world
    from tests.modules.test_calendar_participants import _event, _module, _project

    pool, source = await _world(db)
    await create_owner_conditions_table(pool)
    await broker.create_insight_tables(pool)
    await pool.execute("TRUNCATE TABLE public.owner_conditions")
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    event = _event(attendees=_attendees()).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )
    report = MagicMock(is_available=True, is_stale=False, failed_butlers=())
    monkeypatch.setattr(job, "compute_calendar_sync_report", AsyncMock(return_value=report))
    monkeypatch.setattr(job, "read_journal_heads", AsyncMock(return_value={}))
    db_mgr = db_manager({"switchboard": pool}, modules={"relationship": ["calendar"]})

    async def fan_out(sql, args, butler_names=None):
        return {"relationship": await pool.fetch(sql)}, []

    db_mgr.fan_out_with_status = fan_out

    async def down(*args, **kwargs):
        raise RuntimeError("broker down")

    await _project(_module(pool), source, event)
    monkeypatch.setattr(broker, "propose_insight_candidate", down)
    moved = event.model_copy(
        update={
            "start_at": start + timedelta(hours=2),
            "end_at": start + timedelta(hours=3),
        }
    )
    await _project(_module(pool), source, moved)
    await job.run_calendar_agreement_drift_check(db_mgr)  # opened, its candidate lost
    # The delivered candidate would long since have been pruned: an old episode stays quiet.
    await pool.execute(
        "UPDATE public.owner_conditions SET first_detected_at = now() - interval '100 days'"
    )
    monkeypatch.undo()
    monkeypatch.setattr(job, "compute_calendar_sync_report", AsyncMock(return_value=report))
    monkeypatch.setattr(job, "read_journal_heads", AsyncMock(return_value={}))

    result = await job.run_calendar_agreement_drift_check(db_mgr)

    assert result["queued"] == 0
    assert await pool.fetchval("SELECT count(*) FROM insight_candidates") == 0
