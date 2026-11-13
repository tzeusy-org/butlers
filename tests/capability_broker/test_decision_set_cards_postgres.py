"""The goal runner's decision-set card sweep, against the real ledgers (bu-jv1moa.75).

A real broker parks real effects, the real ledger proposes the sets, and the sweep reads them
through the same loader the API uses and sends through the real owner-delivery ledger. Only the
provider transport and the owner-suppression check are doubles.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from butlers.capability_broker.owner_decisions import (
    DecisionSetResolver,
    OwnerContext,
    OwnerDecisions,
)
from butlers.core import owner_delivery as od
from butlers.core.decision_sets import DecisionSetLedger
from butlers.goal_runner.decision_set_cards import SEND_LIMIT, DecisionSetCards
from butlers.modules.approvals.decision_set_delivery import SendResult
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

CLASSES = {NOTIFY.identity: False, RULES.identity: True}
OWNER_CTX = OwnerContext(decided_by="owner:test", via="test")


class Transport:
    def __init__(self, outcome: str = "sent", raises: bool = False) -> None:
        self.outcome = outcome
        self.raises = raises
        self.envelopes: list[dict] = []

    async def __call__(self, envelope: dict) -> SendResult:
        self.envelopes.append(envelope)
        if self.raises:
            raise RuntimeError("provider timed out")
        return SendResult(self.outcome)  # type: ignore[arg-type]


async def _none(*_args, **_kwargs):
    return None


async def _owner(_pool):
    return "9001"


async def _set(w, texts=("one", "two")):
    grants = (_grant(NOTIFY), _grant(RULES))
    goal = await w.goal(*grants, max_calls=20)
    lead, token = await w.invocation(goal, *grants)
    effects = []
    for text in texts:
        call = {"channel": "telegram", "recipient": "12345", "message": text}
        result = await w.broker.call(goal, token, NOTIFY.identity, call)
        assert result.status == "parked", result
        effects.append(result.effect_id)
    async with w.owner.acquire() as connection, connection.transaction():
        proposed = await DecisionSetLedger(connection).create(goal, lead, effects, CLASSES)
    MINE.append(str(proposed.set_id))
    return proposed, effects


async def _sweeper(w, transport, *, live=True, suppression=_none, rollout=True):
    await w.owner.execute("UPDATE public.owner_delivery_rollout SET enabled = $1", rollout)
    # Pinned a minute before the earliest open set elapses; never the live clock.
    soonest = await w.owner.fetchval(
        "SELECT min(expires_at) FROM switchboard.decision_set_versions WHERE state = 'open'"
    )
    at = soonest - timedelta(minutes=1)
    return DecisionSetCards(
        w.owner,
        action_pools=[("messenger", w.domain)],
        transport=transport,
        recipient=_owner,
        suppression=suppression,
        clock=lambda: at,
        live=live,
    )


MINE: list[str] = []


def _refs() -> list[str]:
    return [f"{set_id}:%" for set_id in MINE]


async def _presentations(w) -> int:
    return await w.owner.fetchval(
        "SELECT count(*) FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id "
        "WHERE s.kind = 'decision_set' AND s.source_ref LIKE ANY($1::text[])",
        _refs(),
    )


async def _subjects(w) -> int:
    return await w.owner.fetchval(
        "SELECT count(*) FROM public.owner_delivery_subjects "
        "WHERE kind = 'decision_set' AND source_ref LIKE ANY($1::text[])",
        _refs(),
    )


async def _tokens(w) -> int:
    return await w.owner.fetchval(
        "SELECT count(*) FROM public.owner_delivery_tokens t "
        "JOIN public.owner_delivery_subjects s ON s.id = t.subject_id "
        "WHERE s.kind = 'decision_set' AND s.source_ref LIKE ANY($1::text[])",
        _refs(),
    )


async def _retractions(w):
    async with w.owner.acquire() as connection:
        found = await od.pending_retractions(connection, surface="telegram")
    return [t for t in found if (t.source_ref or "").split(":")[0] in MINE] if found else []


async def _reset(w) -> None:
    """Earlier tests' sets and cards stay (the ledger is append-only); they are closed, not mine."""
    MINE.clear()
    await w.owner.execute(
        "UPDATE switchboard.decision_set_versions SET state = 'cancelled' WHERE state = 'open'"
    )


