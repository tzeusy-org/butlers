"""Run or Drop a refused owner request through its card token, over real PostgreSQL."""

from uuid import uuid4

import pytest

from butlers.core import owner_delivery as od
from butlers.core.goal_admission import Evidence, GoalAdmission
from butlers.goal_runner.owner_recovery import OwnerRecovery, OwnerRetryEvidence, bound
from tests.switchboard.goal_admission_fixtures import Catalogs, Sources, limits
from tests.switchboard.goal_ledger_fixtures import (  # noqa: F401
    admit,
    configure,
    ledger,
    ledger_url,
)
from tests.switchboard.test_owner_failure_postgres import (  # noqa: F401
    Sender,
    asks,
    owner_goal,
    reporter,
    world,
)
from tests.switchboard.test_pending_ask_expiry_postgres import refuse_owner_message

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def admitter(facts=None):
    async def admit_on(conn, source, attempt):
        reader = OwnerRetryEvidence(Sources(facts or Evidence("message", "p-2", owner=True)))
        return await GoalAdmission(bound(conn), reader, Catalogs()).admit_submission(
            source, attempt
        )

    return admit_on


def recovery(runtime, facts=None):
    return OwnerRecovery(runtime, admitter(facts))


async def refused_ask(owner, runtime):
    """A refused owner message with its subject open; returns (source, ask row)."""
    source, _ = await refuse_owner_message(owner, runtime)
    await reporter(runtime, Sender()).sweep()
    (row,) = [r for r in await asks(owner) if r["cause"] == "admission_refused"]
    return source, row


async def test_cards_are_offered_only_for_runnable_asks_and_are_coalesced(world):  # noqa: F811
    owner, runtime, _ = world
    _, row = await refused_ask(owner, runtime)
    await configure(owner, limits(new_goals=5))
    await owner_goal(runtime, owner)  # a failed goal: told, but cannot be run
    await reporter(runtime, Sender()).sweep()
    batch = await recovery(runtime).cards()
    assert batch.counts == {"admission_refused": 1, "failed": 1}
    assert {c.ask_id for c in batch.cards} >= {row["id"]}
    assert all(set(c.tokens) == {"approve", "reject"} for c in batch.cards)


async def test_no_cards_while_delivery_is_dark(world):  # noqa: F811
    owner, runtime, _ = world
    await refused_ask(owner, runtime)
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = false")
    assert (await recovery(runtime).cards()).cards == ()


async def test_run_admits_again_once_and_clears_the_reference(world):  # noqa: F811
    owner, runtime, _ = world
    source, row = await refused_ask(owner, runtime)
    (card,) = (await recovery(runtime).cards()).cards
    # Still over quota: Run is refused, nothing changes and the card keeps working.
    refused = await recovery(runtime).decide(card.tokens["approve"], row["id"], "approve")
    assert (refused.outcome, refused.reason) == ("refused", "still_refused")
    (after,) = [r for r in await asks(owner) if r["id"] == row["id"]]
    assert after["state"] == "open" and after["original_ask"] is not None

    await configure(owner, limits(new_goals=5))
    won = await recovery(runtime).decide(card.tokens["approve"], row["id"], "approve")
    assert (won.outcome, won.state) == ("won", "run")
    (after,) = [r for r in await asks(owner) if r["id"] == row["id"]]
    assert (after["state"], after["original_ask"]) == ("run", None)
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goals WHERE source_key = $1", source.key
        )
        == 1
    )
    again = await recovery(runtime).decide(card.tokens["approve"], row["id"], "approve")
    assert again.outcome == "duplicate"
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goals WHERE source_key = $1", source.key
        )
        == 1
    )
    # The other half of the pair is dead once the ask is decided.
    other = await recovery(runtime).decide(card.tokens["reject"], row["id"], "reject")
    assert other.outcome == "refused"


async def test_drop_ends_the_ask_without_admitting_anything(world):  # noqa: F811
    owner, runtime, _ = world
    source, row = await refused_ask(owner, runtime)
    (card,) = (await recovery(runtime).cards()).cards
    dropped = await recovery(runtime).decide(card.tokens["reject"], row["id"], "reject")
    assert (dropped.outcome, dropped.state) == ("won", "dropped")
    (after,) = [r for r in await asks(owner) if r["id"] == row["id"]]
    assert (after["state"], after["original_ask"]) == ("dropped", None)
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goals WHERE source_key = $1", source.key
        )
        == 0
    )


async def test_a_token_asked_for_the_wrong_action_is_refused(world):  # noqa: F811
    owner, runtime, _ = world
    _, row = await refused_ask(owner, runtime)
    (card,) = (await recovery(runtime).cards()).cards
    result = await recovery(runtime).decide(card.tokens["reject"], row["id"], "approve")
    assert (result.outcome, result.reason) == ("refused", "action_mismatch")


async def card_for(runtime, ask_id, facts=None):
    batch = await recovery(runtime, facts).cards()
    return next(c for c in batch.cards if c.ask_id == ask_id)


async def test_run_on_a_failed_goal_admits_a_new_goal_from_the_retry_source(world):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await owner_goal(runtime, owner)
    await owner.execute("UPDATE switchboard.goals SET exposed = true WHERE id = $1", goal_id)
    await reporter(runtime, Sender()).sweep()
    (row,) = await asks(owner)
    card = await card_for(runtime, row["id"])
    won = await recovery(runtime).decide(card.tokens["approve"], row["id"], "approve")
    assert (won.outcome, won.state) == ("won", "run")
    retry = await owner.fetchrow(
        "SELECT * FROM switchboard.goals WHERE source_ref = $1", f"owner_retry:{row['id']}"
    )
    assert retry is not None and retry["id"] != goal_id and retry["origin"] == "owner"
    assert retry["exposed"] is True  # the failed goal's label applies again
    assert retry["caused_by_goal"] is None
    again = await recovery(runtime).decide(card.tokens["approve"], row["id"], "approve")
    assert again.outcome == "duplicate"
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goals WHERE source_ref = $1",
            f"owner_retry:{row['id']}",
        )
        == 1
    )
    # The retry fails in turn: the owner is told again, under the retry's own ask.
    await owner.execute("UPDATE switchboard.goals SET status = 'failed' WHERE id = $1", retry["id"])
    await reporter(runtime, Sender()).sweep()
    again_asks = [r for r in await asks(owner) if r["goal_id"] == retry["id"]]
    assert len(again_asks) == 1 and again_asks[0]["state"] == "open"


