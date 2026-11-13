"""The shared owner approve-and-dispatch path and the decision-set resolver (bu-4c7nvp.1.3, PR-B).

Real PostgreSQL, through the broker with an in-process approvals domain. Every owner decision of
parked goal effects, one item or a whole set, commits a receipt with the effects' ledger
transition first, then mirrors the decision to the owning domain, then dispatches the stored call
through ``Broker.execute_approved``. A crash anywhere after the receipt is re-driven once and never
re-dispatched. A set decision is exact: a stale version, plan or epoch, a changed item, a Stop or
an elapsed set refuses the whole decision; an unticked item stays parked (Q3 = B); a rule or
schedule change is decided only when ticked on its own (Q4).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.owner_decisions import (
    DecisionSetResolver,
    OwnerContext,
    OwnerDecisionRefused,
    OwnerDecisions,
)
from butlers.core.decision_sets import DecisionSetLedger
from butlers.core.goal_waiting import WaitLedger
from tests.capability_broker.test_broker_ledger import (
    NOTIFY,
    RULES,
    _grant,
    docker_available,
    world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

OWNER_CTX = OwnerContext(decided_by="owner:test", via="test")
CLASSES = {NOTIFY.identity: False, RULES.identity: True}


def _notify(text: str) -> dict:
    return {"entity_id": str(uuid4()), "message": text, "channel": "telegram"}


async def _parked(w, *, notes=2, rules=0):
    """A running goal with ``notes`` parked notifies and ``rules`` parked rule changes."""
    grants = (_grant(NOTIFY), _grant(RULES))
    goal = await w.goal(*grants, max_calls=20)
    lead, token = await w.invocation(goal, *grants)
    effects = []
    for index in range(notes):
        result = await w.broker.call(goal, token, NOTIFY.identity, _notify(f"note {index}"))
        assert result.status == "parked"
        effects.append(result.effect_id)
    for index in range(rules):
        result = await w.broker.call(goal, token, RULES.identity, {"rule": f"r{index}"})
        assert result.status == "parked"
        effects.append(result.effect_id)
    return goal, lead, effects


async def _propose(w, goal, lead, effects):
    async with w.owner.acquire() as connection, connection.transaction():
        return await DecisionSetLedger(connection).create(goal, lead, effects, CLASSES)


async def _refusal(awaitable) -> str:
    with pytest.raises(OwnerDecisionRefused) as refused:
        await awaitable
    return refused.value.reason


async def _state(w, proposed):
    return await w.owner.fetchval(
        "SELECT state FROM switchboard.decision_set_versions WHERE set_id = $1 AND version = $2",
        proposed.set_id,
        proposed.version,
    )


async def _receipt_items(w, effect_id: UUID):
    return await w.owner.fetch(
        "SELECT d.decided_by, d.via, d.set_id, i.outcome FROM switchboard.owner_decision_items i "
        "JOIN switchboard.owner_decisions d ON d.id = i.decision_id WHERE i.effect_id = $1",
        effect_id,
    )


def _picks(proposed, *, explicit=False, skip=()):
    return {
        item.effect_id: item.item_digest
        for item in proposed.items
        if item.explicit_only == explicit and item.effect_id not in skip
    }


# -- one item: the shared path --------------------------------------------------------------


async def test_an_owner_approval_runs_the_stored_call_once_through_the_shared_path(
    broker_db_url,
):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        stored = (await w.action(effect))["tool_args"]
        outcome = await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        assert (outcome.outcome, outcome.status) == ("approved", "committed")
        assert len(w.forwarder.calls) == 1 and w.forwarder.calls[0][1] == stored
        assert (await w.effect(effect))["status"] == "committed"
        action = await w.action(effect)
        assert (action["status"], action["decided_by"]) == ("executed", "owner:test")
        [receipt] = await _receipt_items(w, effect)
        assert tuple(receipt) == ("owner:test", "test", None, "approved")

        # A repeated approval decides nothing and never re-dispatches.
        again = await _refusal(OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX))
        assert again == "stale" and len(w.forwarder.calls) == 1


async def test_an_owner_rejection_ends_the_effect_and_its_domain_action(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        outcome = await OwnerDecisions(w.broker, live=True).reject(goal, effect, OWNER_CTX)
        assert (outcome.outcome, outcome.status) == ("rejected", "rejected")
        assert (await w.effect(effect))["status"] == "rejected"
        assert (await w.action(effect))["status"] == "rejected"
        late = await _refusal(OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX))
        assert late == "stale" and w.forwarder.calls == []


async def test_an_elapsed_domain_action_voids_the_approval_without_dispatch(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        await w.domain.execute(
            "UPDATE pending_actions SET expires_at = now() - interval '1 minute' WHERE id = $1",
            effect,
        )
        outcome = await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        assert (outcome.outcome, outcome.status) == ("approved", "expired")
        assert (await w.effect(effect))["status"] == "expired"
        assert (await w.action(effect))["status"] == "expired"
        assert w.forwarder.calls == []


async def test_unknown_or_foreign_effects_and_ended_goals_are_refused(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        other, _, _ = await _parked(w)
        decisions = OwnerDecisions(w.broker, live=True)
        assert await _refusal(decisions.approve(goal, uuid4(), OWNER_CTX)) == "resource"
        assert await _refusal(decisions.approve(other, effect, OWNER_CTX)) == "resource"
        with pytest.raises(ValueError):
            OwnerContext(decided_by="", via="test")
        await w.owner.execute("UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal)
        assert await _refusal(decisions.approve(goal, effect, OWNER_CTX)) == "goal"
        assert w.forwarder.calls == []


async def test_a_crash_before_the_domain_mirror_is_redriven_once(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        domains = w.broker.domains
        real = domains.decide

        async def crash(*args, **kwargs):
            raise ConnectionError("domain unreachable")

        domains.decide = crash
        with pytest.raises(ConnectionError):
            await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        # The receipt and the approval committed; nothing reached the domain or dispatched.
        assert (await w.effect(effect))["status"] == "approved"
        assert (await w.action(effect))["status"] == "pending"
        assert w.forwarder.calls == []

        domains.decide = real
        assert await OwnerDecisions(w.broker, live=True).redrive() == 1
        assert (await w.effect(effect))["status"] == "committed"
        assert (await w.action(effect))["status"] == "executed"
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0
        assert len(w.forwarder.calls) == 1


async def test_a_crash_between_the_domain_mirror_and_dispatch_is_redriven_once(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        real = w.broker.execute_approved

        async def crash(*args, **kwargs):
            raise ConnectionError("broker restarted")

        w.broker.execute_approved = crash
        with pytest.raises(ConnectionError):
            await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        assert (await w.effect(effect))["status"] == "approved"
        assert (await w.action(effect))["status"] == "approved"

        w.broker.execute_approved = real
        assert await OwnerDecisions(w.broker, live=True).redrive() == 1
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0
        assert len(w.forwarder.calls) == 1
        assert (await w.effect(effect))["status"] == "committed"


@pytest.mark.parametrize("ended", ["expired", "cancelled", "rejected"])
async def test_a_domain_action_that_already_ended_never_dispatches(broker_db_url, ended):
    """approved -> expired | cancelled | rejected: the domain ended its action first."""
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        await w.domain.execute(
            "UPDATE pending_actions SET status = $2 WHERE id = $1", effect, ended
        )
        outcome = await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        assert (outcome.outcome, outcome.status) == ("approved", ended)
        assert (await w.effect(effect))["status"] == ended
        assert (await w.action(effect))["status"] == ended
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0
        assert w.forwarder.calls == []


@pytest.mark.parametrize("crash_at", ["mirror", "dispatch"])
async def test_two_concurrent_sweeps_dispatch_an_interrupted_approval_once(broker_db_url, crash_at):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        domains, broker = w.broker.domains, w.broker
        target, name = (domains, "decide") if crash_at == "mirror" else (broker, "execute_approved")
        real = getattr(target, name)

        async def crash(*args, **kwargs):
            raise ConnectionError("process died")

        setattr(target, name, crash)
        with pytest.raises(ConnectionError):
            await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        setattr(target, name, real)
        assert w.forwarder.calls == []

        await asyncio.gather(
            OwnerDecisions(w.broker, live=True).redrive(),
            OwnerDecisions(w.broker, live=True).redrive(),
        )
        assert len(w.forwarder.calls) == 1
        assert (await w.effect(effect))["status"] == "committed"
        assert (await w.action(effect))["status"] == "executed"
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0
        assert len(w.forwarder.calls) == 1


async def test_the_path_is_dark_by_default(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w)
        dark = OwnerDecisions(w.broker)
        assert await _refusal(dark.approve(goal, effects[0], OWNER_CTX)) == "unavailable"
        assert await _refusal(dark.reject(goal, effects[0], OWNER_CTX)) == "unavailable"
        proposed = await _propose(w, goal, lead, effects)
        refused = await _refusal(
            DecisionSetResolver(dark).decide(
                proposed.set_id, proposed.version, _picks(proposed), {}, "approve", OWNER_CTX
            )
        )
        assert refused == "unavailable"
        assert await dark.redrive() == 0
        assert await w.owner.fetchval("SELECT count(*) FROM switchboard.owner_decisions") == 0
        assert [(await w.effect(e))["status"] for e in effects] == ["parked", "parked"]
        assert w.forwarder.calls == []


async def test_a_dispatched_effect_is_never_redriven(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        w.forwarder.receipt = TimeoutError()
        outcome = await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        assert outcome.status == "unknown"
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0
        assert len(w.forwarder.calls) == 1


async def test_a_crashed_rejection_mirror_is_redriven(broker_db_url):
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        domains = w.broker.domains
        real = domains.decide

        async def crash(*args, **kwargs):
            raise ConnectionError("domain unreachable")

        domains.decide = crash
        with pytest.raises(ConnectionError):
            await OwnerDecisions(w.broker, live=True).reject(goal, effect, OWNER_CTX)
        assert (await w.effect(effect))["status"] == "rejected"
        assert (await w.action(effect))["status"] == "pending"
        domains.decide = real
        assert await OwnerDecisions(w.broker, live=True).redrive() == 1
        assert (await w.action(effect))["status"] == "rejected"
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0


# -- decision sets: the resolver ------------------------------------------------------------


async def test_approve_all_runs_every_ticked_item_and_decides_the_version(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=2, rules=1)
        proposed = await _propose(w, goal, lead, effects)
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))
        decided = await resolver.decide(
            proposed.set_id,
            proposed.version,
            _picks(proposed),
            _picks(proposed, explicit=True),
            "approve",
            OWNER_CTX,
        )
        assert [o.status for o in decided.outcomes] == ["committed"] * 3
        assert len(w.forwarder.calls) == 3
        assert await _state(w, proposed) == "decided"
        for effect in effects:
            [receipt] = await _receipt_items(w, effect)
            assert (receipt["set_id"], receipt["outcome"]) == (proposed.set_id, "approved")
        stale = await _refusal(
            resolver.decide(
                proposed.set_id, proposed.version, _picks(proposed), {}, "approve", OWNER_CTX
            )
        )
        assert stale == "stale" and len(w.forwarder.calls) == 3


async def test_a_rule_change_is_never_decided_inside_an_all_selection(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=2, rules=1)
        proposed = await _propose(w, goal, lead, effects)
        everything = {item.effect_id: item.item_digest for item in proposed.items}
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))
        refused = await _refusal(
            resolver.decide(proposed.set_id, proposed.version, everything, {}, "approve", OWNER_CTX)
        )
        assert refused == "policy"
        # Nor may a plain item be smuggled through the explicit field.
        refused = await _refusal(
            resolver.decide(
                proposed.set_id, proposed.version, {}, _picks(proposed), "approve", OWNER_CTX
            )
        )
        assert refused == "policy"
        assert await _state(w, proposed) == "open"
        assert [(await w.effect(e))["status"] for e in effects] == ["parked"] * 3
        assert w.forwarder.calls == []


async def test_an_unticked_item_stays_parked_for_its_own_later_decision(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=3)
        proposed = await _propose(w, goal, lead, effects)
        left = effects[2]
        decisions = OwnerDecisions(w.broker, live=True)
        decided = await DecisionSetResolver(decisions).decide(
            proposed.set_id,
            proposed.version,
            _picks(proposed, skip=(left,)),
            {},
            "approve",
            OWNER_CTX,
        )
        assert [(o.outcome, o.status) for o in decided.outcomes] == [
            ("approved", "committed"),
            ("approved", "committed"),
            ("left", "parked"),
        ]
        assert (await w.effect(left))["status"] == "parked"
        assert (await w.action(left))["status"] == "pending"
        # Its own later decision is still the owner's.
        later = await decisions.approve(goal, left, OWNER_CTX)
        assert later.status == "committed" and len(w.forwarder.calls) == 3


async def test_a_set_rejection_rejects_only_the_ticked_items(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=3)
        proposed = await _propose(w, goal, lead, effects)
        decided = await DecisionSetResolver(OwnerDecisions(w.broker, live=True)).decide(
            proposed.set_id,
            proposed.version,
            _picks(proposed, skip=(effects[2],)),
            {},
            "reject",
            OWNER_CTX,
        )
        assert [o.status for o in decided.outcomes] == ["rejected", "rejected", "parked"]
        assert [(await w.action(e))["status"] for e in effects] == [
            "rejected",
            "rejected",
            "pending",
        ]
        assert w.forwarder.calls == []


async def test_a_changed_or_unknown_item_or_empty_selection_refuses_the_whole_decision(
    broker_db_url,
):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=2)
        proposed = await _propose(w, goal, lead, effects)
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))

        async def decide(selected):
            return await _refusal(
                resolver.decide(
                    proposed.set_id, proposed.version, selected, {}, "approve", OWNER_CTX
                )
            )

        changed = {**_picks(proposed), effects[0]: "0" * 64}
        assert await decide(changed) == "stale"
        assert await decide({**_picks(proposed), uuid4(): "0" * 64}) == "policy"
        assert await decide({}) == "policy"
        # One item decided on its own since the set was shown: the set is stale as a whole.
        await OwnerDecisions(w.broker, live=True).reject(goal, effects[0], OWNER_CTX)
        assert await decide(_picks(proposed)) == "stale"
        assert (await w.effect(effects[1]))["status"] == "parked"
        assert w.forwarder.calls == []


async def test_a_superseded_version_or_moved_plan_or_epoch_is_stale(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=3)
        first = await _propose(w, goal, lead, effects[:2])
        second = await _propose(w, goal, lead, effects)
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))

        async def decide(proposed):
            return await _refusal(
                resolver.decide(
                    proposed.set_id, proposed.version, _picks(proposed), {}, "approve", OWNER_CTX
                )
            )

        assert await decide(first) == "stale"
        async with w.owner.acquire() as connection, connection.transaction():
            # A changed premise is the goal's next plan version.
            await WaitLedger(connection).append_plan(goal, 0, ["m2"], [], [])
        assert await decide(second) == "stale"

        third = await _propose(w, goal, lead, effects[1:])
        # A successor lead (a recovery) moves the goal to epoch 1.
        await w.owner.execute(
            "UPDATE switchboard.invocations SET status = 'failed' WHERE id = $1", lead
        )
        await w.invocation(goal, _grant(NOTIFY), _grant(RULES), epoch=1)
        assert await decide(third) == "stale"
        assert w.forwarder.calls == []


async def test_stop_cancels_the_open_set_and_an_elapsed_set_expires(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=2)
        proposed = await _propose(w, goal, lead, effects)
        await w.owner.execute("UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal)
        # The Stop's own transaction cancelled the open set (sw_073 trigger).
        assert await _state(w, proposed) == "cancelled"
        await w.broker.cancel_parked(goal)
        # Any terminal status does the same, whichever path ends the goal.
        ended_goal, ended_lead, ended_effects = await _parked(w, notes=2)
        ended = await _propose(w, ended_goal, ended_lead, ended_effects)
        await w.owner.execute(
            "UPDATE switchboard.goals SET status = 'budget_exhausted' WHERE id = $1", ended_goal
        )
        assert await _state(w, ended) == "cancelled"
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))
        refused = await _refusal(
            resolver.decide(
                proposed.set_id, proposed.version, _picks(proposed), {}, "approve", OWNER_CTX
            )
        )
        assert refused == "goal"

        goal, lead, effects = await _parked(w, notes=2)
        await w.owner.execute(
            "UPDATE switchboard.budgets SET deadline = now() - interval '1 second' "
            "WHERE goal_id = $1",
            goal,
        )
        elapsed = await _propose(w, goal, lead, effects)
        refused = await _refusal(
            resolver.decide(
                elapsed.set_id, elapsed.version, _picks(elapsed), {}, "approve", OWNER_CTX
            )
        )
        assert refused == "expired"
        assert await _state(w, elapsed) == "expired"
        assert w.forwarder.calls == []


async def test_two_racing_set_decisions_dispatch_each_item_once(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=2)
        proposed = await _propose(w, goal, lead, effects)
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))
        outcomes = await asyncio.gather(
            *(
                resolver.decide(
                    proposed.set_id, proposed.version, _picks(proposed), {}, "approve", OWNER_CTX
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        refused = [o for o in outcomes if isinstance(o, OwnerDecisionRefused)]
        assert len(refused) == 1 and refused[0].reason == "stale"
        assert len(w.forwarder.calls) == 2


async def test_a_broker_refusal_of_one_item_does_not_stop_the_others(broker_db_url):
    async with world(broker_db_url) as w:
        goal, lead, effects = await _parked(w, notes=2)
        proposed = await _propose(w, goal, lead, effects)
        real = w.broker.execute_approved
        calls = []

        async def first_refused(goal_id, effect_id, approval_ref, arguments):
            calls.append(effect_id)
            if len(calls) == 1:
                raise BrokerRefused(Reason.CATALOG)
            return await real(goal_id, effect_id, approval_ref, arguments)

        w.broker.execute_approved = first_refused
        decided = await DecisionSetResolver(OwnerDecisions(w.broker, live=True)).decide(
            proposed.set_id, proposed.version, _picks(proposed), {}, "approve", OWNER_CTX
        )
        assert [(o.status, o.reason) for o in decided.outcomes] == [
            ("cancelled", Reason.CATALOG),
            ("committed", None),
        ]


@pytest.mark.parametrize("reason", [Reason.CAPABILITY, Reason.CATALOG, Reason.POLICY])
async def test_a_lasting_broker_refusal_ends_the_approval_instead_of_redriving_it(
    broker_db_url, reason
):
    """N1: only a transient refusal stays approved; nothing is re-driven every sweep."""
    async with world(broker_db_url) as w:
        goal, _, (stuck, waits) = await _parked(w)
        real = w.broker.execute_approved

        async def refused(goal_id, effect_id, approval_ref, arguments):
            if effect_id == stuck:
                raise BrokerRefused(reason)
            raise BrokerRefused(Reason.UNAVAILABLE)

        w.broker.execute_approved = refused
        decisions = OwnerDecisions(w.broker, live=True)
        lasting = await decisions.approve(goal, stuck, OWNER_CTX)
        assert (lasting.status, lasting.reason) == ("cancelled", reason)
        assert (await w.effect(stuck))["status"] == "cancelled"
        assert (await w.action(stuck))["execution_result"]["status"] == "cancelled"
        transient = await decisions.approve(goal, waits, OWNER_CTX)
        assert (transient.status, transient.reason) == ("approved", Reason.UNAVAILABLE)

        w.broker.execute_approved = real
        # Only the transient one is re-driven, and it dispatches once.
        assert await decisions.redrive() == 1
        assert (await w.effect(waits))["status"] == "committed"
        assert (await w.effect(stuck))["status"] == "cancelled"
        assert len(w.forwarder.calls) == 1
        assert await decisions.redrive() == 0


async def test_a_domain_call_changed_after_parking_never_dispatches(broker_db_url):
    """C3b: the domain's stored call must match the receipt's digest, or it is cancelled."""
    async with world(broker_db_url) as w:
        goal, _, (effect, _) = await _parked(w)
        changed = {**(await w.action(effect))["tool_args"], "message": "something else"}
        await w.domain.execute(
            "UPDATE pending_actions SET tool_args = $2 WHERE id = $1", effect, changed
        )
        outcome = await OwnerDecisions(w.broker, live=True).approve(goal, effect, OWNER_CTX)
        assert (outcome.status, outcome.reason) == ("cancelled", Reason.POLICY)
        assert (await w.effect(effect))["status"] == "cancelled"
        assert w.forwarder.calls == []
        assert await OwnerDecisions(w.broker, live=True).redrive() == 0
        assert w.forwarder.calls == []
