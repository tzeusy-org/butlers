"""The Gmail exposure rule at the broker-to-domain seam, on the migrated ledger (bu-kqme7f.10).

AC9 of bu-kqme7f.5, deferred from #5969: reading mail exposes the invocation; a third-party
goal's label change on the thread that admitted it dispatches and is recorded; an injected
archive of another thread parks for the owner and changes nothing; the sixth modify call in a
goal is refused. Also the cap's accounting (a replay never counts twice, a failed or rejected
call never counts) and the sw_083 binding's own guarantees.
"""

from __future__ import annotations

import shutil
from uuid import uuid4

import asyncpg
import pytest

from butlers.capability_broker.contracts import Catalog, Reason
from butlers.capability_broker.domains import DomainReceipt
from butlers.capability_broker.policy import GMAIL_MODIFY_CAP
from butlers.core.capability_json import arguments_digest
from tests.capability_broker.helpers import BROKER_DOMAIN, capability
from tests.capability_broker.test_broker_ledger import StaticCatalog, _grant, _refused, world

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

ACCOUNT, THREAD = "owner@example.test", "18c0ffee"
TRIGGER = (ACCOUNT, THREAD)
SEARCH = capability("gmail_search", "read", BROKER_DOMAIN, output_trust="untrusted")
MODIFY = capability(
    "gmail_modify_threads",
    "record",
    BROKER_DOMAIN,
    resource_params=("account",),
    safety_args=("account", "thread_ids", "add_labels", "remove_labels"),
)
CATALOG = StaticCatalog(Catalog("catalog-v1", (SEARCH, MODIFY)))


def _label(name: str, thread: str = THREAD) -> dict:
    return {"account": ACCOUNT, "thread_ids": [thread], "add_labels": [name]}


async def _third_party(w, *, trigger=TRIGGER):
    grants = (_grant(SEARCH), _grant(MODIFY))
    goal = await w.goal(*grants, exposed=True, origin="third_party", trigger=trigger)
    invocation, token = await w.invocation(goal, *grants)
    return goal, invocation, token