async def test_a_capability_unavailable_ask_cannot_be_run(world):  # noqa: F811
    owner, runtime, owner_id = world
    await owner_goal(runtime, owner)
    await reporter(runtime, Sender()).sweep()
    (row,) = await asks(owner)
    await owner.execute(
        "UPDATE switchboard.pending_asks SET cause = 'capability_unavailable' WHERE id = $1",
        row["id"],
    )
    async with runtime.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
        presentation = await od.present(
            conn,
            row["subject_id"],
            surface="dashboard",
            route_ref="owner:dashboard",
            actions=("approve",),
            expires_at=await conn.fetchval("SELECT clock_timestamp() + interval '1 hour'"),
        )
    result = await recovery(runtime).decide(presentation.tokens["approve"], row["id"], "approve")
    assert (result.outcome, result.reason) == ("refused", "cannot_run")
    (after,) = await asks(owner)
    assert after["state"] == "open"


async def test_a_refused_forwarded_run_keeps_its_exposure_and_a_failed_run_is_told(world):  # noqa: F811
    owner, runtime, _ = world
    facts = Evidence("message", "p-2", owner=True, forwarded=True, outside_content=True)
    source, _ = await refuse_owner_message(owner, runtime, facts)
    await reporter(runtime, Sender()).sweep()
    (row,) = [r for r in await asks(owner) if r["cause"] == "admission_refused"]
    await configure(owner, limits(new_goals=5))
    card = await card_for(runtime, row["id"], facts)
    won = await recovery(runtime, facts).decide(card.tokens["approve"], row["id"], "approve")
    assert won.state == "run"
    goal = await owner.fetchrow("SELECT * FROM switchboard.goals WHERE source_key = $1", source.key)
    assert goal["exposed"] is True
    # That goal fails; its ask must not collide with the refused ask that shares the source key.
    await owner.execute("UPDATE switchboard.goals SET status = 'failed' WHERE id = $1", goal["id"])
    await reporter(runtime, Sender()).sweep()
    told = [r for r in await asks(owner) if r["goal_id"] == goal["id"]]
    assert len(told) == 1 and told[0]["source_key"] == source.key + "#run"


async def test_a_pending_ask_token_never_redeems_an_approval_and_the_reverse(world):  # noqa: F811
    owner, runtime, owner_id = world
    _, row = await refused_ask(owner, runtime)
    (card,) = (await recovery(runtime).cards()).cards
    async with runtime.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
        decided = []

        async def decide(c, verb):
            decided.append(verb)
            return {}

        # The pending-ask token presented as an approval of the same reference.
        wrong = await od.redeem(
            conn,
            card.tokens["approve"],
            kind="approval",
            source_ref=str(row["id"]),
            expected_action="approve",
            owner_entity=owner_id,
            surface="dashboard",
            current_action_digest=od.digest_of({"kind": "pending_ask", "ask": str(row["id"])}),
            decide=decide,
        )
        assert (wrong.outcome, wrong.reason) == ("refused", "subject_mismatch")
        assert decided == []
        # An approval subject's token, redeemed as this pending ask.
        subject = await od.open_subject(
            conn,
            kind="approval",
            source_ref=str(uuid4()),
            owner_entity=owner_id,
            content_digest=od.digest_of("c"),
            action_digest=od.digest_of("a"),
        )
        card_for_approval = await od.present(
            conn,
            subject.id,
            surface="dashboard",
            route_ref="owner:dashboard",
            actions=("approve",),
            expires_at=await conn.fetchval("SELECT clock_timestamp() + interval '1 hour'"),
        )
        reverse = await od.redeem(
            conn,
            card_for_approval.tokens["approve"],
            kind="pending_ask",
            source_ref=str(row["id"]),
            expected_action="approve",
            owner_entity=owner_id,
            surface="dashboard",
            current_action_digest=od.digest_of("a"),
            decide=decide,
        )
        assert (reverse.outcome, reverse.reason) == ("refused", "subject_mismatch")
        assert decided == []


async def test_other_subject_kinds_still_refuse_a_decision(world):  # noqa: F811
    owner, runtime, owner_id = world
    async with runtime.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
        ref = str(uuid4())
        subject = await od.open_subject(
            conn,
            kind="goal_answer",
            source_ref=ref,
            owner_entity=owner_id,
            content_digest=od.digest_of("c"),
            action_digest=od.digest_of("a"),
        )
        card = await od.present(
            conn,
            subject.id,
            surface="dashboard",
            route_ref="owner:dashboard",
            actions=("approve",),
            expires_at=await conn.fetchval("SELECT clock_timestamp() + interval '1 hour'"),
        )

        async def decide(c, verb):
            raise AssertionError("must not run")

        result = await od.redeem(
            conn,
            card.tokens["approve"],
            kind="goal_answer",
            source_ref=ref,
            expected_action="approve",
            owner_entity=owner_id,
            surface="dashboard",
            current_action_digest=od.digest_of("a"),
            decide=decide,
        )
        assert (result.outcome, result.reason) == ("refused", "action_unsupported")
