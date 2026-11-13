"""A settled goal is judged by its receipts, not the lead's claim (bu-4c7nvp.1.3 PR-C).

Real broker, approvals domain and ledger. Dark (the default), the settle resolution is what it was
and nothing is recorded. Live, a lead that claims ``completed`` gets it only when every plan
requirement, every owner anchor and every relied effect has its proof; otherwise ``partial`` with
the missing proof recorded once in ``goal_outcome_evidence``. Owner anchors come from server
facts (here, the owner's approval and the reply an owner message is owed), survive a re-plan that
leaves them out, and can never be claimed by the lead.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.owner_decisions import OwnerContext, OwnerDecisions
from butlers.goal_runner import evidence
from butlers.goal_runner.evidence import Requirement
from butlers.goal_runner.ledger import RunnerLedger
from tests.goal_runner.test_goal_runner import (
    NOTIFY,
    SEARCH,
    SEND,
    TO_OWNER,
    _grant,
    harness,
    refused,
)

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)

OWNER = OwnerContext(decided_by="owner:test", via="test")
LIVE = "butlers.goal_runner.evidence.EVIDENCE_COMPLETION_LIVE"
# The harness goal is admitted from an owner message, so it owes the owner a reply (D11).
REPLY = {"ref": "reply", "evidence": "owner_delivered", "anchor": "owner"}


async def _parked(h, goal, token, text: str):
    args = {"entity_id": str(uuid4()), "message": text, "channel": "telegram"}
    parked = await h.w.broker.call(goal, token, NOTIFY.identity, args)
    assert parked.status == "parked"
    return parked.effect_id


async def _running(h, count=1):
    goal = await h.goal(_grant(NOTIFY), _grant(SEARCH), _grant(SEND))
    lead = await h.lead(goal, count)
    return goal, lead


async def _complete(h, goal, token, relied=()):
    """Answer the owner, then claim ``completed``; returns the settled status."""
    reply = await h.w.broker.call(goal, token, SEND.identity, TO_OWNER)
    args = {
        "outcome": "completed",
        "reply_effect_id": str(reply.effect_id),
        "relied_effects": [str(e) for e in relied],
    }
    await h.call(goal, token, "goal__complete", args)
    await h.runner.supervise_once()
    return await h.goal_status(goal)


async def _evidence(h, goal):
    row = await h.w.owner.fetchrow(
        "SELECT final_status, satisfied::text, missing::text "
        "FROM switchboard.goal_outcome_evidence WHERE goal_id = $1",
        goal,
    )
    if row is None:
        return None
    return row["final_status"], json.loads(row["satisfied"]), json.loads(row["missing"])


async def _requirements(h, goal, version):
    stored = await h.w.owner.fetchval(
        "SELECT requirements::text FROM switchboard.goal_plans WHERE goal_id = $1 AND version = $2",
        goal,
        version,
    )
    return json.loads(stored)


async def test_dark_the_lead_claim_stands_and_nothing_is_recorded(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead = await _running(h)
        effect = await _parked(h, goal, lead.token, "late")
        await OwnerDecisions(h.w.broker, live=True).reject(goal, effect, OWNER)
        assert await _complete(h, goal, lead.token, [effect]) == "completed"
        assert await _evidence(h, goal) is None
        # Dark, the lead cannot name a requirement, and a plan records none.
        other, other_lead = await _running(h, 2)
        named = await _parked(h, other, other_lead.token, "x")
        plan = h.runner.plan(other, other_lead.token, [], [], [], [f"effect:{named}"])
        assert await refused(plan) is Reason.POLICY
        version = await h.runner.plan(other, other_lead.token, [f"effect:{named}"], [], [])
        assert await _requirements(h, other, version) == []


async def test_live_a_relied_effect_that_never_committed_ends_partial(broker_db_url):
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            effect = await _parked(h, goal, lead.token, "late")
            await OwnerDecisions(h.w.broker, live=True).reject(goal, effect, OWNER)
            assert await _complete(h, goal, lead.token, [effect]) == "partial"
            final, satisfied, missing = await _evidence(h, goal)
            assert (final, satisfied) == ("partial", [REPLY])
            relied = {"ref": f"effect:{effect}", "evidence": "committed", "anchor": "lead"}
            assert missing == [{**relied, "found": "rejected"}]
            outcome = await h.w.owner.fetchrow(
                "SELECT outcome, final_status, delivery_failure FROM switchboard.goal_outcomes "
                "WHERE goal_id = $1",
                goal,
            )
            assert tuple(outcome) == ("completed", "partial", None)


async def test_live_an_owner_approval_is_a_requirement_the_lead_cannot_drop(broker_db_url):
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            effect = await _parked(h, goal, lead.token, "late")
            real = h.w.broker.execute_approved

            async def catalog_changed(goal_id, effect_id, approval_ref, arguments):
                raise BrokerRefused(Reason.CATALOG)

            h.w.broker.execute_approved = catalog_changed
            decided = await OwnerDecisions(h.w.broker, live=True).approve(goal, effect, OWNER)
            h.w.broker.execute_approved = real
            assert decided.status == "cancelled"

            # The plan records the owner's anchors though the lead named none, and keeps them.
            anchor = {"ref": f"effect:{effect}", "evidence": "committed", "anchor": "owner"}
            version = await h.runner.plan(goal, lead.token, [], [], [])
            assert await _requirements(h, goal, version) == [anchor, REPLY]
            replanned = await h.runner.plan(goal, lead.token, [f"effect:{effect}"], [], [])
            assert replanned == version + 1
            assert await _requirements(h, goal, replanned) == [anchor, REPLY]

            # The lead never relied on it, yet the owner approved it and it never ran.
            assert await _complete(h, goal, lead.token) == "partial"
            _, satisfied, missing = await _evidence(h, goal)
            assert (satisfied, missing) == ([REPLY], [{**anchor, "found": "cancelled"}])


async def test_live_every_proof_present_completes(broker_db_url):
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            approved = await _parked(h, goal, lead.token, "on my way")
            await OwnerDecisions(h.w.broker, live=True).approve(goal, approved, OWNER)
            assert (await h.w.effect(approved))["status"] == "committed"
            version = await h.runner.plan(goal, lead.token, [], [], [], [f"effect:{approved}"])
            # The same proof asked by owner and lead is recorded once, owner-anchored.
            anchor = {"ref": f"effect:{approved}", "evidence": "committed", "anchor": "owner"}
            assert await _requirements(h, goal, version) == [anchor, REPLY]
            assert await _complete(h, goal, lead.token, [approved]) == "completed"
            assert await _evidence(h, goal) == ("completed", [anchor, REPLY], [])


async def test_live_a_missing_reply_stays_the_delivery_failure_it_was(broker_db_url):
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            await h.call(goal, lead.token, "goal__complete", {"outcome": "completed"})
            await h.runner.supervise_once()
            assert await h.goal_status(goal) == "partial"
            failure = await h.w.owner.fetchval(
                "SELECT delivery_failure FROM switchboard.goal_outcomes WHERE goal_id = $1", goal
            )
            assert failure == "reply_missing"
            assert await _evidence(h, goal) == ("partial", [], [{**REPLY, "found": None}])


async def test_live_a_lead_cannot_claim_an_owner_anchor_or_name_a_foreign_row(broker_db_url):
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            for refs in (["reply"], ["plan:1"], [f"effect:{uuid4()}"]):
                reason = await refused(h.runner.plan(goal, lead.token, [], [], [], refs))
                assert reason in (Reason.POLICY, Reason.RESOURCE), refs
            assert not await h.w.owner.fetchval(
                "SELECT count(*) FROM switchboard.goal_plans WHERE goal_id = $1", goal
            )


def _owner_anchors(count: int) -> tuple[Requirement, ...]:
    return tuple(Requirement(f"effect:{uuid4()}", "committed", "owner") for _ in range(count))


async def test_live_owner_anchors_past_the_bound_never_refuse_a_replan(broker_db_url):
    """N1: owner anchors grow with every approval and reply; a re-plan still records."""
    grown = _owner_anchors(evidence.MAX_REQUIREMENTS + 10)
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            with patch.object(RunnerLedger, "owner_anchors", AsyncMock(return_value=grown[:5])):
                first = await h.runner.plan(goal, lead.token, [], [], [])
            kept = await _requirements(h, goal, first)
            with patch.object(RunnerLedger, "owner_anchors", AsyncMock(return_value=grown)):
                own = await _parked(h, goal, lead.token, "x")
                second = await h.runner.plan(
                    goal, lead.token, [f"effect:{own}"], [], [], [f"effect:{own}"]
                )
            recorded = await _requirements(h, goal, second)
            assert len(recorded) == evidence.MAX_REQUIREMENTS
            assert all(item in recorded for item in kept)
            assert {"ref": f"effect:{own}", "evidence": "committed", "anchor": "lead"} in recorded


async def test_live_a_settle_past_the_evidence_bound_records_a_truncation_marker(broker_db_url):
    """N1: the verdict is judged in full; only the persisted lists are capped."""
    reply = Requirement(REPLY["ref"], REPLY["evidence"], REPLY["anchor"])
    grown = (reply, *_owner_anchors(evidence.MAX_PERSISTED + 12))
    with patch(LIVE, True):
        async with harness(broker_db_url) as h:
            goal, lead = await _running(h)
            with patch.object(RunnerLedger, "owner_anchors", AsyncMock(return_value=grown)):
                assert await _complete(h, goal, lead.token) == "partial"
            recorded = await _evidence(h, goal)
            assert recorded is not None
            final, satisfied, missing = recorded
            assert (final, satisfied) == ("partial", [REPLY])
            assert len(missing) == evidence.MAX_PERSISTED
            unproved = len(grown) - 1  # every synthetic anchor; the reply is proved
            assert missing[-1] == {"truncated": unproved - (evidence.MAX_PERSISTED - 1)}