async def test_a_gmail_search_exposes_the_invocation(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        grants = (_grant(SEARCH), _grant(MODIFY))
        goal = await w.goal(*grants)
        invocation, token = await w.invocation(goal, *grants)
        await w.broker.call(goal, token, SEARCH.identity, {"account": ACCOUNT, "query": "x"})
        exposed = await w.owner.fetchval(
            "SELECT exposed FROM switchboard.invocations WHERE id = $1", invocation
        )
        assert exposed is True
        # Now exposed, the owner's goal is bound too: with no triggering thread, it parks.
        parked = await w.broker.call(goal, token, MODIFY.identity, _label("Receipts"))
        assert parked.status == "parked" and len(w.forwarder.calls) == 1


async def test_a_label_on_the_triggering_thread_dispatches_and_is_recorded(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, invocation, token = await _third_party(w)
        args = _label("Receipts")
        result = await w.broker.call(goal, token, MODIFY.identity, args)
        assert result.status == "committed"
        assert [call[1] for call in w.forwarder.calls] == [args]
        effect = await w.effect(result.effect_id)
        assert (effect["goal_id"], effect["invocation_id"]) == (goal, invocation)
        assert (effect["capability"], effect["status"]) == (MODIFY.identity, "committed")
        assert effect["args_digest"] == arguments_digest(args)


@pytest.mark.parametrize(
    "args",
    [
        # The injected instruction: archive some other thread.
        {"account": ACCOUNT, "thread_ids": ["0ther"], "remove_labels": ["INBOX"]},
        {"account": ACCOUNT, "thread_ids": [THREAD], "remove_labels": ["INBOX"]},
        {"account": ACCOUNT, "thread_ids": [THREAD], "remove_labels": ["UNREAD"]},
        {"account": ACCOUNT, "thread_ids": [THREAD, "0ther"], "add_labels": ["Receipts"]},
        {"account": "else@example.test", "thread_ids": [THREAD], "add_labels": ["Receipts"]},
    ],
)
async def test_an_injected_or_archiving_change_parks_and_changes_nothing(broker_db_url, args):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, invocation, token = await _third_party(w)
        result = await w.broker.call(goal, token, MODIFY.identity, args)
        assert result.status == "parked" and w.forwarder.calls == []
        assert (await w.effect(result.effect_id))["status"] == "parked"
        action = await w.action(result.effect_id)
        assert action["status"] == "pending" and action["tool_args"] == args
        assert (action["goal_origin"], action["goal_exposed"]) == ("third_party", True)
        # Per-event only: no standing rule may approve it.
        assert action["goal_rule_possible"] is False


async def test_the_owner_approved_park_runs_the_exact_call(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, token = await _third_party(w)
        args = {"account": ACCOUNT, "thread_ids": ["0ther"], "remove_labels": ["INBOX"]}
        parked = await w.broker.call(goal, token, MODIFY.identity, args)
        done = await w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, args)
        assert done.status == "committed" and w.forwarder.calls[-1][1] == args


async def test_a_goal_no_gmail_message_admitted_parks_every_change(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, token = await _third_party(w, trigger=None)
        result = await w.broker.call(goal, token, MODIFY.identity, _label("Receipts"))
        assert result.status == "parked" and w.forwarder.calls == []


async def test_the_sixth_modify_call_in_a_goal_is_refused(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, token = await _third_party(w)
        for n in range(GMAIL_MODIFY_CAP - 1):
            result = await w.broker.call(goal, token, MODIFY.identity, _label(f"L{n}"))
            assert result.status == "committed"
        # A park counts too: it may still change mail once approved.
        other = {"account": ACCOUNT, "thread_ids": ["0ther"], "add_labels": ["L9"]}
        assert (await w.broker.call(goal, token, MODIFY.identity, other)).status == "parked"
        refused = await _refused(w.broker.call(goal, token, MODIFY.identity, _label("L5")))
        assert refused.reason is Reason.POLICY
        assert len(w.forwarder.calls) == GMAIL_MODIFY_CAP - 1
        counted = await w.owner.fetchval(
            "SELECT count(*) FROM switchboard.effects WHERE goal_id = $1", goal
        )
        assert counted == GMAIL_MODIFY_CAP
        recorded = await w.owner.fetchval(
            "SELECT count(*) FROM switchboard.broker_refusals WHERE goal_id = $1 "
            "AND capability = $2 AND reason = 'policy_refused'",
            goal,
            MODIFY.identity,
        )
        assert recorded == 1


async def test_a_replay_never_counts_twice(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, token = await _third_party(w)
        first = await w.broker.call(goal, token, MODIFY.identity, _label("L0"))
        replay = await _refused(w.broker.call(goal, token, MODIFY.identity, _label("L0")))
        assert (replay.reason, replay.effect_id) == (Reason.DUPLICATE, first.effect_id)
        for n in range(1, GMAIL_MODIFY_CAP):
            assert (await w.broker.call(goal, token, MODIFY.identity, _label(f"L{n}"))).value
        refused = await _refused(w.broker.call(goal, token, MODIFY.identity, _label("L9")))
        assert refused.reason is Reason.POLICY


async def test_a_replay_at_the_cap_is_refused_as_a_duplicate(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, token = await _third_party(w)
        results = [
            await w.broker.call(goal, token, MODIFY.identity, _label(f"L{n}"))
            for n in range(GMAIL_MODIFY_CAP)
        ]
        assert all(result.status == "committed" for result in results)
        last = GMAIL_MODIFY_CAP - 1
        replay = await _refused(w.broker.call(goal, token, MODIFY.identity, _label(f"L{last}")))
        assert (replay.reason, replay.effect_id) == (Reason.DUPLICATE, results[-1].effect_id)
        assert len(w.forwarder.calls) == GMAIL_MODIFY_CAP
        policy = await w.owner.fetchval(
            "SELECT count(*) FROM switchboard.broker_refusals WHERE goal_id = $1 "
            "AND reason = 'policy_refused'",
            goal,
        )
        assert policy == 0
        # A new call at the cap is still refused by the cap.
        refused = await _refused(w.broker.call(goal, token, MODIFY.identity, _label("L9")))
        assert refused.reason is Reason.POLICY


async def test_a_failed_or_rejected_call_never_burns_the_cap(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, token = await _third_party(w)
        w.forwarder.receipt = DomainReceipt("failed")
        failed = await w.broker.call(goal, token, MODIFY.identity, _label("L0"))
        assert (await w.effect(failed.effect_id))["status"] == "failed"
        w.forwarder.receipt = DomainReceipt("committed", "receipt-1", {"status": "ok"})
        other = {"account": ACCOUNT, "thread_ids": ["0ther"], "add_labels": ["L0"]}
        rejected = await w.broker.call(goal, token, MODIFY.identity, other)
        await w.domain.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE id = $1", rejected.effect_id
        )
        assert await w.broker.sync_parked(goal) == 1
        assert (await w.effect(rejected.effect_id))["status"] == "rejected"
        # The failed call may be retried; it and four more run before the cap.
        for n in range(GMAIL_MODIFY_CAP):
            result = await w.broker.call(goal, token, MODIFY.identity, _label(f"L{n}"))
            assert result.status == "committed"
        refused = await _refused(w.broker.call(goal, token, MODIFY.identity, _label("L9")))
        assert refused.reason is Reason.POLICY


async def test_an_unexposed_owner_goal_keeps_the_record_behavior(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal = await w.goal(_grant(MODIFY))
        _, token = await w.invocation(goal, _grant(MODIFY))
        for n in range(GMAIL_MODIFY_CAP + 1):
            args = {"account": ACCOUNT, "thread_ids": [f"t{n}"], "remove_labels": ["INBOX"]}
            assert (await w.broker.call(goal, token, MODIFY.identity, args)).status == "committed"


# -- the sw_083 binding ------------------------------------------------------------


async def test_the_binding_is_immutable_and_only_an_exact_message_pair(broker_db_url):
    async with world(broker_db_url, catalog=CATALOG) as w:
        goal, _, _ = await _third_party(w)
        with pytest.raises(asyncpg.RaiseError):
            await w.owner.execute(
                "UPDATE switchboard.goals SET trigger_gmail_thread = '0ther' WHERE id = $1", goal
            )
        bare = await w.goal(_grant(MODIFY))
        with pytest.raises(asyncpg.RaiseError):
            await w.owner.execute(
                "UPDATE switchboard.goals SET trigger_gmail_account = $2, "
                "trigger_gmail_thread = $3 WHERE id = $1",
                bare,
                ACCOUNT,
                THREAD,
            )
        for trigger in ((ACCOUNT, None), ("Owner@example.test", THREAD), (ACCOUNT, "a/b")):
            with pytest.raises(asyncpg.CheckViolationError):
                await w.goal(_grant(MODIFY), trigger=trigger)
        with pytest.raises(asyncpg.CheckViolationError):
            await w.owner.execute(
                "INSERT INTO switchboard.goals (id, source_key, source_kind, source_ref, "
                "occurrence_key, origin, admitted_exposed, exposed, ceiling, ceiling_digest, "
                "catalog_digest, config_digest, chain_depth, trigger_gmail_account, "
                "trigger_gmail_thread) VALUES ($1, $2, 'occurrence', 'ref', 'o', 'obligation', "
                "true, true, '[]'::jsonb, 'd', 'catalog-v1', 'config', 0, $3, $4)",
                uuid4(),
                f"occurrence:{uuid4()}",
                ACCOUNT,
                THREAD,
            )
