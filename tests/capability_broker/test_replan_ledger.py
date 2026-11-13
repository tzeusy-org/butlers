"""The calendar replan exception on a migrated database (RFC 0038 D13, bu-qlx055 q12 = B).

A live, unexposed pure move of one calendar event is first dispatched on the
domain's condition. Accepted, it commits with no park; declined, it parks
exactly as before; ambiguous, it is unknown and never parked. Dark, nothing
changes.
"""

from __future__ import annotations

import shutil
from typing import Any, cast

import pytest

from butlers.capability_broker import policy
from butlers.capability_broker.contracts import Catalog, Reason
from butlers.capability_broker.domains import DomainReceipt
from butlers.core.tool_catalog import RecipientParameters
from tests.capability_broker.helpers import capability
from tests.capability_broker.test_broker_ledger import (
    CATALOG,
    DOMAIN,
    StaticCatalog,
    _grant,
    _law,
    _refused,
    world,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

MOVE_EVENT = capability(
    "calendar_update_event",
    "communicate",
    DOMAIN,
    output_trust="untrusted",
    resource_params=("event_id",),
    recipient_params=RecipientParameters(
        argument_names=("attendees", "event_id"),
        undeterminable=True,
        undeterminable_condition="always for inherited/implicit party set",
    ),
    categories=frozenset(),
)
REPLAN_CATALOG = StaticCatalog(Catalog("catalog-v1", (*CATALOG.capabilities, MOVE_EVENT)))
MOVE = {"event_id": "evt-1", "start_at": "2026-02-20T16:00:00+00:00"}
MOVED = {"status": "updated", "undo": {"tool": "calendar_update_event", "arguments": {}}}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(policy, "REPLAN_MOVES_LIVE", True)


async def _goal(w):
    goal = await w.goal(_grant(MOVE_EVENT))
    _, token = await w.invocation(goal, _grant(MOVE_EVENT))
    return goal, token


async def test_an_accepted_move_commits_with_no_park(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)
        w.forwarder.receipt = DomainReceipt("committed", "r-1", MOVED)
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, MOVE)
        assert result.status == "committed" and result.approval_ref is None
        [(_, args, assertion, _)] = w.forwarder.calls
        assert args == MOVE
        assert w.verify(assertion, MOVE_EVENT.name, MOVE)["conditional"] == "replan_move"
        assert (await w.effect(result.effect_id))["status"] == "committed"
        assert await w.action(result.effect_id) is None


async def test_a_declined_move_parks_exactly_as_before(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)
        w.forwarder.receipt = DomainReceipt("declined")
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, MOVE)
        assert result.status == "parked"
        effect = await w.effect(result.effect_id)
        assert (effect["status"], effect["approval_ref"]) == ("parked", result.approval_ref)
        action = await w.action(result.effect_id)
        assert action["status"] == "pending" and action["tool_args"] == MOVE
        # The owner's approval runs the stored call unconditionally.
        w.forwarder.receipt = DomainReceipt("committed", "r-2", MOVED)
        assert result.effect_id is not None and result.approval_ref is not None
        done = await w.broker.execute_approved(goal, result.effect_id, result.approval_ref, MOVE)
        assert done.status == "committed"
        approved = w.verify(w.forwarder.calls[-1][2], MOVE_EVENT.name, MOVE)
        assert "conditional" not in approved and approved["approval_ref"] == result.approval_ref


async def test_dark_a_move_parks_and_nothing_is_dispatched(broker_db_url):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, MOVE)
        assert result.status == "parked" and w.forwarder.calls == []


async def test_anything_but_a_pure_move_parks_and_nothing_is_dispatched(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)
        renamed = {**MOVE, "title": "Renamed"}
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, renamed)
        assert result.status == "parked" and w.forwarder.calls == []


async def test_owner_always_ask_parks_a_move_without_offering_it(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)
        await _law(w, "capability", MOVE_EVENT.identity, "always_ask")
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, MOVE)
        assert result.status == "parked" and w.forwarder.calls == []


async def test_a_park_only_grant_parks_a_move_without_offering_it(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        grant = _grant(MOVE_EVENT, park_only=True)
        goal = await w.goal(grant)
        _, token = await w.invocation(goal, grant)
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, MOVE)
        assert result.status == "parked" and w.forwarder.calls == []


async def test_an_ambiguous_move_is_unknown_and_never_parked(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)
        w.forwarder.receipt = cast(Any, RuntimeError("stream broke after the call was sent"))
        result = await w.broker.call(goal, token, MOVE_EVENT.identity, MOVE)
        assert result.status == "unknown"
        assert (await w.effect(result.effect_id))["status"] == "unknown"
        assert await w.action(result.effect_id) is None


async def test_a_decline_after_stop_cancels_the_effect_and_never_parks(broker_db_url, live):
    async with world(broker_db_url, catalog=REPLAN_CATALOG) as w:
        goal, token = await _goal(w)

        class StopThenDecline:
            async def dispatch(self, reservation, args, assertion, *, idempotency_key):
                await w.owner.execute(
                    "UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal
                )
                return DomainReceipt("declined")

        w.forwarder.via = cast(Any, StopThenDecline())
        refused = await _refused(w.broker.call(goal, token, MOVE_EVENT.identity, MOVE))
        assert refused.reason is Reason.GOAL
        [effect] = await w.owner.fetch(
            "SELECT status, approval_ref FROM switchboard.effects WHERE goal_id = $1", goal
        )
        assert (effect["status"], effect["approval_ref"]) == ("cancelled", None)
        assert (
            await w.domain.fetchval("SELECT count(*) FROM pending_actions WHERE goal_id = $1", goal)
            == 0
        )
