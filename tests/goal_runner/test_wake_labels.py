"""A wake lead starts with the label of what woke it (RFC 0038 S1, S2.2; bu-dhloi1.305 PR5a).

The fire's label is the wait's registered label joined with the labels of the events that hold
its true leaves; wake admission writes it on the wake epoch. The lead the wake launches starts
with that epoch's label, so a fire on outside content (a forwarded reply, or any event whose
producer gave no label) starts an exposed lead, and its first owner-bound call is asserted
under exposure. A relaunch or recovery lead inherits it through the earlier leads' labels. The
grant is untouched: still the ceiling ∩ the envelope snapshot ∩ the published catalog.
"""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.domains import DomainReceipt
from butlers.conditions import ConditionEvaluator, ConditionEvent
from butlers.conditions.sql import PostgresConditionStore
from butlers.core.goal_admission import Evidence, Grant, SourceRef, decode_grants
from butlers.core.goal_labels import Label
from butlers.core.reply_scope import scoped_reply_ref
from butlers.goal_runner.contracts import HostOutcome
from tests.capability_broker.test_broker_ledger import NOTIFY, OWNER
from tests.goal_runner.test_goal_owner_input_wait import owner_input, prompted
from tests.goal_runner.test_goal_runner import SEARCH, _grant, _retryable, harness
from tests.goal_runner.test_goal_wait_until import AT, decision, parked, wait_args, wait_until
from tests.goal_runner.test_goal_wake_runner import PROMPT_SCOPE, fire_for, waiting_on_conditions
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, pools

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


def _decoded(value) -> frozenset[Grant]:
    return frozenset(decode_grants(json.loads(value) if isinstance(value, str) else value))


async def first_owner_notify(h, goal: UUID, lead) -> bool:
    """The lead's first call to the owner commits; returns its assertion's ``exposed`` claim."""
    h.w.forwarder.receipt = DomainReceipt("committed", "receipt-woken", {"sent": True})
    args = {"channel": "telegram", "message": "Here is what I found"}
    before = len(h.w.forwarder.calls)
    sent = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
    assert sent.status == "committed"
    return h.w.verify(h.w.forwarder.calls[before][2], "notify", args)["exposed"]


@pytest.mark.parametrize(("fired", "exposed"), [(Label.owner(), False), (Label.unknown(), True)])
async def test_a_wake_lead_starts_with_the_label_of_the_fire_that_woke_it(
    broker_db_url, fired, exposed
):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead, grants=(_grant(NOTIFY),))
        assert (await h.runner.admit_wake(replace(fire_for(), label=fired))).state == "admitted"

        await h.runner.supervise_once()
        woken = await h.launched(2)

        row = await h.row(woken.invocation_id)
        assert (row["exposed"], row["exposure_cause"]) == (exposed, "inbox" if exposed else None)
        assert Label.from_row(1, row["label_instr"], row["label_data"], row["label_sens"]).covers(
            fired
        )
        # The label never touches the grant: ceiling ∩ envelope snapshot ∩ catalog, as before.
        assert _decoded(row["grant_snapshot"]) == {_grant(NOTIFY)}
        assert await first_owner_notify(h, goal, woken) is exposed


async def test_an_unlabelled_producers_event_wakes_an_exposed_lead(broker_db_url):
    """A producer that labels nothing (the evaluator's other callers) is missing provenance."""
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        assert await wait_until(h, goal, lead.token, wait_args(decision(effect))) == {
            "status": "waiting"
        }
        instance = await h.w.owner.fetchval(
            "SELECT g.condition_instance_ids[1] FROM switchboard.wait_generations g "
            "WHERE g.goal_id = $1",
            goal,
        )
        unlabelled = ConditionEvent(
            id=str(uuid4()),
            source="goal.decision",
            position=1,
            at=AT,
            tag="decision",
            data={"subject_id": str(effect), "outcome": "approved"},
        )
        evaluator = ConditionEvaluator(PostgresConditionStore(h.w.broker.pool))
        fired = await evaluator.evaluate(instance, [unlabelled], now=AT)
        assert fired.intent is not None and fired.intent.label.covers(Label.unknown())
        assert (await h.runner.admit_wake(fired.intent)).state == "admitted"

        await h.runner.supervise_once()
        woken = await h.launched(2)

        row = await h.row(woken.invocation_id)
        assert (row["exposed"], row["exposure_cause"]) == (True, "inbox")
        assert _decoded(row["grant_snapshot"]) == _decoded(
            (await h.row(lead.invocation_id))["grant_snapshot"]
        )
        assert await first_owner_notify(h, goal, woken) is True


async def owner_reply(broker_db_url, *, forwarded: bool, locator: str = "100:77"):
    """The owner answers the prompt at ``locator``; ``forwarded`` carries outside content."""
    evidence = Evidence(
        "message",
        "owner",
        owner=True,
        forwarded=forwarded,
        reply_delivery=scoped_reply_ref(
            **PROMPT_SCOPE,
            recipient_entity=OWNER,
            recipient_channel="telegram_bot",
            locator=locator,
        ),
        owner_entity=OWNER,
        owner_channel="telegram_bot",
    )
    async with pools(broker_db_url) as (owner, runtime):
        await configure(owner, limits())
        return await admit(runtime, evidence, SourceRef("message", f"tg-{uuid4()}"), uuid4())


@pytest.mark.parametrize("forwarded", [False, True])
async def test_a_reply_wakes_its_goal_with_the_replys_own_label(broker_db_url, forwarded):
    """A plain owner reply wakes an unexposed lead; a reply forwarding outside content wakes an
    exposed one. The label is the reply's own inbox row's, not the goal's or the prompt's."""
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        args = wait_args(owner_input(goal, prompt))
        assert await wait_until(h, goal, lead.token, args) == {"status": "waiting"}
        joined = await owner_reply(broker_db_url, forwarded=forwarded)
        assert (joined.result, joined.goal_id) == ("joined", goal)

        await h.runner.feed_conditions()
        assert await h.goal_status(goal) == "admitted"
        await h.runner.supervise_once()
        woken = await h.launched(2)

        row = await h.row(woken.invocation_id)
        assert row["exposed"] is forwarded
        assert row["exposure_cause"] == ("inbox" if forwarded else None)
        assert await first_owner_notify(h, goal, woken) is forwarded


async def test_a_relaunched_exposed_wake_lead_stays_exposed_with_the_same_grant(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead, grants=(_grant(NOTIFY),))
        await h.runner.admit_wake(replace(fire_for(), label=Label.unknown()))
        # The wake lead fails with no capability call: its one D14 relaunch follows.
        h.host.script = [HostOutcome(failure=_retryable())]

        await h.runner.supervise_once()
        woken = await h.launched(2)
        relaunch = await h.launched(3)

        first, again = await h.row(woken.invocation_id), await h.row(relaunch.invocation_id)
        assert again["relaunch_of"] == woken.invocation_id
        assert (first["exposed"], again["exposed"]) == (True, True)
        assert again["exposure_cause"] == "recovery"
        assert _decoded(again["grant_snapshot"]) == _decoded(first["grant_snapshot"])
