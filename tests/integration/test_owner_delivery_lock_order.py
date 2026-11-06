"""Forced-contention proof of the owner-delivery lock order (bu-dhloi1.48, RFC 0038 S6).

A gate transaction holds one row, so the path under test stops part-way with its earlier
locks held. ``_until_waiting`` then reads which statement each session is blocked on, and
competitors start against that exact state. Releasing the gate lets PostgreSQL's detector
judge the interleaving: a cycle surfaces as ``DeadlockDetectedError``. Each path below
therefore shows the order it really locks in, not only that a race happened to pass.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

from butlers.core import owner_delivery as od
from butlers.core.approval_delivery_worker import (
    ApprovalDeliveryWorker,
    DeliveryClaim,
    HandoffResult,
)
from butlers.modules.approvals.delivery_lifecycle import (
    PendingTransition,
    defer_pending_action,
    transition_pending_action,
)
from butlers.modules.approvals.delivery_recovery import (
    ApprovalDeliveryRenderer,
    ApprovalDeliveryRepository,
)
from butlers.modules.approvals.goal_park import (
    GoalLineage,
    cancel_goal_effects,
    park_goal_effect,
)
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.owner_delivery_bridge import owner_presentation_for
from tests.integration.test_owner_delivery_ledger import (
    _RECIPIENT,
    _enable,
    _HeldHandoff,
    _park,
    _present,
    _redeem,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_DEADLINE_S = 20

# What a gate holds.
_HOLD_ACTION = "SELECT 1 FROM pending_actions WHERE id = $1 FOR UPDATE"
_HOLD_SUBJECT = "SELECT 1 FROM public.owner_delivery_subjects WHERE source_ref = $1 FOR UPDATE"
_HOLD_INTENT = "SELECT 1 FROM approval_delivery_intents WHERE action_id = $1 FOR UPDATE"
_HOLD_RFC = "SELECT 1 FROM approval_delivery_presentations WHERE id = $1 FOR UPDATE"
_HOLD_CARD = "SELECT 1 FROM public.owner_delivery_presentations WHERE id = $1 FOR UPDATE"

# The statement a session is blocked on, as pg_stat_activity shows it.
_ON_MARKER_ACTION = "FOR UPDATE OF pa"
_ON_DECISION_ACTION = "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE"
_ON_SUBJECT = "FROM public.owner_delivery_subjects WHERE id = $1 FOR UPDATE"
_ON_SUBJECT_CLOSE = "UPDATE public.owner_delivery_subjects SET state = $3"
_ON_INTENT = "FROM approval_delivery_intents WHERE action_id = $1 FOR UPDATE"
_ON_RFC_DEFER = "FROM approval_delivery_presentations AS p WHERE p.intent_id = $1"
_ON_RFC_RESULT = "state = 'handoff_started' FOR UPDATE"
_ON_RFC_RETRY = "state = 'claimed' FOR UPDATE"
_ON_RFC_HEARTBEAT = "SET claim_expires_at = clock_timestamp()"
_ON_CARD = "FROM public.owner_delivery_presentations WHERE id = $1 FOR UPDATE"


@pytest.fixture
async def contended(db) -> asyncpg.Pool:
    """A fresh core+approvals database, rollout on, with room for every waiter and the gates."""
    pool = await db("core", "approvals", max_size=12)
    await pool.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true, worker_enabled = false"
    )
    if not await pool.fetchval("SELECT count(*) FROM public.entities WHERE 'owner' = ANY(roles)"):
        await pool.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Synthetic owner', 'person', ARRAY['owner'])"
        )
    await _enable(pool)
    return pool


@asynccontextmanager
async def _holding(pool: asyncpg.Pool, statement: str, *args: Any) -> AsyncIterator[None]:
    async with pool.acquire() as gate, gate.transaction():
        assert await gate.fetch(statement, *args), "the gate must hold a real row"
        yield


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


async def _until_waiting(pool: asyncpg.Pool, *statements: str) -> None:
    """Return once exactly these statements are blocked on a lock, or fail naming the others."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _DEADLINE_S / 2
    while True:
        rows = await pool.fetch(
            "SELECT query FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
        )
        waiting = [_normalized(row["query"]) for row in rows]
        unmatched = list(waiting)
        for statement in statements:
            match = next((q for q in unmatched if statement in q), None)
            if match is not None:
                unmatched.remove(match)
        if len(waiting) == len(statements) and not unmatched:
            return
        if loop.time() >= deadline:
            pytest.fail(f"expected waiters {statements}, saw {waiting}")
        await asyncio.sleep(0.02)


