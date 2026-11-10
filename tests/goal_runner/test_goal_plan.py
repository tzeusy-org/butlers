"""``goal.plan``: a lead records its plan as versions of goal-owned references (RFC 0038 S2).

bu-dhloi1.7. A plan is premise, evidence and step references, each a server-validated id this
goal owns (an effect, an inbox row, a child, an earlier plan version), never free text. Identical
content keeps the current version; changed content appends the next one with its predecessor. A
wait binds the version current when it opened and keeps it, and a successor lead starts from the
latest version by reference.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.core.goal_wake import WakeAdmission
from tests.capability_broker.test_broker_ledger import NOTIFY, SEND, TO_OWNER, _joined_reply
from tests.goal_runner.test_goal_runner import _grant, harness, refused
from tests.goal_runner.test_goal_wake_runner import owner_prompt

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


def plan(premise=(), evidence=(), steps=()) -> dict:
    return {
        "premise_refs": list(premise),
        "evidence_refs": list(evidence),
        "step_refs": list(steps),
    }


async def plans(h, goal: UUID) -> list:
    return await h.w.owner.fetch(
        "SELECT version, predecessor_version, premise_refs::text AS premise, "
        "evidence_refs::text AS evidence, step_refs::text AS steps, created_epoch "
        "FROM switchboard.goal_plans WHERE goal_id = $1 ORDER BY version",
        goal,
    )


async def goal_plan_version(h, goal: UUID):
    return await h.w.owner.fetchval(
        "SELECT plan_version FROM switchboard.goals WHERE id = $1", goal
    )


async def wake_by_reply(h, goal: UUID, prompt: UUID) -> UUID:
    """Admission joins the owner's exact reply and wakes the goal; returns the inbox row."""
    inbox_id = await _joined_reply(h.w, goal, prompt)
    decision = await h.w.owner.fetchval(
        "SELECT decision_id FROM switchboard.goal_inbox WHERE id = $1", inbox_id
    )
    async with h.runner._txn() as ledger:
        waits = WakeAdmission(ledger.connection)
        envelope = await waits.answer_envelope(goal, prompt)
        assert envelope is not None
        woken = await waits.admit_answer(envelope, decision, inbox_id)
    assert woken.state == "admitted"
    return inbox_id


async def test_a_changed_premise_appends_the_next_version_and_the_same_content_does_not(
    broker_db_url,
):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        sent = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        searched = await h.w.broker.call(
            goal, lead.token, SEND.identity, {**TO_OWNER, "text": "again"}
        )
        assert sent.effect_id is not None and searched.effect_id is not None
        assert sent.effect_id != searched.effect_id
        first = plan(premise=[f"effect:{sent.effect_id}"], steps=[f"effect:{searched.effect_id}"])

        assert await h.call(goal, lead.token, "goal__plan", first) == {
            "status": "planned",
            "plan_version": 1,
        }
        # A retry of the same content is the same version, not a new one.
        assert (await h.call(goal, lead.token, "goal__plan", first))["plan_version"] == 1
        changed = plan(
            premise=[f"effect:{searched.effect_id}"],
            evidence=[f"effect:{sent.effect_id}"],
            steps=["plan:1"],
        )
        assert (await h.call(goal, lead.token, "goal__plan", changed))["plan_version"] == 2

        v1, v2 = await plans(h, goal)
        assert (v1["version"], v1["predecessor_version"]) == (1, None)
        assert (v2["version"], v2["predecessor_version"]) == (2, 1)
        assert f"effect:{sent.effect_id}" in v1["premise"]  # the approved version is unchanged
        assert f"effect:{searched.effect_id}" in v2["premise"] and "plan:1" in v2["steps"]
        assert v1["created_epoch"] == v2["created_epoch"] == 0
        assert await goal_plan_version(h, goal) == 2


async def test_a_wait_keeps_its_version_and_each_successor_starts_from_the_latest(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND), _grant(NOTIFY))
        lead = await h.lead(goal)
        first = await owner_prompt(h, goal, lead)
        await h.call(goal, lead.token, "goal__plan", plan(premise=[f"effect:{first}"]))
        await h.call(goal, lead.token, "goal__wait_owner", {"effect_id": str(first)})
        envelope = await h.w.owner.fetchrow(
            "SELECT id, plan_version FROM switchboard.wait_envelopes WHERE goal_id = $1", goal
        )
        assert envelope["plan_version"] == 1

        reply = await wake_by_reply(h, goal, first)
        await h.runner.supervise_once()
        woken = await h.launched(2)
        assert "plan_version: 1" in woken.prompt
        assert f"plan_premise: effect:{first}" in woken.prompt

        # The successor's changed premise is version 2; the wait it woke from keeps version 1.
        second = await owner_prompt(h, goal, woken)
        result = await h.call(
            goal,
            woken.token,
            "goal__plan",
            plan(premise=[f"effect:{second}"], evidence=[f"inbox:{reply}"], steps=["plan:1"]),
        )
        assert result["plan_version"] == 2
        assert (
            await h.w.owner.fetchval(
                "SELECT plan_version FROM switchboard.wait_envelopes WHERE id = $1",
                envelope["id"],
            )
            == 1
        )
        await h.call(goal, woken.token, "goal__wait_owner", {"effect_id": str(second)})
        assert (
            await h.w.owner.fetchval(
                "SELECT plan_version FROM switchboard.wait_envelopes "
                "WHERE goal_id = $1 AND state = 'open'",
                goal,
            )
            == 2
        )

        await wake_by_reply(h, goal, second)
        await h.runner.supervise_once()
        latest = await h.launched(3)
        assert "plan_version: 2" in latest.prompt
        assert f"plan_premise: effect:{second}" in latest.prompt
        assert f"plan_evidence: inbox:{reply}" in latest.prompt
        assert f"plan_premise: effect:{first}" not in latest.prompt


async def test_a_reference_the_goal_does_not_own_is_refused_and_records_nothing(broker_db_url):
    async with harness(broker_db_url) as h:
        other = await h.goal(_grant(SEND))
        other_lead = await h.lead(other)
        foreign = await h.w.broker.call(other, other_lead.token, SEND.identity, TO_OWNER)
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal, count=2)
        own = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)

        for refs in (
            plan(premise=[f"effect:{foreign.effect_id}"]),  # another goal's effect
            plan(evidence=[f"inbox:{uuid4()}"]),  # no such inbox row
            plan(steps=[f"child:{other_lead.invocation_id}"]),  # another goal's lead
            plan(steps=["plan:1"]),  # no version 1 yet
        ):
            reason = await refused(h.call(goal, lead.token, "goal__plan", refs))
            assert reason is Reason.RESOURCE, refs
        own_ref = f"effect:{own.effect_id}"
        for refs in (
            plan(premise=["book the 7pm table"]),  # free text is never a reference
            plan(premise=[f"effect:{own.effect_id.hex}"]),  # not the canonical id spelling
            plan(premise=[own_ref, own_ref]),  # the same reference twice
            plan(steps=[own_ref] * 33),  # past the bound
            {"premise_refs": [own_ref]},  # every list is named
        ):
            reason = await refused(h.call(goal, lead.token, "goal__plan", refs))
            assert reason is Reason.POLICY, refs
        assert await plans(h, goal) == []
        assert await goal_plan_version(h, goal) is None
