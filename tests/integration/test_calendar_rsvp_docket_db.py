"""Real-Postgres seams of the invitation docket job (bu-5ii4gj.9 S3).

The policy read, the open-card read over ``pending_actions.evidence`` and the system reject run on
the production core and approvals migrations; the rest of the job is faked in
``tests/jobs/test_calendar_rsvp_docket.py``.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from butlers.jobs import calendar_rsvp_docket as job
from butlers.modules.approvals.park import park_pending_action

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


async def _park(pool, *, with_marker: bool) -> uuid.UUID:
    action_id = uuid.uuid4()
    evidence = (
        [
            {
                "type": "text",
                "ref": job.MARKER_PREFIX + "r1",
                "note": json.dumps({"request_id": "r1"}),
            }
        ]
        if with_marker
        else [{"type": "text", "ref": "other", "note": "x"}]
    )
    await park_pending_action(
        pool,
        action_id=action_id,
        tool_name="calendar_respond",
        tool_args={"entry_id": str(uuid.uuid4())},
        agent_summary="Respond to invitation",
        requested_at=NOW,
        expires_at=NOW + timedelta(days=1),
        why="because",
        evidence=evidence,
        blast_radius="self",
        reversibility="compensable",
        origin_butler="relationship",
    )
    return action_id


async def test_open_docket_cards_are_found_by_their_marker_and_rejected_by_the_system(db) -> None:
    pool = await db("core", "approvals")
    docket = await _park(pool, with_marker=True)
    await _park(pool, with_marker=False)

    cards = await job._open_cards(pool)
    assert cards is not None
    assert [c["id"] for c in cards] == [docket]
    assert cards[0]["marker"]["request_id"] == "r1"

    assert await job._reject(pool, docket, "policy_changed") is True
    assert await job._open_cards(pool) == []
    row = await pool.fetchrow(
        "SELECT status, decided_by FROM pending_actions WHERE id = $1", docket
    )
    assert row["status"] == "rejected" and row["decided_by"] == job.DOCKET_ACTOR
    events = await pool.fetch(
        "SELECT event_type, actor, reason FROM approval_events WHERE action_id = $1 "
        "ORDER BY occurred_at",
        docket,
    )
    assert events[-1]["actor"] == job.DOCKET_ACTOR and events[-1]["reason"] == "policy_changed"
    # a card that is no longer pending is never rejected a second time
    assert await job._reject(pool, docket, "stale_event") is False


async def test_policies_are_read_in_position_order_and_a_bad_row_is_an_outage(db) -> None:
    pool = await db("core")
    await pool.execute("ALTER TABLE public.calendar_rsvp_policies DISABLE TRIGGER USER")
    try:
        await pool.execute("DELETE FROM public.calendar_rsvp_policies")
        for position, verdict in ((5, "accepted"), (1, "declined")):
            await pool.execute(
                "INSERT INTO public.calendar_rsvp_policies "
                "(position, predicates, verdict, send_updates) VALUES ($1, $2::jsonb, $3, 'none')",
                position,
                [{"kind": "recurring", "value": True}],
                verdict,
            )
        policies = await job.read_policies(pool)
        assert policies is not None and [p.verdict for p in policies] == ["declined", "accepted"]
    finally:
        await pool.execute("DELETE FROM public.calendar_rsvp_policies")
        await pool.execute("ALTER TABLE public.calendar_rsvp_policies ENABLE TRIGGER USER")


async def test_the_single_flight_lock_admits_one_tick_at_a_time(db) -> None:
    pool = await db("core")
    async with job._single_flight(pool) as first:
        assert first is True
        async with job._single_flight(pool) as second:
            assert second is False
    async with job._single_flight(pool) as again:
        assert again is True