async def _finish(*tasks: Awaitable[Any]) -> list[Any]:
    """Every result, failing on any exception: a cycle arrives as DeadlockDetectedError."""
    results = await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True),
        _DEADLINE_S,
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    return results


@dataclass(frozen=True)
class _Card:
    action_id: uuid.UUID
    repository: ApprovalDeliveryRepository
    claim: DeliveryClaim
    tokens: dict[str, str]
    card_id: uuid.UUID


async def _claimed_card(pool: asyncpg.Pool, *, handed_off: bool = False) -> _Card:
    """A bridged card whose worker claim is prepared and, optionally, past its marker."""
    action_id = await _park(pool, card=True)
    repository = ApprovalDeliveryRepository(pool)
    claim = await repository.claim_next()
    assert claim is not None
    tokens = await repository.prepare_owner_delivery(claim, _RECIPIENT)
    assert tokens is not None
    async with pool.acquire() as conn:
        card_id = await owner_presentation_for(
            conn, claim.presentation_id, claim.presentation_generation
        )
    assert card_id is not None
    if handed_off:
        assert await repository.mark_handoff_started(claim) is True
    return _Card(action_id, repository, claim, tokens, card_id)


async def _decide(pool: asyncpg.Pool, action_id: uuid.UUID) -> PendingTransition:
    return await transition_pending_action(
        pool,
        action_id=action_id,
        target_status=ActionStatus.REJECTED,
        decided_by="owner",
        event_actor="owner",
        event_reason="synthetic decision",
    )


async def _settled(pool: asyncpg.Pool, card: _Card) -> dict[str, Any]:
    row = await pool.fetchrow(
        "SELECT pa.status AS action, s.state AS subject, p.state AS card, "
        "(SELECT state FROM approval_delivery_presentations WHERE id = $2) AS rfc, "
        "ARRAY(SELECT DISTINCT outcome FROM approval_delivery_attempts "
        "      WHERE presentation_id = $2 ORDER BY outcome) AS attempts "
        "FROM pending_actions pa "
        "JOIN public.owner_delivery_subjects s ON s.source_ref = pa.id::text "
        "JOIN public.owner_delivery_presentations p ON p.id = $3 "
        "WHERE pa.id = $1",
        card.action_id,
        card.claim.presentation_id,
        card.card_id,
    )
    return dict(row)


async def test_marker_holding_the_action_makes_a_decision_queue_behind_it(contended) -> None:
    card = await _claimed_card(contended)

    async with _holding(contended, _HOLD_SUBJECT, str(card.action_id)):
        marking = asyncio.create_task(card.repository.mark_handoff_started(card.claim))
        await _until_waiting(contended, _ON_SUBJECT)
        deciding = asyncio.create_task(_decide(contended, card.action_id))
        await _until_waiting(contended, _ON_SUBJECT, _ON_DECISION_ACTION)

    marked, decided = await _finish(marking, deciding)

    assert marked is True and decided.changed
    assert await _settled(contended, card) == {
        "action": "rejected",
        "subject": "decided",
        "card": "unknown",
        "rfc": "cancelled",
        "attempts": ["started"],
    }
    # The send was already committed to, so its late result is still receipted.
    assert await card.repository.complete_handoff(
        card.claim, HandoffResult("confirmed", provider_reference="90")
    )
    settled = await _settled(contended, card)
    assert settled["card"] == "sent" and settled["attempts"] == ["confirmed", "started"]


async def test_decision_holding_the_action_refuses_the_marker_behind_it(contended) -> None:
    card = await _claimed_card(contended)

    async with _holding(contended, _HOLD_INTENT, card.action_id):
        deciding = asyncio.create_task(_decide(contended, card.action_id))
        await _until_waiting(contended, _ON_INTENT)
        marking = asyncio.create_task(card.repository.mark_handoff_started(card.claim))
        await _until_waiting(contended, _ON_INTENT, _ON_MARKER_ACTION)

    decided, marked = await _finish(deciding, marking)

    assert decided.changed and marked is False
    assert await _settled(contended, card) == {
        "action": "rejected",
        "subject": "decided",
        # Never handed off, so nothing outside the ledger renders it: the close retracts it.
        "card": "retracted",
        "rfc": "cancelled",
        "attempts": [],
    }


