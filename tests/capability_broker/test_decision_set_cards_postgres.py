"""The goal runner's decision-set card sweep, against the real ledgers (bu-jv1moa.75).

A real broker parks real effects, the real ledger proposes the sets, and the sweep reads them
through the same loader the API uses and sends through the real owner-delivery ledger. Only the
provider transport and the owner-suppression check are doubles.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import asyncpg
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
from butlers.modules.approvals.delivery_recovery import ApprovalDeliveryRepository
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
    def __init__(
        self, outcome: str = "sent", raises: bool = False, provider_ref: str | None = None
    ) -> None:
        self.provider_ref = provider_ref
        self.outcome = outcome
        self.raises = raises
        self.envelopes: list[dict] = []

    async def __call__(self, envelope: dict) -> SendResult:
        self.envelopes.append(envelope)
        if self.raises:
            raise RuntimeError("provider timed out")
        return SendResult(self.outcome, self.provider_ref)  # type: ignore[arg-type]


async def _none(*_args, **_kwargs):
    return None


async def _owner(_pool):
    return "9001"


async def _set(w, texts=("one", "two")):
    proposed, effects, *_ = await _set_full(w, texts)
    return proposed, effects


async def _set_full(w, texts=("one", "two")):
    # With admission on, each parked effect gets its own approval delivery presentation.
    await w.domain.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await w.domain.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
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
    return proposed, effects, goal, lead, token


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


async def _members(w) -> int:
    return await w.owner.fetchval(
        "SELECT count(*) FROM public.owner_delivery_subject_members m "
        "JOIN public.owner_delivery_subjects s ON s.id = m.subject_id "
        "WHERE s.kind = 'decision_set' AND s.source_ref LIKE ANY($1::text[])",
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
    # Only this test's items may keep a digest or a card claimable on the domain.
    await w.domain.execute(
        "UPDATE pending_actions SET status = 'cancelled' WHERE status = 'pending'"
    )
    await w.domain.execute(
        "UPDATE approval_delivery_presentations SET state = 'cancelled', next_attempt_at = NULL, "
        "claim_token = NULL, claim_expires_at = NULL "
        "WHERE state IN ('ready', 'claimed', 'retry_wait')"
    )
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


async def test_a_failing_transport_stops_the_sweep_at_the_first_failure(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        for _ in range(SEND_LIMIT + 2):
            await _set(w)
        transport = Transport("failed")
        sweep = await _sweeper(w, transport)
        assert await sweep.sweep() == 0
        # Pre-call failures are global (no client, suppressed, no recipient): one try, not all.
        assert len(transport.envelopes) == 1
        assert await _presentations(w) == 1
        assert await _tokens(w) == 2


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


# -- the held individual cards (bu-jv1moa.80) -----------------------------------------------------


async def _held(w, effects) -> list[bool]:
    return [
        await w.domain.fetchval("SELECT public.owner_delivery_item_held('messenger', $1)", str(e))
        for e in effects
    ]


async def _claimed_presentations(w) -> set:
    """Claim everything claimable on the domain; the ids of the presentations handed out."""
    repo = ApprovalDeliveryRepository(w.domain)
    claimed = set()
    while (claim := await repo.claim_next()) is not None:
        claimed.add(claim.presentation_id)
    return claimed


async def _own_presentations(w, effects) -> set:
    """The claimable presentations that carry these items: their own, or their digest's."""
    rows = await w.domain.fetch(
        "SELECT DISTINCT p.id FROM approval_delivery_presentations p "
        "LEFT JOIN approval_delivery_intents i ON i.id = p.intent_id "
        "LEFT JOIN approval_delivery_cohort_members m ON m.cohort_id = p.cohort_id "
        "LEFT JOIN approval_delivery_intents mi ON mi.id = m.intent_id "
        "WHERE p.presentation_mode <> 'collapsed' AND p.state IN ('ready', 'retry_wait') "
        "AND (i.action_id = ANY($1::uuid[]) OR mi.action_id = ANY($1::uuid[]))",
        effects,
    )
    return {r["id"] for r in rows}