async def test_a_set_is_sent_once_across_sweeps_and_a_restart(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        transport = Transport()
        sweep = await _sweeper(w, transport)
        assert await sweep.sweep() == 1
        assert await sweep.sweep() == 0
        restarted = await _sweeper(w, transport)
        assert await restarted.sweep() == 0
        assert len(transport.envelopes) == 1
        verbs = [a["verb"] for a in transport.envelopes[0]["actions"]]
        assert verbs == ["approve", "reject", "open_dashboard"]
        assert (await _subjects(w), await _presentations(w)) == (1, 1)


async def test_two_concurrent_sweeps_send_one_card(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        transport = Transport()
        first, second = await _sweeper(w, transport), await _sweeper(w, transport)
        await asyncio.gather(first.sweep(), second.sweep())
        assert len(transport.envelopes) == 1
        assert await _presentations(w) == 1


async def test_nothing_happens_while_the_decision_path_or_the_rollout_is_off(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        transport = Transport()
        assert await (await _sweeper(w, transport, live=False)).sweep() == 0
        assert await (await _sweeper(w, transport, rollout=False)).sweep() == 0
        assert transport.envelopes == []
        assert (await _subjects(w), await _presentations(w)) == (0, 0)


async def test_a_suppressed_owner_gets_no_presentation_and_no_token(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        transport = Transport()

        async def quiet(*_args, **_kwargs):
            return "quiet_hours"

        sweep = await _sweeper(w, transport, suppression=quiet)
        assert await sweep.sweep() == 0
        assert transport.envelopes == []
        assert (await _subjects(w), await _presentations(w), await _tokens(w)) == (0, 0, 0)
        # Once the owner may be reached again, the very same set is sent.
        assert await (await _sweeper(w, transport)).sweep() == 1


async def test_an_ambiguous_send_is_never_resent_and_mints_no_fresh_tokens(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        sweep = await _sweeper(w, Transport(raises=True))
        assert await sweep.sweep() == 1
        tokens = await _tokens(w)
        again = Transport()
        assert await (await _sweeper(w, again)).sweep() == 0
        assert again.envelopes == []
        assert await _tokens(w) == tokens


async def test_a_proven_non_send_is_presented_again_with_fresh_tokens(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        assert await (await _sweeper(w, Transport("failed"))).sweep() == 0
        retry = Transport()
        assert await (await _sweeper(w, retry)).sweep() == 1
        assert len(retry.envelopes) == 1


async def test_an_open_only_set_is_not_card_sent(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        _, effects = await _set(w)
        await w.domain.execute(
            "UPDATE pending_actions SET tool_args = $2::jsonb WHERE id = $1",
            effects[1],
            '{"channel":"telegram","recipient":"12345","message":"edited behind the card"}',
        )
        transport = Transport()
        assert await (await _sweeper(w, transport)).sweep() == 0
        assert transport.envelopes == []
        assert (await _subjects(w), await _presentations(w)) == (0, 0)


async def test_a_set_whose_view_cannot_be_read_is_skipped_not_fatal(broker_db_url):
    class Down:
        def acquire(self):
            raise RuntimeError("pool down")

    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)
        transport = Transport()
        sweep = await _sweeper(w, transport)
        sweep.action_pools = (("messenger", Down()),)
        assert await sweep.sweep() == 0
        assert transport.envelopes == []
        assert (await _subjects(w), await _presentations(w)) == (0, 0)


async def test_a_sweep_sends_at_most_the_bound_oldest_first(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        proposed = [(await _set(w))[0] for _ in range(SEND_LIMIT + 2)]
        transport = Transport()
        sweep = await _sweeper(w, transport)
        assert await sweep.sweep() == SEND_LIMIT
        sent = {
            a["reference"]["params"]["set_id"]
            for e in transport.envelopes
            for a in e["actions"]
            if a["verb"] == "open_dashboard"
        }
        assert sent == {str(p.set_id) for p in proposed[:SEND_LIMIT]}
        assert await sweep.sweep() == 2


async def _sent_set(w):
    proposed, effects = await _set(w)
    sweep = await _sweeper(w, Transport())
    assert await sweep.sweep() == 1
    assert await _retractions(w) == []
    return proposed, effects, sweep


async def _state(w, proposed) -> str:
    return await w.owner.fetchval(
        "SELECT state FROM public.owner_delivery_subjects "
        "WHERE kind = 'decision_set' AND source_ref = $1",
        f"{proposed.set_id}:{proposed.version}",
    )


@pytest.mark.parametrize(
    "left,closed", [("superseded", "cancelled"), ("cancelled", "cancelled"), ("expired", "expired")]
)
async def test_a_superseded_cancelled_or_expired_version_closes_its_card_and_lists_a_retraction(
    broker_db_url, left, closed
):
    async with world(broker_db_url) as w:
        await _reset(w)
        proposed, _, sweep = await _sent_set(w)
        await w.owner.execute(
            "UPDATE switchboard.decision_set_versions SET state = $3 "
            "WHERE set_id = $1 AND version = $2",
            proposed.set_id,
            proposed.version,
            left,
        )
        await sweep.sweep()
        assert await _state(w, proposed) == closed
        assert len(await _retractions(w)) == 1
        await sweep.sweep()
        assert len(await _retractions(w)) == 1


@pytest.mark.parametrize("decision,closed", [("approve", "decided"), ("reject", "decided")])
async def test_a_dashboard_decision_closes_the_card_with_the_receipts_verdict(
    broker_db_url, decision, closed
):
    async with world(broker_db_url) as w:
        await _reset(w)
        proposed, _, sweep = await _sent_set(w)
        resolver = DecisionSetResolver(OwnerDecisions(w.broker, live=True))
        picks = {i.effect_id: i.item_digest for i in proposed.items if not i.explicit_only}
        await resolver.decide(proposed.set_id, proposed.version, picks, {}, decision, OWNER_CTX)
        assert await _state(w, proposed) not in ("decided", "cancelled", "expired")
        await sweep.sweep()
        row = await w.owner.fetchrow(
            "SELECT state, decision FROM public.owner_delivery_subjects "
            "WHERE kind = 'decision_set' AND source_ref = $1",
            f"{proposed.set_id}:{proposed.version}",
        )
        assert (row["state"], row["decision"]) == (closed, decision)
        assert len(await _retractions(w)) == 1


async def test_a_sweep_never_raises_when_the_ledger_is_unreadable(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        await _set(w)

        async def broken(_pool):
            raise RuntimeError(f"lookup failed {uuid4()}")

        sweep = await _sweeper(w, Transport())
        sweep._recipient = broken
        assert await sweep.sweep() == 0