async def test_close_holding_the_subject_queues_record_send_and_the_result(contended) -> None:
    """close_for_authority holds the subject and then needs the RFC 0023 rows."""
    card = await _claimed_card(contended, handed_off=True)

    async def settle() -> str:
        async with contended.acquire() as conn, conn.transaction():
            return await od.record_send(conn, card.card_id, outcome="sent", provider_ref="91")

    async with _holding(contended, _HOLD_INTENT, card.action_id):
        deciding = asyncio.create_task(_decide(contended, card.action_id))
        await _until_waiting(contended, _ON_INTENT)
        completing = asyncio.create_task(
            card.repository.complete_handoff(
                card.claim, HandoffResult("confirmed", provider_reference="91")
            )
        )
        await _until_waiting(contended, _ON_INTENT, _ON_SUBJECT)
        settling = asyncio.create_task(settle())
        await _until_waiting(contended, _ON_INTENT, _ON_SUBJECT, _ON_SUBJECT)

    decided, completed, settled_outcome = await _finish(deciding, completing, settling)

    assert decided.changed and completed is True and settled_outcome == "sent"
    assert await _settled(contended, card) == {
        "action": "rejected",
        "subject": "decided",
        "card": "sent",
        "rfc": "cancelled",
        "attempts": ["confirmed", "started"],
    }


@pytest.mark.parametrize(
    ("decision", "blocked_on"),
    [
        pytest.param("transition", _ON_SUBJECT_CLOSE, id="close-for-authority"),
        pytest.param("token", _ON_SUBJECT, id="token-redemption"),
    ],
)
async def test_result_holding_the_subject_queues_a_decision_holding_the_action(
    contended, decision: str, blocked_on: str
) -> None:
    """The cycle #5553 fixed: result holds subject+RFC row, decision holds action+wants subject."""
    card = await _claimed_card(contended, handed_off=True)

    async def decide() -> Any:
        if decision == "transition":
            return await _decide(contended, card.action_id)
        return await _redeem(contended, card.action_id, card.tokens["reject"], surface="telegram")

    async with _holding(contended, _HOLD_CARD, card.card_id):
        completing = asyncio.create_task(
            card.repository.complete_handoff(
                card.claim, HandoffResult("confirmed", provider_reference="92")
            )
        )
        await _until_waiting(contended, _ON_CARD)
        deciding = asyncio.create_task(decide())
        await _until_waiting(contended, _ON_CARD, blocked_on)

    completed, decided = await _finish(completing, deciding)

    assert completed is True
    assert decided.changed if decision == "transition" else decided.outcome == "won"
    assert await _settled(contended, card) == {
        "action": "rejected",
        "subject": "decided",
        "card": "sent",
        "rfc": "delivered",
        "attempts": ["confirmed", "started"],
    }


async def test_defer_never_waits_for_a_held_subject(contended) -> None:
    card = await _claimed_card(contended)

    async with _holding(contended, _HOLD_SUBJECT, str(card.action_id)):
        deferred = await asyncio.wait_for(
            defer_pending_action(contended, action_id=card.action_id, hours=1, actor="owner"),
            _DEADLINE_S / 2,
        )

    assert deferred.changed
    states = await contended.fetch(
        "SELECT presentation_generation AS generation, state "
        "FROM approval_delivery_presentations ORDER BY presentation_generation"
    )
    assert [(r["generation"], r["state"]) for r in states] == [(1, "superseded"), (2, "ready")]