async def test_a_sent_set_card_holds_each_items_own_card_until_the_set_closes(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        proposed, effects = await _set(w)
        assert await _held(w, effects) == [False, False]
        sweep = await _sweeper(w, Transport())
        assert await sweep.sweep() == 1
        assert await _held(w, effects) == [True, True]
        own = await _own_presentations(w, effects)
        assert own
        assert not (own & await _claimed_presentations(w))
        # Held is not dropped: the presentations are untouched and come back once the set closes.
        assert {
            r["state"]
            for r in await w.domain.fetch(
                "SELECT state FROM approval_delivery_presentations WHERE id = ANY($1::uuid[])",
                list(own),
            )
        } == {"ready"}
        await w.owner.execute(
            "UPDATE switchboard.decision_set_versions SET state = 'cancelled' WHERE set_id = $1",
            proposed.set_id,
        )
        await sweep.sweep()
        assert await _held(w, effects) == [False, False]
        assert own <= await _claimed_presentations(w)


async def test_an_item_shared_by_two_live_set_cards_is_released_only_when_both_close(
    broker_db_url,
):
    async with world(broker_db_url) as w:
        await _reset(w)
        first, effects, goal, lead, token = await _set_full(w)
        sweep = await _sweeper(w, Transport())
        assert await sweep.sweep() == 1
        extra = await w.broker.call(
            goal,
            token,
            NOTIFY.identity,
            {"channel": "telegram", "recipient": "12345", "message": "three"},
        )
        async with w.owner.acquire() as connection, connection.transaction():
            second = await DecisionSetLedger(connection).create(
                goal, lead, [*effects, extra.effect_id], CLASSES
            )
        assert second.version == first.version + 1
        MINE.append(str(second.set_id))
        assert await sweep.sweep() == 1  # the new version is its own card; the old one is closed
        assert await _held(w, effects) == [True, True]
        await w.owner.execute(
            "UPDATE switchboard.decision_set_versions SET state = 'cancelled' "
            "WHERE set_id = $1 AND version = $2",
            second.set_id,
            second.version,
        )
        await sweep.sweep()
        assert await _held(w, effects) == [False, False]


async def test_the_old_version_alone_released_leaves_the_item_held_by_the_new_one(
    broker_db_url,
):
    async with world(broker_db_url) as w:
        await _reset(w)
        first, effects, goal, lead, token = await _set_full(w)
        sweep = await _sweeper(w, Transport())
        await sweep.sweep()
        extra = await w.broker.call(
            goal,
            token,
            NOTIFY.identity,
            {"channel": "telegram", "recipient": "12345", "message": "three"},
        )
        async with w.owner.acquire() as connection, connection.transaction():
            await DecisionSetLedger(connection).create(
                goal, lead, [*effects, extra.effect_id], CLASSES
            )
        await sweep.sweep()
        subjects = await w.owner.fetch(
            "SELECT source_ref, state FROM public.owner_delivery_subjects "
            "WHERE kind = 'decision_set' AND source_ref LIKE $1",
            f"{first.set_id}:%",
        )
        assert len(subjects) == 2
        assert sum(r["state"] == "cancelled" for r in subjects) == 1
        # The superseded version's subject is closed; the live one still holds both items.
        assert await _held(w, effects) == [True, True]


async def test_no_hold_without_a_sent_card(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        _, effects = await _set(w)
        await (await _sweeper(w, Transport(), live=False)).sweep()
        await (await _sweeper(w, Transport(), rollout=False)).sweep()
        assert await _held(w, effects) == [False, False]
        assert await _members(w) == 0
        failing = await _sweeper(w, Transport("failed"))
        await failing.sweep()
        assert await _held(w, effects) == [False, False]
        assert await _own_presentations(w, effects)


async def test_an_open_only_set_holds_nothing(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        _, effects = await _set(w)
        await w.domain.execute(
            "UPDATE pending_actions SET tool_args = $2::jsonb WHERE id = $1",
            effects[1],
            '{"channel":"telegram","recipient":"12345","message":"edited behind the card"}',
        )
        await (await _sweeper(w, Transport())).sweep()
        assert await _held(w, effects) == [False, False]


async def test_a_held_item_is_not_reported_due_or_stuck(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        _, effects = await _set(w)
        repo = ApprovalDeliveryRepository(w.domain)
        before = await repo.backlog_snapshot()
        await (await _sweeper(w, Transport())).sweep()
        after = await repo.backlog_snapshot()
        assert before.due_count >= 1 and after.due_count == 0


async def test_the_member_table_is_append_only_and_narrowly_granted(broker_db_url):
    async with world(broker_db_url) as w:
        table = "public.owner_delivery_subject_members"
        roles = [
            r["rolname"]
            for r in await w.owner.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname LIKE 'butler\\_%\\_rw'"
            )
        ]
        for role in roles:
            privileges = {
                p: await w.owner.fetchval("SELECT has_table_privilege($1, $2, $3)", role, table, p)
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            if role == "butler_switchboard_rw":
                assert privileges == {
                    "SELECT": True,
                    "INSERT": True,
                    "UPDATE": False,
                    "DELETE": False,
                    "TRUNCATE": False,
                }
            else:
                assert not any(privileges.values()), role
            can_ask = await w.owner.fetchval(
                "SELECT has_function_privilege($1, "
                "'public.owner_delivery_item_held(text, text)', 'EXECUTE')",
                role,
            )
            assert can_ask is True, role
        public_can = await w.owner.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a "
            "WHERE p.oid = 'public.owner_delivery_item_held(text, text)'::regprocedure "
            "AND a.grantee = 0)"
        )
        assert public_can is False
        # No role outside the butler runtime roles (PUBLIC included) may ask.
        strangers = await w.owner.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'butler\\_%\\_rw' "
            "AND NOT rolsuper AND rolname NOT LIKE 'pg\\_%' "
            "AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid "
            "WHERE m.member = pg_roles.oid AND b.rolname LIKE 'butler\\_%\\_rw') "
            "AND oid <> (SELECT proowner FROM pg_proc "
            "WHERE oid = 'public.owner_delivery_item_held(text, text)'::regprocedure)"
        )
        for row in strangers:
            assert not await w.owner.fetchval(
                "SELECT has_function_privilege($1, "
                "'public.owner_delivery_item_held(text, text)', 'EXECUTE')",
                row["rolname"],
            ), row["rolname"]
        await _reset(w)
        await _set(w)
        await (await _sweeper(w, Transport())).sweep()
        assert await _members(w) == 2
        for statement in (
            f"UPDATE {table} SET member_ref = member_ref",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await w.owner.execute(statement)


async def test_a_member_is_minted_only_at_an_unresolved_subject(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        proposed, _ = await _set(w)
        sweep = await _sweeper(w, Transport())
        await sweep.sweep()
        await w.owner.execute(
            "UPDATE switchboard.decision_set_versions SET state = 'cancelled' WHERE set_id = $1",
            proposed.set_id,
        )
        await sweep.sweep()
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await w.owner.execute(
                "INSERT INTO public.owner_delivery_subject_members "
                "(subject_id, owning_schema, member_schema, member_ref) "
                "SELECT id, owning_schema, 'messenger', 'x' FROM public.owner_delivery_subjects "
                "WHERE source_ref = $1",
                f"{proposed.set_id}:{proposed.version}",
            )


async def test_a_sent_card_records_the_provider_message_id_so_it_can_be_retracted(broker_db_url):
    async with world(broker_db_url) as w:
        await _reset(w)
        proposed, _ = await _set(w)
        sweep = await _sweeper(w, Transport(provider_ref="4242"))
        assert await sweep.sweep() == 1
        await w.owner.execute(
            "UPDATE switchboard.decision_set_versions SET state = 'cancelled' WHERE set_id = $1",
            proposed.set_id,
        )
        await sweep.sweep()
        [target] = await _retractions(w)
        assert target.provider_ref == "4242"
