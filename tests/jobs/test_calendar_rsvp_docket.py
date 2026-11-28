"""The invitation docket job (bu-5ii4gj.9 S3, REQ-calendar-invitation-docket-003, -004, -007, -008).

The seams (policy read, invitation and calendar reads, the gated tool call and the system reject)
are faked; the pure pieces run as they are.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from butlers.core.temporal.rsvp_policy import Policy
from butlers.jobs import calendar_rsvp_docket as job

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
START = NOW + timedelta(days=2)
TITLE = "Board offsite"  # third-party text that must never reach a card


def _policy(pid="p1", *, verdict="declined", send_updates="none", version=1, revoked=None, **kw):
    predicates = kw.get("predicates") or [
        {"kind": "overlaps_focus_or_out_of_office", "value": True}
    ]
    return Policy.from_row(
        {
            "id": pid,
            "position": kw.get("position", 1),
            "predicates": predicates,
            "verdict": verdict,
            "send_updates": send_updates,
            "version": version,
            "revoked_at": revoked,
        }
    )


def _row(instance_id=None, *, etag="e1", butler="relationship", start=START, **over):
    row = {
        "instance_id": instance_id or uuid4(),
        "event_id": uuid4(),
        "event_etag": etag,
        "title": TITLE,
        "butler_name": butler,
        "writable": True,
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(hours=1),
        "instance_status": "confirmed",
        "event_type": "default",
        "recurrence_rule": None,
        "event_metadata": {
            "organizer": "boss@example.test",
            "organizer_verdict": "known_entity",
            "attendees": [{"self": True, "response_status": "needsAction"}],
        },
    }
    return row | over


def _focus(start=START):
    return {
        "instance_id": uuid4(),
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(hours=2),
        "instance_status": "confirmed",
        "event_type": "focusTime",
        "event_metadata": {},
    }


def _fake_lock():
    held = {"by": None}

    @contextlib.asynccontextmanager
    async def lock(pool):
        got = held["by"] is None
        if got:
            held["by"] = object()
        try:
            yield got
        finally:
            if got:
                held["by"] = None

    return lock


class _Env:
    def __init__(self, monkeypatch, *, policies, rows, owner_rows=None, open_cards=None):
        self.park = AsyncMock(
            return_value={"status": "pending_approval", "action_id": str(uuid4())}
        )
        client = SimpleNamespace(call_tool=AsyncMock())
        self.client = client
        self.mgr = SimpleNamespace(get_client=AsyncMock(return_value=client))
        self.reject = AsyncMock(return_value=True)
        self.read = SimpleNamespace(rows=rows, failed_butlers=[], status_available=True)
        monkeypatch.setattr(job, "docket_butler_configured", lambda: True)
        monkeypatch.setattr(job, "read_policies", AsyncMock(return_value=policies))
        monkeypatch.setattr(job, "_open_cards", AsyncMock(return_value=open_cards or []))
        monkeypatch.setattr(job, "query_calendar_invitations", AsyncMock(return_value=self.read))
        monkeypatch.setattr(
            job,
            "query_owner_calendar_rows",
            AsyncMock(return_value=(owner_rows if owner_rows is not None else [_focus()], True)),
        )
        monkeypatch.setattr(job, "get_scheduling_preferences", AsyncMock(return_value=None))
        monkeypatch.setattr(job, "read_organizer_roles", AsyncMock(return_value=frozenset()))
        monkeypatch.setattr(job, "_park", self.park)
        monkeypatch.setattr(job, "_reject", self.reject)
        self.db = MagicMock()
        monkeypatch.setattr(job, "_single_flight", _fake_lock())

    async def run(self):
        return await job.run_calendar_rsvp_docket(self.db, self.mgr, now=NOW)


# --------------------------------------------------------------------------- identity


def test_the_request_id_follows_every_input_of_the_prepared_answer() -> None:
    base = dict(
        event_id="ev",
        occurrence="2026-07-03T09:00:00+00:00",
        etag="e1",
        policy_id="p1",
        policy_version=1,
        verdict="declined",
        send_updates="none",
    )
    one = job.docket_request_id(**base)
    assert one == job.docket_request_id(**base)
    for field, value in (
        ("event_id", "ev2"),
        ("occurrence", "2026-07-04T09:00:00+00:00"),
        ("etag", "e2"),
        ("policy_id", "p2"),
        ("policy_version", 2),
        ("verdict", "accepted"),
        ("send_updates", "all"),
    ):
        assert job.docket_request_id(**(base | {field: value})) != one, field


# --------------------------------------------------------------------------- facts


def test_a_focus_block_and_an_accepted_event_are_told_apart_and_noise_is_ignored() -> None:
    row = _row()
    accepted = {
        "instance_id": uuid4(),
        "instance_starts_at": START,
        "instance_ends_at": START + timedelta(hours=1),
        "event_type": "default",
        "event_metadata": {"attendees": [{"self": True, "response_status": "accepted"}]},
    }
    free = accepted | {"instance_id": uuid4(), "event_metadata": {"transparency": "transparent"}}
    unanswered = accepted | {
        "instance_id": uuid4(),
        "event_metadata": {"attendees": [{"self": True, "response_status": "needsAction"}]},
    }
    declined = accepted | {
        "instance_id": uuid4(),
        "event_metadata": {"attendees": [{"self": True, "response_status": "declined"}]},
    }
    far = accepted | {
        "instance_id": uuid4(),
        "instance_starts_at": START + timedelta(days=1),
        "instance_ends_at": START + timedelta(days=1, hours=1),
    }
    assert job.overlap_facts(row, [free, unanswered, declined, far, row]) == (False, False)
    assert job.overlap_facts(row, [accepted]) == (False, True)
    assert job.overlap_facts(row, [_focus()]) == (True, False)


def test_the_facts_carry_lead_time_attendees_and_recurrence() -> None:
    facts = job.invitation_facts(
        _row(recurrence_rule="RRULE:FREQ=DAILY"),
        now=NOW,
        owner_rows=[],
        outside_hours=False,
        organizer_roles=frozenset({"manager"}),
    )
    assert (facts.lead_time_hours, facts.attendee_count, facts.recurring) == (48, 1, True)
    assert facts.organizer_verdict == "known_entity" and facts.organizer_roles == {"manager"}
    unread = job.invitation_facts(
        _row(), now=NOW, owner_rows=None, outside_hours=None, organizer_roles=frozenset()
    )
    assert unread.overlaps_focus_or_out_of_office is None and unread.outside_declared_hours is None


# --------------------------------------------------------------------------- the card


def test_the_dossier_cites_the_policy_version_and_facts_and_never_event_text() -> None:
    policy = _policy(version=4)
    from butlers.core.temporal.rsvp_policy import InvitationFacts, evaluate_policies

    match = evaluate_policies(
        [policy], InvitationFacts(overlaps_focus_or_out_of_office=True)
    ).prepared
    assert match is not None
    dossier = job.build_dossier(match, request_id="r1", entry_id="e", etag="et")
    assert "p1" in dossier["_why"] and "version 4" in dossier["_why"]
    assert "Nothing is sent until you approve" in dossier["_why"]
    marker = job.parse_marker(dossier["_evidence"])
    assert marker == {
        "request_id": "r1",
        "entry_id": "e",
        "policy_id": "p1",
        "policy_version": 4,
        "verdict": "declined",
        "send_updates": "none",
        "etag": "et",
    }
    assert any(
        i["ref"] == "rsvp-fact:overlaps_focus_or_out_of_office" for i in dossier["_evidence"]
    )
    assert TITLE not in json.dumps(dossier)
    assert job.parse_marker([{"type": "text", "ref": "x", "note": "y"}]) is None


def test_supersede_reasons_are_closed_and_ordered() -> None:
    from butlers.core.temporal.rsvp_policy import InvitationFacts, evaluate_policies

    facts = InvitationFacts(overlaps_focus_or_out_of_office=True)
    marker = {
        "etag": "e1",
        "policy_id": "p1",
        "policy_version": 1,
        "verdict": "declined",
        "send_updates": "none",
    }

    def reason(policies, *, etag="e1", revoked=False, evaluation="eval"):
        ev = evaluate_policies(policies, facts) if evaluation == "eval" else None
        return job.supersede_reason(marker, evaluation=ev, policy_revoked=revoked, etag=etag)

    assert reason([_policy()]) is None
    assert reason([_policy()], evaluation=None) == "stale_event"
    assert reason([_policy()], etag="e2") == "stale_event"
    assert reason([_policy(revoked=NOW)], revoked=True) == "policy_revoked"
    assert reason([_policy(predicates=[{"kind": "recurring", "value": True}])]) == (
        "policy_no_longer_matches"
    )
    assert reason([_policy(version=2)]) == "policy_changed"
    assert reason([_policy(verdict="tentative")]) == "policy_changed"
    assert set(job.REJECT_REASONS) == {
        "stale_event",
        "policy_changed",
        "policy_revoked",
        "policy_no_longer_matches",
    }


# --------------------------------------------------------------------------- the run


async def test_a_matched_invitation_parks_one_exact_card_and_a_rerun_parks_none(
    monkeypatch,
) -> None:
    row = _row()
    env = _Env(monkeypatch, policies=[_policy()], rows=[row])
    summary = await env.run()
    assert (summary["prepared"], summary["complete"]) == (1, True)
    args, dossier = env.park.await_args.args[1:]
    assert args["entry_id"] == str(row["instance_id"])
    assert (args["response_status"], args["send_updates"]) == ("declined", "none")
    marker = job.parse_marker(dossier["_evidence"])
    assert marker is not None and marker["request_id"] == args["request_id"]

    again = _Env(
        monkeypatch,
        policies=[_policy()],
        rows=[row],
        open_cards=[{"id": uuid4(), "marker": marker}],
    )
    rerun = await again.run()
    assert (rerun["prepared"], rerun["already_parked"]) == (0, 1)
    again.park.assert_not_awaited()
    again.reject.assert_not_awaited()


async def test_a_changed_etag_rejects_the_old_card_and_parks_a_new_one(monkeypatch) -> None:
    old = _row(etag="e1")
    first = _Env(monkeypatch, policies=[_policy()], rows=[old])
    await first.run()
    marker = job.parse_marker(first.park.await_args.args[2]["_evidence"])
    card_id = uuid4()
    moved = old | {"event_etag": "e2"}
    env = _Env(
        monkeypatch,
        policies=[_policy()],
        rows=[moved],
        open_cards=[{"id": card_id, "marker": marker}],
    )
    summary = await env.run()
    assert summary["prepared"] == 1
    env.reject.assert_awaited_once()
    assert env.reject.await_args.args[1:] == (card_id, "stale_event")
    assert summary["rejected"] == {"stale_event": 1}


@pytest.mark.parametrize(
    ("policies", "reason"),
    [
        ([_policy(version=2)], "policy_changed"),
        ([_policy(revoked=NOW)], "policy_revoked"),
        ([_policy(predicates=[{"kind": "recurring", "value": True}])], "policy_no_longer_matches"),
    ],
)
async def test_a_card_whose_policy_changed_is_rejected_with_a_closed_reason(
    monkeypatch, policies, reason
) -> None:
    row = _row()
    first = _Env(monkeypatch, policies=[_policy()], rows=[row])
    await first.run()
    marker = job.parse_marker(first.park.await_args.args[2]["_evidence"])
    card_id = uuid4()
    env = _Env(
        monkeypatch, policies=policies, rows=[row], open_cards=[{"id": card_id, "marker": marker}]
    )
    await env.run()
    assert env.reject.await_args.args[1:] == (card_id, reason)


async def test_a_card_for_an_event_that_is_no_longer_pending_is_rejected_stale(monkeypatch) -> None:
    row = _row()
    first = _Env(monkeypatch, policies=[_policy()], rows=[row])
    await first.run()
    marker = job.parse_marker(first.park.await_args.args[2]["_evidence"])
    card_id = uuid4()
    env = _Env(
        monkeypatch, policies=[_policy()], rows=[], open_cards=[{"id": card_id, "marker": marker}]
    )
    await env.run()
    assert env.reject.await_args.args[1:] == (card_id, "stale_event")


async def test_disagreeing_policies_prepare_nothing_and_are_named(monkeypatch) -> None:
    env = _Env(
        monkeypatch,
        policies=[
            _policy("a", verdict="declined", position=1),
            _policy("b", verdict="accepted", position=2),
        ],
        rows=[_row()],
    )
    summary = await env.run()
    env.park.assert_not_awaited()
    assert summary["disagreed"][0]["policies"] == ("a", "b")


async def test_another_butlers_entry_is_never_prepared(monkeypatch) -> None:
    env = _Env(monkeypatch, policies=[_policy()], rows=[_row(butler="general")])
    summary = await env.run()
    env.park.assert_not_awaited()
    assert summary["prepared"] == 0


async def test_an_invitation_outage_prepares_and_rejects_nothing(monkeypatch) -> None:
    row = _row()
    first = _Env(monkeypatch, policies=[_policy()], rows=[row])
    await first.run()
    marker = job.parse_marker(first.park.await_args.args[2]["_evidence"])
    env = _Env(
        monkeypatch,
        policies=[_policy()],
        rows=[row],
        open_cards=[{"id": uuid4(), "marker": marker}],
    )
    env.read.status_available = False
    summary = await env.run()
    assert summary["status"] == "invitations_unavailable" and summary["complete"] is False
    assert summary["unprepared"]["invitations_unavailable"] == 1
    env.park.assert_not_awaited()
    env.reject.assert_not_awaited()


async def test_unreadable_calendar_facts_never_reject_a_card_and_never_match(monkeypatch) -> None:
    row = _row()
    first = _Env(monkeypatch, policies=[_policy()], rows=[row])
    await first.run()
    marker = job.parse_marker(first.park.await_args.args[2]["_evidence"])
    env = _Env(
        monkeypatch,
        policies=[_policy()],
        rows=[row],
        open_cards=[{"id": uuid4(), "marker": marker}],
    )
    monkeypatch.setattr(job, "query_owner_calendar_rows", AsyncMock(return_value=([], False)))
    summary = await env.run()
    assert summary["complete"] is False and summary["degraded"] == ["calendar"]
    env.park.assert_not_awaited()  # an unknown fact never matches
    env.reject.assert_not_awaited()  # and the card is neither confirmed nor refuted


async def test_a_card_that_could_not_be_parked_is_counted_unprepared(monkeypatch) -> None:
    env = _Env(monkeypatch, policies=[_policy()], rows=[_row()])
    env.park.return_value = {"error": "response_preparation_unavailable"}
    summary = await env.run()
    assert summary["prepared"] == 0 and summary["complete"] is False
    assert summary["unprepared"] == {"response_preparation_unavailable": 1}


async def test_the_job_refuses_to_run_outside_the_docket_butler(monkeypatch) -> None:
    env = _Env(monkeypatch, policies=[_policy()], rows=[_row()])
    monkeypatch.setattr(job, "docket_butler_configured", lambda: False)
    summary = await env.run()
    assert summary["status"] == "docket_butler_not_configured"
    env.park.assert_not_awaited()


async def test_an_unreadable_policy_store_is_an_outage_not_an_empty_docket(monkeypatch) -> None:
    env = _Env(monkeypatch, policies=None, rows=[_row()])
    summary = await env.run()
    assert summary["status"] == "policies_unavailable" and summary["complete"] is False
    env.park.assert_not_awaited()
    env.reject.assert_not_awaited()


def test_the_job_is_dark_until_switched_on(monkeypatch) -> None:
    monkeypatch.delenv(job.DOCKET_LIVE_ENV, raising=False)
    assert job.docket_live() is False
    monkeypatch.setenv(job.DOCKET_LIVE_ENV, "1")
    assert job.docket_live() is True


def test_the_job_only_ever_calls_the_gated_tool_and_never_sends() -> None:
    source = open(job.__file__).read()
    assert "calendar_respond" in source
    assert "approve_action" not in source and "send_updates=" not in source.replace(
        "send_updates=match", ""
    )


async def test_a_second_concurrent_tick_parks_nothing(monkeypatch) -> None:
    env = _Env(monkeypatch, policies=[_policy()], rows=[_row()])
    gate = asyncio.Event()

    async def slow_park(*args):
        await gate.wait()
        return {"status": "pending_approval"}

    monkeypatch.setattr(job, "_park", slow_park)
    first = asyncio.create_task(env.run())
    await asyncio.sleep(0.05)
    second = await env.run()
    assert second["status"] == "another_tick_running" and second["prepared"] == 0
    gate.set()
    assert (await first)["prepared"] == 1


async def test_a_park_that_times_out_is_counted_unprepared(monkeypatch) -> None:
    async def hang(*args, **kw):
        await asyncio.sleep(10)

    monkeypatch.setattr(job, "PARK_TIMEOUT_S", 0.01)
    mgr = SimpleNamespace(get_client=AsyncMock(return_value=SimpleNamespace(call_tool=hang)))
    result = await job._park(mgr, {"entry_id": "e"}, {"_why": "w"})
    assert result == {"error": "response_transport_unavailable"}


# --------------------------------------------------------------------------- degraded ticks


async def _card(monkeypatch, row, policies=None):
    first = _Env(monkeypatch, policies=policies or [_policy()], rows=[row])
    await first.run()
    return job.parse_marker(first.park.await_args.args[2]["_evidence"])


async def test_an_unreadable_organizer_keeps_the_card_and_parks_nothing(monkeypatch) -> None:
    row = _row()
    marker = await _card(monkeypatch, row)
    card_id = uuid4()
    env = _Env(
        monkeypatch,
        policies=[_policy()],
        rows=[row],
        open_cards=[{"id": card_id, "marker": marker}],
    )
    monkeypatch.setattr(job, "read_organizer_roles", AsyncMock(return_value=None))
    summary = await env.run()
    env.reject.assert_not_awaited()
    env.park.assert_not_awaited()
    assert summary["unprepared"]["organizer_unreadable"] == 1
    assert summary["unprepared"]["card_kept_unreadable"] == 1 and summary["complete"] is False


async def test_a_partial_calendar_read_keeps_the_card_and_parks_the_lower_policy_never(
    monkeypatch,
) -> None:
    row = _row()
    higher = _policy("a", position=1)
    lower = _policy("b", position=2, predicates=[{"kind": "recurring", "value": True}])
    row = row | {"recurrence_rule": "RRULE:FREQ=DAILY"}
    marker = await _card(monkeypatch, row, [higher, lower])
    card_id = uuid4()
    env = _Env(
        monkeypatch,
        policies=[higher, lower],
        rows=[row],
        open_cards=[{"id": card_id, "marker": marker}],
    )
    monkeypatch.setattr(job, "query_owner_calendar_rows", AsyncMock(return_value=([], False)))
    summary = await env.run()
    env.reject.assert_not_awaited()  # not policy_no_longer_matches, not policy_changed
    env.park.assert_not_awaited()  # b would win on a partial read; it must not be prepared
    assert summary["unprepared"]["card_kept_unreadable"] == 1


async def test_an_invitation_beyond_the_page_limit_keeps_its_card(monkeypatch) -> None:
    row = _row()
    marker = await _card(monkeypatch, row)
    env = _Env(
        monkeypatch,
        policies=[_policy()],
        rows=[_row(butler="general")],
        open_cards=[{"id": uuid4(), "marker": marker}],
    )
    monkeypatch.setattr(job, "INVITATION_LIMIT", 1)
    summary = await env.run()
    env.reject.assert_not_awaited()
    env.park.assert_not_awaited()
    assert "invitations_truncated" in summary["degraded"]


async def test_a_degraded_tick_still_rejects_for_fact_independent_reasons(monkeypatch) -> None:
    row = _row(etag="e1")
    marker = await _card(monkeypatch, row)
    for policies, listed, reason in (
        ([_policy()], row | {"event_etag": "e2"}, "stale_event"),
        ([_policy(revoked=NOW)], row, "policy_revoked"),
        ([_policy(version=2)], row, "policy_changed"),
    ):
        card_id = uuid4()
        env = _Env(
            monkeypatch,
            policies=policies,
            rows=[listed],
            open_cards=[{"id": card_id, "marker": marker}],
        )
        monkeypatch.setattr(job, "query_owner_calendar_rows", AsyncMock(return_value=([], False)))
        await env.run()
        assert env.reject.await_args.args[1:] == (card_id, reason), reason
        env.park.assert_not_awaited()


async def test_a_vanished_entry_is_kept_while_the_calendar_cannot_be_read(monkeypatch) -> None:
    marker = await _card(monkeypatch, _row())
    env = _Env(
        monkeypatch, policies=[_policy()], rows=[], open_cards=[{"id": uuid4(), "marker": marker}]
    )
    monkeypatch.setattr(job, "query_owner_calendar_rows", AsyncMock(return_value=([], False)))
    summary = await env.run()
    env.reject.assert_not_awaited()
    assert summary["unprepared"]["card_kept_unreadable"] == 1


async def test_an_exception_in_the_run_is_run_failed_not_lock_unavailable(monkeypatch) -> None:
    env = _Env(monkeypatch, policies=[_policy()], rows=[_row()])
    monkeypatch.setattr(job, "_run", AsyncMock(side_effect=RuntimeError("boom")))
    assert (await env.run())["status"] == "run_failed"

    @contextlib.asynccontextmanager
    async def broken(pool):
        raise RuntimeError("no lock")
        yield

    monkeypatch.setattr(job, "_single_flight", broken)
    assert (await env.run())["status"] == "lock_unavailable"


@pytest.mark.parametrize("status", sorted(job._DECIDED_REPLAYS))
async def test_a_replay_of_a_settled_card_is_owner_decided_not_already_parked(
    monkeypatch, status
) -> None:
    env = _Env(monkeypatch, policies=[_policy()], rows=[_row()])
    env.park.return_value = {"status": status}
    summary = await env.run()
    assert (summary["owner_decided"], summary["already_parked"], summary["prepared"]) == (1, 0, 0)


def test_the_replay_set_is_exactly_what_calendar_respond_can_return() -> None:
    """Pinned to the literals in calendar_response.py, so a new status cannot slip past."""
    import re
    from pathlib import Path

    source = (Path(job.__file__).parents[1] / "modules/calendar_response.py").read_text()
    returned = set(re.findall(r'"status": "([a-z_]+)"', source)) | set(
        re.findall(r'^\s+status = "(failed)" if known', source, re.M)
    )
    assert "pending_approval" in returned
    assert job._DECIDED_REPLAYS == returned - {"pending_approval"}