async def test_defer_holding_the_rfc_rows_never_cycles_with_a_result_and_a_decision(
    contended,
) -> None:
    """Defer takes action then RFC rows; a result takes subject then RFC rows, never the action."""
    card = await _claimed_card(contended, handed_off=True)

    async with _holding(contended, _HOLD_RFC, card.claim.presentation_id):
        deferring = asyncio.create_task(
            defer_pending_action(contended, action_id=card.action_id, hours=1, actor="owner")
        )
        await _until_waiting(contended, _ON_RFC_DEFER)
        completing = asyncio.create_task(
            card.repository.complete_handoff(
                card.claim, HandoffResult("confirmed", provider_reference="93")
            )
        )
        await _until_waiting(contended, _ON_RFC_DEFER, _ON_RFC_RESULT)
        deciding = asyncio.create_task(_decide(contended, card.action_id))
        await _until_waiting(contended, _ON_RFC_DEFER, _ON_RFC_RESULT, _ON_DECISION_ACTION)

    deferred, completed, decided = await _finish(deferring, completing, deciding)

    assert deferred.changed and completed is True and decided.changed
    assert await _settled(contended, card) == {
        "action": "rejected",
        "subject": "decided",
        "card": "sent",
        "rfc": "delivered",
        "attempts": ["confirmed", "started"],
    }
    successor = await contended.fetchval(
        "SELECT state FROM approval_delivery_presentations WHERE presentation_generation = 2"
    )
    assert successor == "cancelled"


async def test_retry_reclaim_and_heartbeat_hold_only_the_presentation(contended) -> None:
    """With action, subject and intent all held, none of these has anything to wait for."""
    card = await _claimed_card(contended)

    async with (
        _holding(contended, _HOLD_ACTION, card.action_id),
        _holding(contended, _HOLD_SUBJECT, str(card.action_id)),
        _holding(contended, _HOLD_INTENT, card.action_id),
    ):
        retried = await asyncio.wait_for(
            card.repository.record_prestart_retry(card.claim, "transport_unavailable"),
            _DEADLINE_S / 2,
        )
        assert retried is True
        await contended.execute(
            "UPDATE approval_delivery_presentations SET next_attempt_at = clock_timestamp() "
            "WHERE id = $1",
            card.claim.presentation_id,
        )
        reclaimed = await asyncio.wait_for(card.repository.claim_next(), _DEADLINE_S / 2)
        assert reclaimed is not None
        assert reclaimed.presentation_id == card.claim.presentation_id
        assert reclaimed.claim_fence == card.claim.claim_fence + 1
        assert await asyncio.wait_for(card.repository.heartbeat(reclaimed), _DEADLINE_S / 2)


async def test_cancel_ineligible_holds_only_the_presentation(contended) -> None:
    action_id = await _park(contended)
    await _present(contended, action_id, "telegram")
    await contended.execute(
        "UPDATE pending_actions SET expires_at = clock_timestamp() + interval '50 milliseconds' "
        "WHERE id = $1",
        action_id,
    )
    await asyncio.sleep(0.1)
    repository = ApprovalDeliveryRepository(contended)

    async with (
        _holding(contended, _HOLD_ACTION, action_id),
        _holding(contended, _HOLD_SUBJECT, str(action_id)),
        _holding(contended, _HOLD_INTENT, action_id),
    ):
        cancelled = await asyncio.wait_for(
            repository.cancel_ineligible_presentations(), _DEADLINE_S / 2
        )

    assert cancelled == 1


async def test_presentation_only_paths_do_contend_on_the_presentation(contended) -> None:
    """The control for the two tests above: the presentation row is what these paths need."""
    action_id = await _park(contended)
    rfc_id = await contended.fetchval(
        "SELECT p.id FROM approval_delivery_presentations p "
        "JOIN approval_delivery_intents i ON i.id = p.intent_id WHERE i.action_id = $1",
        action_id,
    )
    repository = ApprovalDeliveryRepository(contended)

    async with _holding(contended, _HOLD_RFC, rfc_id):
        assert await asyncio.wait_for(repository.claim_next(), _DEADLINE_S / 2) is None
    claim = await repository.claim_next()
    assert claim is not None

    async with _holding(contended, _HOLD_RFC, rfc_id):
        beating = asyncio.create_task(repository.heartbeat(claim))
        await _until_waiting(contended, _ON_RFC_HEARTBEAT)
        retrying = asyncio.create_task(
            repository.record_prestart_retry(claim, "transport_unavailable")
        )
        await _until_waiting(contended, _ON_RFC_HEARTBEAT, _ON_RFC_RETRY)

    assert await _finish(beating, retrying) == [True, True]


