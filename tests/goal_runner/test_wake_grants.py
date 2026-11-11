"""A wake lead is narrowed by what woke it; a failed lead's replacement keeps that narrowing.

RFC 0038 S2.2 (bu-dhloi1.305 PR3). A wake lead's grant is the goal ceiling ∩ the fired envelope's
grant snapshot ∩ current policy (the live published catalog). An own wait (an owner answer or an
approval) snapshots the waiting lead's grant ∩ the ceiling, so its wake is not narrowed further.
Any other successor (relaunch, recovery, escalation) takes the ceiling ∩ the latest lead's grant:
a failure never widens a narrowed lead back to the ceiling.
"""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from butlers.core.goal_admission import Grant, decode_grants
from butlers.goal_runner.contracts import HostOutcome
from tests.capability_broker.test_broker_ledger import CATALOG, StaticCatalog
from tests.goal_runner.test_goal_approval_wait import parked_and_waiting
from tests.goal_runner.test_goal_plan import wake_by_reply
from tests.goal_runner.test_goal_runner import (
    NOTIFY,
    SEARCH,
    SEND,
    TO_OWNER,
    _grant,
    _retryable,
    harness,
)
from tests.goal_runner.test_goal_wake_runner import fire_for, owner_prompt, waiting_on_conditions

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


def _decoded(value) -> frozenset[Grant]:
    return frozenset(decode_grants(json.loads(value) if isinstance(value, str) else value))


async def held(h, invocation_id: UUID) -> frozenset[Grant]:
    """The grant the ledger recorded for an invocation."""
    return _decoded((await h.row(invocation_id))["grant_snapshot"])


async def snapshot_of(h, goal: UUID) -> frozenset[Grant]:
    return _decoded(
        await h.w.owner.fetchval(
            "SELECT grant_snapshot FROM switchboard.wait_envelopes WHERE goal_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            goal,
        )
    )


async def woken_by_conditions(h, goal: UUID, *grants: Grant):
    """The goal's first lead waits on a conditions envelope snapshotting ``grants``; it fires."""
    lead = await h.lead(goal)
    await waiting_on_conditions(h, goal, lead, grants=grants)
    admitted = await h.runner.admit_wake(fire_for())
    assert admitted.state == "admitted"
    return lead


async def test_a_condition_fire_wake_holds_only_the_envelope_snapshot(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND))
        lead = await woken_by_conditions(h, goal, _grant(SEARCH))
        assert await held(h, lead.invocation_id) == {_grant(SEARCH), _grant(SEND)}

        await h.runner.supervise_once()
        woken = await h.launched(2)

        assert await held(h, woken.invocation_id) == {_grant(SEARCH)}


async def test_a_wake_never_holds_more_than_the_ceiling(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND, park_only=True))
        # A snapshot naming a capability outside the ceiling, and SEND without park-only.
        await woken_by_conditions(h, goal, _grant(SEARCH), _grant(SEND), _grant(NOTIFY))

        await h.runner.supervise_once()
        woken = await h.launched(2)

        assert await held(h, woken.invocation_id) == {
            _grant(SEARCH),
            _grant(SEND, park_only=True),
        }


async def test_a_policy_change_while_the_goal_waited_narrows_the_wake(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND))
        await woken_by_conditions(h, goal, _grant(SEARCH), _grant(SEND))
        # SEND stops being published while the goal waits; the catalog identity is unchanged.
        h.w.broker.catalog = StaticCatalog(
            replace(CATALOG, capabilities=tuple(c for c in CATALOG.capabilities if c != SEND))
        )

        await h.runner.supervise_once()
        woken = await h.launched(2)

        assert await held(h, woken.invocation_id) == {_grant(SEARCH)}


async def test_an_owner_reply_wake_is_not_narrowed_further(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND), _grant(NOTIFY))
        lead = await h.lead(goal)
        prompt = await owner_prompt(h, goal, lead)
        await h.call(goal, lead.token, "goal__wait_owner", {"effect_id": str(prompt)})
        lead_grant = await held(h, lead.invocation_id)
        # The own wait records what the waiting lead held, not an empty snapshot.
        assert await snapshot_of(h, goal) == lead_grant == {_grant(SEND), _grant(NOTIFY)}

        await wake_by_reply(h, goal, prompt)
        await h.runner.supervise_once()
        woken = await h.launched(2)

        assert await held(h, woken.invocation_id) == lead_grant


async def test_an_approval_wake_is_not_narrowed_further(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, parked = await parked_and_waiting(h)
        lead_grant = await held(h, lead.invocation_id)
        assert await snapshot_of(h, goal) == lead_grant == {_grant(NOTIFY), _grant(SEARCH)}
        for status in ("approved", "dispatched", "committed"):
            await h.w.owner.execute(
                "UPDATE switchboard.effects SET status = $2 WHERE id = $1", parked.effect_id, status
            )

        await h.runner.supervise_once()
        woken = await h.launched(2)

        assert await held(h, woken.invocation_id) == lead_grant


async def test_a_settle_wait_snapshots_no_grant(broker_db_url):
    async with harness(broker_db_url) as h:
        # A lead completes relying on a still-parked effect: the goal settles. Nothing is ever
        # launched from a settle wait, so its envelope holds no grant.
        goal = await h.goal(_grant(NOTIFY), _grant(SEND))
        lead = await h.lead(goal)
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
        assert parked.status == "parked"
        reply = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        completion = {
            "outcome": "completed",
            "reply_effect_id": str(reply.effect_id),
            "relied_effects": [str(parked.effect_id)],
        }
        assert await h.call(goal, lead.token, "goal__complete", completion) == {"status": "waiting"}
        kind = await h.w.owner.fetchval(
            "SELECT kind FROM switchboard.wait_envelopes WHERE goal_id = $1", goal
        )
        assert kind == "settle"
        assert await snapshot_of(h, goal) == frozenset()


async def test_a_failed_narrowed_wake_lead_is_relaunched_still_narrowed(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND))
        await woken_by_conditions(h, goal, _grant(SEARCH))
        # The wake lead fails with no capability call: its one D14 relaunch follows.
        h.host.script = [HostOutcome(failure=_retryable())]

        await h.runner.supervise_once()
        woken = await h.launched(2)
        relaunch = await h.launched(3)

        row = await h.row(relaunch.invocation_id)
        assert (row["relaunch_of"], row["lead_epoch"]) == (woken.invocation_id, 2)
        assert await held(h, woken.invocation_id) == {_grant(SEARCH)}
        assert await held(h, relaunch.invocation_id) == {_grant(SEARCH)}
