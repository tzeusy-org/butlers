"""The packet a launched invocation actually receives, built from the ledger (D15)."""

from __future__ import annotations

import shutil

import pytest

from tests.capability_broker.test_broker_ledger import RULES, SEARCH, SEND, TO_OWNER
from tests.goal_runner.test_goal_runner import SCHEMA, _grant, harness

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


def section(packet: str, name: str) -> str:
    start = packet.index(f"<{name}>")
    return packet[start : packet.index(f"</{name}>", start)]


async def test_a_first_lead_gets_its_grant_and_the_goals_budget(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(RULES), max_calls=7)
        lead = await h.lead(goal)
        packet = lead.prompt
        assert packet.startswith("<goal_packet version=1>")
        assert f"capability: {SEARCH.identity}" in section(packet, "grant")
        assert f"capability: {RULES.identity}" in section(packet, "grant")
        budget = section(packet, "budget")
        assert "goal_calls_left: 7" in budget
        assert "your_turns: 40" in budget and "your_tokens: 500" in budget
        assert "<ledger>" not in packet


async def test_the_packet_states_what_is_left_after_calls_were_made(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND), max_calls=7)
        old = await h.lead(goal)
        await h.w.broker.call(goal, old.token, SEND.identity, TO_OWNER)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            old.invocation_id,
        )
        await h.runner.supervise_once()
        new = await h.launched(2)
        assert "goal_calls_left: 6" in section(new.prompt, "budget")


async def test_a_summary_lead_after_recovery_gets_the_ledger_built_from_the_rows(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND))
        old = await h.lead(goal)
        child = await h.delegate(goal, old.token, SEARCH.identity)
        await h.launched(2)
        reply = await h.w.broker.call(goal, old.token, SEND.identity, TO_OWNER)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            old.invocation_id,
        )
        await h.runner.supervise_once()
        new = await h.launched(3)
        ledger = section(new.prompt, "ledger")
        assert f"effect: {reply.effect_id} {SEND.identity} " in ledger
        assert f"child: {child} " in ledger
        assert "previous lead" in section(new.prompt, "task")
        assert "epoch: 1" in new.prompt
        assert f"capability: {SEND.identity}" in section(new.prompt, "grant")


async def test_a_child_packet_is_its_brief_grant_and_share_never_the_leads_context(
    broker_db_url,
):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND), _grant(RULES), max_calls=9)
        lead = await h.lead(goal)
        await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        child = await h.delegate(goal, lead.token, SEARCH.identity, calls=2, tokens=50)
        launch = await h.launched(2)
        assert launch.invocation_id == child
        packet = launch.prompt
        assert "objective: Find the receipt" in section(packet, "brief")
        assert '"found"' in section(packet, "brief")  # the ledger's fixed result schema
        grant = section(packet, "grant")
        assert f"capability: {SEARCH.identity}" in grant
        assert SEND.identity not in packet and RULES.identity not in grant
        budget = section(packet, "budget")
        assert "your_calls_left: 2" in budget and "your_tokens: 50" in budget
        assert "goal_calls_left" not in budget
        for lead_only in ("<ledger>", "origin:", "epoch:", "exposed:", "goal__complete"):
            assert lead_only not in packet
        assert str(lead.invocation_id) not in packet  # not even its parent's identifier


async def test_launch_facts_read_a_childs_fixed_result_schema_back_from_the_ledger(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        child = await h.delegate(goal, lead.token, SEARCH.identity)
        launch = await h.launched(2)
        facts = await _facts(h, goal, child)
        assert facts.result_schema == SCHEMA
        assert launch.invocation_id == child


async def _facts(h, goal, invocation):
    from butlers.goal_runner.ledger import RunnerLedger

    async with h.w.broker._transaction() as ledger:
        return await RunnerLedger(ledger.connection).launch_facts(goal, invocation)