async def test_bridged_result_does_not_wait_for_a_held_action_row(contended) -> None:
    """The executor holds the action across the tool call; a bridged result must not queue."""
    action_id = await _park(contended, card=True)
    runtime = _HeldHandoff(HandoffResult("confirmed", provider_reference="94"))
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(contended), ApprovalDeliveryRenderer(), runtime
    )
    delivering = asyncio.create_task(worker.process_one())
    await asyncio.wait_for(runtime.handed.wait(), _DEADLINE_S / 2)
    assert all(
        a["callback_token"].startswith("od1:")
        for a in runtime.envelopes[0]["actions"]
        if "callback_token" in a
    )

    async with _holding(contended, _HOLD_ACTION, action_id):
        runtime.release.set()
        assert await asyncio.wait_for(delivering, _DEADLINE_S / 2) is True
        row = await contended.fetchrow(
            "SELECT p.state, p.provider_ref FROM public.owner_delivery_presentations p "
            "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id "
            "WHERE s.source_ref = $1",
            str(action_id),
        )

    assert dict(row) == {"state": "sent", "provider_ref": "94"}


async def _goal_effects(pool: asyncpg.Pool, goal_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Two presented effects, ``(low, high)``, the high id parked first so heap order differs."""
    low, high = sorted((uuid.uuid4(), uuid.uuid4()))
    for effect_id in (high, low):
        await park_goal_effect(
            pool,
            lineage=GoalLineage(goal_id, uuid.uuid4(), 0, False, "owner", effect_id),
            tool_name="relationship_assert_fact",
            tool_args={"effect": str(effect_id)},
            allow_standing_rule=False,
            recipient_is_owner=True,
            origin_butler="relationship",
        )
        await _present(pool, effect_id, "telegram")
    return low, high


async def test_goal_cancel_batch_takes_the_lowest_action_first(contended) -> None:
    goal_id = uuid.uuid4()
    low, high = await _goal_effects(contended, goal_id)

    async with _holding(contended, _HOLD_ACTION, low):
        cancelling = asyncio.create_task(cancel_goal_effects(contended, goal_id, [high, low]))
        await _until_waiting(contended, _ON_DECISION_ACTION)
        # Blocked on the low id, the batch holds nothing yet: the high id is still free.
        async with contended.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM pending_actions WHERE id = $1 FOR UPDATE NOWAIT", high
            )

    [ended] = await _finish(cancelling)
    assert ended == [low, high]


async def test_overlapping_goal_cancel_batches_never_cycle(contended) -> None:
    goal_id = uuid.uuid4()
    low, high = await _goal_effects(contended, goal_id)

    async with _holding(contended, _HOLD_ACTION, high):
        first = asyncio.create_task(cancel_goal_effects(contended, goal_id, [low, high]))
        await _until_waiting(contended, _ON_DECISION_ACTION)
        second = asyncio.create_task(cancel_goal_effects(contended, goal_id, [high, low]))
        await _until_waiting(contended, _ON_DECISION_ACTION, _ON_DECISION_ACTION)

    assert await _finish(first, second) == [[low, high], []]


@pytest.mark.parametrize("attempt", range(5))
async def test_every_path_racing_on_one_card_never_deadlocks(contended, attempt: int) -> None:
    """An unforced sweep over the whole order. It can only ever find a cycle, not disprove one."""
    card = await _claimed_card(contended)

    async def hand_off() -> bool:
        if not await card.repository.mark_handoff_started(card.claim):
            return False
        return await card.repository.complete_handoff(
            card.claim, HandoffResult("confirmed", provider_reference=f"95{attempt}")
        )

    racers: list[Callable[[], Awaitable[Any]]] = [
        hand_off,
        lambda: _redeem(contended, card.action_id, card.tokens["reject"], surface="telegram"),
        lambda: defer_pending_action(contended, action_id=card.action_id, hours=1, actor="owner"),
        lambda: card.repository.heartbeat(card.claim),
        lambda: card.repository.cancel_ineligible_presentations(),
    ]

    results = await _finish(*(racer() for racer in racers))

    assert results[1].outcome == "won"
    assert (await _settled(contended, card))["action"] == "rejected"
